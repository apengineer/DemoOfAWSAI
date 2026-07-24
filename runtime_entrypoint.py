"""AgentCore Runtime entrypoint (canonical).

This file lives at the project root so the direct-code-deploy zip includes the
whole project (backend/ and config/). It wraps the same EkycAgentService used
locally in a BedrockAgentCoreApp, so the agent runs unchanged on AgentCore
Runtime.

When deployed:
* Each runtimeSessionId gets its own isolated microVM (Runtime isolation).
* AgentCore Memory is reached via the container's execution role (same code).
* OpenTelemetry is auto-instrumented, so traces/metrics flow to CloudWatch
  GenAI observability with no extra code.

Invoke payload (JSON):
    {"tenant_id": "...", "user_id": "...", "session_id": "...", "prompt": "..."}

The client should pass the SAME value as both the InvokeAgentRuntime
runtimeSessionId and payload.session_id so Runtime isolation and Memory
scoping line up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bedrock_agentcore import BedrockAgentCoreApp  # noqa: E402

from backend.agent import EkycAgentService  # noqa: E402
from backend.memory_store import get_memory_store  # noqa: E402

app = BedrockAgentCoreApp()
_service = EkycAgentService(get_memory_store())


@app.entrypoint
def invoke(payload):
    tenant_id = payload.get("tenant_id", "demo")
    user_id = payload.get("user_id", "user")
    session_id = payload.get("session_id") or f"{tenant_id}-{user_id}"
    message = payload.get("prompt") or payload.get("message", "")

    result = _service.run_turn(tenant_id, user_id, session_id, message)
    return {
        "reply": result["reply"],
        "actor_id": result["actor_id"],
        "completed_steps": result["state"].get("completed_steps", []),
        "status": result["state"].get("status"),
        "decision": result["state"].get("decision"),
    }


if __name__ == "__main__":
    app.run()
