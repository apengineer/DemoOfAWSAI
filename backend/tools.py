"""Config-driven mock eKYC verification tools.

These simulate calls to an identity-verification provider (document auth,
liveness, face match, watchlist screening). They return deterministic mock
results so the demo runs with no external IDV dependency, and they update
the shared onboarding state that gets persisted to AgentCore Memory.

Each builder returns Strands ``@tool`` callables bound to a SessionContext,
so the agent can advance the onboarding while the backend keeps a clean,
serialisable record of progress.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from strands import tool

from .observability import recorder


@dataclass
class SessionContext:
    """Mutable per-request state shared between the agent and its tools."""

    tenant_id: str
    user_id: str
    session_id: str
    flow: Dict[str, Any]
    mock: Dict[str, Any]
    state: Dict[str, Any] = field(default_factory=dict)
    # Step ids the presenter wants to fail this turn (from message keywords).
    force_fail: List[str] = field(default_factory=list)

    def ensure_state(self) -> None:
        self.state.setdefault("flow_id", self.flow.get("id"))
        self.state.setdefault("status", "in_progress")
        self.state.setdefault("completed_steps", [])
        self.state.setdefault("collected_data", {})
        self.state.setdefault("step_results", {})

    def complete_step(self, step_id: str, result: Dict[str, Any]) -> None:
        self.ensure_state()
        if step_id not in self.state["completed_steps"]:
            self.state["completed_steps"].append(step_id)
        self.state["step_results"][step_id] = result

    def next_pending_step(self) -> str | None:
        self.ensure_state()
        done = set(self.state["completed_steps"])
        for step in self.flow["steps"]:
            if step["id"] not in done:
                return step["id"]
        return None


def build_tools(ctx: SessionContext) -> List[Callable]:
    mock = ctx.mock or {}

    def _failed(step_id: str) -> bool:
        return step_id in ctx.force_fail

    @tool
    def collect_personal_details(full_name: str, date_of_birth: str, country: str) -> str:
        """Record the applicant's personal details (name, date of birth, country).

        Call this once the applicant has provided their basic identifying
        information so onboarding can proceed to document verification.
        """
        with recorder.span("tool.collect_personal_details", step="collect_details"):
            ctx.ensure_state()
            ctx.state["collected_data"].update(
                {"full_name": full_name, "date_of_birth": date_of_birth, "country": country}
            )
            ctx.complete_step("collect_details", {"status": "captured"})
            return (
                f"Personal details captured for {full_name} "
                f"(DOB {date_of_birth}, country {country})."
            )

    @tool
    def verify_document(document_type: str) -> str:
        """Authenticate a government-issued ID document and extract its data.

        document_type should be one of: passport, driver_licence, national_id.
        """
        with recorder.span("tool.verify_document", step="document_verification", document_type=document_type) as sp:
            ctx.ensure_state()
            if _failed("document_verification"):
                sp.attributes["outcome"] = "fail"
                ctx.state["step_results"]["document_verification"] = {"status": "failed", "reason": "low_image_quality"}
                return (
                    "Document verification FAILED: the image was too blurry to read "
                    "the security features. Ask the applicant to retake a clearer photo."
                )
            conf = mock.get("document_confidence", 0.97)
            ctx.state["collected_data"]["document_type"] = document_type
            ctx.complete_step("document_verification", {"status": "verified", "confidence": conf})
            sp.attributes["confidence"] = conf
            return (
                f"Document ({document_type}) verified as authentic with confidence "
                f"{conf:.2f}. Extracted name and date of birth match the applicant."
            )

    @tool
    def check_liveness() -> str:
        """Run a passive liveness / injection-attack check on the selfie capture."""
        with recorder.span("tool.check_liveness", step="liveness_check") as sp:
            ctx.ensure_state()
            if _failed("liveness_check"):
                sp.attributes["outcome"] = "fail"
                ctx.state["step_results"]["liveness_check"] = {"status": "failed", "reason": "possible_spoof"}
                return (
                    "Liveness check FAILED: signs of a presentation/injection attack "
                    "were detected. Ask the applicant to retry in good lighting."
                )
            score = mock.get("liveness_score", 0.99)
            ctx.complete_step("liveness_check", {"status": "passed", "score": score})
            sp.attributes["score"] = score
            return f"Liveness confirmed (score {score:.2f}). The selfie is from a live, present person."

    @tool
    def match_face() -> str:
        """Biometrically compare the selfie to the portrait on the verified document."""
        with recorder.span("tool.match_face", step="face_match") as sp:
            ctx.ensure_state()
            if _failed("face_match"):
                sp.attributes["outcome"] = "fail"
                ctx.state["step_results"]["face_match"] = {"status": "failed", "reason": "below_threshold"}
                return (
                    "Face match FAILED: the selfie did not match the document portrait "
                    "above threshold. Manual review recommended."
                )
            score = mock.get("face_match_score", 0.96)
            ctx.complete_step("face_match", {"status": "matched", "score": score})
            sp.attributes["score"] = score
            return f"Face match successful (similarity {score:.2f}). Same person as the document."

    @tool
    def run_aml_screening() -> str:
        """Screen the verified identity against sanctions and PEP watchlists."""
        with recorder.span("tool.run_aml_screening", step="aml_screening") as sp:
            ctx.ensure_state()
            ctx.complete_step("aml_screening", {"status": "clear", "hits": 0})
            sp.attributes["hits"] = 0
            return "Sanctions/PEP screening complete: no watchlist matches found."

    @tool
    def finalize_decision() -> str:
        """Combine all verification signals into the final onboarding decision."""
        with recorder.span("tool.finalize_decision", step="decision") as sp:
            ctx.ensure_state()
            required = [s["id"] for s in ctx.flow["steps"] if s["id"] != "decision"]
            done = set(ctx.state["completed_steps"])
            failed = [
                sid for sid, r in ctx.state["step_results"].items()
                if isinstance(r, dict) and r.get("status") in {"failed"}
            ]
            if failed:
                decision = "refer"
            elif all(s in done for s in required):
                decision = "approve"
            else:
                decision = "incomplete"
            ctx.state["decision"] = decision
            if decision == "approve":
                ctx.state["status"] = "approved"
                ctx.complete_step("decision", {"status": "approved"})
            else:
                ctx.complete_step("decision", {"status": decision})
            sp.attributes["decision"] = decision
            missing = [s for s in required if s not in done]
            if decision == "approve":
                return "Onboarding decision: APPROVED. All identity checks passed."
            if decision == "refer":
                return "Onboarding decision: REFER for manual review (one or more checks failed)."
            return f"Onboarding decision: INCOMPLETE. Still pending: {', '.join(missing)}."

    by_name = {
        "collect_personal_details": collect_personal_details,
        "verify_document": verify_document,
        "check_liveness": check_liveness,
        "match_face": match_face,
        "run_aml_screening": run_aml_screening,
        "finalize_decision": finalize_decision,
    }
    # Only expose tools referenced by the configured flow (keeps it reusable).
    enabled = [step.get("tool") for step in ctx.flow.get("steps", [])]
    return [by_name[name] for name in enabled if name in by_name]


def detect_forced_failures(message: str, flow: Dict[str, Any], mock: Dict[str, Any]) -> List[str]:
    """Map demo keywords in the user's message to steps that should fail,
    so a presenter can show retries (e.g. type 'the photo is blurry')."""
    msg = (message or "").lower()
    fails: List[str] = []
    for step_id, keyword in (mock.get("fail_keywords") or {}).items():
        if keyword and keyword.lower() in msg:
            fails.append(step_id)
    return fails
