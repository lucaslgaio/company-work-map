"""JSON adapter for BrainStore.transact_nora(callback, event_id).

The backend supplies conflict checking/durability and owns the same company ledger.
This module never creates an independent database or performs a channel effect.
"""
from dataclasses import asdict
from datetime import datetime
from typing import Callable, TypeVar
from uuid import uuid4

from .models import (
    Attempt, InboundEvent, NoraState, Question, QuestionStatus, ReceivedAnswer,
    Round, RoundItem, RoundStatus, Route, SCHEMA_VERSION,
)

T = TypeVar("T")


def _time(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _question(raw: dict) -> Question:
    data = dict(raw)
    data["created_at"] = _time(data["created_at"])
    data["status"] = QuestionStatus(data["status"])
    for key in ("observation_ids", "missing_links", "answer_ids", "conflict_ids", "revisit_evidence_ids", "evidence", "known_facts", "issue_ids", "issues"):
        data[key] = tuple(data.get(key, ()))
    return Question(**data)


def _json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def encode_state(state: NoraState, company_id: str) -> dict:
    return _json({
        "schema_version": SCHEMA_VERSION, "company_id": company_id,
        "route": asdict(state.route) if state.route else None,
        "questions": {key: asdict(q) for key, q in state.questions.items()},
        "rounds": {key: asdict(r) for key, r in state.rounds.items()},
        "suppressions": [{"question_id": key[0], "digest": key[1], "round_id": value}
                         for key, value in sorted(state.suppressions.items())],
        "inbox": {key: asdict(answer) for key, answer in state.inbox.items()},
        "reminders": [{"question_id": key[0], "digest": key[1], "round_id": value}
                      for key, value in sorted(state.reminders.items())],
        "waiting_question_ids": list(state.waiting_question_ids),
        "freshness": state.freshness,
        "binding_request": state.binding_request,
        "pending_followup": state.pending_followup,
    })


def decode_state(raw: dict, company_id: str) -> NoraState:
    if not raw:
        return NoraState()
    if set(raw) <= {"route", "binding_request"} and isinstance(raw.get("route"), dict):
        # Compatibility for the exact pre-codec owner-binding shape. No rounds,
        # answers or reservations existed in that shape; never discard runtime data.
        raw = {"schema_version": SCHEMA_VERSION, "company_id": company_id, **raw}
    if raw.get("schema_version") != SCHEMA_VERSION or raw.get("company_id") != company_id:
        raise ValueError("Nora state schema/company mismatch")
    route = Route(**raw["route"]) if raw.get("route") else None
    if route and route.company_id != company_id:
        raise ValueError("Route belongs to another company")
    questions = {key: _question(q) for key, q in raw.get("questions", {}).items()}
    if any(q.company_id != company_id or q.id != key for key, q in questions.items()):
        raise ValueError("Question identity/company mismatch")
    rounds = {}
    for key, raw_round in raw.get("rounds", {}).items():
        data = dict(raw_round)
        data["route"] = Route(**data["route"])
        data["slot"] = _time(data["slot"])
        data["retry_after"] = _time(data.get("retry_after"))
        data["status"] = RoundStatus(data["status"])
        data["items"] = tuple(RoundItem(i["label"], _question(i["question"]), i["digest"], i.get("reminder", False), _time(i.get("prior_ask_at")), i.get("why_chosen"), tuple(i.get("evidence", ()))) for i in data["items"])
        attempts = []
        for raw_attempt in data["attempts"]:
            a = dict(raw_attempt)
            a["started_at"], a["deadline"] = _time(a["started_at"]), _time(a["deadline"])
            a["finished_at"] = _time(a.get("finished_at"))
            a["outcome"] = RoundStatus(a["outcome"])
            attempts.append(Attempt(**a))
        data["attempts"] = tuple(attempts)
        data["recovery_evidence_ids"] = tuple(data.get("recovery_evidence_ids", ()))
        r = Round(**data)
        if r.id != key or r.route.company_id != company_id or any(i.question.company_id != company_id for i in r.items):
            raise ValueError("Round identity/company mismatch")
        rounds[key] = r
    inbox = {}
    for key, raw_answer in raw.get("inbox", {}).items():
        data = dict(raw_answer)
        event = dict(data["event"])
        event["received_at"] = _time(event["received_at"])
        data["event"] = InboundEvent(**event)
        for field in ("candidate_question_ids", "question_ids"):
            data[field] = tuple(data[field])
        data["question_versions"] = tuple(tuple(version) for version in data["question_versions"])
        data["conversation_contexts"] = tuple(data.get("conversation_contexts", ()))
        answer = ReceivedAnswer(**data)
        if answer.source_key != key or answer.company_id != company_id:
            raise ValueError("Answer identity/company mismatch")
        inbox[key] = answer
    return NoraState(route, questions, rounds,
        {(s["question_id"], s["digest"]): s["round_id"] for s in raw.get("suppressions", [])}, inbox,
        {(s["question_id"], s["digest"]): s["round_id"] for s in raw.get("reminders", [])},
        tuple(raw.get("waiting_question_ids", [])), dict(raw.get("freshness", {})), raw.get("binding_request"),
        raw.get("pending_followup"))


class BrainRepository:
    """Wrap a bound company BrainStore's atomic Nora JSON-state callback.

    transact_nora receives (operation, event_id). It calls operation(current_dict)
    and durably commits that mutable dictionary plus the JSON callback result.
    It may retry the pure callback
    on CAS conflicts; it MUST raise if the commit fails/has unknown outcome.
    """
    def __init__(self, company_id: str, transact_nora: Callable):
        self.company_id = company_id
        self.transact_nora = transact_nora

    def transact(self, company_id: str, operation: Callable[[NoraState], T]) -> T:
        if company_id != self.company_id:
            raise ValueError("Repository is bound to another company")
        result: list[T] = []
        def update(raw: dict) -> dict:
            state = decode_state(raw, company_id)
            value = operation(state)
            result[:] = [value]  # Keep only the final successful CAS evaluation.
            encoded = encode_state(state, company_id)
            changed = encoded != raw
            if changed:
                raw.clear()
                raw.update(encoded)
            # Result is JSON; the Python operation result is returned only after CAS.
            return {"schema_version": SCHEMA_VERSION, "changed": changed}
        self.transact_nora(update, event_id=str(uuid4()))
        if not result:
            raise RuntimeError("Backend returned without executing its transaction")
        return result[0]
