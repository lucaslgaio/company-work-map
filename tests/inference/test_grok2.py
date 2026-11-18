"""Report-derived Grok-2 regressions, not replay of the pending raw export.

E10 / G1-G2: the interpreter/worker boundary must retain distinct supported
work from one permitted capture and supplied accepted comparison evidence.
Only the external CLI and the persistence boundary are synthetic doubles.
"""

import copy
import json
import unittest

from company_pilot.inference import ClaudeInterpreter, InterpreterError, SemanticWorker
from tests.inference.test_claude import FakeRunner, analysis, stream, task
from tests.inference.test_worker import Brain


CURRENT_ID = "grok2-derived-capture-26"
PRIOR_ID = "grok2-derived-audio-23"
CURRENT_TEXT = ("Cronograma conferido Bia 26/11: conduítes 100% em todos os ambientes. "
                "Cozinha: verificar a compatibilidade da lava-louça.")
PRIOR_TEXT = "23/11: marquei conduíte como concluído pelo áudio."


def observed_task(name, quote, task_id=None):
    result = task(task_id)
    result["name"] = name
    result["fields"]["action"] = {"value": name, "basis": "observed", "evidence": [
        {"source_id": CURRENT_ID, "quote": quote}]}
    return result


class ReportBrain(Brain):
    def __init__(self):
        super().__init__()
        self.jobs[0].update(id="grok2-observation-job", source_ids=[CURRENT_ID])
        earlier = task("schedule-task")
        earlier["fields"]["action"] = {"value": "Registrar conclusão dos conduítes", "basis": "observed",
            "evidence": [{"source_id": PRIOR_ID, "quote": PRIOR_TEXT}]}
        self.context = {"company": {}, "context": {}, "questions": [], "tasks": [{
            "id": "schedule-task", "version": 1, "name": earlier["name"], "fields": earlier["fields"],
            "evidence_ids": [PRIOR_ID], "superseded_by": []}], "sources": [
                {"id": PRIOR_ID, "kind": "observation", "text": PRIOR_TEXT},
                {"id": "accepted-analysis", "kind": "analysis", "text": "Generated interpretation audit",
                 "input_source_ids": [PRIOR_ID]},
                {"id": "pending-unaccepted", "kind": "observation", "text": "Not accepted business evidence."},
                {"id": CURRENT_ID, "kind": "observation", "text": CURRENT_TEXT},
            ]}
        self.apply_kwargs = None

    def apply_analysis(self, job_id, result, **kwargs):
        self.apply_kwargs = copy.deepcopy(kwargs)
        receipt = super().apply_analysis(job_id, result)
        receipt["task_names"] = [candidate["name"] for candidate in result["tasks"]]
        return receipt


class Grok2ObservationTests(unittest.TestCase):
    def test_e10_two_distinct_tasks_survive_actual_parser_and_worker_with_accepted_context(self):
        brain = ReportBrain()
        decision = analysis(tasks=[
            observed_task("Conferir conclusão dos conduítes", "Cronograma conferido Bia 26/11: conduítes 100% em todos os ambientes.", "schedule-task"),
            observed_task("Verificar compatibilidade da lava-louça", "Cozinha: verificar a compatibilidade da lava-louça."),
        ])
        decision["tasks"][0]["fields"]["trigger"] = {
            "value": "Conclusão anterior marcada pelo áudio", "basis": "reported",
            "evidence": [{"source_id": PRIOR_ID, "quote": PRIOR_TEXT}]}
        runner = FakeRunner(output=stream(decision))
        response = SemanticWorker(brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual(response["receipt"]["task_names"], [
            "Conferir conclusão dos conduítes", "Verificar compatibilidade da lava-louça"])
        payload = json.loads(runner.calls[-1][1]["input"])
        self.assertEqual({source["id"] for source in payload["base_context"]["sources"]}, {CURRENT_ID, PRIOR_ID})
        self.assertEqual(set(brain.apply_kwargs["map_scope"]["source_ids"]), {CURRENT_ID, PRIOR_ID})

    def test_e3_progress_metadata_survives_actual_cli_parser_without_invented_time(self):
        brain = ReportBrain()
        decision = analysis(tasks=[observed_task("Conferir conduítes", CURRENT_TEXT, "schedule-task")])
        update = {"mode": "progress", "effective_at": "2026-11-26T09:00:00-03:00", "evidence": [
            {"source_id": CURRENT_ID, "quote": "Cronograma conferido Bia 26/11: conduítes 100% em todos os ambientes."}]}
        decision["tasks"][0]["fields"]["result"] = {"value": "Conduítes conferidos, 100%", "basis": "observed", "evidence": update["evidence"]}
        decision["tasks"][0]["field_updates"] = {"result": update}
        runner = FakeRunner(output=stream(decision))
        parsed = ClaudeInterpreter("/fake/claude", runner=runner).interpret(brain.jobs[0], brain.context)
        self.assertEqual(parsed["tasks"][0]["field_updates"], {"result": update})

    def test_e4_named_issue_resolution_and_substantive_context_answer_survive_parser(self):
        brain = ReportBrain()
        source = {"id": "grok2-derived-founder-reply", "kind": "answer",
                  "text": "Foi só sala e cozinha. Bia confirma concluído na vistoria."}
        context = copy.deepcopy(brain.context)
        context["sources"].append(source)
        context["tasks"][0]["consistency_issues"] = [{"id": "issue-completion", "description": "Conclusão integral contestada", "evidence": [
            {"source_id": PRIOR_ID, "quote": PRIOR_TEXT}]}]
        context["questions"] = [{"id": "consistency-question", "status": "pending"}, {"id": "context-question", "status": "pending"}]
        job = {"id": "grok2-message-job", "kind": "message", "source_ids": [source["id"]], "task_ids": [],
               "candidate_task_versions": [{"id": "schedule-task", "version": 1}],
               "candidate_question_ids": ["consistency-question", "context-question"],
               "question_ids": ["consistency-question", "context-question"]}
        supported = {"value": "Somente sala e cozinha; Bia confirma na vistoria", "basis": "reported", "evidence": [
            {"source_id": source["id"], "quote": source["text"]}]}
        candidate = task("schedule-task")
        candidate["fields"]["result"] = copy.deepcopy(supported)
        candidate["issue_resolutions"] = [{"issue_ids": ["issue-completion"], "resolution": supported}]
        decision = {**analysis(tasks=[candidate]), "question_answers": [{"question_id": "context-question", "claim": supported}],
                    "message_intent": {"kind": "answer", "task_ids": ["schedule-task"],
                        "question_ids": ["consistency-question", "context-question"], "delegate_to": None,
                        "disposition": None, "evidence": supported["evidence"]}}
        runner = FakeRunner(output=stream(decision))
        parsed = ClaudeInterpreter("/fake/claude", runner=runner).interpret(job, context)
        self.assertEqual(parsed["tasks"][0]["issue_resolutions"], [{"issue_ids": ["issue-completion"], "resolution": supported}])
        self.assertEqual(parsed["question_answers"], [{"question_id": "context-question", "claim": supported}])

    def test_e11_parser_rejects_free_form_task_links(self):
        brain = ReportBrain()
        candidate = observed_task("Verificar compatibilidade da lava-louça", CURRENT_TEXT)
        candidate["links"] = [{"kind": "handoff", "target_id": "schedule-task", "evidence": [
            {"source_id": CURRENT_ID, "quote": CURRENT_TEXT}], "arbitrary_relation": "same project"}]
        runner = FakeRunner(output=stream(analysis(tasks=[candidate])))
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            ClaudeInterpreter("/fake/claude", runner=runner).interpret(brain.jobs[0], brain.context)

    def test_e10_three_supported_tasks_are_kept_and_a_fourth_is_rejected(self):
        brain = ReportBrain()
        text = CURRENT_TEXT + " Pendências: preparar o aditivo por escrito."
        brain.context["sources"][-1]["text"] = text
        candidates = [observed_task("Conferir conduítes", "Cronograma conferido Bia 26/11: conduítes 100% em todos os ambientes.", "schedule-task"),
                      observed_task("Verificar lava-louça", "Cozinha: verificar a compatibilidade da lava-louça."),
                      observed_task("Preparar aditivo escrito", "Pendências: preparar o aditivo por escrito.")]
        decision = analysis(tasks=candidates)
        runner = FakeRunner(output=stream(decision))
        self.assertEqual(ClaudeInterpreter("/fake/claude", runner=runner).interpret(brain.jobs[0], brain.context), decision)
        decision["tasks"].append(observed_task("Quarta candidata", text))
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(decision))).interpret(brain.jobs[0], brain.context)

    def test_e10_accepted_cross_capture_consistency_preserves_both_quotes(self):
        brain = ReportBrain()
        text = "Vistoria Bia 24/11: quarto 2 - parede sem conduíte."
        brain.context["sources"][-1]["text"] = text
        candidate = observed_task("Verificar conduítes na vistoria", text, "schedule-task")
        evidence = [{"source_id": PRIOR_ID, "quote": PRIOR_TEXT}, {"source_id": CURRENT_ID, "quote": text}]
        candidate["consistency_issues"] = [{"description": "Conclusão integral anterior e falta de conduíte na vistoria são incompatíveis.", "evidence": evidence}]
        response = SemanticWorker(brain, ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(analysis(tasks=[candidate]))))).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual(brain.applied[0][1]["tasks"][0]["consistency_issues"][0]["evidence"], evidence)
        self.assertEqual(set(brain.apply_kwargs["map_scope"]["source_ids"]), {CURRENT_ID, PRIOR_ID})

    def test_e10_optional_accepted_comparisons_are_bounded_and_pending_raw_sources_excluded(self):
        brain = ReportBrain()
        for index in range(60):
            source_id = f"accepted-source-{index}"
            brain.context["sources"].extend([
                {"id": source_id, "kind": "observation", "text": f"Registro empresarial aceito {index}."},
                {"id": f"audit-{index}", "kind": "analysis", "text": "Generated audit must not be model context",
                 "input_source_ids": [source_id]},
            ])
        brain.context["sources"].extend([
            {"id": "historic-only", "kind": "company_record", "text": "Never accepted as current work."},
            {"id": "historic-audit", "kind": "analysis", "source_scope": "historic", "text": "Historic receipt",
             "input_source_ids": ["historic-only"]},
        ])
        runner = FakeRunner(output=stream(analysis(tasks=[observed_task("Conferir conduítes", CURRENT_TEXT, "schedule-task")])))
        response = SemanticWorker(brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()
        self.assertEqual(response["status"], "applied", response)
        supplied = {source["id"] for source in json.loads(runner.calls[-1][1]["input"])["base_context"]["sources"]}
        self.assertEqual(supplied, {CURRENT_ID, PRIOR_ID} | {f"accepted-source-{index}" for index in range(10, 60)})
        self.assertEqual(set(brain.apply_kwargs["map_scope"]["source_ids"]), supplied)

    def test_e3_field_update_modes_and_keys_are_strict_but_unknown_time_stays_null(self):
        brain = ReportBrain()
        candidate = observed_task("Verificar conduítes", CURRENT_TEXT, "schedule-task")
        update = {"mode": "conflict", "effective_at": None, "evidence": [{"source_id": CURRENT_ID, "quote": CURRENT_TEXT}]}
        candidate["field_updates"] = {"result": update}
        decision = analysis(tasks=[candidate])
        runner = FakeRunner(output=stream(decision))
        parsed = ClaudeInterpreter("/fake/claude", runner=runner).interpret(brain.jobs[0], brain.context)
        self.assertIsNone(parsed["tasks"][0]["field_updates"]["result"]["effective_at"])
        for invalid in ({"result": {**update, "mode": "correction"}}, {"invented_field": update}):
            candidate["field_updates"] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
                ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(analysis(tasks=[candidate])))).interpret(brain.jobs[0], brain.context)

    def test_e4_issue_resolutions_and_question_answers_are_bounded(self):
        brain = ReportBrain()
        claim = {"value": "Relato", "basis": "reported", "evidence": [{"source_id": PRIOR_ID, "quote": PRIOR_TEXT}]}
        candidate = observed_task("Verificar conduítes", CURRENT_TEXT, "schedule-task")
        for key in ("issue_resolutions", "question_answers"):
            decision = analysis(tasks=[copy.deepcopy(candidate)])
            if key == "issue_resolutions":
                decision["tasks"][0][key] = [{"issue_ids": ["known-issue"], "resolution": claim}] * 21
            else:
                decision[key] = [{"question_id": "known-question", "claim": claim}] * 21
            with self.subTest(key=key), self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
                ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(decision))).interpret(brain.jobs[0], brain.context)

    def test_e11_typed_inferred_link_retains_direction_basis_and_current_evidence(self):
        brain = ReportBrain()
        candidate = observed_task("Produzir lista revisada", CURRENT_TEXT)
        relation = {"kind": "handoff", "target_id": "schedule-task", "basis": "inferred",
                    "evidence": [{"source_id": CURRENT_ID, "quote": CURRENT_TEXT}]}
        candidate["links"] = [relation]
        runner = FakeRunner(output=stream(analysis(tasks=[candidate])))
        parsed = ClaudeInterpreter("/fake/claude", runner=runner).interpret(brain.jobs[0], brain.context)
        self.assertEqual(parsed["tasks"][0]["links"], [relation])
        command = runner.calls[-1][0]
        prompt = command[command.index("--system-prompt") + 1]
        for text in ("produtora", "consumidora", "Coincidência de pessoa, projeto ou nome não prova conexão",
                     "basis inferred", "IDs canônicos", "fonte primária", "relatado pelo fundador", "record_ref"):
            self.assertIn(text, prompt)


if __name__ == "__main__":
    unittest.main()
