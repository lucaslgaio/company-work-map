"""Supplied record extracts are data; these tests never fetch or read objects."""

import copy
import json
import unittest

from company_pilot.inference import ClaudeInterpreter, InterpreterError, SemanticWorker
from company_pilot.inference.schema import ANALYSIS_SCHEMA, schema_for_job, validate_analysis
from tests.inference.test_claude import FakeRunner, analysis, stream, task
from tests.inference.test_worker import Brain


def record(source_id="record-source-2", logical_id="catalog:record-1", *, complete=True, revision="2"):
    return {"id": source_id, "kind": "company_record", "logical_record_id": logical_id,
            "source_record": {"system": "fixture_catalog", "record_id": "record-1",
                              "url": "https://company.invalid/records/1", "revision": revision},
            "extract_complete": complete,
            "object": {"storage_ref": "opaque-fixture-reference", "sha256": "a" * 64,
                       "media_type": "application/pdf", "size_bytes": 120},
            "record_operation": "upsert", "text": "Registro R1: valor 120; situação pendente."}


def extracted(tasks=None, *, complete=True, limitations=None):
    return {**analysis(tasks=[] if tasks is None else tasks),
            "record_extraction": {"complete": complete, "limitations": limitations or []}}


class RecordSchemaTests(unittest.TestCase):
    def test_record_completeness_is_optional_but_typed(self):
        validate_analysis(analysis())
        validate_analysis(extracted(complete=False, limitations=["Trecho parcial."]))
        for invalid in ({"complete": 1, "limitations": []}, {"complete": True},
                        {"complete": False, "limitations": [None]}, {"complete": True, "limitations": [], "object_read": True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_analysis({**analysis(), "record_extraction": invalid})

    def test_record_cli_schema_bound_does_not_narrow_legacy_schema(self):
        self.assertEqual(schema_for_job("company_record")["properties"]["tasks"]["maxItems"], 50)
        self.assertNotIn("maxItems", ANALYSIS_SCHEMA["properties"]["tasks"])
        validate_analysis(extracted([task() for _ in range(50)]), job_kind="company_record")
        with self.assertRaises(ValueError):
            validate_analysis(extracted([task() for _ in range(51)]), job_kind="company_record")


class RecordAdapterTests(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "record-job-2", "kind": "company_record", "source_ids": ["record-source-2"], "task_ids": []}
        self.context = {"company": {}, "context": {}, "tasks": [{"id": "existing-1", "version": 1}],
                        "questions": [], "sources": [record()]}

    def interpret(self, result, context=None):
        runner = FakeRunner(output=stream(result))
        interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
        return interpreter.interpret(self.job, self.context if context is None else context), runner

    def invalid(self, result, context=None):
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            self.interpret(result, context=context)

    def test_up_to_fifty_granular_tasks_or_no_tasks_can_be_extracted(self):
        for count in (0, 2, 50):
            result = extracted([{**task(), "name": f"Tarefa granular {index}"} for index in range(count)])
            self.assertEqual(self.interpret(result)[0], result)
        self.invalid(extracted([task() for _ in range(51)]))

    def test_existing_ids_are_only_supplied_candidates_new_ids_remain_null(self):
        result = extracted([task("existing-1"), task(), task()])
        self.assertEqual(self.interpret(result)[0], result)
        self.invalid(extracted([task("not-supplied")]))
        self.invalid(extracted([task("existing-1"), task("existing-1")]))

    def test_extraction_status_is_required_for_company_record(self):
        self.invalid(analysis(tasks=[]))

    def test_partial_input_requires_false_and_a_nonempty_limit(self):
        context = copy.deepcopy(self.context)
        context["sources"][0]["extract_complete"] = False
        self.invalid(extracted(complete=True), context=context)
        self.invalid(extracted(complete=False), context=context)
        self.invalid(extracted(complete=False, limitations=[" "]), context=context)
        result = extracted([task()], complete=False, limitations=["Somente o trecho inicial foi fornecido."])
        self.assertEqual(self.interpret(result, context=context)[0], result)

    def test_fixture_content_complete_fallback_cannot_claim_full_content(self):
        context = copy.deepcopy(self.context)
        del context["sources"][0]["extract_complete"]
        context["sources"][0]["content"] = {"complete": False}
        self.invalid(extracted(complete=True), context=context)
        result = extracted(complete=False, limitations=["Conteúdo parcial da fixture."])
        self.assertEqual(self.interpret(result, context=context)[0], result)

    def test_prompt_preserves_actual_metadata_and_separates_recorded_from_executed(self):
        result = extracted([task()])
        _, runner = self.interpret(result)
        payload = json.loads(runner.calls[-1][1]["input"])
        self.assertEqual(payload["base_context"]["sources"][0], self.context["sources"][0])
        for phrase in ("estado de negócio registrado", "não que uma pessoa executou ou aprovou",
                       "autor do arquivo", "basis reported", "localizador opaco", "Não abra URL",
                       "não afirme leitura completa de binário", "extract_complete false", "por índice"):
            self.assertIn(phrase, payload["request"])
        command = runner.calls[-1][0]
        cli_schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertEqual(cli_schema["properties"]["tasks"]["maxItems"], 50)

    def test_tombstone_is_rejected_before_any_cli_call(self):
        context = copy.deepcopy(self.context)
        context["sources"][0]["record_operation"] = "removed"
        runner = FakeRunner(output=stream(extracted()))
        interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
        with self.assertRaisesRegex(InterpreterError, "^invalid_job$"):
            interpreter.interpret(self.job, context)
        self.assertEqual(runner.calls, [])

    def test_record_job_requires_its_supplied_immutable_record_source(self):
        context = copy.deepcopy(self.context)
        context["sources"][0]["kind"] = "observation"
        with self.assertRaisesRegex(InterpreterError, "^invalid_context$"):
            self.interpret(extracted(), context=context)


class RecordBrain(Brain):
    def __init__(self):
        super().__init__()
        self.jobs[0].update(kind="company_record", source_ids=["record-source-2"], task_ids=[])
        self.context = {"company": {}, "context": {}, "questions": [], "sources": [
            record(), record("record-source-1", revision="1"),
            {"id": "other-source", "kind": "observation", "text": "Outro trabalho."}],
            "tasks": [{"id": f"task-{index}", "version": 1, "fields": {}, "evidence_ids": ["other-source"],
                       "superseded_by": []} for index in range(30)]}
        self.context["tasks"][0]["evidence_ids"] = ["record-source-1"]
        self.context["tasks"][1].update(evidence_ids=[], fields={"action": {
            "value": "Estado anterior", "basis": "observed",
            "evidence": [{"source_id": "record-source-1", "quote": "Registro R1"}]}})
        self.context["tasks"].append({"id": "superseded", "version": 2,
                                      "evidence_ids": ["record-source-1"], "superseded_by": ["task-0"]})
        self.apply_kwargs = None

    def apply_analysis(self, job_id, result, **kwargs):
        self.apply_kwargs = kwargs
        return super().apply_analysis(job_id, result)


class RecordWorkerTests(unittest.TestCase):
    def test_worker_prefers_current_or_prior_logical_record_evidence_with_twenty_task_bound(self):
        brain = RecordBrain()
        result = extracted([task("task-0"), task()])
        runner = FakeRunner(output=stream(result))
        interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
        self.assertEqual(SemanticWorker(brain, interpreter).run_once()["status"], "applied")
        payload = json.loads(runner.calls[-1][1]["input"])
        candidates = payload["base_context"]["tasks"]
        self.assertEqual(len(candidates), 20)
        self.assertEqual([candidate["id"] for candidate in candidates[:2]], ["task-0", "task-1"])
        self.assertNotIn("superseded", {candidate["id"] for candidate in candidates})
        self.assertEqual({source["id"] for source in payload["base_context"]["sources"]},
                         {"record-source-2", "record-source-1", "other-source"})
        self.assertEqual({ref["id"] for ref in brain.apply_kwargs["map_scope"]["tasks"]}, {task["id"] for task in candidates})
        self.assertEqual(set(brain.apply_kwargs["map_scope"]["source_ids"]), {source["id"] for source in payload["base_context"]["sources"]})
        self.assertEqual(brain.apply_kwargs["metadata"]["prompt_version"], "company-work-evidence-13")

    def test_without_record_match_worker_keeps_bounded_active_candidates(self):
        brain = RecordBrain()
        brain.context["sources"][0]["logical_record_id"] = "another-logical-record"
        runner = FakeRunner(output=stream(extracted()))
        self.assertEqual(SemanticWorker(brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()["status"], "applied")
        payload = json.loads(runner.calls[-1][1]["input"])
        self.assertEqual([candidate["id"] for candidate in payload["base_context"]["tasks"]],
                         [f"task-{index}" for index in range(10, 30)])


class HistoricRecordBrain(RecordBrain):
    def apply_analysis(self, job_id, result, **kwargs):
        primary = self.context["sources"][0]
        entry = self.context["company_records"][0]
        if entry["current_source_id"] != primary["id"]:
            super().apply_analysis(job_id, result, **kwargs)
            return {"state": "superseded_source_revision", "source_scope": "historic",
                    "historic_source_id": primary["id"], "current_source_id": entry["current_source_id"],
                    "tasks": []}
        return super().apply_analysis(job_id, result, **kwargs)


class NeverInterpreter:
    def __init__(self):
        self.calls = 0

    def interpret(self, *_args):
        self.calls += 1
        raise AssertionError("Historic records must not call the model")

    @property
    def last_metadata(self):
        raise AssertionError("Historic records must not reuse model metadata")


class HistoricRecordWorkerTests(unittest.TestCase):
    def historic(self, operation):
        brain = HistoricRecordBrain()
        newer = record("record-source-3", revision="3")
        newer["record_operation"] = operation
        brain.context["sources"].append(newer)
        brain.context["company_records"] = [{"id": "catalog:record-1", "current_source_id": "record-source-3",
                                             "state": "removed" if operation == "removed" else "current"}]
        original_tasks = copy.deepcopy(brain.context["tasks"])
        original_sources = copy.deepcopy(brain.context["sources"])
        interpreter = NeverInterpreter()
        result = SemanticWorker(brain, interpreter).run_once()
        self.assertEqual(interpreter.calls, 0)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["receipt"], {
            "state": "superseded_source_revision", "source_scope": "historic",
            "historic_source_id": "record-source-2", "current_source_id": "record-source-3", "tasks": [],
        })
        self.assertEqual(brain.applied[0][1], {
            "tasks": [], "context_updates": [], "nodes": [], "next_question": None,
            "record_extraction": {"complete": False, "limitations": ["Fonte histórica; existe revisão posterior."]},
        })
        self.assertEqual(brain.apply_kwargs, {})
        self.assertEqual(brain.jobs, [])
        self.assertEqual(brain.context["tasks"], original_tasks)
        self.assertEqual(brain.context["sources"], original_sources)

    def test_newer_revision_closes_delayed_old_job_without_interpreter(self):
        self.historic("upsert")

    def test_newer_tombstone_closes_delayed_old_job_without_interpreter(self):
        self.historic("removed")

    def test_current_catalog_revision_still_uses_interpreter(self):
        brain = HistoricRecordBrain()
        brain.context["company_records"] = [{"id": "catalog:record-1", "current_source_id": "record-source-2", "state": "current"}]
        runner = FakeRunner(output=stream(extracted([task("task-0")])))
        result = SemanticWorker(brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(brain.applied[0][1]["tasks"][0]["task_id"], "task-0")
        self.assertNotIn("source_scope", result["receipt"])


if __name__ == "__main__":
    unittest.main()
