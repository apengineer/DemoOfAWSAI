"""User -> session mapping.

AgentCore Runtime intentionally does NOT map users to sessions for you - the
client backend owns that relationship (and thereby controls isolation). This
module is that owner. Each (tenant, user, attempt) gets a distinct
``runtimeSessionId``; when deployed, that id is passed to
``InvokeAgentRuntime`` so each maps to its own isolated microVM.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from .memory_store import MemoryStore, make_actor_id


def _microvm_repr(session_id: str) -> str:
    """A stable, illustrative microVM identifier for the UI. Locally there is
    no real microVM; once deployed to AgentCore Runtime, the runtimeSessionId
    below is what provisions a dedicated, isolated microVM per session."""
    return "mvm-" + hashlib.sha256(session_id.encode()).hexdigest()[:10]


def _new_session_id(tenant_id: str, user_id: str) -> str:
    # AgentCore InvokeAgentRuntime requires runtimeSessionId of 33-256 chars.
    # A UTC-timestamp prefix keeps ids chronologically sortable (for "resume the
    # latest attempt"); a uuid suffix guarantees uniqueness and length >= 33.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{tenant_id}-{user_id}-{ts}-{uuid.uuid4().hex}"


def start_session(store: MemoryStore, tenant_id: str, user_id: str, force_new: bool = False) -> Dict[str, Any]:
    actor_id = make_actor_id(tenant_id, user_id)
    # Stores return sessions in chronological order, so the latest attempt is
    # last (AgentCore orders by createdAt; local orders by timestamped name).
    existing = store.list_sessions(actor_id)

    if existing and not force_new:
        session_id = existing[-1]
        resumed = True
        state = store.load_state(actor_id, session_id) or {}
    else:
        session_id = _new_session_id(tenant_id, user_id)
        resumed = False
        state = {}

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "actor_id": actor_id,
        "session_id": session_id,
        "runtime_session_id": session_id,
        "microvm": _microvm_repr(session_id),
        "resumed": resumed,
        "state": state,
        "prior_sessions": existing,
    }
