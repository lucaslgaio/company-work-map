"""Round4 frozen semantic proposals pass through the real restricted parser."""

import unittest

from company_pilot.inference import ClaudeInterpreter, InterpreterError
from tests.inference.test_claude import FakeRunner, analysis, stream, task


class Round4ContractTests(unittest.TestCase):
    def test_f3_equivalence_preserves_current_and_prior_exact_evidence(self):
        previous = {"id": "prior-founder", "kind": "answer", "text": "Consolidar fotos no relatório semanal."}
        current = {"id": "current-capture", "kind": "observation", "text": "Juntar imagens no relatório semanal."}
        before = {"value": "Consolidar fotos no relatório semanal", "basis": "reported", "evidence": [
            {"source_id": previous["id"], "quote": previous["text"]}]}
        proposed = task("report-task")
        proposed["fields"]["purpose"] = {"value": "Juntar imagens no relatório semanal", "basis": "observed", "evidence": [
            {"source_id": current["id"], "quote": current["text"]}]}
        update = {"mode": "equivalent", "effective_at": None, "evidence": proposed["fields"]["purpose"]["evidence"],
                  "prior_evidence": before["evidence"]}
        proposed["field_updates"] = {"purpose": update}
        result = analysis(tasks=[proposed])
        runner = FakeRunner(output=stream(result))
        job = {"id": "observation-job", "kind": "observation", "source_ids": [current["id"]], "task_ids": []}
        context = {"tasks": [{"id": "report-task", "version": 1, "fields": {"purpose": before}}], "sources": [current, previous]}
        parsed = ClaudeInterpreter("/fake/claude", runner=runner).interpret(job, context)
        self.assertEqual(parsed["tasks"][0]["field_updates"]["purpose"], update)

    def test_f3_explicit_founder_pointer_preserves_named_question_and_quote(self):
        source = {"id": "founder-pointer", "kind": "answer", "text": "Tem coisa mais importante: quem aprova a compra?"}
        reason = {"value": "Esclarecer quem aprova a compra", "basis": "reported", "evidence": [
            {"source_id": source["id"], "quote": source["text"]}]}
        result = {**analysis(tasks=[]), "attention_requests": [{"question_id": "approval-question", "reason": reason}],
                  "message_intent": {"kind": "context", "task_ids": [], "question_ids": ["approval-question"],
                                     "delegate_to": None, "disposition": None, "evidence": reason["evidence"]}}
        job = {"id": "message-job", "kind": "message", "source_ids": [source["id"]], "task_ids": [],
               "candidate_task_versions": [], "candidate_question_ids": ["approval-question"], "question_ids": ["approval-question"]}
        context = {"tasks": [], "questions": [{"id": "approval-question", "status": "pending"}], "sources": [source]}
        parsed = ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(result))).interpret(job, context)
        self.assertEqual(parsed["attention_requests"], [{"question_id": "approval-question", "reason": reason}])

    def test_attention_request_bound_and_reason_shape_are_strict(self):
        source = {"id": "pointer", "kind": "answer", "text": "Pergunte sobre a aprovação da compra."}
        reason = {"value": "Aprovação", "basis": "reported", "evidence": [{"source_id": "pointer", "quote": source["text"]}]}
        job = {"id": "pointer-job", "kind": "message", "source_ids": ["pointer"], "task_ids": [],
               "candidate_task_versions": [], "candidate_question_ids": ["question"], "question_ids": ["question"]}
        context = {"tasks": [], "questions": [{"id": "question", "status": "pending"}], "sources": [source]}
        result = {**analysis(tasks=[]), "message_intent": {"kind": "context", "task_ids": [], "question_ids": ["question"],
                  "delegate_to": None, "disposition": None, "evidence": reason["evidence"]}}
        result["attention_requests"] = [{"question_id": "question", "reason": reason}] * 10
        ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(result))).interpret(job, context)
        result["attention_requests"].append({"question_id": "question", "reason": reason})
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(result))).interpret(job, context)

    def test_prior_evidence_cannot_be_free_text_or_a_new_authority_field(self):
        item = task()
        item["field_updates"] = {"result": {"mode": "equivalent", "effective_at": None,
            "evidence": [], "prior_evidence": "same meaning"}}
        job = {"id": "shape-job", "kind": "observation", "source_ids": ["source"], "task_ids": []}
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(analysis(tasks=[item])))).interpret(job, {"tasks": [], "sources": []})


if __name__ == "__main__":
    unittest.main()
