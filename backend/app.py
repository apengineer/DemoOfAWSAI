"""FastAPI backend for the eKYC onboarding demo.

Exposes a small JSON API consumed by the static frontend and serves the
frontend itself. Designed for a live walkthrough of Amazon Bedrock +
AgentCore Runtime (session isolation), Memory (per-tenant/user isolation,
pause/resume) and Observability (per-turn traces).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import EkycAgentService
from .config import PROJECT_ROOT, app_config, flow_config, tenants_config
from .memory_store import get_memory_store, make_actor_id
from .observability import recorder
from .runtime_client import RuntimeAgentClient
from .session_manager import start_session

app = FastAPI(title="eKYC Onboarding Demo", version="1.0.0")

store = get_memory_store()

# Select how the agent runs: in-process Strands ("local") or the deployed
# AgentCore Runtime invoked via ARN ("runtime"). Both expose run_turn().
_cfg = app_config()
_agent_mode = _cfg["agent"]["mode"]
if _agent_mode == "runtime":
    agent_service = RuntimeAgentClient(_cfg["agent"]["runtime_arn"], _cfg["aws"]["region"], store)
else:
    agent_service = EkycAgentService(store)

FRONTEND_DIR = PROJECT_ROOT / "frontend"


# ----------------------------- models -----------------------------
class StartRequest(BaseModel):
    tenant_id: str
    user_id: str
    force_new: bool = False


class ChatRequest(BaseModel):
    tenant_id: str
    user_id: str
    session_id: str
    message: str


class IsolationRequest(BaseModel):
    requesting_tenant: str
    requesting_user: str
    target_tenant: str
    target_user: str
    query: str = "onboarding identity verification status"


# ----------------------------- API -----------------------------
@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    tcfg = tenants_config()
    fcfg = flow_config()["flow"]
    acfg = app_config()
    return {
        "branding": tcfg.get("branding", {}),
        "tenants": tcfg.get("tenants", []),
        "flow": {
            "name": fcfg["name"],
            "assurance_level": fcfg.get("assurance_level"),
            "steps": [{"id": s["id"], "title": s["title"]} for s in fcfg["steps"]],
        },
        "model_id": acfg["aws"]["model_id"],
        "region": acfg["aws"]["region"],
        "memory": store.info(),
        "agent_mode": acfg["agent"]["mode"],
    }


@app.post("/api/session/start")
def session_start(req: StartRequest) -> Dict[str, Any]:
    return start_session(store, req.tenant_id, req.user_id, force_new=req.force_new)


@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict[str, Any]:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    try:
        return agent_service.run_turn(req.tenant_id, req.user_id, req.session_id, req.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc


@app.get("/api/progress")
def progress(tenant_id: str, user_id: str, session_id: str) -> Dict[str, Any]:
    actor_id = make_actor_id(tenant_id, user_id)
    return {
        "actor_id": actor_id,
        "session_id": session_id,
        "state": store.load_state(actor_id, session_id) or {},
        "history": store.load_history(actor_id, session_id, k=50),
    }


@app.get("/api/trace")
def trace(session_id: str) -> Dict[str, Any]:
    return {
        "latest": recorder.latest(session_id),
        "history": recorder.history(session_id),
    }


@app.get("/api/memory/recall")
def memory_recall(tenant_id: str, user_id: str, query: str = "identity verification", kind: str = "semantic") -> Dict[str, Any]:
    actor_id = make_actor_id(tenant_id, user_id)
    return {
        "actor_id": actor_id,
        "kind": kind,
        "records": store.recall_long_term(actor_id, query, kind=kind),
    }


@app.post("/api/isolation/cross-tenant-test")
def cross_tenant(req: IsolationRequest) -> Dict[str, Any]:
    requesting_actor = make_actor_id(req.requesting_tenant, req.requesting_user)
    target_actor = make_actor_id(req.target_tenant, req.target_user)
    probe = getattr(store, "cross_tenant_probe", None)
    if probe is None:
        raise HTTPException(status_code=400, detail="backend does not support isolation probe")
    with recorder.trace(f"isolation-{requesting_actor}", name="cross-tenant isolation probe"):
        with recorder.span("isolation.probe", requesting=requesting_actor, target=target_actor):
            return probe(requesting_actor, target_actor, req.query)


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "healthy"}


# ----------------------------- frontend -----------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
