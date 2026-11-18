"""F18 actual caller: explicit simulated identity is not the CLI's claimed model."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.inference import ClaudeInterpreter, InterpreterError, SemanticWorker
from tests.inference.test_claude import FakeRunner, analysis, stream


ARTIFACT_ROOT = Path(tempfile.mkdtemp(prefix="a44-provenance-"))


class Round4ProvenanceTests(unittest.TestCase):
    def parse_metadata(self, runner, provenance=None, default_runner=False):
        kwargs = {"execution_provenance": provenance} if provenance is not None else {}
        job = {"id": "metadata-job", "kind": "observation", "source_ids": ["metadata-source"], "task_ids": []}
        context = {"tasks": [], "sources": [{"id": "metadata-source", "kind": "observation", "text": "Fixture de metadados."}]}
        if default_runner:
            with patch("company_pilot.inference.claude._bounded_run", runner):
                interpreter = ClaudeInterpreter("/fake/claude", **kwargs)
        else:
            interpreter = ClaudeInterpreter("/fake/claude", runner=runner, **kwargs)
        interpreter.interpret(job, context)
        return interpreter.last_metadata

    def test_injected_runner_without_declaration_keeps_actual_identity_unknown(self):
        events = [json.loads(line) for line in stream(analysis()).splitlines()]
        events[0]["provider"] = "anthropic"
        metadata = self.parse_metadata(FakeRunner(output="\n".join(map(json.dumps, events))))
        self.assertEqual(metadata["execution_origin"], "injected")
        self.assertEqual(metadata["execution_identity_basis"], "unreported")
        self.assertIsNone(metadata["execution_provider"])
        self.assertIsNone(metadata["execution_model"])
        self.assertEqual(metadata["reported_provider"], "anthropic")
        self.assertEqual(metadata["reported_model"], "claude-opus-5-5")

    def test_cli_model_prefix_and_auth_provider_do_not_invent_execution_provider(self):
        metadata = self.parse_metadata(FakeRunner(output=stream(analysis())), default_runner=True)
        self.assertEqual(metadata["execution_origin"], "cli")
        self.assertEqual(metadata["execution_identity_basis"], "reported")
        self.assertIsNone(metadata["execution_provider"])
        self.assertEqual(metadata["execution_model"], "claude-opus-5-5")

    def test_explicit_cli_report_stays_reported_and_configured_shim_identity_wins(self):
        events = [json.loads(line) for line in stream(analysis()).splitlines()]
        events[0]["provider"] = "claimed-provider"
        runner = FakeRunner(output="\n".join(map(json.dumps, events)))
        reported = self.parse_metadata(runner, default_runner=True)
        self.assertEqual(reported["execution_provider"], "claimed-provider")
        self.assertEqual(reported["execution_identity_basis"], "reported")
        configured = self.parse_metadata(runner, {"provider": "xai-simulated", "model": "grok-fixture", "origin": "shim"})
        self.assertEqual(configured["execution_provider"], "xai-simulated")
        self.assertEqual(configured["execution_model"], "grok-fixture")
        self.assertEqual(configured["execution_origin"], "shim")
        self.assertEqual(configured["execution_identity_basis"], "configured")
        self.assertEqual(configured["reported_provider"], "claimed-provider")

    def test_untrusted_model_output_cannot_configure_execution_identity(self):
        output = analysis()
        output["execution_provenance"] = {"provider": "forged", "model": "forged", "origin": "queue"}
        with self.assertRaisesRegex(InterpreterError, "^invalid_analysis$"):
            self.parse_metadata(FakeRunner(output=stream(output)))

    def test_configured_identity_is_bounded_and_errors_never_echo_values(self):
        for invalid in ({"provider": "x" * 161, "model": "safe", "origin": "queue"},
                        {"provider": "safe", "model": "private\nvalue", "origin": "queue"},
                        {"provider": "safe", "model": "safe", "origin": "guessed"},
                        {"provider": "safe", "model": "safe", "origin": "queue", "token": "private-value"}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(InterpreterError, "^invalid_execution_provenance$"):
                ClaudeInterpreter("/fake/claude", runner=FakeRunner(), execution_provenance=invalid)

    def test_f18_configured_queue_identity_survives_actual_worker_and_storage_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            brain = Brain(LocalStore(Path(directory)))
            brain.initialize("Empresa sintética — proveniência round4", synthetic=True)
            text = "Ana conferiu o registro empresarial sintético."
            raw = brain.ingest_observation({"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()),
                "occurred_at": "2026-11-27T12:00:00+00:00", "policy_version": 1,
                "source": {"kind": "active_window", "app": "Synthetic Work Viewer", "record_ref": "PROVENANCE-SYN"},
                "classification": {"label": "company", "reason": "fixture sintética autorizada"},
                "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True})
            fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
            fields["action"] = {"value": "Conferir registro", "basis": "observed", "evidence": [
                {"source_id": raw["observation_id"], "quote": text}]}
            decision = {"tasks": [{"task_id": None, "name": "Conferir registro", "fields": fields, "links": []}],
                        "nodes": [], "context_updates": [], "next_question": None}
            (ARTIFACT_ROOT / "before.json").write_text(json.dumps(brain.snapshot(), ensure_ascii=False, indent=2) + "\n")
            try:
                runner = FakeRunner(output=stream(decision))
                interpreter = ClaudeInterpreter("/fake/claude", runner=runner, execution_provenance={
                    "provider": "xai-simulated", "model": "grok-bot-round4", "origin": "queue"})
                response = SemanticWorker(brain, interpreter).run_once()
                self.assertEqual(response["status"], "applied", response)
                metadata = interpreter.last_metadata
                (ARTIFACT_ROOT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
                self.assertEqual(metadata["execution_provider"], "xai-simulated")
                self.assertEqual(metadata["execution_model"], "grok-bot-round4")
                self.assertEqual(metadata["execution_origin"], "queue")
                self.assertEqual(metadata["execution_identity_basis"], "configured")
                self.assertEqual(metadata["reported_model"], "claude-opus-5-5")
                self.assertIsNone(metadata["reported_provider"])
                self.assertEqual(metadata["model"], "claude-opus-5-5")
                source = next(source for source in brain.snapshot()["sources"] if source["kind"] == "analysis")
                self.assertEqual(source["provider"], "xai-simulated")
                self.assertEqual(source["storage_provider"], "local_fixture")
                self.assertEqual(source["interpretation"]["execution_origin"], "queue")
                events = [json.loads(content) for path, content in brain.store.read().files.items() if path.startswith("ledger/")]
                analysis_event = next(event for event in events if event["kind"] == "analysis")
                self.assertEqual(analysis_event["provider"], "xai-simulated")
                self.assertEqual(analysis_event["storage_provider"], "local_fixture")
                (ARTIFACT_ROOT / "analysis-source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n")
                (ARTIFACT_ROOT / "analysis-ledger.json").write_text(json.dumps(analysis_event, ensure_ascii=False, indent=2) + "\n")
                rendered = brain.store.read().files.get(f"views/sources/{source['id']}.md")
                if rendered:
                    (ARTIFACT_ROOT / "analysis-source-card.md").write_text(rendered)
            finally:
                (ARTIFACT_ROOT / "after.json").write_text(json.dumps(brain.snapshot(), ensure_ascii=False, indent=2) + "\n")
                print("PROVENANCE_ARTIFACTS: " + str(ARTIFACT_ROOT))


if __name__ == "__main__":
    unittest.main()
