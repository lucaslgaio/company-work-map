"""Aligned development clocks, actual LocalStore/projection/scheduler; no model/channel."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.nora import BrainRepository, InboundEvent, Nora, Route, SendReceipt
from company_pilot.nora.engine import question_label
from company_pilot.nora.worker import health
from company_pilot.operator import Operator, drain_nora_inbox, project_questions, queue_status


class OperatorOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime.fromisoformat("2026-11-17T10:00:00-03:00")
        clock_patch = patch("company_pilot.brain.engine.now", side_effect=lambda: self.now.isoformat())
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.brain = Brain(LocalStore(Path(self.tmp.name) / "brain"))
        self.brain.initialize("Empresa sintética de revisão de relatórios", synthetic=True)
        self.company = self.brain.snapshot()["company"]["id"]
        self.repository = BrainRepository(self.company, self.brain.transact_nora)
        self.route = Route(self.company, str(uuid4()), 1, "fixture-account", "100000000000@g.us", "fixture-native-founder", "fixture-founder", True)
        self.repository.transact(self.company, lambda state: setattr(state, "route", self.route))
        self.sent = []
        class Sender:
            def send(_self, route, text):
                self.sent.append(text)
                return SendReceipt(route.account, route.conversation, "fixture-sent-" + str(len(self.sent)))
        self.nora = Nora(self.company, self.repository, lambda: self.now, Sender())
        payload = self.observation("Ana revisa o Relatório de Qualidade no Chrome.")
        receipt = self.brain.ingest_observation(payload)
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == receipt["observation_id"])
        fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
        fields["action"] = {"value": "Revisar o Relatório de Qualidade", "basis": "observed", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}
        self.brain.apply_analysis(receipt["job_id"], {"tasks": [{"task_id": None, "name": "Revisar relatório", "fields": fields, "links": []}], "nodes": [], "context_updates": [], "next_question": None})

    def observation(self, text):
        return {"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()), "occurred_at": self.now.isoformat(),
            "policy_version": 1, "source": {"kind": "active_window", "app": "com.google.Chrome", "record_ref": "Relatório de Qualidade"},
            "classification": {"label": "company", "reason": "fixture business record", "context_version": 1},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}

    def test_worker_paused_keeps_answers_visible_and_does_not_repeat_pending_task(self):
        before = self.brain.snapshot()
        task = before["tasks"][0]
        selected = before["questions"][0]["id"]
        receipt = self.brain.record_answer([selected], "Ana revisa para liberar o relatório ao cliente.", str(uuid4()))
        self.now += timedelta(hours=4)
        operator = Operator(None, brain=self.brain, nora=self.nora, clock=lambda: self.now)
        tick = operator.once()
        self.assertEqual(tick["nora"], "empty")
        self.assertEqual(tick["analysis"], "not_run")
        self.assertEqual(tick["processing"]["oldest_queued_answer_age_seconds"], 14400)
        state = health(self.nora)
        self.assertEqual(state["processing"]["queued_answer_count"], 1)
        self.assertEqual(state["processing"]["oldest_queued_answer_age_seconds"], 14400)
        self.assertEqual(state["pending_questions"], len(before["questions"]))
        self.assertFalse(self.sent)
        answer = next(s for s in self.brain.snapshot()["sources"] if s["id"] == receipt["answer_id"])
        fields = deepcopy(task["fields"])
        for field in task["missing_fields"]:
            fields[field] = {"value": "informação sintética " + field, "basis": "reported", "evidence": [{"source_id": answer["id"], "quote": answer["text"]}]}
        self.brain.apply_analysis(receipt["job_id"], {"tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}], "nodes": [], "context_updates": [], "next_question": None})
        self.now += timedelta(hours=4)
        operator.once()
        status = health(self.nora)
        self.assertEqual(status["processing"]["queued_answer_count"], 0)
        self.assertEqual(status["pending_questions"], 0)
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertTrue(status["processing"]["map_last_change_observed_at"])

    def test_disposition_suppresses_only_targeted_unchanged_gap_and_new_context_reopens(self):
        selected = self.brain.snapshot()["questions"][0]["id"]
        # Explicit typed semantic fixture at the canonical transaction seam.
        # This tests projection/scheduling; interpretation is a separate proof.
        disposition = {"kind": "delegate", "delegate_to": "Renan", "disposition": None, "source_id": "fixture-intent"}
        def attach(state, _actor):
            question = state.questions[selected]
            basis = {"missing_links": sorted(question["missing_links"]), "revisit_evidence_ids": [],
                     "conflict": deepcopy(question.get("conflict")), "resend_request_id": question.get("resend_request_id")}
            state.put("questions", {**question, "response_disposition": {**disposition, "uncertainty_basis": basis}})
            return {"state": "fixture"}
        self.brain._transaction("fixture_disposition", str(uuid4()), {}, attach)
        project_questions(self.brain, self.nora)
        self.nora.tick()
        raw = self.brain.snapshot()["nora"]
        self.assertNotIn(selected, [item["question"]["id"] for round in raw["rounds"].values() for item in round["items"]])
        self.assertEqual(health(self.nora)["delegated_unchanged_questions"], 1)
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == selected)["status"], "pending")
        source_id = self.brain.ingest_observation(self.observation("Surgiu uma nova divergência no relatório."))["observation_id"]
        def change(state, _actor):
            q = state.questions[selected]
            state.put("questions", {**q, "observation_ids": q["observation_ids"] + [source_id], "revisit_evidence_ids": [source_id]})
            return {"state": "fixture"}
        self.brain._transaction("fixture_new_context", str(uuid4()), {}, change)
        self.now += timedelta(hours=4)
        project_questions(self.brain, self.nora)
        self.assertTrue(self.nora.repository.transact(self.nora.company_id, lambda state:
            any(q.id == selected for q, _reminder in self.nora._eligible(state, self.nora.now()))))
        self.nora.tick()
        # Reopening restores eligibility; one-message priority may serve a more
        # consequential unsent question first. It does not force a batch.
        self.assertEqual(health(self.nora)["delegated_unchanged_questions"], 0)

    def test_queue_readback_does_not_expose_message_bodies(self):
        self.brain.record_answer([self.brain.snapshot()["questions"][0]["id"]], "PRIVATE FIXTURE BODY", str(uuid4()))
        status = queue_status(self.brain.snapshot(), self.now)
        self.assertEqual(status["queued_answer_count"], 1)
        self.assertNotIn("PRIVATE FIXTURE BODY", str(status))

    def test_not_now_keeps_genuine_gap_visible_without_repeated_ask(self):
        selected = self.brain.snapshot()["questions"][0]["id"]
        def attach(state, _actor):
            question = state.questions[selected]
            basis = {"missing_links": sorted(question["missing_links"]), "revisit_evidence_ids": [],
                     "conflict": deepcopy(question.get("conflict")), "resend_request_id": question.get("resend_request_id")}
            state.put("questions", {**question, "response_disposition": {
                "kind": "dismiss", "disposition": "not_now", "delegate_to": None, "source_id": "fixture-explicit-not-now", "uncertainty_basis": basis}})
            return {"state": "fixture"}
        self.brain._transaction("fixture_not_now", str(uuid4()), {}, attach)
        project_questions(self.brain, self.nora)
        self.nora.tick()
        self.now += timedelta(days=1)
        project_questions(self.brain, self.nora)
        self.nora.tick()
        state = self.brain.snapshot()
        self.assertEqual(next(q for q in state["questions"] if q["id"] == selected)["status"], "pending")
        self.assertEqual(health(self.nora)["dismissed_unchanged_questions"], 1)
        self.assertNotIn(selected, [item["question"]["id"] for round in state["nora"]["rounds"].values() for item in round["items"]])

    def test_handoff_unknown_replay_freezes_scope_before_downstream_commit(self):
        project_questions(self.brain, self.nora)
        selected = self.brain.snapshot()["questions"][0]["id"]
        text = question_label(selected) + ": Ana prepara o relatório para o cliente."
        self.nora.receive(InboundEvent(self.route.account, self.route.conversation,
            self.route.founder_sender, "fixture-unknown", self.now, text))
        calls = []
        def targets(_text):
            snap = self.brain.snapshot()
            return {"open_question_ids": [q["id"] for q in snap["questions"] if q["status"] == "pending"],
                    "correction_targets": [{"task_id": t["id"], "version": t["version"]} for t in snap["tasks"]]}
        def message(body, event_id, **kwargs):
            # Adapter-seam fixture for the reviewed new signature, reusing the
            # actual immutable answer transaction, not semantic intent/model.
            calls.append(deepcopy(kwargs))
            versions = {item["id"]: item["version"] for item in kwargs["candidate_task_versions"]}
            result = self.brain.record_answer(kwargs["question_ids"], body, event_id,
                source=kwargs["source"], candidate_question_ids=kwargs["candidate_question_ids"], base_versions=versions)
            if len(calls) == 1:
                raise TimeoutError("fixture lost response after committed answer")
            return result
        with patch.object(self.brain, "message_targets", side_effect=targets, create=True) as selector, \
             patch.object(self.brain, "record_message", side_effect=message, create=True):
            first = drain_nora_inbox(self.brain)
            self.assertEqual(first["pending_intake"], 1)
            def unrelated_change(state, _actor):
                task = next(iter(state.tasks.values()))
                state.put("tasks", {**task, "name": "Revisar relatório atualizado"})
                return {"state": "fixture"}
            self.brain._transaction("fixture_map_change", str(uuid4()), {}, unrelated_change)
            second = drain_nora_inbox(self.brain)
            self.assertEqual(second["queued_answers"], 1)
            self.assertEqual(selector.call_count, 1)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len([s for s in self.brain.snapshot()["sources"] if s["kind"] == "answer"]), 1)
        inbox = next(iter(self.brain.snapshot()["nora"]["inbox"].values()))
        self.assertEqual(inbox["status"], "queued_for_analysis")
        self.assertEqual(inbox["event"]["text"], text)


if __name__ == "__main__": unittest.main()
