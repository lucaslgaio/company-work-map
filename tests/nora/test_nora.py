"""Synthetic failure/integration checks; no real Nora route or company facts.

The SQLite/pickle store exists ONLY as a controlled test fixture. Production must
inject the selected brain's durable transaction adapter; never unpickle user data.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import pickle
import json
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from uuid import NAMESPACE_URL, uuid5

from company_pilot.nora import (
    InboundEvent, KnownUnsent, Nora, NoraState, Question, QuestionStatus,
    ReplayConflict, Route, RoundStatus, Schedule, SendReceipt, UnauthorizedInbound,
)
from company_pilot.nora.openclaw import OpenClawSender, inbound_from_record
from company_pilot.nora.repository import BrainRepository, decode_state, encode_state
from company_pilot.nora.worker import health, run
from company_pilot.nora.engine import digest, question_digest, question_label


def uid(name):
    return str(uuid5(NAMESPACE_URL, "synthetic:" + name))


def dt(raw):
    return datetime.fromisoformat(raw)


class FixtureRepository:
    def __init__(self, path):
        self.path = path
        self.fail_when = None
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("create table if not exists company (id text primary key, state blob not null)")
            db.commit()

    def transact(self, company_id, operation):
        with self.transaction(company_id) as state:
            return operation(state)

    @contextmanager
    def transaction(self, company_id):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("begin immediate")
            row = db.execute("select state from company where id = ?", (company_id,)).fetchone()
            state = pickle.loads(row[0]) if row else NoraState()
            yield state
            if self.fail_when and self.fail_when(state):
                raise RuntimeError("synthetic durable commit failure")
            db.execute("insert into company values (?, ?) on conflict(id) do update set state=excluded.state",
                       (company_id, pickle.dumps(state)))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


class Clock:
    def __init__(self):
        self.value = dt("2026-11-17T10:00:00-03:00")

    def __call__(self):
        return self.value


class FakeSender:
    def __init__(self, *outcomes):
        self.calls = []
        self.outcomes = list(outcomes)
        self.lock = threading.Lock()

    def send(self, route, text):
        with self.lock:
            self.calls.append((route, text))
            number = len(self.calls)
            outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(route, text)
        return outcome or SendReceipt(route.account, route.conversation, f"synthetic-channel-{number}")


class NoraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = FixtureRepository(str(Path(self.tmp.name) / "fixture.sqlite"))
        self.company = uid("company")
        self.route = Route(self.company, uid("route"), 1, "synthetic-account",
                           "100000000000@g.us", "synthetic-founder", uid("founder-actor"), True)
        with self.repo.transaction(self.company) as state:
            state.route = self.route
        self.clock = Clock()
        self.sender = FakeSender()
        self.nora = Nora(self.company, self.repo, self.clock, self.sender)

    def question(self, number=0, **changes):
        q = Question(uid(f"q{number}"), self.company, uid(f"task{number}"), 1, 1,
            self.clock.value + timedelta(seconds=number), f"context{number % 2}",
            ("Pagamentos", "Atendimento")[number % 2], f"Atividade sintética {number}",
            "A qual entrega este registro se refere?", f"https://brain.invalid/evidence/{number}",
            (uid(f"observation{number}"),), ("purpose",), evidence=({"source_id": uid(f"observation{number}"), "quote": f"Registro sintético da atividade {number}", "locator": f"synthetic-fixture:{number}"},))
        return replace(q, **changes)

    def seed(self, count=1):
        questions = [self.question(i) for i in range(count)]
        for q in questions:
            self.nora.put_question(q)
        return questions

    def state(self):
        with self.repo.transaction(self.company) as state:
            return state

    def event(self, **changes):
        event = InboundEvent(self.route.account, self.route.conversation,
            self.route.founder_sender, "synthetic-inbound-1", self.clock.value,
            "Foi para a entrega combinada.", "synthetic-channel-1")
        return replace(event, **changes)

    def test_pending_overflow_new_context_and_one_batch_per_slot(self):
        questions = self.seed(7)
        for hour in (10, 14, 18):
            self.clock.value = dt(f"2026-11-17T{hour}:00:00-03:00")
            result = self.nora.tick()
            self.assertEqual(result.outcome, "accepted")
            self.assertEqual(len(self.state().rounds[result.round_id].items), 1)
            self.nora.tick()
        self.assertEqual(len(self.sender.calls), 3)
        self.assertEqual(result.pending_count, 7)
        self.assertGreater(result.overflow_count, 0)
        self.clock.value = dt("2026-11-17T22:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "not_due")
        self.assertTrue(all(q.status == QuestionStatus.PENDING for q in self.state().questions.values()))

    def test_unrelated_partial_answer_keeps_original_ask_and_single_reminder(self):
        q = self.seed()[0]
        original = self.nora.tick()
        changed = replace(q, revision=2, task_version=2, answer_ids=(uid("unrelated-answer"),))
        self.nora.put_question(changed)
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.clock.value = dt("2026-11-18T10:00:00-03:00")
        reminder = self.nora.tick()
        item = self.state().rounds[reminder.round_id].items[0]
        self.assertTrue(item.reminder)
        self.assertEqual(item.prior_ask_at, self.state().rounds[original.round_id].attempts[0].finished_at)
        self.nora.put_question(replace(changed, revision=3, answer_ids=changed.answer_ids + (uid("other-partial"),)))
        self.clock.value = dt("2026-11-19T10:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "empty")

    def test_activity_questions_have_one_description_link_and_natural_answer_instruction(self):
        name = "Atualizar planejamento no com.google.Chrome"
        q = self.question(context_name=name, activity_summary=name, missing_links=("performer",))
        self.nora.put_question(q)
        self.nora.put_question(self.question(1, missing_links=("purpose",)))
        result = self.nora.tick()
        text = self.sender.calls[0][1]
        self.assertNotIn("com.google.Chrome", text)
        self.assertIn("Chrome", text)
        self.assertEqual(text.count("?"), 1)
        self.assertIn("Quem executa", text)
        self.assertIn("Pode responder por aqui", text)
        self.assertNotIn(q.source_url, text)
        self.assertLessEqual(len(text), 400)
        item = self.state().rounds[result.round_id].items[0]
        self.assertEqual(item.evidence, q.evidence)
        self.assertEqual(item.why_chosen["eligible_count"], 2)

    def test_raw_reply_waiting_handoff_does_not_trigger_the_same_question(self):
        q = self.seed()[0]
        received = self.nora.receive(self.event(reply_to=None))
        self.assertEqual(received.question_ids, ())
        self.assertEqual(received.candidate_question_ids, (q.id,))
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(len(self.sender.calls), 0)
        self.assertEqual(self.state().questions[q.id].status, QuestionStatus.PENDING)

    def test_natural_reply_waiting_handoff_holds_candidates_without_selecting_them(self):
        questions = self.seed(3)
        for hour in (10, 14, 18):
            self.clock.value = dt(f"2026-11-17T{hour}:00:00-03:00")
            self.nora.tick()
        event = self.event(message_id="natural-unassigned-handoff", reply_to=None,
                           text="Ana faz a revisão e Renan recebe o relatório.")
        received = self.nora.receive(event)
        self.assertEqual(received.question_ids, ())
        self.assertEqual(set(received.candidate_question_ids), {q.id for q in questions})
        self.clock.value = dt("2026-11-18T10:00:00-03:00")
        tick = self.nora.tick()
        self.assertEqual(tick.outcome, "empty")
        self.assertEqual(tick.pending_count, 3)
        self.assertEqual(len(self.sender.calls), 3)
        self.assertEqual(self.nora.receive(event), received)
        status = health(self.nora)
        self.assertEqual(status["raw_answers_waiting_handoff"], 1)
        self.assertEqual(status["questions_waiting_analysis"], 3)

    def test_quoted_round_candidates_are_current_but_explicit_resolved_reference_survives(self):
        first, second = self.seed(2)
        self.nora.tick()
        self.nora.put_question(replace(first, revision=2, status=QuestionStatus.RESOLVED))
        reply = self.nora.receive(self.event())
        self.assertEqual(reply.candidate_question_ids, (first.id,))
        self.assertEqual(reply.question_ids, (first.id,))
        explicit = self.nora.receive(self.event(message_id="correction-resolved",
            text=f"{question_label(first.id)}: corrigindo a resposta anterior."))
        self.assertEqual(explicit.question_ids, (first.id,))

    def test_legacy_answer_digest_migrates_history_without_rewriting_sent_round(self):
        q = self.question(answer_ids=(uid("old-answer"),))
        self.nora.put_question(q)
        sent = self.nora.tick()
        old_digest = digest({"missing": sorted(q.missing_links), "relevant_context": [],
                             "answers": sorted(q.answer_ids), "conflicts": [], "requested_resend": None})
        with self.repo.transaction(self.company) as state:
            frozen = state.rounds[sent.round_id]
            state.rounds[sent.round_id] = replace(frozen, items=(replace(frozen.items[0], digest=old_digest),))
            state.suppressions = {(q.id, old_digest): sent.round_id}
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "empty")
        restored = self.state()
        self.assertEqual(restored.rounds[sent.round_id].items[0].digest, old_digest)
        self.assertEqual(restored.suppressions[(q.id, question_digest(q))], sent.round_id)

    def test_no_unconfigured_route_no_catchup_and_first_use_timestamp(self):
        self.seed()
        with self.repo.transaction(self.company) as state:
            state.route = replace(self.route, enabled=False)
        self.assertEqual(self.nora.tick().outcome, "route_unavailable")
        self.assertEqual(len(self.sender.calls), 0)
        with self.repo.transaction(self.company) as state:
            state.route = self.route
        self.clock.value = dt("2026-11-17T23:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "not_due")
        self.assertEqual(self.nora.next_scheduled_at(), dt("2026-11-18T10:00:00-03:00"))
        self.clock.value = dt("2026-11-18T10:06:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "not_due")
        self.clock.value = dt("2026-11-18T14:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "accepted")
        self.assertEqual(len(self.sender.calls), 1)

    def test_concurrent_workers_claim_one_durable_attempt(self):
        self.seed()
        def slow(route, text):
            time.sleep(0.1)
            return SendReceipt(route.account, route.conversation, "concurrent-receipt")
        self.sender.outcomes.append(slow)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.nora.tick(), range(8)))
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(self.state().rounds), 1)
        self.assertTrue(any(r.outcome == "accepted" for r in results))

    def test_commit_before_send_failure_has_no_external_effect(self):
        self.seed()
        self.repo.fail_when = lambda state: any(r.status == RoundStatus.SENDING for r in state.rounds.values())
        with self.assertRaises(RuntimeError):
            self.nora.tick()
        self.assertEqual(self.sender.calls, [])
        self.repo.fail_when = None
        self.assertEqual(self.nora.tick().outcome, "accepted")

    def test_commit_after_send_failure_recovers_unknown_after_restart(self):
        self.seed()
        self.repo.fail_when = lambda state: any(r.status == RoundStatus.ACCEPTED for r in state.rounds.values())
        with self.assertRaises(RuntimeError):
            self.nora.tick()
        self.assertEqual(len(self.sender.calls), 1)
        restarted_repo = FixtureRepository(self.repo.path)
        restarted = Nora(self.company, restarted_repo, self.clock, self.sender)
        self.clock.value += timedelta(seconds=46)
        self.assertEqual(restarted.tick().outcome, "unknown")
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.assertEqual(restarted.tick().outcome, "empty")
        self.assertEqual(len(self.sender.calls), 1)

    def test_process_crash_after_marker_never_blindly_resends(self):
        self.seed()
        self.sender.outcomes.append(SystemExit("synthetic process death"))
        with self.assertRaises(SystemExit):
            self.nora.tick()
        self.clock.value += timedelta(seconds=46)
        self.assertEqual(self.nora.tick().outcome, "unknown")
        self.assertEqual(len(self.sender.calls), 1)

    def test_known_unsent_retries_are_bounded_and_carry_to_next_slot(self):
        self.seed()
        self.sender.outcomes.extend([KnownUnsent()] * 3)
        self.assertEqual(self.nora.tick().outcome, "known_unsent")
        self.assertEqual(self.nora.tick().outcome, "retry_deferred")
        for _ in range(2):
            self.clock.value += timedelta(seconds=30)
            self.assertEqual(self.nora.tick().outcome, "known_unsent")
        self.clock.value += timedelta(seconds=30)
        self.assertEqual(self.nora.tick().outcome, "retry_deferred")
        self.assertEqual(len(self.sender.calls), 3)
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "accepted")
        self.assertEqual(len(self.sender.calls), 4)

    def test_unknown_requires_verified_recovery_and_never_sends_during_reconcile(self):
        self.seed()
        self.sender.outcomes.append(TimeoutError("synthetic timeout"))
        result = self.nora.tick()
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(self.nora.tick().outcome, "unknown")
        with self.assertRaises(ValueError):
            self.nora.reconcile_unknown(result.round_id, evidence_id="")
        self.nora.reconcile_unknown(result.round_id, evidence_id=uid("recovery"), verified_unsent=True)
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.nora.tick().outcome, "expired")
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "accepted")

    def test_invalid_receipt_is_unknown_and_positive_reconciliation_correlates(self):
        self.seed()
        self.sender.outcomes.append(SendReceipt("wrong-account", self.route.conversation, "wrong"))
        result = self.nora.tick()
        self.assertEqual(result.outcome, "unknown")
        with self.assertRaises(ValueError):
            self.nora.reconcile_unknown(result.round_id, evidence_id="e", receipt=SendReceipt("wrong", "wrong", "id"))
        self.nora.reconcile_unknown(result.round_id, evidence_id=uid("recovery"),
            receipt=SendReceipt(self.route.account, self.route.conversation, "recovered-message"))
        answer = self.nora.receive(self.event(reply_to="recovered-message"))
        self.assertEqual(answer.correlation, "single_question_reply")

    def test_route_revision_stops_unsent_round_then_waits_for_next_slot(self):
        self.seed()
        self.sender.outcomes.append(KnownUnsent())
        self.nora.tick()
        self.clock.value += timedelta(seconds=30)
        with self.repo.transaction(self.company) as state:
            state.route = replace(self.route, revision=2)
        self.assertEqual(self.nora.tick().outcome, "changed_before_send")
        self.assertEqual(len(self.sender.calls), 1)

    def test_recovery_cannot_bind_another_rounds_message_receipt(self):
        self.seed()
        self.nora.tick()
        self.nora.put_question(self.question(2))
        self.sender.outcomes.append(TimeoutError())
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        unknown = self.nora.tick()
        with self.assertRaisesRegex(ValueError, "another round"):
            self.nora.reconcile_unknown(unknown.round_id, evidence_id=uid("recovery"),
                receipt=SendReceipt(self.route.account, self.route.conversation, "synthetic-channel-1"))

    def test_wrong_account_conversation_sender_and_company_are_denied(self):
        self.seed()
        self.nora.tick()
        for field in ("account", "conversation", "sender"):
            with self.subTest(field=field), self.assertRaises(UnauthorizedInbound):
                self.nora.receive(self.event(**{field: "untrusted"}, text="I am the founder"))
        other = Nora(uid("another-company"), self.repo, self.clock, self.sender)
        with self.assertRaises(UnauthorizedInbound):
            other.receive(self.event())
        self.assertEqual(len(self.state().inbox), 0)

    def test_night_answer_is_attributed_idempotent_and_does_not_send(self):
        q = self.seed()[0]
        self.nora.tick()
        self.clock.value = dt("2026-11-18T01:00:00-03:00")
        event = self.event()
        answer = self.nora.receive(event)
        replay = self.nora.receive(replace(event, received_at=event.received_at + timedelta(minutes=1)))
        self.assertEqual(answer, replay)
        self.assertEqual(answer.actor_id, self.route.founder_actor_id)
        self.assertEqual(answer.question_ids, (q.id,))
        with self.assertRaises(ReplayConflict):
            self.nora.receive(replace(event, text="Changed payload under same ID"))
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.state().questions[q.id].status, QuestionStatus.PENDING)

    def test_stale_and_contradictory_replies_preserve_both_sources(self):
        q = self.seed()[0]
        self.nora.tick()
        self.nora.put_question(replace(q, revision=2, task_version=4))
        first = self.nora.receive(self.event())
        correction = self.nora.receive(self.event(message_id="correction", text="Corrigindo: era outra entrega."))
        self.assertEqual(first.question_versions, ((q.id, 1, 1),))
        self.assertNotEqual(first.id, correction.id)
        self.assertEqual(len(self.state().inbox), 2)
        self.assertEqual(self.state().questions[q.id].task_version, 4)
        self.assertEqual(first.status, "awaiting_interpretation")

    def test_multi_question_ambiguity_and_explicit_partial_answer(self):
        questions = self.seed(2)
        self.nora.tick()
        self.clock.value += timedelta(hours=4)
        self.nora.tick()
        ambiguous = self.nora.receive(self.event(reply_to=None))
        self.assertEqual(ambiguous.question_ids, ())
        self.assertEqual(set(ambiguous.candidate_question_ids), {q.id for q in questions})
        self.assertEqual(len(ambiguous.conversation_contexts), 2)
        partial = self.nora.receive(self.event(message_id="partial", reply_to="synthetic-channel-2"))
        self.assertEqual(partial.question_ids, (questions[1].id,))
        invalid = self.nora.receive(self.event(message_id="invalid", text="P99: resposta"))
        self.assertEqual(invalid.correlation, "invalid_question_labels")
        unmatched = self.nora.receive(self.event(message_id="unmatched", reply_to="unrecognized-message"))
        self.assertEqual(unmatched.question_ids, ())
        self.assertEqual(unmatched.correlation, "unmatched_reply")
        self.assertTrue(all(q.status == QuestionStatus.PENDING for q in self.state().questions.values()))

    def test_unquoted_reply_single_pending_and_no_latest_round_guess_for_many(self):
        q = self.seed()[0]
        self.nora.tick()
        single = self.nora.receive(self.event(message_id="unquoted-one", reply_to=None))
        self.assertEqual(single.question_ids, (q.id,))
        self.assertEqual(single.correlation, "single_active_sent_question")
        other = self.question(2)
        self.nora.put_question(other)
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.nora.tick()
        plain = self.nora.receive(self.event(message_id="unquoted-many", reply_to=None, text="P1: era para aquela entrega"))
        self.assertEqual(plain.question_ids, ())
        self.assertEqual(set(plain.candidate_question_ids), {q.id, other.id})
        label = next(item.label for r in self.state().rounds.values() for item in r.items if item.question.id == q.id)
        explicit = self.nora.receive(self.event(message_id="stable-reference", reply_to=None, text=f"{label}: era aquela entrega"))
        self.assertEqual(explicit.question_ids, (q.id,))
        self.assertEqual(explicit.round_id, None)

    def test_one_scheduled_reminder_after_24h_then_pending_for_rest_of_week(self):
        q = self.seed()[0]
        first = self.nora.tick()
        for day in range(7):
            for hour in (6, 10, 14, 18, 22):
                self.clock.value = dt(f"2026-11-{17 + day:02d}T{hour:02d}:00:00-03:00")
                self.nora.tick()
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(self.sender.calls[1][1].count("?"), 1)
        self.assertNotIn("Q-", self.sender.calls[1][1])
        self.assertEqual(self.state().questions[q.id].status, QuestionStatus.PENDING)
        reminders = [r for r in self.state().rounds.values() if any(i.reminder for i in r.items)]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0].slot, dt("2026-11-18T10:00:00-03:00"))
        self.assertNotEqual(reminders[0].id, first.round_id)

    def test_unknown_reminder_is_not_retried_or_repeated(self):
        self.seed()
        self.nora.tick()
        self.sender.outcomes.append(TimeoutError())
        self.clock.value = dt("2026-11-18T10:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "unknown")
        self.clock.value = dt("2026-11-19T10:00:00-03:00")
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(len(self.sender.calls), 2)

    def test_due_reminder_reserves_capacity_with_continuous_fresh_arrivals(self):
        original = self.seed()[0]
        self.nora.tick()
        number = 100
        for day in range(18, 24):
            for hour in (10, 14, 18):
                self.clock.value = dt(f"2026-11-{day:02d}T{hour}:00:00-03:00")
                for _ in range(3):
                    self.nora.put_question(self.question(number))
                    number += 1
                result = self.nora.tick()
                self.assertEqual(len(self.state().rounds[result.round_id].items), 1)
                self.assertGreaterEqual(result.overflow_count, 1)
        reminders = [item for r in self.state().rounds.values() for item in r.items
                     if item.question.id == original.id and item.reminder]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(self.state().questions[original.id].status, QuestionStatus.PENDING)

    def test_nontext_reply_is_visible_waits_for_text_and_never_acknowledges(self):
        q = self.seed()[0]
        self.nora.tick()
        for index, media_type in enumerate(("audio/ogg", "image/jpeg")):
            answer = self.nora.receive(self.event(message_id=f"media-{index}", reply_to=None,
                                                 text="", media_type=media_type))
            self.assertEqual(answer.status, "awaiting_text")
            self.assertEqual(answer.event.media_type, media_type)
            self.assertEqual(answer.question_ids, (q.id,))
        self.assertEqual(health(self.nora)["media_replies_awaiting_text"], 2)
        self.assertEqual(self.state().questions[q.id].status, QuestionStatus.PENDING)
        self.assertEqual(len(self.sender.calls), 1)

    def test_stable_reference_corrects_resolved_question_from_another_round(self):
        q = self.seed()[0]
        self.nora.tick()
        original_label = next(item.label for r in self.state().rounds.values() for item in r.items)
        self.nora.put_question(replace(q, revision=2, status=QuestionStatus.RESOLVED))
        self.nora.put_question(self.question(2))
        self.clock.value = dt("2026-11-17T14:00:00-03:00")
        self.nora.tick()
        answer = self.nora.receive(self.event(message_id="correction-stable", reply_to="synthetic-channel-2",
                                             text=f"Corrigindo {original_label}: era outra entrega."))
        self.assertEqual(answer.question_ids, (q.id,))
        self.assertEqual(self.state().questions[q.id].status, QuestionStatus.RESOLVED)

    def test_json_codec_keeps_reminder_time_and_media_marker(self):
        self.seed()
        self.nora.tick()
        self.clock.value = dt("2026-11-18T10:00:00-03:00")
        self.nora.tick()
        self.nora.receive(self.event(message_id="audio-for-codec", text="", media_type="audio/ogg"))
        encoded = json.loads(json.dumps(encode_state(self.state(), self.company)))
        restored = decode_state(encoded, self.company)
        self.assertEqual(encode_state(restored, self.company), encoded)
        self.assertEqual(len(restored.reminders), 1)
        self.assertTrue(any(i.prior_ask_at for r in restored.rounds.values() for i in r.items))
        self.assertEqual(next(iter(restored.inbox.values())).status, "awaiting_text")

    def test_empty_round_is_silent_and_resolved_questions_are_not_sent(self):
        self.nora.put_question(self.question(status=QuestionStatus.RESOLVED))
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(self.sender.calls, [])

    def test_four_recurring_rounds_do_not_repeat_missing_purpose(self):
        q = self.seed()[0]
        self.nora.tick()
        for revision, hour in enumerate((14, 18), start=2):
            q = replace(q, revision=revision,
                        observation_ids=q.observation_ids + (uid(f"recurrence-{hour}"),))
            self.nora.put_question(q)
            self.clock.value = dt(f"2026-11-17T{hour:02d}:00:00-03:00")
            self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(self.state().questions[q.id].observation_ids), 3)
        self.assertEqual(self.state().questions[q.id].status, QuestionStatus.PENDING)

    def test_canonical_missing_fields_prevent_model_key_churn(self):
        q = self.seed()[0]
        with self.assertRaises(ValueError):
            replace(q, missing_links=("link_to_translation_service_activity",))
        with self.assertRaises(ReplayConflict):
            self.nora.put_question(replace(q, id=uid("duplicate-model-question")))
        self.assertEqual(len(self.state().questions), 1)

    def test_json_brain_callback_replays_before_commit_without_duplicate_effect(self):
        class JsonBackend:
            raw = {}
            commits = 0
            def transact_nora(inner, operation, event_id):
                # Simulate a discarded optimistic evaluation; the callback may replay.
                operation(json.loads(json.dumps(inner.raw)))
                updated = json.loads(json.dumps(inner.raw))
                result = operation(updated)
                if updated != inner.raw:
                    inner.commits += 1
                inner.raw = json.loads(json.dumps(updated))
                return result
        backend = JsonBackend()
        repository = BrainRepository(self.company, backend.transact_nora)
        repository.transact(self.company, lambda state: setattr(state, "route", self.route))
        nora = Nora(self.company, repository, self.clock, self.sender)
        nora.put_question(self.question())
        self.assertEqual(nora.tick().outcome, "accepted")
        answer = nora.receive(self.event())
        self.assertEqual(nora.receive(self.event()), answer)
        # A no-op poll/health must not create a content commit every fifteen seconds.
        before = backend.commits
        self.assertEqual(nora.tick().outcome, "accepted")
        report = health(nora)
        self.assertEqual(report["channel_connectivity"], "not_probed")
        self.assertEqual(backend.commits, before)
        self.assertEqual(len(self.sender.calls), 1)
        state = decode_state(backend.raw, self.company)
        self.assertEqual(encode_state(state, self.company), backend.raw)
        with self.assertRaises(ValueError):
            decode_state(backend.raw, uid("wrong-company"))

    def test_worker_reports_metadata_and_stops_without_activating_any_default_route(self):
        self.seed()
        stop = threading.Event()
        reports = []
        def report(event):
            reports.append(event)
            stop.set()
        run(self.nora, stop, report=report)
        self.assertEqual(reports[0]["outcome"], "accepted")
        self.assertNotIn("text", reports[0])
        self.assertEqual(len(self.sender.calls), 1)

    def test_clock_crosses_grace_before_sender(self):
        self.seed()
        ticks = iter([self.clock.value, self.clock.value + timedelta(minutes=6),
                      self.clock.value + timedelta(minutes=6)])
        nora = Nora(self.company, self.repo, lambda: next(ticks), self.sender)
        self.assertEqual(nora.tick().outcome, "known_unsent")
        self.assertEqual(self.sender.calls, [])


class ScheduleTests(unittest.TestCase):
    def test_quiet_boundaries_and_foreign_zone_same_instant(self):
        schedule = Schedule()
        for raw in ["2026-11-17T23:59:59-03:00", "2026-11-18T00:00:00-03:00", "2026-11-18T05:59:59-03:00"]:
            self.assertIsNone(schedule.due_at(dt(raw)))
            self.assertEqual(schedule.next_at(dt(raw)), dt("2026-11-18T10:00:00-03:00"))
        self.assertEqual(schedule.due_at(dt("2026-11-18T05:00:00-08:00")), dt("2026-11-18T10:00:00-03:00"))
        self.assertIsNone(schedule.due_at(dt("2026-11-18T10:05:00-03:00")))
        with self.assertRaises(ValueError):
            schedule.next_at(datetime(2026, 11, 17))

    def test_every_minute_has_only_three_unique_slots_per_day(self):
        schedule = Schedule()
        start = dt("2026-11-17T00:00:00-03:00")
        slots = set()
        for minute in range(48 * 60):
            slot = schedule.due_at(start + timedelta(minutes=minute))
            if slot:
                slots.add(slot)
                self.assertEqual(slot.astimezone(start.tzinfo).hour % 4, 2)
        self.assertEqual(len(slots), 6)


class OpenClawTests(unittest.TestCase):
    def setUp(self):
        self.route = Route(uid("company"), uid("route"), 1, "synthetic", "100000000000@g.us",
                           "synthetic-founder", uid("actor"), True)

    def test_arguments_are_literal_and_only_matching_route_can_send(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, 'log\n{"payload":{"result":{"messageId":"receipt"}}}', "")
        sender = OpenClawSender("/synthetic/openclaw", self.route, runner=runner)
        text = "Literal `command` and $(command), quotes ' are data"
        self.assertEqual(sender.send(self.route, text).message_id, "receipt")
        self.assertEqual(calls[0][0][-1], text)
        self.assertNotIn("shell", calls[0][1])
        with self.assertRaises(KnownUnsent):
            sender.send(replace(self.route, account="other"), text)
        self.assertEqual(len(calls), 1)

    def test_nonzero_timeout_missing_receipt_are_unknown_and_errors_redacted(self):
        for result in [subprocess.CompletedProcess([], 1, "", "sensitive body"),
                       subprocess.CompletedProcess([], 0, '{}', "")]:
            sender = OpenClawSender("/synthetic/openclaw", self.route, runner=lambda *a, **k: result)
            with self.assertRaises(RuntimeError) as raised:
                sender.send(self.route, "sensitive body")
            self.assertNotIn("sensitive body", str(raised.exception))
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("sensitive body", 30)
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            OpenClawSender("/synthetic/openclaw", self.route, runner=timeout).send(self.route, "sensitive body")

    def test_missing_binary_is_known_unsent(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError()
        with self.assertRaises(KnownUnsent):
            OpenClawSender("/synthetic/openclaw", self.route, runner=missing).send(self.route, "body")

    def test_strict_bridge_projection_retains_account_and_identity(self):
        raw = {"schema_version": 1, "account": "synthetic", "conversation": self.route.conversation,
               "sender": self.route.founder_sender, "message_id": "inbound", "received_at": "2026-11-17T09:00:00Z",
               "text": "Resposta", "reply_to": "receipt"}
        event = inbound_from_record(raw)
        self.assertEqual((event.account, event.sender, event.reply_to), ("synthetic", "synthetic-founder", "receipt"))
        for bad in [dict(raw, account=None), dict(raw, sender=""), dict(raw, received_at="2026-11-17T09:00:00")]:
            with self.assertRaises(ValueError):
                inbound_from_record(bad)


if __name__ == "__main__":
    unittest.main()
