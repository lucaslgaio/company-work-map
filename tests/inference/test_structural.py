"""Typed founder-intent boundary; model and Brain are deliberately simulated."""

import copy
import json
from pathlib import Path
import unittest

from company_pilot.inference import ClaudeInterpreter, InterpreterError, SemanticWorker
from company_pilot.inference.schema import ANALYSIS_SCHEMA, FIELD_NAMES, validate_analysis
from tests.inference.test_claude import FakeRunner, analysis, stream, task
from tests.inference.test_worker import Brain


def claim(value="Ana"):
    return {"value": value, "basis": "reported", "evidence": [
        {"source_id": "answer-1", "quote": "Divida a tarefa e atribua a Ana."}]}


def operation(op, **fields):
    return {"op": op, **fields, "evidence": claim()["evidence"],
            "expected_versions": [{"id": "task-1", "version": 1}]}


def structural(*changes):
    return {**analysis(tasks=[]), "map_changes": list(changes)}


class StructuralSchemaTests(unittest.TestCase):
    def test_all_eight_typed_variants_validate(self):
        successor = {"id": None, "name": "Parte A", "fields": {}, "evidence_ids": ["source-1"]}
        changes = [
            operation("add", kind="task", id=None, name="Nova tarefa", fields={}),
            operation("rename", id="task-1", name="Novo nome"),
            operation("reassign", id="task-1", performer=claim()),
            operation("correct", id="task-1", fields={"purpose": claim("Finalidade corrigida")}),
            operation("split", id="task-1", successors=[successor, copy.deepcopy(successor)]),
            operation("merge", ids=["task-1", "task-2"], successors=[successor]),
            operation("connect", **{"from": "task-1", "to": "person-1", "kind": "performed_by"}),
            operation("disconnect", id="edge-1"),
        ]
        self.assertEqual(validate_analysis(structural(*changes))["map_changes"], changes)

    def test_organization_add_and_all_partial_fields_are_accepted(self):
        validate_analysis(structural(operation("add", kind="organization", id=None,
                                               name="Organização", fields={})))
        for name in FIELD_NAMES:
            with self.subTest(field=name):
                validate_analysis(structural(operation("correct", id="task-1", fields={name: claim()})))

    def test_optional_empty_map_changes_preserves_old_schema(self):
        validate_analysis(analysis())
        validate_analysis({**analysis(), "map_changes": []})

    def test_unknown_fields_and_operation_keys_are_rejected(self):
        for change in (operation("correct", id="task-1", fields={"free_text_gap": claim()}),
                       operation("rename", id="task-1", name="Nome", arbitrary=True),
                       operation("delete", id="task-1")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_analysis(structural(change))

    def test_versions_require_positive_integers_excluding_booleans(self):
        for version in (True, False, 0, -1, 1.0, "1", None):
            change = operation("rename", id="task-1", name="Nome")
            change["expected_versions"][0]["version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_analysis(structural(change))

    def test_each_operation_requires_both_common_evidence_and_versions(self):
        for key in ("evidence", "expected_versions"):
            change = operation("rename", id="task-1", name="Nome")
            del change[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_analysis(structural(change))

    def test_twenty_map_operations_allowed_and_twenty_one_rejected(self):
        change = operation("rename", id="task-1", name="Nome")
        validate_analysis(structural(*[copy.deepcopy(change) for _ in range(20)]))
        with self.assertRaises(ValueError):
            validate_analysis(structural(*[copy.deepcopy(change) for _ in range(21)]))

    def test_null_is_forbidden_for_existing_object_ids(self):
        with self.assertRaises(ValueError):
            validate_analysis(structural(operation("rename", id=None, name="Nome")))
        with self.assertRaises(ValueError):
            validate_analysis(structural(operation("connect", **{
                "from": "task-1", "to": None, "kind": "contains"})))

    def test_unknown_edge_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_analysis(structural(operation("connect", **{
                "from": "task-1", "to": "person-1", "kind": "invented"})))

    def test_published_schema_matches_runtime_schema(self):
        path = Path(__file__).resolve().parents[2] / "contracts/v1/analysis.schema.json"
        self.assertEqual(json.loads(path.read_text()), ANALYSIS_SCHEMA)


class StructuralAdapterTests(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "job-1", "kind": "answer", "source_ids": ["answer-1"], "task_ids": ["task-1"]}
        self.context = {"company": {}, "context": {}, "tasks": [{"id": "task-1", "version": 1}],
                        "nodes": [], "edges": [], "questions": [], "sources": [
                            {"id": "answer-1", "text": "Divida a tarefa e atribua a Ana."}],
                        "map_scope": {"tasks": [{"id": "task-1", "version": 1}],
                                      "nodes": [], "edges": [], "source_ids": ["answer-1"]}}

    def interpret(self, result, job=None):
        runner = FakeRunner(output=stream(result))
        interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
        return interpreter.interpret(job or self.job, self.context), runner

    def test_structural_answer_skips_normal_task_cardinality(self):
        result = structural(operation("rename", id="task-1", name="Nome corrigido"))
        parsed, runner = self.interpret(result)
        self.assertEqual(parsed, result)
        payload = json.loads(runner.calls[-1][1]["input"])
        self.assertEqual(payload["base_context"]["map_scope"], self.context["map_scope"])
        for phrase in ("intenção explícita", "resposta atual verificada", "citada literalmente",
                       "ativas ou inativas", "Preserve todos", "preserve citações", "ambíguo"):
            self.assertIn(phrase, payload["request"])

    def test_structural_output_is_exclusive_of_other_result_channels(self):
        for key, extra in (
            ("tasks", [task("task-1")]),
            ("nodes", [{"kind": "person", "name": "Ana", "evidence": claim()["evidence"]}]),
            ("context_updates", [{"topic": "people", **claim()}]),
            ("answer_assignments", [{"answer_id": "answer-1", "question_ids": [], "evidence": []}]),
            ("next_question", {"topic": "people", "text": "Quem faz?"}),
        ):
            result = structural(operation("rename", id="task-1", name="Nome"))
            result[key] = extra
            with self.subTest(key=key), self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
                self.interpret(result)

    def test_only_answer_jobs_with_addressed_tasks_allow_map_changes(self):
        result = structural(operation("rename", id="task-1", name="Nome"))
        for job in ({**self.job, "kind": "observation"}, {**self.job, "kind": "interview"},
                    {**self.job, "kind": "disambiguation"}, {**self.job, "task_ids": []}):
            with self.subTest(job=job), self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
                self.interpret(result, job=job)

    def test_empty_structural_array_keeps_normal_answer_cardinality(self):
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            self.interpret(structural())
        ordinary = {**analysis([task("task-1")]), "map_changes": []}
        self.assertEqual(self.interpret(ordinary)[0], ordinary)


class ScopedBrain(Brain):
    def __init__(self):
        super().__init__()
        self.jobs[0].update(kind="answer", source_ids=["answer-1"], task_ids=["task-1"])
        self.context = {
            "company": {}, "context": {}, "questions": [],
            "tasks": [{"id": "task-1", "version": 3, "fields": {"performer": {
                "value": "Ana", "basis": "reported", "evidence": [
                    {"source_id": "claim-only", "quote": "Ana faz."}]}},
                "evidence_ids": ["predecessor-source"], "superseded_by": []}] + [
                    {"id": f"other-{index}", "version": 1, "fields": {}, "evidence_ids": [],
                     "superseded_by": []} for index in range(25)] + [
                    {"id": "superseded-task", "version": 1, "superseded_by": ["task-1"]}],
            "nodes": [{"id": f"person-{index}", "version": 2, "name": f"Pessoa {index}",
                       "evidence_ids": ["node-source"]} for index in range(25)] + [
                       {"id": "unrelated-node", "version": 1, "name": "Irrelevante"}],
            "edges": [{"id": f"edge-{index}", "version": 4, "from": "task-1",
                       "to": f"person-{index}", "kind": "performed_by", "active": index != 0,
                       "evidence": [{"source_id": "edge-only", "quote": "Executa."}]}
                      for index in range(25)],
            "sources": [{"id": source_id, "text": text} for source_id, text in (
                ("answer-1", "Divida a tarefa e atribua a Ana."), ("predecessor-source", "Registrou."),
                ("claim-only", "Ana faz."), ("edge-only", "Executa."), ("node-source", "Equipe."),
                ("unrelated-source", "Fora do escopo."))],
        }
        self.apply_kwargs = None

    def apply_analysis(self, job_id, result, **kwargs):
        self.apply_kwargs = kwargs
        return super().apply_analysis(job_id, result)


class StructuralWorkerTests(unittest.TestCase):
    def test_worker_supplies_bounded_candidates_and_all_incident_edges(self):
        brain = ScopedBrain()
        class Capture:
            def interpret(self, job, context):
                self.context = context
                return structural(operation("rename", id="task-1", name="Nome"))
        interpreter = Capture()
        self.assertEqual(SemanticWorker(brain, interpreter).run_once()["status"], "applied")
        context = interpreter.context
        self.assertEqual(len(context["tasks"]), 21)
        self.assertEqual(context["tasks"][0]["id"], "task-1")
        self.assertNotIn("superseded-task", {task["id"] for task in context["tasks"]})
        self.assertLessEqual(len(context["nodes"]), 20)
        self.assertEqual(len(context["edges"]), 25)
        self.assertFalse(context["edges"][0]["active"])
        supplied_sources = {source["id"] for source in context["sources"]}
        self.assertEqual(supplied_sources, {"answer-1", "predecessor-source", "claim-only", "edge-only", "node-source"})
        self.assertEqual(set(brain.apply_kwargs["map_scope"]["source_ids"]), supplied_sources)
        self.assertEqual(brain.apply_kwargs["map_scope"]["edges"][0], {"id": "edge-0", "version": 4})

    def test_map_scope_cannot_be_supplied_or_mutated_by_interpreter_metadata(self):
        brain = ScopedBrain()
        class Mutating:
            last_metadata = {"map_scope": {"tasks": [{"id": "forged", "version": 999}]}}
            def interpret(self, job, context):
                context["map_scope"]["tasks"][0]["version"] = 999
                context["tasks"][0]["version"] = 999
                return structural(operation("rename", id="task-1", name="Nome"))
        self.assertEqual(SemanticWorker(brain, Mutating()).run_once()["status"], "applied")
        self.assertEqual(brain.apply_kwargs["map_scope"]["tasks"][0], {"id": "task-1", "version": 3})
        self.assertNotEqual(brain.apply_kwargs["map_scope"], brain.apply_kwargs["metadata"]["map_scope"])
        self.assertEqual(brain.context["tasks"][0]["version"], 3)

    def test_normal_answer_does_not_gain_map_scope_keyword(self):
        brain = ScopedBrain()
        class Ordinary:
            def interpret(self, job, context):
                return analysis([task("task-1")])
        self.assertEqual(SemanticWorker(brain, Ordinary()).run_once()["status"], "applied")
        self.assertEqual(brain.apply_kwargs, {})

    def test_mocked_structural_result_cannot_bypass_job_kind(self):
        brain = Brain()
        class Invalid:
            def interpret(self, job, context):
                return structural(operation("rename", id="task-1", name="Nome"))
        result = SemanticWorker(brain, Invalid()).run_once()
        self.assertEqual(result["error_code"], "invalid_analysis")
        self.assertEqual(brain.applied, [])
        self.assertEqual(len(brain.jobs), 1)


if __name__ == "__main__":
    unittest.main()
