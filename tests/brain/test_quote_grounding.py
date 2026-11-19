"""Quote grounding keeps the founder's original span and still rejects inventions."""
import copy
import json
import unicodedata
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, LocalStore
from company_pilot.brain.quotes import MANUAL_INTERVIEW_MESSAGE, locate_source_span
from company_pilot.cli import main
from company_pilot.inference import ClaudeInterpreter, InterpreterError, SemanticWorker
from company_pilot.operator import Operator, queue_status, write_private_json
from tests.inference.test_claude import FakeRunner, analysis, stream


def nfd(text):
    return unicodedata.normalize("NFD", text)


def nfc(text):
    return unicodedata.normalize("NFC", text)


SOURCE = "A equipe entrega o relatório — «pronto» — na sexta.\n\nO cliente confere o pagamento da revisão v1.2."
FUZZY_SOURCE = "Precisamos entregar o relatório mensal ao cliente amanhã de manhã."


class QuoteNormalizationTests(unittest.TestCase):
    def test_nfd_accent_matches_nfc_source_and_returns_the_original_slice(self):
        quote = nfd("o relatório")
        self.assertNotEqual(quote, "o relatório")
        span = locate_source_span(SOURCE, quote)
        self.assertEqual(span, "o relatório")
        self.assertIn(span, SOURCE)

    def test_nfc_quote_matches_nfd_macos_source(self):
        source = nfd("O relatório segue com a equipe nesta semana.")
        self.assertIn("\u0301", source)
        span = locate_source_span(source, "O RELATÓRIO SEGUE")
        self.assertEqual(span, nfd("O relatório segue"))
        self.assertIn(span, source)
        self.assertNotEqual(span, "O RELATÓRIO SEGUE")

    def test_curly_quotes_dashes_whitespace_case_and_minor_punctuation(self):
        curly = "Ele disse “pronto” hoje e a equipe seguiu o trabalho."
        self.assertEqual(locate_source_span(curly, 'Ele disse "pronto" hoje'), "Ele disse “pronto” hoje")
        self.assertEqual(locate_source_span(SOURCE, 'o relatório - "pronto"'), "o relatório — «pronto»")
        self.assertEqual(locate_source_span(SOURCE, "na sexta. O cliente"), "na sexta.\n\nO cliente")
        self.assertEqual(locate_source_span(SOURCE, "A EQUIPE ENTREGA O RELATÓRIO"), "A equipe entrega o relatório")
        self.assertEqual(locate_source_span(SOURCE, "na sexta O cliente confere"), "na sexta.\n\nO cliente confere")
        self.assertEqual(locate_source_span("fim… agora segue o relatório", "fim... agora segue"), "fim… agora segue")

    def test_internal_version_dot_is_not_ignored(self):
        self.assertIsNone(locate_source_span(SOURCE, "revisão v12"))
        self.assertEqual(locate_source_span(SOURCE, "revisão v1.2"), "revisão v1.2")

    def test_unique_typo_uses_the_source_word_and_a_word_swap_does_not(self):
        span = locate_source_span(FUZZY_SOURCE, "Precisamos entregar o relatóio mensal ao cliente amanhã de manhã.")
        self.assertEqual(span, FUZZY_SOURCE)
        self.assertIsNone(locate_source_span(FUZZY_SOURCE, "Precisamos revisar o relatório mensal ao cliente amanhã de manhã."))

    def test_ambiguous_typo_and_invented_quote_do_not_match(self):
        doubled = FUZZY_SOURCE + " " + FUZZY_SOURCE.replace("relatório", "relatorio")
        self.assertIsNone(locate_source_span(doubled, "Precisamos entregar o relatóio mensal ao cliente amanhã de manhã."))
        self.assertIsNone(locate_source_span(SOURCE, "A empresa exporta café especial para Lisboa desde 1999."))


class QuoteGroundingBrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética de citações", synthetic=True)

    def answer(self, text):
        receipt = self.brain.interview_answer(text, str(uuid4()))
        source = next(item for item in self.brain.snapshot()["sources"] if item["id"] == receipt["source_id"])
        return receipt, source

    def test_normalized_interview_quote_is_stored_as_the_exact_source_span(self):
        raw, source = self.answer(nfd(SOURCE))
        model_quote = "A EQUIPE ENTREGA O RELATÓRIO - \"pronto\""
        self.assertNotIn(model_quote, source["text"])
        result = {"tasks": [], "nodes": [], "next_question": None, "context_updates": [{
            "topic": "activities", "value": "Entrega do relatório", "basis": "reported",
            "evidence": [{"source_id": source["id"], "quote": model_quote}]}]}
        receipt = self.brain.apply_analysis(raw["job_id"], result)
        self.assertEqual(receipt["state"], "applied")
        stored = self.brain.snapshot()["context"]["activities"]["evidence"][0]["quote"]
        self.assertIn(stored, source["text"])
        self.assertEqual(stored, nfd("A equipe entrega o relatório — «pronto»"))
        self.assertNotEqual(stored, model_quote)

    def test_invented_quote_is_still_rejected_and_the_retry_names_it(self):
        raw, source = self.answer(SOURCE)
        invented = "A empresa exporta café especial para Lisboa desde 1999."
        worker = SemanticWorker(self.brain, _Scripted(lambda _source: _update(source, invented)))
        response = worker.run_once()
        self.assertEqual(response["error_code"], "analysis_rejected")
        self.assertEqual(response["rejection_reason"], "source_quote_not_found")
        self.assertEqual(self.brain.snapshot()["context"], {})
        job = self.brain.snapshot()["pending_jobs"][0]
        self.assertEqual(job["quote_failures"], [{"source_id": source["id"], "quote": invented, "reason": "source_quote_not_found"}])
        seen = []

        class Recording:
            def interpret(self, job, context):
                seen.append(copy.deepcopy(job))
                raise InterpreterError("model_unavailable")

        job["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
        self.brain.pending_jobs = lambda: [copy.deepcopy(job)]
        SemanticWorker(self.brain, Recording()).run_once()
        self.assertEqual(seen[0]["quote_failures"][0]["reason"], "source_quote_not_found")
        self.assertIn(invented, seen[0]["quote_failures"][0]["quote"])
        payload = _prompt_payload({**job, "kind": "interview", "task_ids": []}, source)
        self.assertEqual(payload["rejection_reason"], "source_quote_not_found")
        self.assertEqual(payload["quote_failures"][0]["quote"], invented)
        self.assertIn("source_quote_not_found", payload["request"])
        self.assertIn("quote_failures", payload["request"])

    def test_one_invented_interview_claim_is_dropped_and_the_grounded_claim_remains(self):
        raw, source = self.answer(SOURCE)
        invented = "A empresa exporta café especial para Lisboa desde 1999."
        result = {"tasks": [], "nodes": [], "next_question": {"topic": "people", "text": "Quem confere o pagamento?"},
                  "context_updates": [
                      {"topic": "activities", "value": "Entrega do relatório", "basis": "reported",
                       "evidence": [{"source_id": source["id"], "quote": "A EQUIPE ENTREGA O RELATÓRIO"}]},
                      {"topic": "tracking", "value": "Exportação inventada", "basis": "reported",
                       "evidence": [{"source_id": source["id"], "quote": invented}]}]}
        receipt = self.brain.apply_analysis(raw["job_id"], result)
        self.assertEqual(receipt["state"], "applied")
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["context"]["activities"]["evidence"][0]["quote"], "A equipe entrega o relatório")
        self.assertNotIn("tracking", snapshot["context"])
        self.assertEqual(receipt["omitted_quotes"][0]["quote"], invented)
        self.assertEqual(receipt["omitted_quotes"][0]["reason"], "source_quote_not_found")
        self.assertEqual(snapshot["pending_jobs"], [])

    def test_repeated_interview_rejection_recommends_the_manual_route(self):
        raw, _source = self.answer(SOURCE)
        for _attempt in range(2):
            self.brain.mark_job_error(raw["job_id"], "analysis_rejected", rejection_reason="source_quote_not_found")
        self.assertNotIn("recommended_route", self.brain.interview_next())
        self.brain.mark_job_error(raw["job_id"], "analysis_rejected", rejection_reason="source_quote_not_found")
        self.assertEqual(self.brain.pending_jobs(), [])
        progress = self.brain.interview_next()
        self.assertEqual(progress["state"], "pending_analysis")
        self.assertEqual(progress["recommended_route"], "manual")
        self.assertEqual(progress["founder_message"], MANUAL_INTERVIEW_MESSAGE)
        self.assertTrue(any(command.endswith("operator commit-literal") for command in progress["manual_commands"]))
        self.assertIn("operator prompt", progress["founder_message"])
        self.assertIn(MANUAL_INTERVIEW_MESSAGE, self.brain.store.read().files["views/fila.md"])
        status = queue_status(self.brain.snapshot(), datetime(2026, 11, 19, tzinfo=timezone.utc))
        self.assertEqual(status["recommended_route"], "manual")
        self.assertIn("operator commit-literal", status["summary"])
        self.assertNotIn("relatório", status["summary"])
        root = Path(self.temp.name)
        config_dir = root / "cli"
        config, health = config_dir / "company.json", config_dir / "health.json"
        write_private_json(config, {"schema_version": 1, "mode": "synthetic",
                                    "store": {"kind": "local", "root": str(root)},
                                    "operator": {"health_file": str(health)}})
        out = StringIO()
        self.assertEqual(main(["--config", str(config), "status"], out=out, err=StringIO()), 0)
        reported = json.loads(out.getvalue())
        self.assertEqual(reported["version"], "0.1.2")
        self.assertEqual(reported["recommended_route"], "manual")
        self.assertEqual(reported["founder_message"], MANUAL_INTERVIEW_MESSAGE)
        self.assertTrue(any("operator apply" in command for command in reported["manual_commands"]))
        operator = Operator(SemanticWorker(self.brain, _Scripted(lambda _source: (_ for _ in ()).throw(AssertionError("blind retry")))),
                            brain=self.brain, health_file=health)
        once = operator.once()
        self.assertEqual(once["processing"]["recommended_route"], "manual")
        health_out = StringIO()
        self.assertEqual(main(["--config", str(config), "operator", "health"], out=health_out, err=StringIO()), 0)
        health_report = json.loads(health_out.getvalue())
        self.assertEqual(health_report["processing"]["recommended_route"], "manual")
        self.assertIn("operator commit-literal", health_report["processing"]["summary"])
        self.assertNotIn(SOURCE, health_out.getvalue())


def _update(source, quote, topic="activities", value="Fato"):
    return {"tasks": [], "nodes": [], "context_updates": [{
        "topic": topic, "value": value, "basis": "reported",
        "evidence": [{"source_id": source["id"], "quote": quote}]}], "next_question": None}


class _Scripted:
    def __init__(self, builder):
        self.builder = builder

    def interpret(self, job, context):
        source = next(item for item in context["sources"] if item["id"] == job["source_ids"][0])
        return self.builder(source)


def _prompt_payload(job, source):
    runner = FakeRunner(output=stream(analysis(tasks=[])))
    interpreter = ClaudeInterpreter("/fake/claude", runner=runner)
    context = {"company": {}, "context": {}, "sources": [{"id": source["id"], "text": source["text"]}],
               "tasks": [], "questions": []}
    interpreter.interpret(job, context)
    return json.loads(runner.calls[-1][1]["input"])


if __name__ == "__main__":
    unittest.main()
