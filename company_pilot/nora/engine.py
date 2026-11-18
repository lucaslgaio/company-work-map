"""One company/slot, one durable outbox; answers remain evidence for the brain.

The repository contract supplies durable atomicity. There is deliberately no default
memory store and no default/fallback channel in production code.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from .models import (
    Attempt, Clock, InboundEvent, KnownUnsent, NoraState, Question, QuestionStatus,
    ReceivedAnswer, ReplayConflict, Repository, Round, RoundItem, RoundStatus,
    Route, Sender, SendReceipt, TickResult, UnauthorizedInbound, aware,
)
from .schedule import Schedule


PENDING = {QuestionStatus.PENDING, QuestionStatus.PARTIAL}
ANSWERABLE = PENDING | {QuestionStatus.AWAITING_PERSON}


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def uncertainty_digest(missing_links, revisit_evidence_ids, conflict_ids, resend_request_id, issue_ids=()) -> str:
    scope = {
        "missing": sorted(missing_links),
        "relevant_context": sorted(revisit_evidence_ids),
        # Answer sources are retained, but a task-wide partial answer is not a
        # reason to re-ask every other gap. Revisit/conflict evidence is scoped.
        "answers": [],
        "conflicts": sorted(conflict_ids),
        "requested_resend": resend_request_id,
    }
    if issue_ids:
        scope["issue_ids"] = sorted(issue_ids)
    return digest(scope)


def question_digest(question: Question) -> str:
    """Only meaningful new context permits a revisit; rephrasing/time does not."""
    return uncertainty_digest(question.missing_links, question.revisit_evidence_ids,
                              question.conflict_ids, question.resend_request_id, question.issue_ids)


def conflict_ids(conflict) -> tuple[str, ...]:
    return (sha256(json.dumps(conflict, sort_keys=True).encode()).hexdigest(),) if conflict else ()


def disposition_basis_digest(basis: dict) -> str:
    required = {"missing_links", "revisit_evidence_ids", "conflict", "resend_request_id"}
    if not isinstance(basis, dict) or not required <= set(basis) or set(basis) - required - {"issue_ids"}:
        raise ValueError("disposition_uncertainty_basis_invalid")
    return uncertainty_digest(basis["missing_links"], basis["revisit_evidence_ids"],
                              conflict_ids(basis["conflict"]), basis["resend_request_id"], basis.get("issue_ids", ()))


def waiting_questions(state: NoraState) -> set[str]:
    waiting = set(state.waiting_question_ids)
    for answer in state.inbox.values():
        if answer.status != "awaiting_interpretation":
            continue
        scope = answer.handoff_scope or {}
        # Holding a frozen candidate set is not selecting any of its questions.
        # A failed/unknown handoff must not turn a natural reply into a reminder.
        selected = answer.question_ids or scope.get("question_ids", ())
        candidates = scope.get("candidate_question_ids", ()) if answer.handoff_scope is not None else answer.candidate_question_ids
        waiting.update(selected or candidates)
    return waiting


def question_label(question_id: str) -> str:
    """Stable visible reference; collisions are treated as ambiguous on intake."""
    return "Q-" + sha256(question_id.encode("utf-8")).hexdigest()[:8].upper()


def disposition_pauses(question: Question) -> bool:
    return bool(question.response_disposition and question.response_disposition.get("kind") in ("dismiss", "delegate")
                and question.disposition_digest == question_digest(question))


from .presentation import display_text, render, rank_question, askability, selection_reason, low_value_cross_apply, disagreement_message


class Nora:
    def __init__(self, company_id: str, repository: Repository, clock: Clock,
                 sender: Sender, *, schedule: Schedule | None = None,
                 batch_limit: int = 1, attempt_timeout: timedelta = timedelta(seconds=45),
                 retry_delay: timedelta = timedelta(seconds=30), max_attempts: int = 3,
                 reminder_after: timedelta | None = timedelta(hours=24), founder_name: str | None = None):
        if not company_id or batch_limit != 1 or max_attempts < 1:
            raise ValueError("Invalid company/batch/retry configuration")
        if attempt_timeout <= timedelta(0) or retry_delay <= timedelta(0):
            raise ValueError("Attempt timeout and retry delay must be positive")
        if reminder_after is not None and reminder_after < timedelta(hours=24):
            raise ValueError("A reminder must wait at least twenty-four hours")
        self.company_id = company_id
        self.repository = repository
        self.clock = clock
        self.sender = sender
        self.schedule = schedule or Schedule()
        self.batch_limit = batch_limit
        self.attempt_timeout = attempt_timeout
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts
        self.reminder_after = reminder_after
        if founder_name is not None and (not isinstance(founder_name, str) or not founder_name.strip()
                or len(founder_name) > 120 or not founder_name.isprintable()):
            raise ValueError("configured_recipient_name_required")
        self.founder_name = founder_name

    def now(self) -> datetime:
        return aware(self.clock()).astimezone(timezone.utc)

    def next_scheduled_at(self) -> datetime:
        return self.schedule.next_at(self.now())

    def put_question(self, question: Question) -> None:
        """Persist the brain's canonical projection; preserve every frozen round."""
        if question.company_id != self.company_id:
            raise ValueError("Question belongs to another company")
        def operation(state: NoraState) -> None:
            previous = state.questions.get(question.id)
            if previous and question != previous and question.revision <= previous.revision:
                raise ReplayConflict("Changed question needs a newer revision")
            if question.status in PENDING and any(q.id != question.id and q.task_id == question.task_id
                    and q.status in PENDING and set(q.missing_links) & set(question.missing_links)
                    for q in state.questions.values()):
                raise ReplayConflict("The task's missing link already has a tracked question")
            state.questions[question.id] = question
        self.repository.transact(self.company_id, operation)

    @staticmethod
    def _release(state: NoraState, round: Round) -> None:
        for item in round.items:
            marks = state.reminders if item.reminder else state.suppressions
            for key in ((item.question.id, item.digest), (item.question.id, question_digest(item.question))):
                if marks.get(key) == round.id:
                    del marks[key]

    def _recover(self, state: NoraState, now: datetime) -> None:
        # Existing deliveries keep their immutable text/digest. Re-index only
        # reservation history under the scoped semantic key after this upgrade.
        for marks in (state.suppressions, state.reminders):
            normalized = {}
            for (qid, old_digest), round_id in sorted(marks.items(), key=lambda entry: state.rounds[entry[1]].slot):
                prior = next((item for item in state.rounds[round_id].items
                              if item.question.id == qid and old_digest in (item.digest, question_digest(item.question))), None)
                key = (qid, question_digest(prior.question)) if prior else (qid, old_digest)
                normalized.setdefault(key, round_id)
            marks.clear()
            marks.update(normalized)
        for round in list(state.rounds.values()):
            if round.status == RoundStatus.SENDING and round.attempts[-1].deadline <= now:
                attempt = replace(round.attempts[-1], outcome=RoundStatus.UNKNOWN,
                                  finished_at=now, diagnostic="attempt_lease_expired")
                state.rounds[round.id] = replace(round, status=RoundStatus.UNKNOWN,
                                                attempts=round.attempts[:-1] + (attempt,))
            elif round.status in {RoundStatus.PREPARED, RoundStatus.KNOWN_UNSENT} and now >= self.schedule.closes_at(round.slot):
                state.rounds[round.id] = replace(round, status=RoundStatus.EXPIRED)
                self._release(state, round)

    @staticmethod
    def _accepted_ask_at(state: NoraState, question: Question) -> datetime | None:
        key = (question.id, question_digest(question))
        original_id = state.suppressions.get(key)
        if not original_id:
            return None
        original = state.rounds[original_id]
        accepted = [a.finished_at for a in original.attempts
                    if a.outcome == RoundStatus.ACCEPTED and a.finished_at]
        return max(accepted) if original.status == RoundStatus.ACCEPTED and accepted else None

    def _reminder_due(self, state: NoraState, question: Question, now: datetime) -> bool:
        key = (question.id, question_digest(question))
        accepted_at = self._accepted_ask_at(state, question)
        return bool(self.reminder_after is not None and key not in state.reminders
                    and accepted_at and now - accepted_at >= self.reminder_after)

    def _eligible(self, state: NoraState, now: datetime) -> list[tuple[Question, bool]]:
        waiting = waiting_questions(state)
        # Until delivery is reconciled, a new projection cannot turn an unknown
        # transport result into permission to send that question again.
        waiting.update(item.question.id for round in state.rounds.values()
                       if round.status in (RoundStatus.UNKNOWN, RoundStatus.SENDING) for item in round.items)
        pending = sorted((q for q in state.questions.values()
                          if q.company_id == self.company_id and q.status in PENDING and q.id not in waiting
                          and not disposition_pauses(q) and askability(q) is None),
                         key=lambda q: (q.created_at, q.id))
        already_asked = []
        for sent in state.rounds.values():
            if sent.status != RoundStatus.ACCEPTED:
                continue
            for item in sent.items:
                already_asked.append(item.question)
        def duplicate(q):
            if q.priority_hint:
                return False
            key = (q.activity_summary.casefold().strip(), tuple(q.missing_links))
            return any(other.id != q.id and (other.activity_summary.casefold().strip(), tuple(other.missing_links)) == key for other in already_asked)
        useful = [q for q in pending if not duplicate(q) and (q.id, question_digest(q)) not in state.suppressions]
        if any(not low_value_cross_apply(q) for q in useful):
            useful = [q for q in useful if not low_value_cross_apply(q)]
        fresh = [(q, False) for q in useful]
        reminders = [(q, True) for q in pending if self._reminder_due(state, q, now)]
        reminders.sort(key=lambda entry: (self._accepted_ask_at(state, entry[0]),
                                         entry[0].created_at, entry[0].id))
        return sorted(fresh + reminders, key=lambda entry: rank_question(entry[0], entry[1], now))

    def _make_round(self, state: NoraState, route: Route, slot: datetime, now: datetime) -> Round:
        follow = state.pending_followup or {}
        question = state.questions.get(follow.get("question_id", ""))
        if question and question.status in PENDING and follow.get("first_name") and follow.get("second_name"):
            text = disagreement_message(follow["first_name"], follow["first_text"], follow["second_name"], follow["second_text"])
            why, evidence = selection_reason(question, False, now, 1)
            why = {**why, "reason": "As duas pessoas autorizadas deram respostas diferentes."}
            item = RoundItem(question_label(question.id), question, question_digest(question), False, None, why, evidence)
            round_id = str(uuid5(NAMESPACE_URL, f"nora:{self.company_id}:{slot.isoformat()}:disagreement"))
            round = Round(round_id, slot, route, (item,), text, digest(text), RoundStatus.PREPARED, decision_reason="responder_disagreement")
            state.rounds[round.id] = round
            state.pending_followup = None
            return round
        eligible = self._eligible(state, now)
        local_day = slot.astimezone(ZoneInfo("America/Sao_Paulo")).date()
        sent_today = sum(r.slot.astimezone(ZoneInfo("America/Sao_Paulo")).date() == local_day
                         and r.status in (RoundStatus.ACCEPTED, RoundStatus.SENDING, RoundStatus.UNKNOWN)
                         for r in state.rounds.values())
        if sent_today >= 3:
            eligible = []
        items = []
        for q, reminder in eligible[:1]:
            prior = None
            if reminder:
                prior = self._accepted_ask_at(state, q)
            why, evidence = selection_reason(q, reminder, now, len(eligible))
            items.append(RoundItem(question_label(q.id), q, question_digest(q), reminder, prior, why, evidence))
        items = tuple(items)
        round_id = str(uuid5(NAMESPACE_URL, f"nora:{self.company_id}:{slot.isoformat()}"))
        text = render(items, slot, founder_name=self.founder_name) if items else ""
        round = Round(round_id, slot, route, items, text, digest(text),
                      RoundStatus.PREPARED if items else RoundStatus.EMPTY,
                      decision_reason="three_message_daily_limit" if sent_today >= 3 else "selected_one_question" if items else "no_eligible_question")
        state.rounds[round.id] = round
        for item in items:
            marks = state.reminders if item.reminder else state.suppressions
            marks[(item.question.id, item.digest)] = round.id
        return round

    def _still_current(self, state: NoraState, round: Round) -> bool:
        if round.decision_reason == "responder_disagreement":
            return bool(state.route == round.route and round.route.enabled and len(round.items) == 1)
        if state.route != round.route or not round.route.enabled or len(round.items) != 1:
            return False
        for item in round.items:
            q = state.questions.get(item.question.id)
            if (not q or q.status not in PENDING or q.revision != item.question.revision
                    or q.id in waiting_questions(state) or disposition_pauses(q) or askability(q) is not None):
                return False
            if question_digest(q) != item.digest:
                return False
        return True

    def _result(self, state: NoraState, outcome: str, now: datetime,
                round_id: str | None = None) -> TickResult:
        pending = [q for q in state.questions.values()
                   if q.company_id == self.company_id and q.status in ANSWERABLE]
        overflow = len(self._eligible(state, now))
        return TickResult(outcome, self.schedule.next_at(now, strictly_after=True),
                          round_id, len(pending), overflow)

    def tick(self) -> TickResult:
        """Plan/claim durably, then invoke one sender outside the transaction.

        Calling this periodically is safe with concurrent workers IF the injected
        repository fulfills its atomicity contract. No slot backfill is performed.
        """
        now = self.now()
        def operation(state: NoraState):
            self._recover(state, now)
            slot = self.schedule.due_at(now)
            route = state.route
            if slot is None:
                return self._result(state, "not_due", now)
            if not route or route.company_id != self.company_id or not route.enabled:
                return self._result(state, "route_unavailable", now)
            round = next((r for r in state.rounds.values() if r.slot == slot), None)
            if round is None:
                round = self._make_round(state, route, slot, now)
            if round.status not in {RoundStatus.PREPARED, RoundStatus.KNOWN_UNSENT}:
                return self._result(state, round.status.value, now, round.id)
            if not self._still_current(state, round):
                state.rounds[round.id] = replace(round, status=RoundStatus.EXPIRED)
                self._release(state, round)
                return self._result(state, "changed_before_send", now, round.id)
            if len(round.attempts) >= self.max_attempts or (round.retry_after and now < round.retry_after):
                return self._result(state, "retry_deferred", now, round.id)
            attempt = Attempt(str(uuid4()), now, now + self.attempt_timeout)
            round = replace(round, status=RoundStatus.SENDING,
                            attempts=round.attempts + (attempt,), retry_after=None)
            state.rounds[round.id] = round
            return round, attempt
        planned = self.repository.transact(self.company_id, operation)
        if isinstance(planned, TickResult):
            return planned
        round, attempt = planned
        # The durable SENDING marker exists before any external effect. A process
        # death from here on leaves a lease that recovers as UNKNOWN, never unsent.
        if self.schedule.due_at(self.now()) != round.slot:
            return self._finish(round, attempt, RoundStatus.KNOWN_UNSENT, diagnostic="slot_closed_before_transport")
        try:
            receipt = self.sender.send(round.route, round.text)
        except KnownUnsent:
            return self._finish(round, attempt, RoundStatus.KNOWN_UNSENT, diagnostic="transport_did_not_start")
        except Exception:
            return self._finish(round, attempt, RoundStatus.UNKNOWN, diagnostic="transport_outcome_unknown")
        if not isinstance(receipt, SendReceipt) or not self._valid_receipt(round, receipt):
            return self._finish(round, attempt, RoundStatus.UNKNOWN, diagnostic="invalid_transport_receipt")
        return self._finish(round, attempt, RoundStatus.ACCEPTED, receipt=receipt)

    @staticmethod
    def _valid_receipt(round: Round, receipt: SendReceipt) -> bool:
        return (receipt.account == round.route.account
                and receipt.conversation == round.route.conversation
                and isinstance(receipt.message_id, str) and bool(receipt.message_id.strip()))

    @staticmethod
    def _receipt_used(state: NoraState, round: Round, receipt: SendReceipt) -> bool:
        return any(r.id != round.id
            and (r.route.account, r.route.conversation) == (receipt.account, receipt.conversation)
            and any(a.message_id == receipt.message_id for a in r.attempts)
            for r in state.rounds.values())

    def _finish(self, sent: Round, attempt: Attempt, outcome: RoundStatus,
                *, receipt: SendReceipt | None = None, diagnostic: str | None = None) -> TickResult:
        now = self.now()
        def operation(state: NoraState, receipt=receipt, outcome=outcome,
                      diagnostic=diagnostic) -> TickResult:
            round = state.rounds[sent.id]
            if round.attempts[-1].id != attempt.id:
                raise ReplayConflict("Attempt changed during transport")
            # A positive receipt arriving after lease recovery is still real evidence.
            if receipt and self._receipt_used(state, round, receipt):
                receipt, outcome, diagnostic = None, RoundStatus.UNKNOWN, "duplicate_transport_receipt"
            completed = replace(attempt, outcome=outcome, finished_at=now,
                                message_id=receipt.message_id if receipt else None,
                                diagnostic=diagnostic)
            state.rounds[round.id] = replace(round, status=outcome,
                attempts=round.attempts[:-1] + (completed,),
                retry_after=now + self.retry_delay if outcome == RoundStatus.KNOWN_UNSENT else None)
            return self._result(state, outcome.value, now, round.id)
        return self.repository.transact(self.company_id, operation)

    def reconcile_unknown(self, round_id: str, *, evidence_id: str,
                          receipt: SendReceipt | None = None, verified_unsent: bool = False) -> None:
        """Trusted administrative seam; caller must authorize/verify recovery evidence.

        No send occurs here. Verified-unsent releases questions for the NEXT slot.
        This method is not exposed to founder prose or the channel adapter.
        """
        if not evidence_id or (receipt is not None) == verified_unsent:
            raise ValueError("Supply exactly one verified receipt or unsent disposition")
        def operation(state: NoraState) -> None:
            round = state.rounds[round_id]
            if round.status != RoundStatus.UNKNOWN:
                raise ValueError("Only unknown delivery can be reconciled")
            if receipt and not self._valid_receipt(round, receipt):
                raise ValueError("Receipt route does not match")
            if receipt and self._receipt_used(state, round, receipt):
                raise ValueError("Receipt already belongs to another round")
            attempt = replace(round.attempts[-1],
                outcome=RoundStatus.ACCEPTED if receipt else RoundStatus.KNOWN_UNSENT,
                message_id=receipt.message_id if receipt else None,
                diagnostic="reconciled_accepted" if receipt else "reconciled_unsent")
            state.rounds[round.id] = replace(round,
                status=RoundStatus.ACCEPTED if receipt else RoundStatus.EXPIRED,
                attempts=round.attempts[:-1] + (attempt,),
                recovery_evidence_ids=round.recovery_evidence_ids + (evidence_id,))
            if verified_unsent:
                self._release(state, round)
        self.repository.transact(self.company_id, operation)

    def receive(self, event: InboundEvent) -> ReceivedAnswer:
        """Validate trusted channel identity and durably preserve the raw answer.

        The brain consumes this inbox and performs its answer/map transaction. Receipt
        here never marks a question resolved and never emits an acknowledgment message.
        """
        aware(event.received_at)
        if not event.message_id or (not event.text.strip() and not event.media_type):
            raise ValueError("Inbound needs a stable message ID and text or a media marker")
        def operation(state: NoraState) -> ReceivedAnswer:
            route = state.route
            allowed = {}
            if route and route.responders:
                # The operator identity on the route is not an answerer once people are named.
                for sender_id, actor_id, name in route.responders:
                    if sender_id != route.founder_sender:
                        allowed[sender_id] = (name, actor_id)
            elif route:
                allowed = {route.founder_sender: ("", route.founder_actor_id)}
            if not route or not route.enabled or route.company_id != self.company_id or (
                event.account, event.conversation
            ) != (route.account, route.conversation) or event.sender not in allowed:
                raise UnauthorizedInbound("Inbound identity is outside the verified founder route")
            responder_name, responder_actor = allowed[event.sender]
            source_key = digest([self.company_id, event.account, event.conversation, event.message_id])
            source_hash = digest([event.account, event.conversation, event.sender,
                                  event.message_id, event.text, event.reply_to, event.media_type])
            existing = state.inbox.get(source_key)
            if existing:
                if existing.source_hash != source_hash:
                    raise ReplayConflict("Same inbound ID has different content")
                return existing
            round = next((r for r in state.rounds.values()
                if (r.route.account, r.route.conversation) == (event.account, event.conversation)
                and any(a.message_id == event.reply_to and a.outcome == RoundStatus.ACCEPTED
                        for a in r.attempts) and event.reply_to is not None), None)
            quoted_round = round is not None
            active_contexts = {}
            for sent in sorted(state.rounds.values(), key=lambda value: value.slot):
                if (sent.route.account, sent.route.conversation) != (event.account, event.conversation) or not any(
                    a.outcome == RoundStatus.ACCEPTED and a.started_at <= event.received_at for a in sent.attempts):
                    continue
                for item in sent.items:
                    current = state.questions.get(item.question.id)
                    if current and current.status in ANSWERABLE:
                        active_contexts[(item.question.id, item.digest)] = (sent, item)
            if round is None and event.reply_to is None and len(active_contexts) == 1:
                only = next(iter(active_contexts.values()))[0]
                if len(only.items) == 1:
                    round = only
            # Unquoted replies use pending candidates in this bound company conversation,
            # never an assumed latest P1. Stable IDs can be resolved across rounds.
            pending = tuple(sorted((q for q in state.questions.values()
                if q.company_id == self.company_id and q.status in ANSWERABLE), key=lambda q: (q.created_at, q.id)))
            if round:
                candidate_questions = tuple(item.question for item in round.items
                    if (len(round.items) == 1 and quoted_round) or
                    (state.questions.get(item.question.id) and state.questions[item.question.id].status in ANSWERABLE))
            elif event.reply_to is None:
                sent_question_ids = {key[0] for key in active_contexts}
                candidate_questions = tuple(q for q in pending if not active_contexts or q.id in sent_question_ids)
            else:
                candidate_questions = ()
            candidates = tuple(q.id for q in candidate_questions)
            stable_labels = re.findall(r"\bQ-[0-9A-F]{8}\b", event.text.upper())
            legacy_labels = re.findall(r"(?im)^\s*P(\d+)\b", event.text) if round else []
            if stable_labels:
                # An explicit stable reference can correct an older/resolved question,
                # including when the founder quotes a different round's message.
                known = {q.id: q for q in candidate_questions}
                for q in state.questions.values():
                    if q.company_id == self.company_id and question_label(q.id) in stable_labels:
                        known.setdefault(q.id, q)
                candidate_questions = tuple(known.values())
                candidates = tuple(q.id for q in candidate_questions)
            selected: tuple[str, ...] = ()
            reason = "unmatched_reply" if not candidates else "needs_question_attribution"
            by_label: dict[str, list[str]] = {}
            for q in candidate_questions:
                by_label.setdefault(question_label(q.id), []).append(q.id)
            if stable_labels and all(len(by_label.get(label, [])) == 1 for label in stable_labels):
                selected = tuple(dict.fromkeys(by_label[label][0] for label in stable_labels))
                reason = "explicit_stable_question_reference"
            elif stable_labels:
                reason = "invalid_or_ambiguous_question_reference"
            elif legacy_labels and round:
                legacy = {i.label: i.question.id for i in round.items}
                if all(f"P{label}" in legacy for label in legacy_labels):
                    selected = tuple(dict.fromkeys(legacy[f"P{label}"] for label in legacy_labels))
                    reason = "explicit_question_labels"
                else:
                    reason = "invalid_question_labels"
            elif len(candidates) == 1 and round is not None and len(round.items) == 1:
                selected, reason = candidates, "single_question_reply" if quoted_round else "single_active_sent_question"
            # Explicit old labels remain correction evidence, separate from the
            # current open-candidate list. Preserve frozen delivery bases.
            selected_bases = {q.id: q for q in candidate_questions}
            if round:
                for item in round.items:
                    if item.question.id in selected:
                        selected_bases.setdefault(item.question.id, item.question)
            contexts = [(round, item) for item in round.items] if round else list(active_contexts.values())[-20:]
            conversation = tuple({"round_id": sent.id, "question_id": item.question.id,
                "semantic_digest": item.digest, "task_version": item.question.task_version,
                "question_revision": item.question.revision,
                "sent_at": max((a.finished_at or a.started_at for a in sent.attempts if a.outcome == RoundStatus.ACCEPTED), default=sent.slot).isoformat(),
                "scheduled_at": sent.slot.isoformat(),
                "text": sent.text, "why_chosen": item.why_chosen, "evidence": list(item.evidence)} for sent, item in contexts)
            answer = ReceivedAnswer(
                str(uuid5(NAMESPACE_URL, "nora-answer:" + source_key)), source_key,
                source_hash, self.company_id, responder_actor, event,
                round.id if round else None, candidates, selected,
                tuple((q.id, q.task_version, q.revision) for q in selected_bases.values()), reason,
                status="awaiting_text" if event.media_type else "awaiting_interpretation", conversation_contexts=conversation,
                responder_name=responder_name)
            state.inbox[source_key] = answer
            self._note_disagreement(state, answer)
            return answer
        return self.repository.transact(self.company_id, operation)

    @staticmethod
    def _note_disagreement(state: NoraState, answer: ReceivedAnswer) -> None:
        if not answer.responder_name or not answer.question_ids:
            return
        qid = answer.question_ids[0]
        others = [item for item in state.inbox.values()
                  if item.id != answer.id and qid in item.question_ids and item.responder_name
                  and item.responder_name != answer.responder_name]
        if not others:
            return
        previous = others[-1]
        if " ".join(previous.event.text.casefold().split()) == " ".join(answer.event.text.casefold().split()):
            return
        state.pending_followup = {"question_id": qid, "first_name": previous.responder_name,
                                  "first_text": previous.event.text, "second_name": answer.responder_name,
                                  "second_text": answer.event.text}
