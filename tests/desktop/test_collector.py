from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

from company_pilot.desktop.collector import Collector, NativeFailure, PermanentDeliveryError, flush
from company_pilot.desktop.native import NativeAdapter
from company_pilot.desktop.outbox import CapacityError, Outbox
from company_pilot.desktop.policy import Policy
from company_pilot.desktop.transport import DirectoryFixtureTransport
from install.bootstrap import startup_files

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
DEVICE = "00000000-0000-4000-8000-000000000001"


def policy(images=True):
    return Policy.parse({"schema_version": 1, "policy_id": "test-policy", "version": 1,
                         "expires_at": (NOW + timedelta(hours=1)).isoformat(), "participant_kind": "team",
                         "revoked": False, "screenshots": {"team": images, "founder": True},
                         "context": {"work_markers": ["SYNTHETIC-COMPANY"], "tool_hints": ["test.browser"],
                                     "record_refs": ["https://example.invalid/company"], "personal_markers": ["PRIVATE-SENTINEL"]}})


class NativeStub:
    def __init__(self, title="SYNTHETIC-COMPANY payment TEST-42"):
        self.surface = {"app_id": "test.browser", "window_id": "42", "process_id": 7,
                        "title": title, "record_ref": "", "text": "TEST-42 amount settled",
                        "evidence_level": "synthetic", "secure_context": False}
        self.context_calls = self.text_calls = self.image_calls = 0
        self.image_error = None
        self.after_image = None

    def context(self):
        self.context_calls += 1
        return self.surface.copy()

    def text(self, expected):
        self.text_calls += 1
        return self.surface.copy()

    def screenshot(self, expected):
        self.image_calls += 1
        if self.image_error:
            raise NativeFailure(self.image_error)
        if self.after_image:
            self.after_image()
        return b"\x89PNG\r\n\x1a\nsynthetic-only"


class TransportStub:
    def __init__(self):
        self.events = {}
        self.uploads = []
        self.fail_after_store = False
        self.deny = False

    def upload_attachment(self, event_id, data, digest):
        self.uploads.append((event_id, data, digest))
        return {"id": "opaque-evidence", "sha256": digest}

    def send_observation(self, payload):
        if self.deny:
            raise PermanentDeliveryError("contains SECRET which must not be logged")
        previous = self.events.get(payload["event_id"])
        if previous is not None and previous != payload:
            raise PermanentDeliveryError("changed payload")
        self.events[payload["event_id"]] = payload
        if self.fail_after_store:
            self.fail_after_store = False
            raise ConnectionError("lost response SECRET")
        return {"observation_id": payload["event_id"], "inspect_url": "fixture:" + payload["event_id"]}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "queue.sqlite"
        self.box = Outbox(self.path)
        self.current = policy()
        self.native = NativeStub()
        self.collector = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: NOW, episode_seconds=0)

    def tearDown(self):
        self.box.close()
        self.directory.cleanup()

    def payload(self):
        return json.loads(self.box.db.execute("SELECT payload FROM events ORDER BY created_at LIMIT 1").fetchone()[0])

    def test_team_false_never_calls_image_and_missing_decision_is_false(self):
        self.current = policy(False)
        self.assertEqual(self.collector.tick()["status"], "queued")
        self.assertEqual(self.native.image_calls, 0)
        self.assertNotIn("image_sha256", self.payload()["evidence"])
        raw = {"schema_version": 1, "policy_id": "p", "version": 1, "participant_kind": "team", "expires_at": (NOW + timedelta(hours=1)).isoformat(), "revoked": False}
        self.assertFalse(Policy.parse(raw).permits_image(NOW))

    def test_expired_or_revoked_policy_never_reads_surface(self):
        for changed in (replace(policy(), expires_at=NOW), replace(policy(), revoked=True)):
            self.current = changed
            self.assertEqual(self.collector.tick()["status"], "policy_unavailable")
        self.assertEqual(self.native.context_calls, 0)

    def test_app_without_confirmed_name_is_shared(self):
        self.native.surface.update(title="Cronograma da semana", text="Medição 80 por cento", record_ref="")
        self.current = replace(self.current, work_markers=(), record_refs=())
        self.assertEqual(self.collector.tick()["classification"], "company")
        body = self.payload()
        self.assertEqual(body["classification"]["reason"], "work_computer_default")
        self.assertIn("Medição 80 por cento", body["evidence"]["text"])
        self.assertGreater(self.native.text_calls, 0)
        self.assertEqual(self.box.quarantine_pending(NOW.timestamp()), [])

    def test_personal_secure_and_worker_are_excluded_even_with_company_marker(self):
        for change in ({"title": "SYNTHETIC-COMPANY PRIVATE-SENTINEL"}, {"secure_context": True}, {"origin": "company-pilot-semantic-worker"}):
            self.native = NativeStub()
            self.native.surface.update(change)
            collector = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: NOW)
            self.assertEqual(collector.tick()["status"], "excluded")
            self.assertEqual((self.native.text_calls, self.native.image_calls), (0, 0))
        self.assertEqual(self.box.status(), {})

    def test_record_reference_does_not_match_lookalike_prefix(self):
        self.native.surface.update(title="unknown", record_ref="https://example.invalid/company-secrets")
        lookalike = self.current.classify(self.native.surface, NOW)
        self.assertEqual(lookalike.state, "company")
        self.assertFalse(any(signal.startswith("record_ref:") for signal in lookalike.signals))
        self.native.surface["record_ref"] = "https://example.invalid/company/TEST-42"
        matched = self.current.classify(self.native.surface, NOW)
        self.assertEqual(matched.state, "company")
        self.assertTrue(any(signal.startswith("record_ref:") for signal in matched.signals))

    def test_native_timeout_retains_text_and_explicit_gap(self):
        self.native.image_error = "native_helper_timeout"
        self.assertEqual(self.collector.tick()["status"], "queued")
        body = self.payload()
        self.assertEqual(body["capture_gaps"], ["native_helper_timeout"])
        self.assertNotIn("image_sha256", body["evidence"])

    def test_focus_change_during_image_never_queues_image_or_private_surface(self):
        self.native.after_image = lambda: self.native.surface.update(title="PRIVATE-AFTER-FOCUS")
        self.assertEqual(self.collector.tick()["status"], "focus_changed")
        self.assertEqual(self.box.status(), {})

    def test_revocation_during_image_discards_image(self):
        self.native.after_image = lambda: setattr(self, "current", replace(self.current, screenshot_allowed=False))
        self.assertEqual(self.collector.tick()["status"], "queued")
        self.assertNotIn("image_sha256", self.payload()["evidence"])

    def test_unchanged_observation_dedup_survives_restart(self):
        self.current = policy(False)
        self.assertEqual(self.collector.tick()["status"], "queued")
        self.box.close()
        self.box = Outbox(self.path)
        self.collector.outbox = self.box
        self.assertEqual(self.collector.tick()["status"], "unchanged")
        self.assertEqual(self.box.status(), {"pending": 1})

    def test_same_event_id_cannot_change_payload_or_image(self):
        self.collector.tick()
        body = self.payload()
        body["source"]["title"] = "changed"
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.box.enqueue(body)

    def test_abandoned_lease_can_be_reclaimed_after_restart(self):
        self.collector.tick()
        first = self.box.claim(NOW.timestamp())
        self.assertIsNone(self.box.claim(NOW.timestamp() + 10))
        self.box.close()
        self.box = Outbox(self.path)
        second = self.box.claim(NOW.timestamp() + 61)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaisesRegex(RuntimeError, "lease_lost"):
            self.box.update_claim(first, state="delivered")

    def test_retry_after_lost_ack_uses_same_event_attachment_and_outbound(self):
        self.collector.tick()
        transport = TransportStub()
        transport.fail_after_store = True
        self.assertEqual(flush(self.box, transport, lambda: self.current, lambda: NOW)["retry"], 1)
        self.box.close()
        self.box = Outbox(self.path)
        result = flush(self.box, transport, lambda: self.current, lambda: NOW + timedelta(seconds=10))
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(len(transport.events), 1)
        self.assertEqual(len(transport.uploads), 1)
        self.assertEqual(self.box.status(), {"delivered": 1})

    def test_revocation_before_delivery_suppresses_native_image_upload(self):
        self.collector.tick()
        self.current = policy(False)
        transport = TransportStub()
        result = flush(self.box, transport, lambda: self.current, lambda: NOW)
        self.assertEqual((result["blocked"], result["delivered"]), (1, 1))
        self.assertEqual(transport.uploads, [])
        self.assertNotIn("attachments", next(iter(transport.events.values())))
        self.assertNotIn("image_sha256", next(iter(transport.events.values()))["evidence"])

    def test_expired_policy_prevents_queue_upload(self):
        self.collector.tick()
        transport = TransportStub()
        flush(self.box, transport, lambda: replace(self.current, expires_at=NOW), lambda: NOW)
        self.assertEqual(transport.uploads, [])
        self.assertEqual(transport.events, {})
        self.assertEqual(self.box.status(), {"pending": 1})

    def test_denied_delivery_is_visible_and_does_not_log_backend_secrets(self):
        self.current = policy(False)
        self.collector.tick()
        transport = TransportStub()
        transport.deny = True
        self.assertEqual(flush(self.box, transport, lambda: self.current, lambda: NOW)["blocked"], 1)
        error = self.box.db.execute("SELECT last_error FROM events").fetchone()[0]
        self.assertNotIn("SECRET", error)
        self.assertEqual(self.box.status(), {"blocked_delivery": 1})

    def test_capacity_failure_does_not_advance_cursor_or_discard_existing_data(self):
        self.box.max_content_bytes = 1
        self.assertEqual(self.collector.tick()["status"], "local_storage_capacity_reached")
        self.assertEqual(self.box.status(), {})
        self.box.max_content_bytes = 1_000_000
        self.assertEqual(self.collector.tick()["status"], "queued")


class IntegrationScaffoldTests(unittest.TestCase):
    def test_native_timeout_and_stderr_are_sanitized(self):
        with patch("company_pilot.desktop.native.platform.system", return_value="Darwin"):
            adapter = NativeAdapter(Path("/fixture/helper"), timeout=0.01)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("SECRET command", 0.01, stderr="SECRET")):
            with self.assertRaisesRegex(NativeFailure, "^native_helper_timeout$"):
                adapter.context()

    def test_mac_startup_uses_absolute_arguments_and_no_shell(self):
        prefix = Path("/tmp/Pilot With Spaces")
        _, data = startup_files(prefix, prefix / "config.json", Path("/fixture/python"), "Darwin")
        plist = plistlib.loads(data)
        self.assertEqual(plist["ProgramArguments"][-1], str(prefix / "config.json"))
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["ProgramArguments"][0], "/fixture/python")

    def test_windows_task_is_interactive_least_privilege_and_xml_escaped(self):
        _, data = startup_files(Path("C:/A&B Pilot"), Path("C:/A&B Pilot/config.json"), Path("C:/Program Files/Python/python.exe"), "Windows")
        root = ET.fromstring(data)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:LogonType", ns).text, "InteractiveToken")
        self.assertEqual(root.find(".//t:RunLevel", ns).text, "LeastPrivilege")
        self.assertNotIn("SYSTEM", data.decode("utf-16"))

    def test_fixture_receiver_is_idempotent_and_never_claims_online(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver = DirectoryFixtureTransport(Path(directory))
            body = {"event_id": str(uuid.uuid4()), "development_fixture": True}
            first = receiver.send_observation(body)
            self.assertEqual(first, receiver.send_observation(body))
            with self.assertRaises(PermanentDeliveryError):
                receiver.send_observation(dict(body, changed=True))
            self.assertFalse(receiver.probe()["online"])


if __name__ == "__main__":
    unittest.main()
