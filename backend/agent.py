"""Strands eKYC onboarding agent (Amazon Bedrock).

The backend is intentionally *stateless*: on every turn we rebuild the agent,
rehydrating conversation history and onboarding state from AgentCore Memory.
This makes the isolation/observability story crisp - the process holds no
durable state; AgentCore Memory is the single source of truth, scoped per
tenant+user.
"""
from __future__ import annotations

from typing import Any, Dict, List

from strands import Agent
from strands.models import BedrockModel

from .config import app_config, flow_config
from .memory_store import MemoryStore, make_actor_id
from .observability import recorder
from .tools import SessionContext, build_tools, detect_forced_failures


def _to_strands_messages(history: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = []
    for turn in history:
        role = turn.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        msgs.append({"role": role, "content": [{"text": turn.get("content", "")}]})
    return msgs


def _extract_text(result: Any) -> str:
    msg = getattr(result, "message", None)
    if isinstance(msg, dict):
        parts = [b.get("text", "") for b in msg.get("content", []) if isinstance(b, dict)]
        text = "".join(parts).strip()
        if text:
            return text
    return str(result).strip()


def _build_system_prompt(ctx: SessionContext) -> str:
    flow = ctx.flow
    steps_desc = "\n".join(
        f"  {i+1}. [{s['id']}] {s['title']} -> tool `{s.get('tool')}`: {s['description']}"
        for i, s in enumerate(flow["steps"])
    )
    ctx.ensure_state()
    state = ctx.state
    next_step = ctx.next_pending_step()
    completed = ", ".join(state.get("completed_steps", [])) or "none yet"
    collected = ", ".join(f"{k}={v}" for k, v in state.get("collected_data", {}).items()) or "none yet"

    return f"""You are a digital identity onboarding assistant guiding a person through a
remote eKYC ({flow.get('assurance_level', 'IAL2')}) onboarding journey called "{flow['name']}".

The journey has these ordered steps, each advanced by calling its tool:
{steps_desc}

Operating rules:
- Guide the applicant through ONE step at a time, in order. Be warm, brief and clear.
- Keep a professional tone suitable for a bank. Do not use emojis.
- Write plain conversational text. Do NOT use markdown formatting (no **bold**, no
  headings, no bullet characters) - the chat UI shows raw text.
- NEVER claim a check passed or failed on your own. You MUST call the step's tool and
  report exactly what the tool returns. The tools are the only source of verification truth.
- Before document verification, make sure you have collected personal details.
- If a tool reports a FAILURE, explain it plainly and invite the applicant to retry that step.
- When all checks are done, call `finalize_decision` and tell the applicant the outcome.
- Do not ask for real personal data; this is a demo, accept whatever the applicant provides.

RESUMED ONBOARDING STATE (loaded from AgentCore Memory for this tenant+user):
- Overall status: {state.get('status')}
- Completed steps: {completed}
- Collected data: {collected}
- Next pending step: {next_step or 'all steps complete'}

If steps are already completed, do NOT repeat them. Briefly welcome the applicant back,
summarise progress, and continue from the next pending step.
"""


class EkycAgentService:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.cfg = app_config()
        self.flow = flow_config()["flow"]
        self.mock = flow_config().get("mock", {})
        aws = self.cfg["aws"]
        self.model = BedrockModel(
            region_name=aws["region"],
            model_id=aws["model_id"],
            temperature=aws.get("temperature", 0.3),
            max_tokens=aws.get("max_tokens", 1024),
        )

    def run_turn(self, tenant_id: str, user_id: str, session_id: str, message: str) -> Dict[str, Any]:
        actor_id = make_actor_id(tenant_id, user_id)

        with recorder.trace(session_id, name=f"onboarding turn ({tenant_id}/{user_id})"):
            with recorder.span("agent.session", tenant=tenant_id, user=user_id, actor=actor_id,
                               session=session_id, model=self.cfg["aws"]["model_id"]):
                # 1) Rehydrate state + history from Memory (scoped to actor+session)
                state = self.store.load_state(actor_id, session_id) or {}
                history = self.store.load_history(actor_id, session_id, k=20)

                ctx = SessionContext(
                    tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                    flow=self.flow, mock=self.mock, state=state,
                    force_fail=detect_forced_failures(message, self.flow, self.mock),
                )
                ctx.ensure_state()

                # 2) Build a fresh agent with rehydrated context + bound tools
                agent = Agent(
                    model=self.model,
                    messages=_to_strands_messages(history),
                    tools=build_tools(ctx),
                    system_prompt=_build_system_prompt(ctx),
                    callback_handler=None,
                )

                # 3) Reason (Bedrock model + tool calls happen here)
                with recorder.span("agent.reason"):
                    result = agent(message)
                reply = _extract_text(result)

                # 4) Persist the turn + updated state back to Memory
                self.store.record_turn(actor_id, session_id, message, reply)
                self.store.save_state(actor_id, session_id, ctx.state)

        return {
            "reply": reply,
            "state": ctx.state,
            "actor_id": actor_id,
            "trace": recorder.latest(session_id),
        }
