"""A record author is not an execution witness, across every task-write path.

LocalStore and SemanticWorker are real. Only interpretation is a synthetic
decision; no model, connector, capture, account or network operation runs here.
"""

import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, FIELDS, LocalStore
from company_pilot.inference import SemanticWorker


ERROR = "record_cannot_witness_person_execution"


def uid():
    return str(uuid4())


def empty():
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None}


def claim(source, value, basis="observed"):
    return {"value": value, "basis": basis,
            "evidence": [{"source_id": source["id"], "quote": source["text"]}]}


def record_payload(text):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {"schema_version": 1, "event_id": uid(), "occurred_at": "2026-11-17T12:00:00+00:00",
            "synthetic": True, "source": {"system": "a37-fixture", "record_id": "document-1",
            "url": "https://example.invalid/a37/document-1", "revision": "r1"}, "operation": "upsert",
            "content": {"text": text, "sha256": digest, "complete": False},
            "object": {"storage_ref": "a37-fixture/document-1/r1", "sha256": digest,
                       "media_type": "text/plain", "size_bytes": len(text.encode())}}


class Decision:
    def __init__(self, build):
        self.build = build
        self.calls = []

    def interpret(self, job, context):
        result = self.build(job, context)
        self.calls.append((copy.deepcopy(job), copy.deepcopy(context), copy.deepcopy(result)))
        return result


class ProvenanceFixture(unittest.TestCase):
    record_text = "Documento criado por Ana"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética A37", synthetic=True)
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        raw = self.brain.ingest_company_record(record_payload(self.record_text))
        self.file = self.source(raw["source_id"])
        fields = {name: {"value": None, "basis": "inferred", "evidence": []} for name in FIELDS}
        fields["action"] = claim(self.file, "Manter o registro do documento")
        initial = {**empty(), "tasks": [{"task_id": None, "name": "Registro do documento",
                                        "fields": fields, "links": []}],
                   "record_extraction": {"complete": False, "limitations": ["Somente o extrato fornecido."]}}
        result = SemanticWorker(self.brain, Decision(lambda _job, _context: copy.deepcopy(initial))).run_once()
        self.assertEqual(result["status"], "applied", result)
        self.assertIsNone(self.current_task()["fields"]["performer"]["value"])

    def source(self, source_id):
        return next(source for source in self.brain.snapshot()["sources"] if source["id"] == source_id)

    def current_task(self):
        return self.brain.snapshot()["tasks"][0]

    def queue(self, text, legacy=False):
        task = self.current_task()
        question_id = next(question["id"] for question in self.brain.snapshot()["questions"]
                           if question["task_id"] == task["id"] and "performer" in question["missing_fields"])
        if legacy:
            return self.brain.record_answer([question_id], text, uid())
        return self.brain.record_message(text, uid(), question_ids=[question_id],
                                        candidate_question_ids=[question_id],
                                        candidate_task_versions=[{"id": task["id"], "version": task["version"]}])

    def regular(self, raw, performer, intent_kind="answer", legacy=False):
        task = self.current_task()
        fields = copy.deepcopy(task["fields"])
        fields["performer"] = performer
        result = {**empty(), "tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}]}
        if not legacy:
            source = self.source(raw["answer_id"])
            result["message_intent"] = {"kind": intent_kind, "task_ids": [task["id"]],
                "question_ids": source["question_ids"], "delegate_to": None, "disposition": None,
                "evidence": claim(source, intent_kind, "reported")["evidence"]}
        return result

    def assert_rejected_atomically(self, raw, interpreter):
        before = copy.deepcopy(self.brain.snapshot())
        retained = copy.deepcopy(self.brain.store.read().files)
        response = SemanticWorker(self.brain, interpreter).run_once()
        self.assertEqual(response["status"], "pending", response)
        self.assertEqual(response["error_code"], "analysis_rejected")
        after = self.brain.snapshot()
        for group in ("tasks", "nodes", "edges", "questions", "context", "context_claims",
                      "recognition_markers", "company_records", "sources"):
            self.assertEqual(after[group], before[group], group)
        files = self.brain.store.read().files
        immutable = ("sources/", "records/", "ledger/", "attachments/")
        for path, content in retained.items():
            if path.startswith(immutable):
                self.assertEqual(files[path], content, path)
        for prefix in ("sources/", "records/"):
            self.assertEqual({path for path in files if path.startswith(prefix)},
                             {path for path in retained if path.startswith(prefix)})
        self.assertEqual(self.source(raw["answer_id"]), next(source for source in before["sources"]
                                                             if source["id"] == raw["answer_id"]))
        pending = next(job for job in after["pending_jobs"] if job["id"] == raw["job_id"])
        self.assertEqual(pending["status"], "pending")
        # Worker diagnostics are deliberately generic. The actual core must
        # reject this exact invariant, rather than a malformed scope or quote.
        job, context, result = interpreter.calls[-1]
        kwargs = {"map_scope": context["map_scope"]} if job["kind"] == "message" or result.get("map_changes") else {}
        frozen = self.brain.store.read()
        with self.assertRaisesRegex(BrainError, "^" + ERROR + "$"):
            self.brain.apply_analysis(job["id"], result, **kwargs)
        self.assertEqual(self.brain.store.read(), frozen)


class A37ProvenanceRejectionTests(ProvenanceFixture):
    def test_ordinary_message_cannot_promote_file_author_to_observed_performer(self):
        raw = self.queue("A executora é Ana, segundo o documento.")
        decision = self.regular(raw, claim(self.file, "Ana"))
        self.assert_rejected_atomically(raw, Decision(lambda _job, _context: copy.deepcopy(decision)))

    def test_legacy_answer_cannot_promote_file_author_to_observed_performer(self):
        raw = self.queue("A executora é Ana, segundo o documento.", legacy=True)
        decision = self.regular(raw, claim(self.file, "Ana"), legacy=True)
        self.assert_rejected_atomically(raw, Decision(lambda _job, _context: copy.deepcopy(decision)))

    def structural_rejection(self, operation_kind):
        instructions = {
            "add": "Acrescente a tarefa 'Nova tarefa que não pode persistir', com Ana como executora conforme o documento.",
            "reassign": "Reatribua a execução da tarefa a Ana conforme o documento.",
            "correct": "Corrija o campo de executora da tarefa para Ana conforme o documento.",
            "split": "Divida a tarefa em 'Primeira parte sem inferir pessoa' e 'Segunda parte com pessoa indevida'; a executora da segunda é Ana conforme o documento.",
        }
        raw = self.queue("Correção explícita: renomeie a tarefa para 'Esta renomeação deve ser revertida'. "
                         + instructions[operation_kind])
        source = self.source(raw["answer_id"])
        task = self.current_task()
        bad_performer = claim(self.file, "Ana")

        def build(_job, context):
            versions = [{"id": task["id"], "version": task["version"]}]
            evidence = claim(source, operation_kind, "reported")["evidence"]
            prelude = {"op": "rename", "id": task["id"], "name": "Esta renomeação deve ser revertida",
                       "evidence": evidence, "expected_versions": versions}
            operation = {"op": operation_kind, "evidence": evidence, "expected_versions": versions}
            if operation_kind == "add":
                operation.update(kind="task", id=None, name="Nova tarefa que não pode persistir",
                                 fields={"performer": bad_performer}, expected_versions=[])
            elif operation_kind == "reassign":
                operation.update(id=task["id"], performer=bad_performer)
            elif operation_kind == "correct":
                operation.update(id=task["id"], fields={"performer": bad_performer})
            else:
                incident = [edge for edge in context["edges"] if task["id"] in (edge["from"], edge["to"])]
                operation.update(id=task["id"], expected_versions=versions + [
                    {"id": edge["id"], "version": edge["version"]} for edge in incident], successors=[
                        {"id": None, "name": "Primeira parte sem inferir pessoa", "fields": {}, "evidence_ids": task["evidence_ids"]},
                        {"id": None, "name": "Segunda parte com pessoa indevida", "fields": {"performer": bad_performer},
                         "evidence_ids": task["evidence_ids"]},
                    ])
            return {**empty(), "map_changes": [prelude, operation], "message_intent": {
                "kind": "correction", "task_ids": [task["id"]], "question_ids": source["question_ids"],
                "delegate_to": None, "disposition": None, "evidence": evidence}}

        self.assert_rejected_atomically(raw, Decision(build))

    def test_structural_add_rejects_observed_file_only_performer_atomically(self):
        self.structural_rejection("add")

    def test_structural_reassign_rejects_observed_file_only_performer_atomically(self):
        self.structural_rejection("reassign")

    def test_structural_correct_rejects_observed_file_only_performer_atomically(self):
        self.structural_rejection("correct")

    def test_structural_split_rejects_observed_file_only_performer_atomically(self):
        self.structural_rejection("split")


class A37ProvenanceAcceptanceTests(ProvenanceFixture):
    record_text = "O registro informa que Ana é responsável por conferir documentos."

    def test_explicit_file_role_remains_accepted_as_reported_in_ordinary_message(self):
        raw = self.queue("Segundo o registro, Ana é responsável por conferir documentos.")
        decision = self.regular(raw, claim(self.file, "Ana", "reported"))
        response = SemanticWorker(self.brain, Decision(lambda _job, _context: copy.deepcopy(decision))).run_once()
        self.assertEqual(response["status"], "applied", response)
        performer = self.current_task()["fields"]["performer"]
        self.assertEqual(performer, claim(self.file, "Ana", "reported"))
        self.assertIn(self.file["id"], self.current_task()["evidence_ids"])
        self.assertTrue(any(source["id"] == raw["answer_id"] for source in self.brain.snapshot()["sources"]))

    def test_actual_observation_source_remains_accepted_as_observed_performer(self):
        text = "Ana abriu o registro do documento e conferiu seus dados."
        payload = {"schema_version": 1, "event_id": uid(), "device_id": uid(),
                   "occurred_at": "2026-11-17T12:30:00+00:00",
                   "policy_version": self.brain.snapshot()["company"]["policy_version"],
                   "source": {"kind": "active_window", "app": "Synthetic Record Viewer"},
                   "classification": {"label": "company", "reason": "registro sintético explicitamente autorizado"},
                   "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}
        raw = self.brain.ingest_observation(payload)
        witnessed = self.source(raw["observation_id"])
        task = self.current_task()
        fields = copy.deepcopy(task["fields"])
        fields["performer"] = claim(witnessed, "Ana")
        decision = {**empty(), "tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}]}
        response = SemanticWorker(self.brain, Decision(lambda _job, _context: copy.deepcopy(decision))).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual(self.current_task()["fields"]["performer"], claim(witnessed, "Ana"))
        self.assertIn(witnessed["id"], self.current_task()["evidence_ids"])


if __name__ == "__main__":
    unittest.main()
