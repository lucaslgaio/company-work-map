"""0.1.4: recognition scope, raw rejection codes, no_work and the external analyst."""
import hashlib
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from company_pilot.brain import Brain, LocalStore
from company_pilot.brain.engine import FIELDS
from company_pilot.brain.stores import AccessDenied, BrainError
from company_pilot.inference.worker import SemanticWorker


def uid():
    return str(uuid.uuid4())


class Scripted:
    last_metadata = None

    def __init__(self, build):
        self.build = build

    def interpret(self, job, context):
        return self.build(job, context)


def observation(company, text, title):
    return {"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "policy_version": company["policy_version"], "synthetic": True,
            "classification": {"label": "company", "reason": "work_computer_default",
                               "context_version": company["context_version"]},
            "source": {"app": "com.example.Sheets", "kind": "desktop_window", "origin": "synthetic",
                       "title": title, "record_ref": None, "observed_until": None},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def activity(source_id, quote, name="Conferir a medição"):
    fields = {key: {"value": None, "basis": "inferred", "evidence": []} for key in FIELDS}
    fields["action"] = {"value": name, "basis": "observed", "evidence": [{"source_id": source_id, "quote": quote}]}
    return {"tasks": [{"task_id": None, "name": name, "fields": fields, "links": []}],
            "context_updates": [], "nodes": [], "next_question": None}


class RecognitionScopeTests(unittest.TestCase):
    def test_observation_applies_after_founder_confirms_recognition_names(self):
        with tempfile.TemporaryDirectory() as temp:
            brain = Brain(LocalStore(Path(temp)))
            brain.initialize("Oficina Leste", synthetic=True)
            raw = brain.interview_answer("A Oficina Leste acompanha o Galpao Azul.", uid())
            src = next(item for item in brain.snapshot()["sources"] if item["id"] == raw["source_id"])
            evidence = [{"source_id": src["id"], "quote": src["text"]}]
            brain.apply_analysis(raw["job_id"], {"tasks": [], "context_updates": [], "next_question": None, "nodes": [
                {"kind": "organization", "name": "Oficina Leste", "evidence": evidence},
                {"kind": "project", "name": "Galpao Azul", "evidence": evidence}]})
            brain.confirm_recognition_names([
                {"value": "Oficina Leste", "kind": "company_alias"},
                {"value": "Galpao Azul", "kind": "record_name"}], uid())
            company = brain.snapshot()["company"]
            text = "Medição do Galpao Azul conferida na planilha."
            brain.ingest_observation(observation(company, text, "Medição do Galpao Azul"))

            def build(job, _context):
                return activity(job["source_ids"][0], "Medição do Galpao Azul")

            result = SemanticWorker(brain, Scripted(build)).run_once()
            self.assertEqual(result["status"], "applied", result)
            self.assertIn("Conferir a medição", brain.store.read().files["views/mapa.md"])

    def test_unknown_rejection_code_is_stored_raw_and_free_text_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            brain = Brain(LocalStore(Path(temp)))
            brain.initialize("Oficina Leste", synthetic=True)
            company = brain.snapshot()["company"]
            receipt = brain.ingest_observation(observation(company, "Texto da janela de trabalho.", "Planilha"))

            def explode(*_args, **_kwargs):
                raise BrainError("totally_new_code")

            brain.apply_analysis = explode
            result = SemanticWorker(brain, Scripted(lambda job, _context: activity(job["source_ids"][0], "Texto da janela"))).run_once()
            self.assertEqual(result["error_code"], "analysis_rejected")
            self.assertEqual(result["rejection_reason"], "totally_new_code")
            job = brain.snapshot()["pending_jobs"][0]
            self.assertEqual(job["rejection_reason"], "totally_new_code")
            self.assertEqual(job["reason"], "totally_new_code")
            with self.assertRaises(BrainError):
                brain.mark_job_error(receipt["job_id"], "analysis_rejected", rejection_reason="texto livre da fonte")

    def test_no_work_resolves_and_an_empty_task_list_without_it_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            brain = Brain(LocalStore(Path(temp)))
            brain.initialize("Oficina Leste", synthetic=True)
            company = brain.snapshot()["company"]
            empty = brain.ingest_observation(observation(company, "Nova aba\nFinder", "Nova aba"))
            with self.assertRaises(BrainError):
                brain.apply_analysis(empty["job_id"], {"tasks": [], "context_updates": [], "nodes": [], "next_question": None})
            receipt = brain.apply_analysis(empty["job_id"], {"tasks": [], "context_updates": [], "nodes": [], "next_question": None, "outcome": "no_work"})
            self.assertEqual(receipt["outcome"], "no_work")
            self.assertEqual(receipt["tasks"], [])
            self.assertEqual(brain.snapshot()["tasks"], [])
            self.assertEqual([job for job in brain.snapshot()["pending_jobs"] if job["id"] == empty["job_id"]], [])

    def test_external_analyst_resets_backoff_and_the_founder_cannot_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brain = Brain(LocalStore(root, actor="fundador-sintetico"))
            brain.initialize("Oficina Leste", synthetic=True)
            company = brain.snapshot()["company"]
            receipt = brain.ingest_observation(observation(company, "Texto da janela de trabalho.", "Planilha"))
            brain.mark_job_error(receipt["job_id"], "analysis_rejected", rejection_reason="source_quote_not_found")
            self.assertEqual(brain.pending_jobs(), [])
            brain.assign_external_analyst("analista-sintetico", uid())
            with self.assertRaises(AccessDenied):
                brain.pending_jobs()
            with self.assertRaises(AccessDenied):
                brain.apply_analysis(receipt["job_id"], activity(receipt["observation_id"], "Texto da janela"))
            operator = Brain(LocalStore(root, actor="analista-sintetico"))
            queued = operator.pending_jobs()
            self.assertEqual([job["id"] for job in queued], [receipt["job_id"]])
            self.assertEqual(operator.snapshot()["company"]["semantic_workers"], ["analista-sintetico"])
            self.assertIn("fundador-sintetico", operator.snapshot()["company"]["founders"])


if __name__ == "__main__":
    unittest.main()
