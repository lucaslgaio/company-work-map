"""Round4 F9/F14 report-derived actual-caller regressions, not raw-run replay."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.inference import ClaudeInterpreter, SemanticWorker
from tests.inference.test_claude import FakeRunner, stream


ARTIFACT_ROOT = Path(tempfile.mkdtemp(prefix="a44-inference-"))


def uid():
    return str(uuid4())


def empty():
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None}


def supported(source, value):
    return {"value": value, "basis": "observed", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}


def proposed(source, name, values, task_id=None):
    fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
    fields.update({field: supported(source, value) for field, value in values.items()})
    return {"task_id": task_id, "name": name, "fields": fields, "links": []}


class Round4RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética — round4", synthetic=True)

    def observe(self, text, record_ref, when, thread_id=None):
        source_meta = {"kind": "active_window", "app": "Synthetic Work Viewer", "record_ref": record_ref}
        if thread_id is not None:
            source_meta["thread_id"] = thread_id
        raw = self.brain.ingest_observation({"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": when, "policy_version": self.brain.snapshot()["company"]["policy_version"],
            "source": source_meta,
            "classification": {"label": "company", "reason": "fixture sintética autorizada"},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True})
        return raw, next(source for source in self.brain.snapshot()["sources"] if source["id"] == raw["observation_id"])

    def run_model(self, decision):
        runner = FakeRunner(output=stream(decision))
        response = SemanticWorker(self.brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()
        return response, json.loads(runner.calls[-1][1]["input"])

    def test_f9_f14_recent_same_record_and_older_supported_handoff_survive_uuid_order(self):
        older_report = "00000000-0000-4000-8000-000000000001"
        older_consumer = "00000000-0000-4000-8000-000000000002"
        self.brain.change_map([
            {"op": "add", "kind": "task", "id": older_report, "name": "Juntar fotos do relatório", "fields": {}},
            {"op": "add", "kind": "task", "id": older_consumer, "name": "Cotar componentes", "fields": {}},
        ], uid(), "Tarefas sintéticas conhecidas para revisão de documentos.")
        _, source = self.observe("No relatório semanal: fotos selecionadas para redigir o relatório.", "REPORT-THREAD", "2026-11-23T12:00:00+00:00")
        response, _ = self.run_model({**empty(), "tasks": [proposed(source, "Juntar fotos do relatório", {"action": "Juntar fotos", "result": "Fotos selecionadas"}, older_report)]})
        self.assertEqual(response["status"], "applied", response)
        _, source = self.observe("Cotar componentes recebe como entrada a lista de materiais revisada.", "PURCHASE-RECORD", "2026-11-23T13:00:00+00:00")
        response, _ = self.run_model({**empty(), "tasks": [proposed(source, "Cotar componentes", {"action": "Cotar componentes", "input": "Lista de materiais revisada"}, older_consumer)]})
        self.assertEqual(response["status"], "applied", response)
        noise = [{"op": "add", "kind": "task", "id": f"ffffffff-ffff-4fff-8fff-{index:012d}",
                  "name": f"Revisar registro administrativo {index}", "fields": {"action": f"Revisar registro {index}",
                  "performer": "Ana", "placement": "Projeto Alfa"}} for index in range(20)]
        self.brain.change_map(noise, uid(), "Vinte tarefas sintéticas independentes; compartilhar pessoa ou projeto não prova vínculo.")
        _, source = self.observe("No relatório semanal: redigir o rascunho com as fotos selecionadas.", "REPORT-THREAD", "2026-11-26T12:00:00+00:00")
        response, _ = self.run_model({**empty(), "tasks": [proposed(source, "Redigir relatório semanal", {"action": "Redigir relatório", "result": "Rascunho do relatório"})]})
        self.assertEqual(response["status"], "applied", response)
        recent_id = response["receipt"]["tasks"][0]["task_id"]
        before = self.brain.snapshot()
        self.assertNotIn(recent_id, {task["id"] for task in before["tasks"][-20:]})
        raw, current = self.observe("Mesmo relatório: continuar o rascunho. A lista de materiais revisada será entrada para Cotar componentes.",
                                    "REPORT-THREAD", "2026-11-27T12:00:00+00:00")
        recent = next(task for task in before["tasks"] if task["id"] == recent_id)
        decision = {**empty(), "tasks": [{"task_id": recent_id, "name": recent["name"],
                                          "fields": copy.deepcopy(recent["fields"]), "links": []}]}
        response, payload = self.run_model(decision)
        artifact = ARTIFACT_ROOT / "f9-f14"
        artifact.mkdir()
        for name, value in (("before", before), ("model-request", payload), ("worker-response", response), ("after", self.brain.snapshot())):
            (artifact / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        print("ARTIFACTS: " + str(artifact))
        supplied = {task["id"] for task in payload["base_context"]["tasks"]}
        self.assertTrue({recent_id, older_report, older_consumer} <= supplied, supplied)
        self.assertLessEqual(len(supplied), 20)
        self.assertEqual(response["status"], "applied", response)
        scope = payload["base_context"]["map_scope"]
        expected = {task["id"]: task["version"] for task in before["tasks"] if task["id"] in supplied}
        self.assertEqual({ref["id"]: ref["version"] for ref in scope["tasks"]}, expected)
        after = self.brain.snapshot()
        self.assertEqual(len(after["tasks"]), len(before["tasks"]))
        task_ids = {task["id"] for task in after["tasks"]}
        self.assertFalse(any(edge["from"] in task_ids and edge["to"] in task_ids for edge in after["edges"]))
        self.assertIn(current["id"], scope["source_ids"])
        self.assertEqual(raw["job_id"], response["job_id"])
        files = self.brain.store.read().files
        for task_id in (recent_id, older_report, older_consumer):
            (artifact / (task_id + ".md")).write_text(files[f"views/tasks/{task_id}.md"])

    def test_f14_explicit_same_thread_retains_older_task_without_name_only_candidates(self):
        related_id = "00000000-0000-4000-8000-000000000010"
        name_only_id = "00000000-0000-4000-8000-000000000011"
        self.brain.change_map([
            {"op": "add", "kind": "task", "id": related_id, "name": "Preparar relatório", "fields": {}},
            {"op": "add", "kind": "task", "id": name_only_id, "name": "Outro trabalho de Ana", "fields": {"input": "Ana", "performer": "Ana"}},
        ], uid(), "Fixtures sintéticas; mesmo nome não afirma fluxo entre tarefas.")
        _, source = self.observe("Preparar o relatório da conversa empresarial.", "EMAIL-OLD", "2026-11-23T12:00:00+00:00", "thread-7")
        response, _ = self.run_model({**empty(), "tasks": [proposed(source, "Preparar relatório", {"action": "Preparar relatório"}, related_id)]})
        self.assertEqual(response["status"], "applied", response)
        self.brain.change_map([{"op": "add", "kind": "task", "id": f"ffffffff-ffff-4fff-8fff-{index:012d}",
                               "name": f"Registro independente {index}", "fields": {"action": f"Revisar registro {index}"}}
                              for index in range(20)], uid(), "Fixtures independentes mais recentes.")
        _, current = self.observe("Ana continua a conversa empresarial.", "EMAIL-NEW", "2026-11-27T12:00:00+00:00", "thread-7")
        existing = next(task for task in self.brain.snapshot()["tasks"] if task["id"] == related_id)
        response, payload = self.run_model({**empty(), "tasks": [{"task_id": related_id, "name": existing["name"],
                                          "fields": copy.deepcopy(existing["fields"]), "links": []}]})
        supplied = {task["id"] for task in payload["base_context"]["tasks"]}
        self.assertIn(related_id, supplied)
        self.assertNotIn(name_only_id, supplied)
        self.assertEqual(response["status"], "applied", response)
        self.assertLessEqual(len(supplied), 20)
        self.assertIn(current["id"], payload["base_context"]["map_scope"]["source_ids"])

    def test_f13_message_consumes_frozen_target_and_question_candidates_exactly(self):
        report_id = "00000000-0000-4000-8000-000000000020"
        payment_id = "00000000-0000-4000-8000-000000000021"
        self.brain.change_map([
            {"op": "add", "kind": "task", "id": report_id, "name": "Redigir relatório semanal", "fields": {"action": "Redigir relatório"}},
            {"op": "add", "kind": "task", "id": payment_id, "name": "Controlar pagamentos e saldos", "fields": {"action": "Conferir pagamentos"}},
        ], uid(), "Fixtures independentes de relatório e pagamentos.")
        snapshot = self.brain.snapshot()
        report = next(task for task in snapshot["tasks"] if task["id"] == report_id)
        question = next(q for q in snapshot["questions"] if q["task_id"] == report_id and q["missing_links"] == ["purpose"])
        text = "O relatório semanal consolida fotos para informar o cliente."
        raw = self.brain.record_message(text, uid(), question_ids=[question["id"]], candidate_question_ids=[question["id"]],
                                        candidate_task_versions=[{"id": report_id, "version": report["version"]}])
        source = next(item for item in self.brain.snapshot()["sources"] if item["id"] == raw["answer_id"])
        fields = copy.deepcopy(report["fields"])
        fields["purpose"] = {**supported(source, "Consolidar fotos para informar o cliente"), "basis": "reported"}
        decision = {**empty(), "tasks": [{"task_id": report_id, "name": report["name"], "fields": fields, "links": []}],
                    "message_intent": {"kind": "answer", "task_ids": [report_id], "question_ids": [question["id"]],
                                       "delegate_to": None, "disposition": None, "evidence": supported(source, "Resposta")["evidence"]}}
        response, payload = self.run_model(decision)
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual([task["id"] for task in payload["base_context"]["tasks"]], [report_id])
        self.assertEqual([q["id"] for q in payload["base_context"]["questions"]], [question["id"]])
        self.assertEqual(payload["job"]["candidate_task_versions"], raw["candidate_task_versions"])
        self.assertNotIn(payment_id, {task["id"] for task in payload["base_context"]["tasks"]})


if __name__ == "__main__":
    unittest.main()
