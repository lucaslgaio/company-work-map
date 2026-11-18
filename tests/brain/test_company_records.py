"""Typed company revisions are not a desktop window or execution witness."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import AccessDenied, Brain, BrainError, FIELDS, LocalStore
from company_pilot.inference import SemanticWorker


def uid():
    return str(uuid4())


def payload(revision="r1", text="Uma ordem aprovada possui entrada, execução e entrega.", operation="upsert"):
    return {"schema_version": 1, "event_id": uid(), "occurred_at": "2026-11-17T12:00:00+00:00", "synthetic": True,
        "source": {"system": "fixture-files", "record_id": "record-1", "url": "https://example.invalid/company/record-1", "revision": revision},
        "operation": operation, "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(), "complete": False},
        "object": {"storage_ref": "private-fixture-snapshot/record-1/" + revision, "sha256": hashlib.sha256(text.encode()).hexdigest(), "media_type": "text/plain", "size_bytes": len(text.encode())}}


class CompanyRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética", synthetic=True)

    def test_configuration_revisions_replay_and_removal_keep_sources(self):
        first = payload()
        with self.assertRaisesRegex(AccessDenied, "watchdog_actor_required"):
            self.brain.ingest_company_record(first)
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        receipt = self.brain.ingest_company_record(first)
        retained = copy.deepcopy(self.brain.store.read().files)
        head = self.brain.store.read().head
        self.assertEqual(self.brain.ingest_company_record(first), receipt)
        self.assertEqual(self.brain.store.read().head, head)
        retry = copy.deepcopy(first)
        retry["event_id"] = uid()
        self.assertEqual(self.brain.ingest_company_record(retry), receipt)
        self.assertEqual(self.brain.store.read().head, head)
        changed = payload(text="Outro conteúdo com a mesma revisão.")
        with self.assertRaisesRegex(BrainError, "revision_changed_bytes"):
            self.brain.ingest_company_record(changed)
        second = self.brain.ingest_company_record(payload("r2", "A entrega também exige uma conferência."))
        self.assertNotEqual(receipt["source_id"], second["source_id"])
        removed = self.brain.ingest_company_record(payload("r2", "", "removed"))
        self.assertIsNone(removed["job_id"])
        final = self.brain.snapshot()
        self.assertEqual(final["company_records"][0]["state"], "removed")
        self.assertEqual(len(final["company_records"][0]["history"]), 3)
        self.assertTrue(all(self.brain.store.read().files[path] == content for path, content in retained.items() if path.startswith(("sources/", "records/", "ledger/"))))
        self.assertEqual(final["sources"][0]["kind"], "company_record")

    def test_file_source_analysis_is_partial_and_cannot_witness_performer(self):
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        receipt = self.brain.ingest_company_record(payload())
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == receipt["source_id"])
        fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
        ref = [{"source_id": source["id"], "quote": source["text"]}]
        fields["action"] = {"value": "Executar e entregar uma ordem", "basis": "observed", "evidence": ref}
        result = {"tasks": [{"task_id": None, "name": "Executar ordem", "fields": fields, "links": []}], "nodes": [], "context_updates": [], "next_question": None,
                  "record_extraction": {"complete": False, "limitations": ["Somente o extrato fornecido foi analisado."]}}
        invalid = copy.deepcopy(result)
        invalid["tasks"][0]["fields"]["performer"] = {"value": "Autor do arquivo", "basis": "observed", "evidence": ref}
        with self.assertRaisesRegex(BrainError, "witness_person_execution"):
            self.brain.apply_analysis(receipt["job_id"], invalid)
        class Interpreter:
            def interpret(self, job, context):
                return copy.deepcopy(result)
        response = SemanticWorker(self.brain, Interpreter()).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertFalse(response["receipt"]["record_extraction"]["complete"])
        task = self.brain.snapshot()["tasks"][0]
        self.assertEqual(task["status"], "provisional")
        self.assertIsNone(task["fields"]["performer"]["value"])
        self.assertTrue(all(q["observation_ids"] == [source["id"]] for q in self.brain.snapshot()["questions"]))
        self.brain.ingest_company_record(payload("r2", "", "removed"))
        self.assertEqual(self.brain.snapshot()["tasks"][0], task)

    def test_pending_human_access_blocks_company_sources_even_without_device_participation(self):
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        self.brain.set_access_policy([{"actor_id": "fixture-founder", "read_scope": "all", "modify_scope": "all", "record_categories": []}], uid(), "synthetic-founder-choice")
        with self.assertRaisesRegex(AccessDenied, "human_access_not_verified"):
            self.brain.ingest_company_record(payload())

    def test_delayed_old_revision_after_new_revision_or_tombstone_is_historic_only(self):
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        old = self.brain.ingest_company_record(payload())
        newer = self.brain.ingest_company_record(payload("r2", "A revisão atual descreve outro processo."))
        invalid_old_decision = {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
            "record_extraction": {"complete": False, "limitations": ["Histórico."]}}
        first = self.brain.apply_analysis(old["job_id"], invalid_old_decision)
        self.assertEqual(first["state"], "superseded_source_revision")
        self.assertEqual(first["current_source_id"], newer["source_id"])
        self.assertEqual(self.brain.snapshot()["tasks"], [])
        removed = self.brain.ingest_company_record(payload("r2", "", "removed"))
        second = self.brain.apply_analysis(newer["job_id"], invalid_old_decision)
        self.assertEqual(second["source_scope"], "historic")
        self.assertEqual(second["current_source_id"], removed["source_id"])
        self.assertEqual(self.brain.snapshot()["tasks"], [])
        self.assertEqual(self.brain.snapshot()["pending_jobs"], [])
        self.assertTrue(any(s["id"] == old["source_id"] for s in self.brain.snapshot()["sources"]))

    def test_worker_race_and_delayed_tombstone_never_publish_old_context(self):
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        self.brain.change_map([{"op": "add", "kind": "task", "name": "Atividade sustentada por outra fonte", "fields": {"action": "Conferir um registro independente"}}], uid(), "Atividade sintética independente do arquivo atualizado.")
        unrelated = copy.deepcopy(self.brain.snapshot()["tasks"])
        old = self.brain.ingest_company_record(payload())
        calls = []
        class Delayed:
            def interpret(inner, job, context):
                calls.append(job["id"])
                self.brain.ingest_company_record(payload("r2", "Agora a fonte descreve uma revisão posterior."))
                source = next(s for s in context["sources"] if s["id"] == old["source_id"])
                return {"tasks": [], "nodes": [], "next_question": None,
                    "context_updates": [{"topic": "stages", "value": "Regra histórica", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}], "mode": "add", "replaces": []}],
                    "record_extraction": {"complete": False, "limitations": ["Extrato antigo."]}}
        response = SemanticWorker(self.brain, Delayed()).run_once()
        self.assertEqual(response["receipt"]["source_scope"], "historic")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.brain.snapshot()["context"], {})
        self.assertEqual(self.brain.snapshot()["tasks"], unrelated)
        self.brain.ingest_company_record(payload("r2", "", "removed"))
        class Never:
            def interpret(inner, job, context):
                self.fail("Historic source must be skipped before interpretation")
        response = SemanticWorker(self.brain, Never()).run_once()
        self.assertEqual(response["receipt"]["state"], "superseded_source_revision")
        self.assertEqual(self.brain.snapshot()["pending_jobs"], [])
        self.assertEqual(self.brain.snapshot()["context"], {})
        self.assertEqual(self.brain.snapshot()["tasks"], unrelated)
