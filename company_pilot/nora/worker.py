"""Supervisor-friendly worker loop; construction/import never starts it."""
from dataclasses import asdict
from threading import Event
from typing import Callable

from .engine import Nora, PENDING, ANSWERABLE, disposition_pauses, waiting_questions
from .models import NoraState, RoundStatus, QuestionStatus
from .presentation import askability


def awaiting_delegate(q):
    return q.status == QuestionStatus.AWAITING_PERSON or (q.status in PENDING and disposition_pauses(q) and (q.response_disposition or {}).get("kind") == "delegate")


def health(nora: Nora) -> dict:
    """Metadata only; no message bodies, sender identities or credentials."""
    now = nora.now()
    def inspect(state: NoraState):
        route = state.route
        unknown = [r.id for r in state.rounds.values() if r.status == RoundStatus.UNKNOWN]
        stale = [r.id for r in state.rounds.values() if r.status == RoundStatus.SENDING
                 and r.attempts[-1].deadline <= now]
        freshness = dict(state.freshness)
        for key in ("oldest_pending", "oldest_queued_answer"):
            stamp = freshness.get(key + "_at")
            if stamp:
                from datetime import datetime
                freshness[key + "_age_seconds"] = max(0, int((now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()))
        return {
            "schema_version": 1,
            "route_configured": bool(route and route.enabled and route.company_id == nora.company_id),
            "pending_questions": sum(q.status in ANSWERABLE for q in state.questions.values()),
            "founder_pending_questions": sum(q.status in PENDING and not disposition_pauses(q) and q.privacy_disposition != "withheld" for q in state.questions.values()),
            "awaiting_delegate_questions": sum(awaiting_delegate(q) for q in state.questions.values()),
            "awaiting_delegate": [{"question_id": q.id, "delegate_to": (q.response_disposition or {}).get("delegate_to"), "state": "awaiting_delegate"}
                for q in state.questions.values() if awaiting_delegate(q)],
            "coverage_constraint_count": freshness.get("coverage_constraint_count", 0),
            "coverage_constraints": freshness.get("coverage_constraints", []),
            "unaskable_pending_by_reason": {reason: sum(q.status in PENDING and askability(q) == reason for q in state.questions.values())
                for reason in sorted({askability(q) for q in state.questions.values() if q.status in PENDING and askability(q)})},
            "unknown_delivery_rounds": unknown,
            "stale_sending_rounds": stale,
            "media_replies_awaiting_text": sum(a.status == "awaiting_text" for a in state.inbox.values()),
            "next_scheduled_at": nora.schedule.next_at(now).isoformat(),
            "schedule_timezone": nora.schedule.timezone_name,
            "channel_connectivity": "not_probed",
            "operator_availability": "not_monitored",
            "scheduled_delivery": "requires_running_worker",
            "questions_waiting_analysis": len(waiting_questions(state)),
            "raw_answers_waiting_handoff": sum(answer.status == "awaiting_interpretation" for answer in state.inbox.values()),
            "dismissed_unchanged_questions": sum(disposition_pauses(q) and q.response_disposition["kind"] == "dismiss" for q in state.questions.values() if q.status in PENDING),
            "delegated_unchanged_questions": sum(disposition_pauses(q) and q.response_disposition["kind"] == "delegate" for q in state.questions.values() if q.status in ANSWERABLE),
            "processing": freshness,
        }
    return nora.repository.transact(nora.company_id, inspect)


def run(nora: Nora, stop: Event, *, poll_seconds: float = 15,
        report: Callable[[dict], None] = lambda event: None) -> None:
    """Run on an explicitly configured always-on owner machine/service host.

    The caller installs its OS supervisor and routes signals to stop.set(). No
    GitHub Actions timing guarantee, network health or OS autostart is inferred.
    """
    if not 0 < poll_seconds <= 60:
        raise ValueError("Poll interval must be positive and at most sixty seconds")
    while not stop.is_set():
        try:
            result = asdict(nora.tick())
            result["next_scheduled_at"] = result["next_scheduled_at"].isoformat()
            report({"kind": "nora_tick", **result})
        except Exception as error:
            # Raw exceptions may contain private backend data/commands. Only emit type.
            report({"kind": "nora_worker_error", "error_type": type(error).__name__})
        stop.wait(poll_seconds)
