"""Lightweight in-app trace/span recorder.

This powers the live "Observability" panel in the demo UI without requiring
CloudWatch to be wired up first. It mirrors the structure of an OpenTelemetry
trace (a tree of timed spans with attributes) so the story maps cleanly onto
AgentCore Observability / CloudWatch GenAI observability once deployed.

When the agent runs inside AgentCore Runtime, the runtime auto-instruments
with OpenTelemetry and the same spans show up in CloudWatch. Locally we keep
an in-memory ring buffer of recent traces, keyed by session id.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from threading import Lock
from typing import Any, Deque, Dict, List, Optional


class Span:
    def __init__(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.span_id = uuid.uuid4().hex[:8]
        self.name = name
        self.attributes: Dict[str, Any] = attributes or {}
        self.start = time.time()
        self.end: Optional[float] = None
        self.status = "OK"
        self.error: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        end = self.end if self.end is not None else time.time()
        return round((end - self.start) * 1000, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "attributes": self.attributes,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "start_offset_ms": 0,  # filled in by Trace.to_dict
            "_start": self.start,
        }


class Trace:
    def __init__(self, session_id: str, name: str):
        self.trace_id = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.name = name
        self.start = time.time()
        self.spans: List[Span] = []

    def to_dict(self) -> Dict[str, Any]:
        spans = []
        for s in self.spans:
            d = s.to_dict()
            d["start_offset_ms"] = round((d.pop("_start") - self.start) * 1000, 1)
            spans.append(d)
        total = round((max((s.end or s.start for s in self.spans), default=self.start) - self.start) * 1000, 1)
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "name": self.name,
            "total_ms": total,
            "span_count": len(self.spans),
            "spans": spans,
        }


class TraceRecorder:
    """Process-wide recorder holding recent traces per session."""

    def __init__(self, max_per_session: int = 25):
        self._lock = Lock()
        self._by_session: Dict[str, Deque[Trace]] = defaultdict(
            lambda: deque(maxlen=max_per_session)
        )
        self._current: Dict[int, Trace] = {}  # thread-id -> active trace

    @contextmanager
    def trace(self, session_id: str, name: str):
        import threading

        tr = Trace(session_id, name)
        tid = threading.get_ident()
        self._current[tid] = tr
        try:
            yield tr
        finally:
            with self._lock:
                self._by_session[session_id].append(tr)
            self._current.pop(tid, None)

    @contextmanager
    def span(self, name: str, **attributes: Any):
        import threading

        tr = self._current.get(threading.get_ident())
        span = Span(name, attributes)
        if tr is not None:
            tr.spans.append(span)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.status = "ERROR"
            span.error = str(exc)
            raise
        finally:
            span.end = time.time()

    def latest(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            traces = self._by_session.get(session_id)
            if not traces:
                return None
            return traces[-1].to_dict()

    def history(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in self._by_session.get(session_id, [])]


# Single shared recorder instance.
recorder = TraceRecorder()
