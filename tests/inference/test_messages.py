"""Outcome-repair contracts use explicit synthetic decisions, never live models."""

import copy
import json
import unittest

from company_pilot.inference import ClaudeInterpreter, InterpreterError, SemanticWorker
from company_pilot.inference.schema import validate_analysis
from tests.inference.test_claude import FakeRunner, analysis, stream, task
from tests.inference.test_structural import ScopedBrain, operation

RAW_TEXT = "Ana aprovou; quem executou foi Bruno. Atlas é a conta empresarial."
RAW_REF = {"source_id": "message-1", "quote": RAW_TEXT}


def supported(value, source_id="message-1", quote=RAW_TEXT):
    return {"value": value, "basis": "reported", "evidence": [{"source_id": source_id, "quote": quote}]}


def marker(value="Atlas", **changes):
    return {"value": value, "kind": "business_account", "operation": "add", "evidence": [RAW_REF], **changes}


def context_update(**changes):
    return {"topic": "people", **supported("Bruno executa"), **changes}


def message(kind="correction", task_ids=None, question_ids=None, **output):
    task_ids = [] if task_ids is None else task_ids
    return {**analysis(tasks=[task(task_id) for task_id in task_ids]), "message_intent": {
        "kind": kind, "task_ids": task_ids, "question_ids": question_ids or [],
        "delegate_to": None, "disposition": None, "evidence": [RAW_REF],
    }, **output}


class OutcomeSchemaTests(unittest.TestCase):
    def test_legacy_analyses_and_optional_extensions_validate(self):
        validate_analysis(analysis())
        result = analysis()
        result.update(recognition_markers=[marker()], context_updates=[context_update(mode="add", replaces=[])])
        result["tasks"][0].update(payment={"object": supported(None), "tracking_record": supported(None)},
                                  consistency_issues=[{"description": "Aprovação e execução são distintas.", "evidence": [RAW_REF]}])
        validate_analysis(result)

    def test_markers_have_exact_types_and_required_operation(self):
        for changes in ({"kind": "app"}, {"operation": "replace"}, {"value": None}, {"extra": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_analysis({**analysis(), "recognition_markers": [marker(**changes)]})
        invalid = marker()
        del invalid["operation"]
        with self.assertRaises(ValueError):
            validate_analysis({**analysis(), "recognition_markers": [invalid]})

    def test_context_modes_and_claim_references_are_optional_typed_fields(self):
        for mode in (None, "add", "replace", "conflict"):
            update = context_update(**({"mode": mode, "replaces": ["claim-1"]} if mode else {}))
            validate_analysis({**analysis(), "context_updates": [update]})
        for invalid in (context_update(mode="overwrite"), context_update(replaces=[1]),
                        context_update(replaces=None), context_update(unknown="fact")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_analysis({**analysis(), "context_updates": [invalid]})

    def test_payment_requires_both_claims_and_consistency_issues_require_evidence(self):
        for invalid in ({"object": supported(None)}, {"tracking_record": supported("Livro")},
                        {"object": "Fornecedor", "tracking_record": supported(None)},
                        {"object": supported(None), "tracking_record": supported(None), "supplier": "Empresa"}):
            result = analysis()
            result["tasks"][0]["payment"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_analysis(result)
        result = analysis()
        result["tasks"][0]["consistency_issues"] = [{"description": "Duas versões conflitantes"}]
        with self.assertRaises(ValueError):
            validate_analysis(result)

    def test_intent_enums_and_nullable_attribution(self):
        for kind in ("answer", "correction", "context", "dismiss", "delegate", "chatter", "unclear"):
            validate_analysis(message(kind))
        for key, invalid in (("kind", "team_send"), ("disposition", "delete"), ("delegate_to", 3), ("task_ids", None)):
            result = message()
            result["message_intent"][key] = invalid
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_analysis(result)


class MessageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "message-job", "kind": "message", "source_ids": ["message-1", "assignment-1"],
                    "task_ids": [], "candidate_task_versions": [{"id": "established-1", "version": 3},
                                                                  {"id": "task-2", "version": 1}],
                    "candidate_question_ids": ["question-1"], "question_ids": ["resolved-1"]}
        self.context = {"company": {}, "context": {}, "tasks": [
            {"id": "established-1", "version": 3, "status": "established"}, {"id": "task-2", "version": 1}],
            "questions": [{"id": "question-1", "status": "pending"}, {"id": "resolved-1", "status": "resolved"}],
            "nodes": [], "edges": [], "sources": [{"id": "message-1", "text": RAW_TEXT},
                                                         {"id": "assignment-1", "text": "Generated attribution"}],
            "context_claims": [{"id": "claim-1", "topic": "people", "status": "active", "claim": supported("Ana aprova")}],
            "recognition_markers": [{"id": "marker-1", "status": "active", **marker()}],
            "map_scope": {"tasks": [{"id": "established-1", "version": 3}, {"id": "task-2", "version": 1}],
                          "nodes": [], "edges": [], "source_ids": ["message-1", "assignment-1"]}}

    def interpret(self, result, job=None, context=None):
        runner = FakeRunner(output=stream(result))
        interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
        parsed = interpreter.interpret(job or self.job, self.context if context is None else context)
        return parsed, runner, interpreter

    def invalid(self, result, **kwargs):
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            self.interpret(result, **kwargs)

    def test_correction_can_select_established_task_without_open_question_or_hint(self):
        result = message(task_ids=["established-1"])
        result["tasks"][0]["fields"]["performer"] = supported("Bruno")
        result["tasks"][0]["payment"] = {"object": supported(None), "tracking_record": supported(None)}
        parsed, runner, interpreter = self.interpret(result)
        self.assertEqual(parsed, result)
        self.assertEqual(interpreter.last_metadata["prompt_version"], "company-work-evidence-13")
        payload = json.loads(runner.calls[-1][1]["input"])
        self.assertEqual(payload["job"]["candidate_task_versions"], self.job["candidate_task_versions"])
        self.assertEqual(payload["job"]["task_ids"], [])
        self.assertEqual(payload["base_context"]["context_claims"], self.context["context_claims"])
        for phrase in ("fonte primária", "Correção pode selecionar tarefa estabelecida", "preservam campos copiados",
                       "campos explicitamente corrigidos citam a nova mensagem", "não uma seleção"):
            self.assertIn(phrase, payload["request"])
        system = runner.calls[-1][0][runner.calls[-1][0].index("--system-prompt") + 1]
        for phrase in ("omitir um fato nunca", "IDs das", "context_claims", "não automaticamente um fornecedor",
                       "aplicativo não", "claims object e tracking_record"):
            self.assertIn(phrase, system)

    def test_message_intent_is_required(self):
        self.invalid(analysis(tasks=[]))

    def test_intent_requires_original_primary_message_evidence(self):
        for evidence in ([], [{"source_id": "assignment-1", "quote": "Generated attribution"}],
                         [{"source_id": "message-1", "quote": " "}]):
            result = message("chatter")
            result["message_intent"]["evidence"] = evidence
            self.invalid(result)
        context = copy.deepcopy(self.context)
        context["sources"] = [source for source in context["sources"] if source["id"] != "message-1"]
        self.invalid(message("chatter"), context=context)

    def test_selected_ids_must_be_receipt_candidates_and_actually_supplied(self):
        self.invalid(message(task_ids=["unknown"]))
        self.invalid(message(task_ids=["established-1"], question_ids=["other-question"]))
        context = copy.deepcopy(self.context)
        context["tasks"] = [task for task in context["tasks"] if task["id"] != "established-1"]
        self.invalid(message(task_ids=["established-1"]), context=context)
        self.invalid(message(task_ids=["established-1", "established-1"]))

    def test_ordinary_task_results_match_selected_intent_not_job_hints(self):
        job = {**self.job, "task_ids": ["established-1"]}
        result = message("answer", task_ids=["task-2"])
        self.assertEqual(self.interpret(result, job=job)[0], result)
        result["tasks"] = [task("established-1")]
        self.invalid(result, job=job)

    def test_structural_message_requires_correction_and_selected_tasks(self):
        change = operation("rename", id="established-1", name="Nome explícito")
        result = message(task_ids=["established-1"], tasks=[], map_changes=[change])
        self.assertEqual(self.interpret(result)[0], result)
        self.invalid({**result, "message_intent": {**result["message_intent"], "kind": "answer"}})
        self.invalid(message(tasks=[], map_changes=[change]))
        self.invalid({**result, "recognition_markers": [marker()]})
        self.invalid({**result, "context_updates": [context_update()]})

    def test_nonwrite_intents_cannot_change_graph_context_or_markers(self):
        for kind in ("chatter", "dismiss", "delegate", "unclear"):
            for key, value in (("tasks", [task("established-1")]),
                               ("nodes", [{"kind": "person", "name": "Bruno", "evidence": [RAW_REF]}]),
                               ("context_updates", [context_update()]), ("recognition_markers", [marker()])):
                result = message(kind, question_ids=["question-1"], **{key: value})
                with self.subTest(kind=kind, key=key):
                    self.invalid(result)

    def test_dismissal_and_delegation_select_questions_only_without_team_route(self):
        for kind in ("dismiss", "delegate"):
            result = message(kind, question_ids=["resolved-1"])
            result["message_intent"].update(disposition="not_now", delegate_to="Ana" if kind == "delegate" else None)
            self.assertEqual(self.interpret(result)[0], result)
            self.invalid(message(kind))
            self.invalid(message(kind, task_ids=["established-1"], question_ids=["question-1"], tasks=[]))

    def test_context_intent_can_accumulate_claims_and_markers_with_question_selection(self):
        result = message("context", question_ids=["question-1"],
                         context_updates=[context_update(mode="add")], recognition_markers=[marker()])
        self.assertEqual(self.interpret(result)[0], result)
        self.invalid(message("context", task_ids=["established-1"], tasks=[]))

    def test_answer_and_correction_may_also_carry_supported_context_and_markers(self):
        for kind in ("answer", "correction"):
            result = message(kind, task_ids=["task-2"], context_updates=[context_update()], recognition_markers=[marker()])
            self.assertEqual(self.interpret(result)[0], result)

    def test_message_candidate_versions_are_positive_unique_and_bounded(self):
        for refs in ([{"id": "task-2", "version": True}],
                     [{"id": "task-2", "version": 0}],
                     [{"id": "task-2", "version": 1}] * 2,
                     [{"id": f"task-{i}", "version": 1} for i in range(51)]):
            with self.subTest(refs=refs), self.assertRaisesRegex(InterpreterError, "^invalid_job$"):
                self.interpret(message("chatter"), job={**self.job, "candidate_task_versions": refs})

    def test_legacy_interview_and_answer_accept_optional_new_context_and_markers(self):
        for kind in ("interview", "answer"):
            job = {"id": "legacy", "kind": kind, "source_ids": ["message-1"], "task_ids": []}
            result = {**analysis(tasks=[]), "context_updates": [context_update()], "recognition_markers": [marker()]}
            self.assertEqual(self.interpret(result, job=job)[0], result)


class MessageBrain(ScopedBrain):
    def __init__(self):
        super().__init__()
        self.jobs[0].update(kind="message", source_ids=["message-1"], task_ids=["other-24"],
                            candidate_task_versions=[{"id": "other-4", "version": 1}, {"id": "task-1", "version": 2}],
                            candidate_question_ids=["open-1", "resolved-implicit", "superseded-1"],
                            question_ids=["resolved-explicit"])
        self.context["tasks"][0]["status"] = "established"
        self.context["questions"] = [
            {"id": "open-1", "status": "pending", "evidence_ids": ["question-source"]},
            {"id": "resolved-explicit", "status": "resolved", "evidence_ids": ["resolved-source"]},
            {"id": "resolved-implicit", "status": "resolved", "evidence_ids": ["unrelated-source"]},
            {"id": "superseded-1", "status": "superseded", "evidence_ids": ["unrelated-source"]},
            {"id": "unrelated-open", "status": "pending", "evidence_ids": ["unrelated-source"]},
        ]
        self.context["context_claims"] = [
            {"id": "active-claim", "topic": "people", "status": "active", "claim": supported("Bruno executa", "active-claim-source")},
            {"id": "retired-claim", "topic": "people", "status": "retired", "claim": supported("Versão antiga", "retired-claim-source")},
        ]
        self.context["recognition_markers"] = [
            {"id": "active-marker", "status": "active", "value": "Atlas", "kind": "business_account",
             "evidence": [{"source_id": "marker-source", "quote": "Atlas"}]},
            {"id": "retired-marker", "status": "retired", "value": "Velha conta", "kind": "business_account",
             "evidence": [{"source_id": "retired-marker-source", "quote": "Velha conta"}]},
        ]
        self.context["sources"] += [{"id": sid, "text": text} for sid, text in (
            ("message-1", RAW_TEXT), ("active-claim-source", RAW_TEXT), ("retired-claim-source", "Versão antiga"),
            ("marker-source", "Atlas"), ("retired-marker-source", "Velha conta"),
            ("question-source", "Pergunta"), ("resolved-source", "Resposta anterior"))]


class MessageWorkerTests(unittest.TestCase):
    def test_actual_adapter_and_worker_use_exact_candidates_and_trusted_current_scope(self):
        brain = MessageBrain()
        result = message(task_ids=["task-1"])
        runner = FakeRunner(output=stream(result))
        interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
        outcome = SemanticWorker(brain, interpreter).run_once()
        self.assertEqual(outcome["status"], "applied")
        payload = json.loads(runner.calls[-1][1]["input"])
        context = payload["base_context"]
        self.assertEqual([task["id"] for task in context["tasks"]], ["other-4", "task-1"])
        self.assertNotIn("other-24", [task["id"] for task in context["tasks"]])
        self.assertEqual({q["id"] for q in context["questions"]}, {"open-1", "resolved-explicit"})
        self.assertEqual([claim["id"] for claim in context["context_claims"]], ["active-claim"])
        self.assertEqual([record["id"] for record in context["recognition_markers"]], ["active-marker"])
        source_ids = {source["id"] for source in context["sources"]}
        self.assertTrue({"message-1", "active-claim-source", "marker-source", "question-source", "resolved-source"} <= source_ids)
        self.assertFalse({"retired-claim-source", "retired-marker-source", "unrelated-source"} & source_ids)
        self.assertEqual(brain.apply_kwargs["map_scope"]["tasks"], [{"id": "other-4", "version": 1}, {"id": "task-1", "version": 3}])
        self.assertEqual(payload["job"]["candidate_task_versions"][1]["version"], 2)
        self.assertEqual(set(brain.apply_kwargs["map_scope"]["source_ids"]), source_ids)

    def test_even_nonwriting_message_intent_always_passes_trusted_map_scope(self):
        brain = MessageBrain()
        class Chatter:
            last_metadata = {"map_scope": {"tasks": [{"id": "forged", "version": 999}]}}
            def interpret(self, job, context):
                context["map_scope"]["tasks"][0]["version"] = 999
                return message("chatter")
        self.assertEqual(SemanticWorker(brain, Chatter()).run_once()["status"], "applied")
        self.assertEqual(brain.apply_kwargs["map_scope"]["tasks"][0], {"id": "other-4", "version": 1})
        self.assertNotEqual(brain.apply_kwargs["map_scope"], brain.apply_kwargs["metadata"]["map_scope"])

    def test_overbound_message_candidates_are_not_silently_truncated(self):
        brain = MessageBrain()
        brain.jobs[0]["candidate_task_versions"] = [{"id": f"task-{i}", "version": 1} for i in range(51)]
        runner = FakeRunner(output=stream(message("chatter")))
        result = SemanticWorker(brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()
        self.assertEqual(result["error_code"], "context_unavailable")
        self.assertEqual(runner.calls, [])
        self.assertEqual(brain.applied, [])


if __name__ == "__main__":
    unittest.main()
