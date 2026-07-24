"""Invoke the deployed AgentCore Runtime from the local website.

In "runtime" mode the website does not run the Strands agent in-process.
Instead it calls Amazon Bedrock AgentCore Runtime via ``InvokeAgentRuntime``,
passing the onboarding ``session_id`` as the ``runtimeSessionId`` so each
(tenant, user, attempt) runs in its own isolated microVM. The heavy lifting
(Bedrock reasoning, tool calls, AgentCore Memory reads/writes) happens inside
the runtime; full traces land in CloudWatch GenAI observability.

Because AgentCore Memory is shared (same memory id), the website still reads
onboarding state/history directly from Memory for the progress panel and the
pause/resume + isolation demos.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

import boto3

from .memory_store import MemoryStore, make_actor_id
from .observability import recorder


def _runtime_session_id(memory_session_id: str) -> str:
    """Derive a valid AgentCore runtimeSessionId (33-256 chars) from the memory
    session id. The memory session id can be any length (and several legacy
    sessions are < 33 chars), but InvokeAgentRuntime requires 33-256. We keep
    the original session id for Memory scoping (passed in the payload) and use
    this deterministic, stable derivation only for the runtime/microVM session
    so the same conversation maps to the same microVM."""
    if 33 <= len(memory_session_id) <= 256:
        return memory_session_id
    digest = hashlib.sha256(memory_session_id.encode()).hexdigest()  # 64 chars
    return f"{memory_session_id}-{digest}"[:256]


class RuntimeAgentClient:
    def __init__(self, runtime_arn: str, region: str, store: MemoryStore):
        if not runtime_arn:
            raise ValueError("AGENTCORE_RUNTIME_ARN is required for agent mode 'runtime'")
        self.runtime_arn = runtime_arn
        self.region = region
        self.store = store
        self._client = boto3.client("bedrock-agentcore", region_name=region)

    def run_turn(self, tenant_id: str, user_id: str, session_id: str, message: str) -> Dict[str, Any]:
        actor_id = make_actor_id(tenant_id, user_id)
        payload = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "prompt": message,
        }

        with recorder.trace(session_id, name=f"runtime invoke ({tenant_id}/{user_id})"):
            runtime_session_id = _runtime_session_id(session_id)
            with recorder.span("runtime.invoke_agent_runtime", arn=self.runtime_arn,
                               runtime_session_id=runtime_session_id, tenant=tenant_id, user=user_id):
                resp = self._client.invoke_agent_runtime(
                    agentRuntimeArn=self.runtime_arn,
                    runtimeSessionId=runtime_session_id,
                    contentType="application/json",
                    accept="application/json",
                    payload=json.dumps(payload).encode("utf-8"),
                )
                body = resp.get("response")
                raw = body.read() if hasattr(body, "read") else body
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "replace")
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {"reply": str(raw)}

            # The microVM already persisted state to AgentCore Memory; read the
            # full state back so the progress panel shows collected data too.
            with recorder.span("memory.load_state", backend="agentcore", session=session_id):
                state = self.store.load_state(actor_id, session_id) or {}
                if not state:
                    # Fall back to the slim state the runtime returned.
                    state = {
                        "completed_steps": data.get("completed_steps", []),
                        "status": data.get("status"),
                        "decision": data.get("decision"),
                    }

        return {
            "reply": data.get("reply", ""),
            "state": state,
            "actor_id": actor_id,
            "trace": recorder.latest(session_id),
        }
