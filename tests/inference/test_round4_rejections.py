"""F7 report-derived actual-worker diagnostics; injected interpretation, no model."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, LocalStore
from company_pilot.inference import SemanticWorker


class FixtureInterpreter:
    def __init__(self, builder=None, error=None):
        self.builder = builder
        self.error = error

    def interpret(self, job, context):
        if self.error:
            raise self.error
        source = next(source for source in context["sources"] if source["id"] == job["source_ids"][0])
        return self.builder(source)


class Round4RejectionTests(unittest.TestCase):
    def setUp(self):
        self.fresh_brain()

    def fresh_brain(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética de rejeições round4", synthetic=True)

    def answer(self, text="Acompanhamos o trabalho no diário de obra."):
        receipt = self.brain.interview_answer(text, str(uuid4()))
        source = next(source for source in self.brain.snapshot()["sources"] if source["id"] == receipt["source_id"])
        return receipt, copy.deepcopy(source)

    def marker(self, source, value="Diário de obra", quote=None):
        return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
                "recognition_markers": [{"value": value, "kind": "record_name", "operation": "add",
                                         "evidence": [{"source_id": source["id"], "quote": source["text"] if quote is None else quote}]}]}

    def assert_pending_source_preserved(self, response, raw, source, error_code="analysis_rejected"):
        self.assertEqual(response["status"], "pending")
        self.assertEqual(response["error_code"], error_code)
        self.assertTrue(response["error_recorded"])
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["recognition_markers"], [])
        self.assertEqual(snapshot["context"], {})
        self.assertEqual(snapshot["sources"], [source])
        self.assertEqual(len(snapshot["pending_jobs"]), 1)
        job = snapshot["pending_jobs"][0]
        self.assertEqual(job["id"], raw["job_id"])
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["error_code"], error_code)
        self.assertEqual(job["attempts"], 1)
        self.assertTrue(job["next_attempt_at"])
        return job

    def test_actual_wrong_quote_rejection_is_safe_and_visible_in_queue(self):
        raw, source = self.answer()
        worker = SemanticWorker(self.brain, FixtureInterpreter(lambda source: self.marker(source, quote="Diário de obra")))
        response = worker.run_once()
        job = self.assert_pending_source_preserved(response, raw, source)
        self.assertEqual(response.get("rejection_reason"), "source_quote_not_found")
        self.assertEqual(job.get("rejection_reason"), "source_quote_not_found")
        safe = "Um trecho citado não foi encontrado na fonte preservada. A interpretação não foi aplicada."
        self.assertEqual(job.get("reason"), safe)
        self.assertIn(safe, self.brain.store.read().files["views/fila.md"])

    def test_known_unsupported_and_generic_markers_have_closed_visible_reasons(self):
        cases = (("Acompanhamos no diário de obra.", "Diário financeiro", "marker_name_must_be_literal_in_source",
                  "O nome proposto não corresponde ao trecho disponível. O operador precisa rever essa interpretação."),
                 ("Usamos Gmail.", "Gmail", "app_only_or_generic_marker_not_authorized",
                  "Foi proposto um aplicativo genérico como sinal de trabalho; é necessário um nome específico sustentado pela fonte."))
        for text, value, reason, safe in cases:
            with self.subTest(reason=reason):
                self.fresh_brain()
                raw, source = self.answer(text)
                response = SemanticWorker(self.brain, FixtureInterpreter(lambda source: self.marker(source, value))).run_once()
                job = self.assert_pending_source_preserved(response, raw, source)
                self.assertEqual(response.get("rejection_reason"), reason)
                self.assertEqual(job.get("rejection_reason"), reason)
                self.assertEqual(job.get("reason"), safe)
                self.assertIn(safe, self.brain.store.read().files["views/fila.md"])

    def test_arbitrary_interpreter_exception_is_never_exposed_or_stored(self):
        private = "PRIVATE_INTERPRETER_DETAIL=never-expose-this"
        raw, source = self.answer()
        response = SemanticWorker(self.brain, FixtureInterpreter(error=RuntimeError(private))).run_once()
        job = self.assert_pending_source_preserved(response, raw, source, "interpretation_failed")
        self.assertNotIn("rejection_reason", response)
        self.assertNotIn("rejection_reason", job)
        self.assertNotIn(private, str(response))
        self.assertNotIn(private, str(self.brain.store.read().files))
        self.assertIn("Falha no processamento; o operador tentará novamente", self.brain.store.read().files["views/fila.md"])

    def test_arbitrary_apply_exception_and_known_code_prefix_are_never_exposed(self):
        for error in (RuntimeError("PRIVATE_APPLY_DETAIL=never-expose-this"),
                      BrainError("source_quote_not_found: PRIVATE_APPLY_DETAIL=never-expose-this")):
            with self.subTest(error_type=type(error).__name__):
                self.fresh_brain()
                raw, source = self.answer()
                worker = SemanticWorker(self.brain, FixtureInterpreter(lambda source: self.marker(source)))
                with patch.object(self.brain, "apply_analysis", side_effect=error):
                    response = worker.run_once()
                job = self.assert_pending_source_preserved(response, raw, source)
                self.assertNotIn("rejection_reason", response)
                self.assertNotIn("rejection_reason", job)
                self.assertNotIn("PRIVATE_APPLY_DETAIL", str(response))
                self.assertNotIn("PRIVATE_APPLY_DETAIL", str(self.brain.store.read().files))
                self.assertIn("Falha no processamento; o operador tentará novamente", self.brain.store.read().files["views/fila.md"])

    def test_case_and_accent_variant_applies_through_actual_worker_without_rejection(self):
        raw, source = self.answer()
        response = SemanticWorker(self.brain, FixtureInterpreter(lambda source: self.marker(source, "DIARIO DE OBRA"))).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertNotIn("rejection_reason", response)
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["pending_jobs"], [])
        self.assertEqual(next(item for item in snapshot["sources"] if item["id"] == raw["source_id"]), source)
        self.assertEqual(snapshot["recognition_markers"][0]["evidence"], [{"source_id": source["id"], "quote": source["text"]}])


if __name__ == "__main__":
    unittest.main()
