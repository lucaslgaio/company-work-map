"""Screen Recording instructions name the capturing binary, and granted images are stored."""
from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from company_pilot.brain import Brain, LocalStore
from company_pilot.desktop.collector import Collector, flush
from company_pilot.desktop.github_transport import BrainTransport
from company_pilot.desktop.outbox import Outbox
from company_pilot.desktop.policy import Policy
from test_collector import DEVICE, NOW, NativeStub, policy


class ScreenshotInstructionTests(unittest.TestCase):
    def test_denied_permission_names_the_binary_and_the_go_to_folder_shortcut(self):
        from company_pilot.desktop.__main__ import screen_capture_binary, screenshot_report
        from company_pilot.desktop.policy import utcnow
        current = replace(policy(True), expires_at=utcnow() + timedelta(hours=1))
        binary = screen_capture_binary("Darwin")
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "queue.sqlite")
            try:
                report = screenshot_report(
                    {"capture_enabled": True}, current, box,
                    {"platform": "macOS", "accessibility_granted": True, "screen_recording_granted": False})
            finally:
                box.close()
        self.assertFalse(report["actually_taking"])
        self.assertTrue(report["text_capture_continues"])
        self.assertEqual(report["binary_path"], str(binary))
        self.assertIn(str(binary), report["next_step"])
        self.assertIn("Cmd+Shift+G", report["next_step"])
        self.assertIn("botão +", report["next_step"])
        self.assertIn("Privacidade e Segurança", report["next_step"])
        self.assertIn("Gravação de Tela", report["next_step"])
        self.assertIn("launchctl kickstart", report["next_step"])
        self.assertNotIn("o Python que executa", report["next_step"])


class AttachmentLandingTests(unittest.TestCase):
    def test_granted_image_is_referenced_and_denied_permission_keeps_text_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            brain = Brain(LocalStore(root / "store"))
            brain.initialize("Oficina Leste", synthetic=True)
            company = brain.snapshot()["company"]
            now = datetime.now(timezone.utc)
            current = Policy.parse({
                "schema_version": 1, "policy_id": company["id"], "version": company["policy_version"],
                "expires_at": (now + timedelta(hours=1)).isoformat(), "participant_kind": "founder",
                "revoked": False, "screenshots": {"founder": True, "team": False},
                "capture_allowed": True, "context": {},
            })
            box = Outbox(root / "queue.sqlite")
            try:
                native = NativeStub("Planilha da oficina")
                native.surface.update(text="Texto sem imagem", record_ref="", window_id="1")
                native.image_error = "screen_permission_denied"
                collector = Collector(DEVICE, native, box, lambda: current, lambda: now, episode_seconds=0)
                self.assertEqual(collector.tick()["classification"], "company")
                native.image_error = None
                native.surface.update(text="Texto com imagem", window_id="2")
                self.assertEqual(collector.tick()["classification"], "company")
                flush(box, BrainTransport(brain), lambda: current, clock=lambda: now)
            finally:
                box.close()
            files = brain.store.read().files
            sources = [json.loads(files[path]) for path in files if path.startswith("sources/") and path.endswith(".json")]
            denied = next(source for source in sources if source.get("kind") == "observation" and "Texto sem imagem" in source["text"])
            granted = next(source for source in sources if source.get("kind") == "observation" and "Texto com imagem" in source["text"])
            self.assertIsNone(denied.get("attachment"))
            self.assertIn("screen_permission_denied", denied["observation"]["source"]["capture_gaps"])
            self.assertTrue(granted["attachment"]["path"].startswith("attachments/"))
            self.assertIn(granted["attachment"]["path"], files)
            self.assertEqual(hashlib.sha256(b"\x89PNG\r\n\x1a\nsynthetic-only").hexdigest(), granted["attachment"]["sha256"])


if __name__ == "__main__":
    unittest.main()
