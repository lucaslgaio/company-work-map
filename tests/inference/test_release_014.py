"""External analyst export, rejection, no_work and a bare-repo capture loop."""
import hashlib
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from company_pilot.brain import Brain, LocalStore
from company_pilot.brain.engine import FIELDS
from company_pilot.brain.gitlocal import LocalGitStore
from company_pilot.desktop.collector import Collector, NativeFailure, flush
from company_pilot.desktop.github_transport import BrainTransport
from company_pilot.desktop.outbox import Outbox
from company_pilot.desktop.policy import Policy
from company_pilot.inference import ClaudeInterpreter, InterpreterError
from company_pilot.inference.external import export_jobs, submit_analysis
from tests.inference.test_claude import FakeRunner, analysis, stream


def uid():
    return str(uuid.uuid4())


def observation(company, text, title="Planilha da oficina"):
    return {"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "policy_version": company["policy_version"], "synthetic": True,
            "classification": {"label": "company", "reason": "work_computer_default",
                               "context_version": company["context_version"]},
            "source": {"app": "com.example.Sheets", "kind": "desktop_window", "origin": "synthetic",
                       "title": title, "record_ref": None},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def activity(source_id, quote, name):
    fields = {key: {"value": None, "basis": "inferred", "evidence": []} for key in FIELDS}
    fields["action"] = {"value": name, "basis": "observed", "evidence": [{"source_id": source_id, "quote": quote}]}
    return {"tasks": [{"task_id": None, "name": name, "fields": fields, "links": []}],
            "context_updates": [], "nodes": [], "next_question": None}


class ExternalAnalystTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.brain = Brain(LocalStore(self.root / "store", actor="fundador-sintetico"))
        self.brain.initialize("Oficina Leste", synthetic=True)
        self.brain.assign_external_analyst("analista-sintetico", uid())
        self.operator = Brain(LocalStore(self.root / "store", actor="analista-sintetico"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_reject_and_resubmit(self):
        company = self.brain.snapshot()["company"]
        text = "Medição da laje conferida na planilha."
        self.brain.ingest_observation(observation(company, text))
        exported = export_jobs(self.operator, self.root / "jobs")
        self.assertEqual(exported["pending"], 1)
        job_file = next(path for path in (self.root / "jobs").glob("*.json") if path.name != "index.json")
        payload = json.loads(job_file.read_text(encoding="utf-8"))
        self.assertIn("contract", payload)
        self.assertIn("schema", payload["contract"])
        self.assertIn("no_work", payload["contract"]["instructions"])
        self.assertEqual(payload["sources"][0]["text"], text)
        bad = activity(payload["job"]["source_ids"][0], "frase que nao esta na fonte", "Inventar medição")
        refused = submit_analysis(self.operator, payload["job"]["id"], bad)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["rejection_reason"], "source_quote_not_found")
        self.assertIn("trecho", refused["mensagem"])
        self.assertTrue(refused["quote_failures"])
        good = activity(payload["job"]["source_ids"][0], "Medição da laje", "Conferir a laje")
        applied = submit_analysis(self.operator, payload["job"]["id"], good)
        self.assertTrue(applied["ok"], applied)
        page = self.operator.store.read().files["views/mapa.md"]
        self.assertIn("Conferir a laje", page)
        self.assertEqual(self.operator.analysis_queue(), [])

    def test_no_work_outcome_resolves_an_empty_capture(self):
        company = self.brain.snapshot()["company"]
        self.brain.ingest_observation(observation(company, "Nova aba", "Nova aba"))
        job = self.operator.analysis_queue()[0]
        empty = {"tasks": [], "context_updates": [], "nodes": [], "next_question": None}
        refused = submit_analysis(self.operator, job["id"], empty)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["rejection_reason"], "observation_task_bound_required")
        applied = submit_analysis(self.operator, job["id"], {**empty, "outcome": "no_work"})
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["receipt"]["outcome"], "no_work")
        self.assertEqual(self.operator.snapshot()["tasks"], [])

    def test_interview_answer_stays_on_the_founder_and_is_read_by_the_operator(self):
        self.brain.interview_next()
        text = "A oficina entrega lajes medidas para a obra."
        stored = self.brain.interview_answer(text, uid())
        self.assertEqual(stored["state"], "pending_analysis")
        job = self.operator.analysis_queue()[0]
        self.assertEqual(job["kind"], "interview")
        result = {"tasks": [], "nodes": [], "next_question": None, "context_updates": [{
            "topic": "activities", "value": text, "basis": "reported", "mode": "add",
            "evidence": [{"source_id": stored["source_id"], "quote": text}]}]}
        applied = submit_analysis(self.operator, job["id"], result)
        self.assertTrue(applied["ok"], applied)
        self.assertIn("lajes medidas", self.operator.snapshot()["context"]["activities"]["value"])

    def test_claude_accepts_no_work_and_still_rejects_a_silent_empty_task_list(self):
        job = {"id": "observation-job", "kind": "observation", "source_ids": ["source"], "task_ids": []}
        context = {"tasks": [], "sources": [{"id": "source", "kind": "observation", "text": "Nova aba"}]}
        accepted = {**analysis(tasks=[]), "outcome": "no_work"}
        parsed = ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(accepted))).interpret(job, context)
        self.assertEqual(parsed["outcome"], "no_work")
        self.assertEqual(parsed["tasks"], [])
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            ClaudeInterpreter("/fake/claude", runner=FakeRunner(output=stream(analysis(tasks=[])))).interpret(job, context)


class BareRepoLoopTests(unittest.TestCase):
    def test_founder_capture_reaches_the_map_through_the_external_operator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bare, mirror = root / "records.git", root / "operator.git"
            founder = Brain(LocalGitStore(bare, actor="fundador-sintetico"))
            founder.initialize("Oficina Leste", synthetic=True)
            founder.assign_external_analyst("analista-sintetico", uid())
            company = founder.snapshot()["company"]
            version = company["policy_version"]
            now = datetime.now(timezone.utc)
            policy = Policy.parse({
                "schema_version": 1, "policy_id": company["id"], "version": version,
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "participant_kind": "founder", "revoked": False,
                "screenshots": {"founder": True, "team": False},
                "capture_allowed": True, "context_version": company["context_version"], "context": {},
            })
            box = Outbox(root / "outbox.sqlite")
            try:
                native = _Native()
                device = "00000000-0000-4000-8000-000000000014"
                collector = Collector(device, native, box, lambda: policy, lambda: now, episode_seconds=0)
                native.surface.update(title="Nubank", app_id="br.com.nubank", text="saldo secreto", window_id="1")
                self.assertEqual(collector.tick()["status"], "excluded")
                native.surface.update(title="Nova aba", app_id="com.apple.finder", text="Nova aba", window_id="2")
                self.assertEqual(collector.tick()["classification"], "company")
                native.image_error = "screen_permission_denied"
                native.surface.update(title="Ajustes do Sistema", app_id="com.apple.systempreferences",
                                       text="Ajustes do Sistema", window_id="3")
                denied = collector.tick()
                self.assertEqual(denied["classification"], "company")
                native.image_error = None
                native.surface.update(title="Planilha da laje", app_id="com.example.Sheets",
                                       text="Medição da laje conferida na planilha.", window_id="4")
                granted = collector.tick()
                self.assertEqual(granted["classification"], "company")
                transport = BrainTransport(founder)
                delivered = flush(box, transport, lambda: policy, clock=lambda: now)
                self.assertGreaterEqual(delivered["delivered"], 3)
            finally:
                box.close()
            operator = Brain(LocalGitStore(mirror, actor="analista-sintetico", remote=bare))
            exported = export_jobs(operator, root / "jobs")
            self.assertGreaterEqual(exported["pending"], 3)
            files = operator.store.read().files
            sources = [json.loads(files[path]) for path in files if path.startswith("sources/") and path.endswith(".json")]
            observations = [source for source in sources if source.get("kind") == "observation"]
            self.assertTrue(any("saldo secreto" not in source.get("text", "") for source in observations))
            self.assertFalse(any("saldo secreto" in source.get("text", "") for source in observations))
            denied_source = next(source for source in observations if "Ajustes do Sistema" in source["text"])
            granted_source = next(source for source in observations if "Medição da laje" in source["text"])
            self.assertIsNone(denied_source.get("attachment"))
            self.assertIn("screen_permission_denied", denied_source["observation"]["source"]["capture_gaps"])
            self.assertTrue(granted_source.get("attachment", {}).get("path", "").startswith("attachments/"))
            self.assertIn(granted_source["attachment"]["path"], files)
            for path in (root / "jobs").glob("*.json"):
                if path.name == "index.json":
                    continue
                body = json.loads(path.read_text(encoding="utf-8"))
                source = body["sources"][0]
                if source["text"] in ("Nova aba", "Ajustes do Sistema"):
                    result = {"tasks": [], "context_updates": [], "nodes": [], "next_question": None, "outcome": "no_work"}
                else:
                    result = activity(source["id"], "Medição da laje", "Conferir a laje")
                applied = submit_analysis(operator, body["job"]["id"], result)
                self.assertTrue(applied["ok"], applied)
            page = founder.store.read().files["views/mapa.md"]
            self.assertIn("Conferir a laje", page)
            self.assertNotIn("Nova aba", page)
            self.assertNotIn("saldo secreto", page)


class _Native:
    def __init__(self):
        self.surface = {"app_id": "com.example.Sheets", "window_id": "4", "process_id": 7,
                        "title": "Planilha", "record_ref": "", "text": "janela",
                        "evidence_level": "synthetic", "secure_context": False}
        self.image_error = None

    def context(self):
        return self.surface.copy()

    def text(self, _expected):
        return self.surface.copy()

    def screenshot(self, _expected):
        if self.image_error:
            raise NativeFailure(self.image_error)
        return b"\x89PNG\r\n\x1a\nsynthetic-only"


if __name__ == "__main__":
    unittest.main()
