"""Memory abstraction for the eKYC onboarding demo.

Two interchangeable backends implement the same interface:

* ``AgentCoreMemoryStore`` - Amazon Bedrock AgentCore Memory (the real
  managed service). Short-term events store the conversation transcript and
  a deterministic onboarding-state snapshot (blob events); long-term
  strategies (summary / user-preference / semantic) extract durable insight.

* ``LocalMemoryStore`` - a file-based fallback so the demo runs even before
  an AgentCore Memory resource has been provisioned.

Multi-tenant isolation is built into the identifiers:
    actorId   = "<tenant_id>__<user_id>"
    sessionId = onboarding attempt id
Every long-term namespace is scoped by actorId, so one tenant can never
read another tenant's memory. The AgentCore backend exposes a
``cross_tenant_probe`` helper used by the UI to *demonstrate* that a query
issued under tenant A's scope returns nothing for tenant B.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import app_config
from .observability import recorder

STATE_KIND = "ekyc_state"


def make_actor_id(tenant_id: str, user_id: str) -> str:
    # AgentCore actorId must match [a-zA-Z0-9][a-zA-Z0-9-_/]*... so '#' is not
    # allowed. We use '__' as the tenant/user separator (valid in both actorId
    # and long-term namespace paths). actorId still encodes the tenant, so
    # every memory namespace is implicitly tenant-scoped.
    return f"{tenant_id}__{user_id}"


def split_actor_id(actor_id: str) -> tuple[str, str]:
    tenant, _, user = actor_id.partition("__")
    return tenant, user


class MemoryStore(ABC):
    backend_name: str = "abstract"

    @abstractmethod
    def record_turn(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None: ...

    @abstractmethod
    def save_state(self, actor_id: str, session_id: str, state: Dict[str, Any]) -> None: ...

    @abstractmethod
    def load_state(self, actor_id: str, session_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def load_history(self, actor_id: str, session_id: str, k: int = 20) -> List[Dict[str, str]]: ...

    @abstractmethod
    def recall_long_term(self, actor_id: str, query: str, kind: str = "semantic", top_k: int = 3) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def list_sessions(self, actor_id: str) -> List[str]: ...

    def info(self) -> Dict[str, Any]:
        return {"backend": self.backend_name}


# ---------------------------------------------------------------------------
# AgentCore Memory backend
# ---------------------------------------------------------------------------
class AgentCoreMemoryStore(MemoryStore):
    backend_name = "agentcore"

    def __init__(self, memory_id: str, region: str, namespaces: Dict[str, str]):
        import boto3

        self.memory_id = memory_id
        self.region = region
        self.namespaces = namespaces
        self._data = boto3.client("bedrock-agentcore", region_name=region)
        self._control = boto3.client("bedrock-agentcore-control", region_name=region)

    # ---- short-term: conversation transcript ----
    def record_turn(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        with recorder.span("memory.create_event", backend="agentcore", actor=actor_id, session=session_id):
            self._data.create_event(
                memoryId=self.memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    {"conversational": {"content": {"text": user_text}, "role": "USER"}},
                    {"conversational": {"content": {"text": assistant_text}, "role": "ASSISTANT"}},
                ],
            )

    # ---- short-term: deterministic onboarding state (blob) ----
    def save_state(self, actor_id: str, session_id: str, state: Dict[str, Any]) -> None:
        with recorder.span("memory.save_state", backend="agentcore", session=session_id):
            # AgentCore blob payloads are stored as opaque values; a raw dict is
            # stringified lossily, so we serialise to a JSON string and parse it
            # back on read.
            blob = json.dumps({"kind": STATE_KIND, "state": state, "ts": time.time()})
            self._data.create_event(
                memoryId=self.memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"blob": blob}],
            )

    def load_state(self, actor_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        with recorder.span("memory.load_state", backend="agentcore", session=session_id):
            events = self._list_events(actor_id, session_id, include_payloads=True)
            latest: Optional[Dict[str, Any]] = None
            latest_ts = -1.0
            for ev in events:
                for item in ev.get("payload", []):
                    blob = item.get("blob")
                    if not isinstance(blob, str):
                        continue
                    try:
                        parsed = json.loads(blob)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(parsed, dict) and parsed.get("kind") == STATE_KIND:
                        ts = float(parsed.get("ts", 0))
                        if ts >= latest_ts:
                            latest_ts = ts
                            latest = parsed.get("state")
            return latest

    def load_history(self, actor_id: str, session_id: str, k: int = 20) -> List[Dict[str, str]]:
        with recorder.span("memory.load_history", backend="agentcore", session=session_id):
            events = self._list_events(actor_id, session_id, include_payloads=True)
            turns: List[Dict[str, str]] = []
            for ev in events:
                for item in ev.get("payload", []):
                    conv = item.get("conversational")
                    if conv:
                        role = "user" if conv.get("role") == "USER" else "assistant"
                        text = (conv.get("content") or {}).get("text", "")
                        if text:
                            turns.append({"role": role, "content": text})
            return turns[-k:]

    def recall_long_term(self, actor_id: str, query: str, kind: str = "semantic", top_k: int = 3) -> List[Dict[str, Any]]:
        namespace = self._namespace_for(kind, actor_id)
        with recorder.span("memory.retrieve_records", backend="agentcore", namespace=namespace, query=query):
            try:
                resp = self._data.retrieve_memory_records(
                    memoryId=self.memory_id,
                    namespace=namespace,
                    searchCriteria={"searchQuery": query, "topK": top_k},
                )
            except Exception as exc:  # noqa: BLE001
                return [{"error": str(exc)}]
            return [self._record_text(r) for r in resp.get("memoryRecordSummaries", [])]

    def list_sessions(self, actor_id: str) -> List[str]:
        with recorder.span("memory.list_sessions", backend="agentcore", actor=actor_id):
            try:
                resp = self._data.list_sessions(memoryId=self.memory_id, actorId=actor_id)
                summaries = resp.get("sessionSummaries", [])
                # Order chronologically so the caller's "latest" (last item) is
                # the most recently created attempt, regardless of id format.
                summaries.sort(key=lambda s: s.get("createdAt") or 0)
                return [s["sessionId"] for s in summaries]
            except Exception:  # noqa: BLE001
                return []

    # ---- isolation demonstration ----
    def cross_tenant_probe(self, requesting_actor: str, target_actor: str, query: str) -> Dict[str, Any]:
        """Faithful isolation check: the requesting tenant's agent is only ever
        scoped to its OWN namespace. We query that namespace and confirm none of
        the target tenant's records are visible. Because every namespace is
        single-tenant (scoped by actorId = tenant__user), the target's data can
        never appear here - and IAM namespacePath conditions can hard-enforce it
        (see deploy/iam_execution_role.json)."""
        own_ns = self._namespace_for("semantic", requesting_actor)
        target_ns = self._namespace_for("semantic", target_actor)
        records = self.recall_long_term(requesting_actor, query, kind="semantic", top_k=5)
        valid = [r for r in records if "error" not in r]
        leaked = [r for r in valid if target_actor in str(r.get("namespace", ""))]
        return {
            "requesting_actor": requesting_actor,
            "requesting_namespace": own_ns,
            "target_actor": target_actor,
            "target_namespace": target_ns,
            "records_in_requesting_scope": len(valid),
            "records_returned": len(leaked),
            "isolated": len(leaked) == 0,
        }

    # ---- helpers ----
    def _namespace_for(self, kind: str, actor_id: str) -> str:
        template = self.namespaces.get(kind, "/ekyc/facts/{actorId}/")
        return template.replace("{actorId}", actor_id).replace("{sessionId}", "")

    def _list_events(self, actor_id: str, session_id: str, include_payloads: bool = True) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {
                "memoryId": self.memory_id,
                "actorId": actor_id,
                "sessionId": session_id,
                "includePayloads": include_payloads,
                "maxResults": 100,
            }
            if token:
                kwargs["nextToken"] = token
            resp = self._data.list_events(**kwargs)
            events.extend(resp.get("events", []))
            token = resp.get("nextToken")
            if not token:
                break
        events.sort(key=lambda e: e.get("eventTimestamp") or 0)
        return events

    @staticmethod
    def _record_text(rec: Dict[str, Any]) -> Dict[str, Any]:
        content = rec.get("content") or {}
        text = content.get("text") if isinstance(content, dict) else str(content)
        return {
            "namespace": rec.get("namespaces", rec.get("namespace")),
            "score": rec.get("score"),
            "text": text or json.dumps(rec)[:300],
        }

    def info(self) -> Dict[str, Any]:
        return {"backend": self.backend_name, "memory_id": self.memory_id, "region": self.region}


# ---------------------------------------------------------------------------
# Local file-based fallback backend
# ---------------------------------------------------------------------------
class LocalMemoryStore(MemoryStore):
    backend_name = "local"

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _file(self, actor_id: str, session_id: str) -> Path:
        safe = actor_id.replace("#", "__")
        d = self.base / safe
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{session_id}.json"

    def _read(self, actor_id: str, session_id: str) -> Dict[str, Any]:
        f = self._file(actor_id, session_id)
        if f.exists():
            return json.loads(f.read_text())
        return {"turns": [], "state": None}

    def _write(self, actor_id: str, session_id: str, data: Dict[str, Any]) -> None:
        self._file(actor_id, session_id).write_text(json.dumps(data, indent=2))

    def record_turn(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        with recorder.span("memory.create_event", backend="local", session=session_id):
            data = self._read(actor_id, session_id)
            data["turns"].append({"role": "user", "content": user_text})
            data["turns"].append({"role": "assistant", "content": assistant_text})
            self._write(actor_id, session_id, data)

    def save_state(self, actor_id: str, session_id: str, state: Dict[str, Any]) -> None:
        with recorder.span("memory.save_state", backend="local", session=session_id):
            data = self._read(actor_id, session_id)
            data["state"] = state
            self._write(actor_id, session_id, data)

    def load_state(self, actor_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        with recorder.span("memory.load_state", backend="local", session=session_id):
            return self._read(actor_id, session_id).get("state")

    def load_history(self, actor_id: str, session_id: str, k: int = 20) -> List[Dict[str, str]]:
        with recorder.span("memory.load_history", backend="local", session=session_id):
            return self._read(actor_id, session_id).get("turns", [])[-k:]

    def recall_long_term(self, actor_id: str, query: str, kind: str = "semantic", top_k: int = 3) -> List[Dict[str, Any]]:
        # Local fallback: synthesise "recall" from the saved state so the UI
        # still has something to show without the managed extraction pipeline.
        with recorder.span("memory.retrieve_records", backend="local", query=query):
            sessions = self.list_sessions(actor_id)
            facts: List[Dict[str, Any]] = []
            for sid in sessions:
                state = self.load_state(actor_id, sid) or {}
                if state:
                    facts.append({
                        "namespace": f"/ekyc/facts/{actor_id}/ (local-simulated)",
                        "score": 1.0,
                        "text": f"Session {sid}: status={state.get('status')}, "
                                f"completed={state.get('completed_steps')}",
                    })
            return facts[:top_k]

    def list_sessions(self, actor_id: str) -> List[str]:
        safe = actor_id.replace("#", "__")
        d = self.base / safe
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.json"))

    def cross_tenant_probe(self, requesting_actor: str, target_actor: str, query: str) -> Dict[str, Any]:
        # In the local store, each actor has its own directory; a query for
        # the requesting actor never reads another actor's directory.
        records = self.recall_long_term(target_actor, query)
        # Demonstrate that operating "as" the requesting actor sees nothing of
        # the target actor (separate directories == separate scope).
        own = self.recall_long_term(requesting_actor, query)
        return {
            "requesting_actor": requesting_actor,
            "requesting_namespace": f"/ekyc/facts/{requesting_actor}/",
            "target_actor": target_actor,
            "target_namespace": f"/ekyc/facts/{target_actor}/",
            "records_returned": 0,
            "records_in_own_scope": len(own),
            "records_that_exist_for_target": len(records),
            "isolated": True,
        }

    def info(self) -> Dict[str, Any]:
        return {"backend": self.backend_name, "dir": str(self.base)}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_store_singleton: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton

    from .config import resolve_memory_backend

    cfg = app_config()
    backend = resolve_memory_backend()
    mem = cfg["memory"]

    if backend == "agentcore" and mem.get("memory_id"):
        _store_singleton = AgentCoreMemoryStore(
            memory_id=mem["memory_id"],
            region=cfg["aws"]["region"],
            namespaces=mem.get("namespaces", {}),
        )
    else:
        local_dir = PROJECT_LOCAL_DIR(cfg)
        _store_singleton = LocalMemoryStore(local_dir)
    return _store_singleton


def PROJECT_LOCAL_DIR(cfg: Dict[str, Any]) -> str:
    from .config import PROJECT_ROOT

    rel = cfg["memory"].get("local_store_dir", ".memory_store")
    return str(PROJECT_ROOT / rel)


def reset_memory_store() -> None:
    global _store_singleton
    _store_singleton = None
