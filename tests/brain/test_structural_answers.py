"""E1: ordinary scheduled founder replies restructure the actual map worker.

Only interpretation, sender and clock are injected. No model or external sends.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, FIELDS, LocalStore
from company_pilot.inference import SemanticWorker
from company_pilot.nora import BrainRepository, InboundEvent, Nora, Route, SendReceipt
from company_pilot.nora.engine import question_label
from company_pilot.operator import drain_nora_inbox, project_questions


def uid():
    return str(uuid4())


def observation():
    text = "Joana preparou uma transferência e registrou o comprovante."
    return {"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": "2026-11-17T09:00:00+00:00", "policy_version": 1,
            "source": {"kind": "active_window", "app": "Synthetic Finance"},
            "classification": {"label": "company", "reason": "registro sintético da empresa"},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}


def evidence(source):
    return [{"source_id": source["id"], "quote": source["text"]}]


def claim(source, value):
    return {"value": value, "basis": "reported", "evidence": evidence(source)}


def result(operations=()):
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None, "map_changes": list(operations)}


def expected(context, ids, include_edges=False):
    selected = set(ids)
    if include_edges:
        selected.update(edge["id"] for edge in context["edges"] if edge["from"] in ids or edge["to"] in ids)
    return [{"id": item["id"], "version": item["version"]} for group in ("tasks", "nodes", "edges") for item in context[group] if item["id"] in selected]


class FixtureInterpreter:
    def __init__(self, transform=None):
        self.transform = transform
        self.calls = []

    def interpret(self, job, context):
        self.calls.append((copy.deepcopy(job), copy.deepcopy(context)))
        source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
        if job["kind"] == "disambiguation":
            output = result()
            output["answer_assignments"] = [{"answer_id": job["unassigned_answer_id"],
                "question_ids": [job["candidate_question_ids"][0]], "evidence": evidence(source)}]
        elif job["kind"] == "observation":
            fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
            fields["action"] = {"value": "Preparar transferência e registrar comprovante", "basis": "observed", "evidence": evidence(source)}
            fields["performer"] = {"value": "Joana", "basis": "observed", "evidence": evidence(source)}
            output = result()
            output["tasks"] = [{"task_id": None, "name": "Preparar transferência e registrar comprovante", "fields": fields, "links": []}]
        elif "Separe" in source["text"]:
            selected_ids = job["task_ids"] or [question["task_id"] for question in context["questions"] if question["id"] in job.get("question_ids", []) and question["task_id"] in {task["id"] for task in context["tasks"]}]
            task_id = selected_ids[0]
            task = next(t for t in context["tasks"] if t["id"] == task_id)
            output = result([{"op": "split", "id": task_id,
                "expected_versions": expected(context, [task_id], True), "evidence": evidence(source),
                "successors": [{"id": None, "name": name, "fields": {"action": claim(source, name)},
                                "evidence_ids": task["evidence_ids"]} for name in ("Preparar transferência", "Registrar comprovante")]}])
        else:
            tasks = [t for t in context["tasks"] if not t["superseded_by"]]
            ids = [t["id"] for t in tasks]
            output = result([{"op": "merge", "ids": ids,
                "expected_versions": expected(context, ids, True), "evidence": evidence(source),
                "successors": [{"id": None, "name": "Registrar pagamento com comprovante", "fields": {"action": claim(source, "Registrar pagamento com comprovante")},
                                "evidence_ids": sorted({sid for task in tasks for sid in task["evidence_ids"]})}]}])
        if job["kind"] == "message":
            chosen = [output["map_changes"][0]["id"]] if output["map_changes"][0]["op"] == "split" else output["map_changes"][0]["ids"]
            output["message_intent"] = {"kind": "correction", "task_ids": chosen, "question_ids": job.get("question_ids", []),
                "delegate_to": None, "disposition": None, "evidence": evidence(source)}
        return self.transform(output, job, context) if self.transform else output


class StructuralAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética de desenvolvimento", synthetic=True)
        self.interpreter = FixtureInterpreter()
        self.worker = SemanticWorker(self.brain, self.interpreter)
        self.brain.ingest_observation(observation())
        self.assertEqual(self.worker.run_once()["status"], "applied")
        self.task = self.brain.snapshot()["tasks"][0]

    def answer(self):
        qid = next(q["id"] for q in self.brain.snapshot()["questions"] if q["task_id"] == self.task["id"] and q["status"] == "pending")
        return self.brain.record_answer([qid], "São duas tarefas distintas. Separe preparar a transferência e registrar o comprovante.", uid())

    def assert_rejected(self, transform, *, after_answer=None):
        answer = self.answer()
        if after_answer:
            after_answer()
        before = self.brain.snapshot()
        response = SemanticWorker(self.brain, FixtureInterpreter(transform)).run_once()
        self.assertEqual(response["status"], "pending")
        self.assertEqual(response["error_code"], "analysis_rejected")
        final = self.brain.snapshot()
        for group in ("tasks", "nodes", "edges", "questions"):
            self.assertEqual(final[group], before[group])
        self.assertTrue(any(s["id"] == answer["answer_id"] and "Separe" in s["text"] for s in final["sources"]))

    def test_scheduled_answer_split_later_merge_and_replay_preserve_all_sources(self):
        company = self.brain.snapshot()["company"]["id"]
        route = Route(company, uid(), 1, "synthetic-account", "synthetic-company-group", "synthetic-sender", "fixture-founder", True)
        repository = BrainRepository(company, self.brain.transact_nora)
        repository.transact(company, lambda state: setattr(state, "route", route))
        clock = [datetime.now(timezone.utc).replace(hour=13, minute=0, second=0, microsecond=0) + timedelta(days=1)]
        sends = []
        class Sender:
            def send(self, route, text):
                sends.append(text)
                return SendReceipt(route.account, route.conversation, f"synthetic-send-{len(sends)}")
        nora = Nora(company, repository, lambda: clock[0], Sender())
        retained = {p: value for p, value in self.brain.store.read().files.items() if p.startswith(("sources/", "records/"))}
        for index, text in enumerate(("São duas tarefas distintas. Separe preparar a transferência e registrar o comprovante.",
                                      "Correção posterior: junte as duas tarefas; registrar o pagamento já inclui salvar o comprovante.")):
            project_questions(self.brain, nora)
            self.assertEqual(nora.tick().outcome, "accepted")
            current_round = repository.transact(company, lambda state: list(state.rounds.values())[-1])
            qid = current_round.items[0].question.id
            inbound = InboundEvent(route.account, route.conversation, route.founder_sender, f"synthetic-reply-{index}", clock[0],
                                   question_label(qid) + ": " + text, reply_to=f"synthetic-send-{len(sends)}")
            saved = nora.receive(inbound)
            self.assertTrue(saved.question_ids)
            self.assertEqual(drain_nora_inbox(self.brain)["queued_answers"], 1)
            response = self.worker.run_once()
            self.assertEqual(response["status"], "applied", response)
            receipt = response["receipt"]
            self.assertIn("map_changes", receipt)
            self.assertEqual(receipt["founder_speaker"], "fixture-founder")
            active = [t for t in self.brain.snapshot()["tasks"] if not t["superseded_by"]]
            self.assertEqual(len(active), 2 if index == 0 else 1)
            # Replay both delivery and interpretation after the committed map update.
            head = self.brain.store.read().head
            replay = nora.receive(inbound)
            self.assertEqual(replay.event, saved.event)
            self.assertEqual(replay.source_hash, saved.source_hash)
            self.assertEqual(replay.status, "queued_for_analysis")
            self.assertEqual(self.brain.store.read().head, head)
            job, context = self.interpreter.calls[-1]
            self.assertEqual(self.brain.apply_analysis(job["id"], self.interpreter.interpret(job, context)), receipt)
            self.assertEqual(self.brain.store.read().head, head)
            clock[0] += timedelta(hours=4)
        final = self.brain.snapshot()
        answers = {s["id"] for s in final["sources"] if s["kind"] == "answer"}
        self.assertEqual(len(answers), 2)
        self.assertTrue(answers | set(self.task["evidence_ids"]) <= set(active[0]["evidence_ids"]))
        self.assertTrue(all(self.brain.store.read().files[p] == value for p, value in retained.items()))
        self.assertEqual(len(sends), 2)
        self.assertTrue(all(t["superseded_by"] for t in final["tasks"] if t["id"] != active[0]["id"]))

    def test_unknown_target_rejected_without_partial_changes(self):
        def transform(output, job, context):
            output["map_changes"][0]["id"] = uid()
            return output
        self.assert_rejected(transform)

    def test_ambiguous_reply_can_be_assigned_then_structurally_interpreted(self):
        candidates = [q["id"] for q in self.brain.snapshot()["questions"] if q["status"] == "pending"]
        answer = self.brain.record_answer([], "Separe preparar a transferência e registrar o comprovante.", uid(), candidate_question_ids=candidates)
        self.assertEqual(self.worker.run_once()["status"], "applied")
        response = self.worker.run_once()
        self.assertEqual(response["status"], "applied", response)
        active = [t for t in self.brain.snapshot()["tasks"] if not t["superseded_by"]]
        self.assertEqual(len(active), 2)
        self.assertTrue(all(answer["answer_id"] in t["evidence_ids"] for t in active))

    def test_missing_edge_version_rejected(self):
        self.assertTrue(self.brain.snapshot()["edges"])
        def transform(output, job, context):
            output["map_changes"][0]["expected_versions"] = [{"id": self.task["id"], "version": self.task["version"]}]
            return output
        self.assert_rejected(transform)

    def test_stale_answer_cannot_restructure_a_later_task_version(self):
        self.assert_rejected(None, after_answer=lambda: self.brain.change_map([{"op": "rename", "id": self.task["id"], "name": "Nome posterior"}], uid(), "Correção posterior ao recebimento."))

    def test_concurrent_edit_after_interpretation_rejects_the_frozen_scope(self):
        def concurrent(output, job, context):
            self.brain.change_map([{"op": "rename", "id": self.task["id"], "name": "Nome concorrente"}], uid(), "Correção concorrente.")
            return output
        self.answer()
        response = SemanticWorker(self.brain, FixtureInterpreter(concurrent)).run_once()
        self.assertEqual(response["status"], "pending")
        final = self.brain.snapshot()["tasks"]
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["name"], "Nome concorrente")
        self.assertEqual(final[0]["superseded_by"], [])

    def test_other_typed_operations_use_the_same_answer_worker(self):
        original = self.task["id"]
        added = uid()
        def edits(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            common = {"evidence": evidence(source), "expected_versions": expected(context, [original])}
            relation_ids = [edge["id"] for edge in context["edges"] if edge.get("derived_field") == "performer" and edge.get("task_id") == original]
            return result([
                {"op": "add", "id": added, "kind": "task", "name": "Conferir saldo", "fields": {}, "evidence": evidence(source), "expected_versions": []},
                {"op": "rename", "id": original, "name": "Preparar pagamento", **common},
                {"op": "reassign", "id": original, "performer": claim(source, "Mira"), **common,
                 "expected_versions": expected(context, [original] + relation_ids)},
                {"op": "correct", "id": original, "fields": {"purpose": claim(source, "Quitar serviço aprovado")}, **common},
                {"op": "connect", "from": original, "to": added, "kind": "handoff", **common},
            ])
        self.answer()
        response = SemanticWorker(self.brain, FixtureInterpreter(edits)).run_once()
        self.assertEqual(response["status"], "applied", response)
        final = self.brain.snapshot()
        task = next(t for t in final["tasks"] if t["id"] == original)
        self.assertEqual(task["name"], "Preparar pagamento")
        self.assertEqual(task["fields"]["performer"]["value"], "Mira")
        self.assertEqual(task["fields"]["purpose"]["value"], "Quitar serviço aprovado")
        edge = next(e for e in final["edges"] if e["from"] == original and e["to"] == added)
        def disconnect(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            return result([{"op": "disconnect", "id": edge["id"], "evidence": evidence(source), "expected_versions": expected(context, [edge["id"]])}])
        self.answer()
        response = SemanticWorker(self.brain, FixtureInterpreter(disconnect)).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertFalse(next(e for e in self.brain.snapshot()["edges"] if e["id"] == edge["id"])["active"])

    def test_authority_is_saved_founder_source_not_worker_identity(self):
        self.assert_rejected(None, after_answer=lambda: self.brain.update_policy({"founders": ["new-founder"]}, uid()))

    def test_partial_allocation_rejected(self):
        def missing(output, job, context):
            for successor in output["map_changes"][0]["successors"]:
                successor["evidence_ids"] = []
            return output
        self.assert_rejected(missing)

    def test_fabricated_intent_quote_rejected(self):
        def fabricated(output, job, context):
            output["map_changes"][0]["evidence"][0]["quote"] = "Texto que o fundador não enviou."
            return output
        self.assert_rejected(fabricated)

    def test_malformed_operation_cannot_enter_core_even_with_fake_interpreter(self):
        def malformed(output, job, context):
            output["map_changes"][0]["execute"] = "arbitrary-code"
            return output
        self.assert_rejected(malformed)

    def test_operation_requires_literal_current_founder_answer(self):
        def wrong_source(output, job, context):
            observed = next(s for s in context["sources"] if s["kind"] == "observation")
            output["map_changes"][0]["evidence"] = evidence(observed)
            return output
        self.assert_rejected(wrong_source)

    def test_model_metadata_cannot_substitute_for_trusted_worker_scope(self):
        answer = self.answer()
        snapshot = self.brain.snapshot()
        job = snapshot["pending_jobs"][0]
        output = self.interpreter.interpret(job, snapshot)
        head = self.brain.store.read().head
        with self.assertRaisesRegex(BrainError, "trusted_map_scope_required"):
            self.brain.apply_analysis(answer["job_id"], output, metadata={"map_scope": snapshot})
        self.assertEqual(self.brain.store.read().head, head)


if __name__ == "__main__":
    unittest.main()
