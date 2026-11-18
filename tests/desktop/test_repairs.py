from dataclasses import replace
from datetime import timedelta
import json
import contextlib
import io
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import uuid

from company_pilot.desktop.agent_health import read_health, write_health
from company_pilot.desktop.__main__ import diagnose, main
from company_pilot.desktop.collector import Collector, PermanentDeliveryError, PolicyRefreshRequired, flush
from company_pilot.desktop.github_transport import BrainTransport
from company_pilot.desktop.outbox import Outbox
from install.bootstrap import stable_interpreter, startup_files
from test_collector import DEVICE, NOW, NativeStub, TransportStub, policy


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.box = Outbox(self.root / "queue.sqlite")
        self.native = NativeStub()
        self.policy = policy(False)
        self.now = NOW
        self.collector = Collector(DEVICE, self.native, self.box, lambda: self.policy, lambda: self.now)

    def tearDown(self):
        self.box.close()
        self.directory.cleanup()

    def test_unseeded_task_metadata_is_promoted_after_context_without_private_body(self):
        self.native.surface.update(title="New TEST-900 task", text="PRIVATE UNREAD BODY", record_ref="https://example.invalid/new-task")
        self.assertEqual(self.collector.tick()["classification"], "uncertain")
        first = self.box.db.execute("SELECT payload FROM events").fetchone()[0]
        self.assertNotIn(b"TEST-900", first)
        self.assertNotIn(b"PRIVATE", self.box.db.execute("SELECT surface FROM quarantine").fetchone()[0])
        self.assertEqual(self.native.text_calls, 0)
        self.policy = replace(self.policy, version=2, work_markers=("TEST-900",))
        self.assertEqual(self.collector.reclassify_quarantine(), 1)
        promoted = [json.loads(row[0]) for row in self.box.db.execute("SELECT payload FROM events") if b"metadata_only_no_original_body_or_image" in row[0]][0]
        self.assertIn("New TEST-900 task", promoted["evidence"]["text"])
        self.assertNotIn("PRIVATE", json.dumps(promoted))
        self.assertEqual(promoted["observed_at"], NOW.isoformat())
        self.assertEqual(self.collector.reclassify_quarantine(), 0)
        self.assertEqual(self.native.image_calls, 0)

    def test_quarantine_expiry_removes_only_ephemeral_metadata_and_never_promotes(self):
        self.native.surface["title"] = "unknown task"
        self.collector.tick()
        self.assertEqual(len(self.box.quarantine_pending(NOW.timestamp())), 1)
        self.assertEqual(self.box.quarantine_pending((NOW + timedelta(days=2)).timestamp()), [])
        self.assertEqual(self.box.db.execute("SELECT count(*) FROM quarantine").fetchone()[0], 0)
        self.assertEqual(self.box.status(), {"pending": 1})

    def test_one_hour_editing_becomes_twelve_bounded_episodes(self):
        self.policy = replace(self.policy, expires_at=NOW + timedelta(days=1))
        for index in range(240):
            self.now = NOW + timedelta(seconds=15 * index)
            self.native.surface["text"] = "Synthetic row " + str(index)
            self.collector.tick()
        self.box.finalize_episodes(self.now.timestamp(), force=True)
        self.assertEqual(self.box.status(), {"pending": 12})
        bodies = [json.loads(row[0]) for row in self.box.db.execute("SELECT payload FROM events")]
        self.assertTrue(any("Synthetic row 0" in b["evidence"]["text"] for b in bodies))
        self.assertTrue(any("Synthetic row 239" in b["evidence"]["text"] for b in bodies))

    def test_alternating_unknown_windows_produces_bounded_cumulative_gaps(self):
        for index in range(240):
            self.now = NOW + timedelta(seconds=15 * index)
            self.native.surface.update(title="Unknown title " + str(index % 2), app_id="fixture.app" + str(index % 2), window_id=str(index % 2))
            self.collector.tick()
        self.assertEqual(self.box.status(), {"pending": 24})
        self.assertEqual(self.box.coverage_status()["observed_samples"], 240)
        self.assertEqual([p["distinct_windows"] for p in self.box.coverage_status()["periods"]], [1, 1])
        self.assertEqual(self.native.text_calls, 0)

    def test_episode_survives_restart_and_retains_initial_latest_reading(self):
        self.collector.tick()
        self.box.close()
        self.box = Outbox(self.root / "queue.sqlite")
        self.collector.outbox = self.box
        self.now += timedelta(seconds=30)
        self.native.surface["text"] = "Later actual state"
        self.collector.tick(force_finalize=True)
        body = json.loads(self.box.db.execute("SELECT payload FROM events").fetchone()[0])
        self.assertIn("TEST-42 amount settled", body["evidence"]["text"])
        self.assertIn("Later actual state", body["evidence"]["text"])

    def test_two_documents_in_one_browser_window_keep_separate_evidence(self):
        self.native.surface.update(title="SYNTHETIC-COMPANY document A", text="Literal document A")
        self.collector.tick()
        self.native.surface.update(title="SYNTHETIC-COMPANY document B", text="Literal document B")
        self.collector.tick(force_finalize=True)
        bodies = [json.loads(row[0]) for row in self.box.db.execute("SELECT payload FROM events")]
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            expected = "Literal document " + body["source"]["title"][-1]
            self.assertEqual(body["evidence"]["text"], expected)

    def test_new_personal_exclusion_blocks_queued_image_before_any_upload(self):
        self.policy = policy(True)
        self.collector.episode_seconds = 0
        self.collector.tick()
        self.policy = replace(self.policy, version=2, personal_markers=("TEST-42",))
        remote = TransportStub()
        self.assertEqual(flush(self.box, remote, lambda: self.policy, lambda: self.now)["blocked"], 1)
        self.assertEqual(remote.uploads, [])
        self.assertEqual(remote.events, {})

    def test_flush_uses_one_atomic_batch_for_twenty_observations(self):
        collector = Collector(DEVICE, self.native, self.box, lambda: self.policy, lambda: self.now, episode_seconds=0)
        for index in range(20):
            self.native.surface["text"] = "TEST source " + str(index)
            collector.tick()
        class Batched(TransportStub):
            batches = 0
            def send_observations(self, payloads):
                self.batches += 1
                return [self.send_observation(payload) for payload in payloads]
        transport = Batched()
        result = flush(self.box, transport, lambda: self.policy, lambda: self.now)
        self.assertEqual(result["delivered"], 20)
        self.assertEqual(transport.batches, 1)

    def test_batch_lost_ack_replays_without_duplicate_sources(self):
        self.collector.episode_seconds = 0
        for index in range(3):
            self.native.surface["text"] = "Row " + str(index)
            self.collector.tick()
        class Batch(TransportStub):
            fail = True
            def send_observations(self, payloads):
                result = [self.send_observation(p) for p in payloads]
                if self.fail:
                    self.fail = False
                    raise ConnectionError("lost batch response")
                return result
        remote = Batch()
        self.assertEqual(flush(self.box, remote, lambda: self.policy, lambda: self.now)["retry"], 3)
        self.now += timedelta(seconds=10)
        self.assertEqual(flush(self.box, remote, lambda: self.policy, lambda: self.now)["delivered"], 3)
        self.assertEqual(len(remote.events), 3)

    def test_old_policy_event_gets_new_identity_after_explicit_refresh_rejection(self):
        self.collector.episode_seconds = 0
        self.collector.tick()
        original = json.loads(self.box.db.execute("SELECT payload FROM events").fetchone()[0])
        self.policy = replace(self.policy, version=2)
        class Remote(TransportStub):
            def send_observation(self, payload):
                if payload["policy"]["version"] == 1: raise PolicyRefreshRequired()
                return super().send_observation(payload)
        remote = Remote()
        flush(self.box, remote, lambda: self.policy, lambda: self.now)
        self.assertEqual(self.box.status(), {"blocked_policy": 1, "pending": 1})
        flush(self.box, remote, lambda: self.policy, lambda: self.now)
        received = next(iter(remote.events.values()))
        self.assertNotEqual(original["event_id"], received["event_id"])
        self.assertEqual(original["evidence"], received["evidence"])
        self.assertEqual(received["policy"]["version"], 2)


class AgentAndAdapterTests(unittest.TestCase):
    def config(self, root):
        return {"device_id": DEVICE, "outbox_file": str(root / "queue.sqlite"), "capture_enabled": False}

    def test_interactive_grants_cannot_make_unhealthy_agent_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root)
            class Native:
                def probe(self): return {"platform": "macOS", "accessibility_granted": True, "screen_recording_granted": True}
            class Remote:
                def probe(self): return {"online": True, "authenticated": True, "can_contribute": True}
            current = replace(policy(), expires_at=NOW + timedelta(days=10000))
            checks = diagnose(config, Native(), Remote(), lambda: current, root / "health.json")
            self.assertFalse(checks["native_ready"])
            write_health(root / "health.json", config, Native().probe(), context_verified=False)
            self.assertFalse(diagnose(config, Native(), Remote(), lambda: current, root / "health.json")["native_ready"])
            write_health(root / "health.json", config, {"platform": "macOS", "accessibility_granted": False}, context_verified=True)
            self.assertFalse(diagnose(config, Native(), Remote(), lambda: current, root / "health.json")["native_ready"])

    def test_agent_health_is_invalidated_by_time_config_or_runtime_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); path = root / "health.json"
            write_health(path, config, {}, context_verified=True, now=NOW)
            self.assertTrue(read_health(path, config, NOW)["context_verified"])
            self.assertFalse(read_health(path, config, NOW + timedelta(minutes=2))["ready"])
            self.assertFalse(read_health(path, dict(config, device_id=str(uuid.uuid4())), NOW)["ready"])

    def test_fresh_health_from_dead_worker_cannot_enable_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); path = root / "health.json"
            body = write_health(path, config, {"accessibility_granted": True}, context_verified=True, now=NOW)
            body["pid"] = 2_000_000_000
            path.write_text(json.dumps(body))
            self.assertFalse(read_health(path, config, NOW)["ready"])

    def test_stable_launcher_survives_patch_target_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); old = root / "old-python"; new = root / "new-python"; alias = root / "python3"
            old.write_text("old"); new.write_text("new"); alias.symlink_to(old)
            with patch("install.bootstrap.shutil.which", return_value=str(alias)):
                selected = stable_interpreter(str(old))
            _, data = startup_files(root, root / "config.json", selected, "Darwin")
            self.assertEqual(plistlib.loads(data)["ProgramArguments"][0], str(alias))
            alias.unlink(); alias.symlink_to(new)
            self.assertEqual(Path(plistlib.loads(data)["ProgramArguments"][0]).read_text(), "new")

    def test_brain_wire_is_explicit_and_gap_contains_no_private_fields(self):
        class Brain:
            def send_observations(self, values):
                self.values = values
                return [{"observation_id": v["event_id"], "inspect_url": "fixture:" + v["event_id"]} for v in values]
            def ingest_context_gap(self, value):
                self.gap = value
                return {"observation_id": value["event_id"], "inspect_url": "fixture:gap"}
        brain = Brain(); transport = BrainTransport(brain)
        payload = {"event_id": str(uuid.uuid4()), "device_id": DEVICE, "observed_at": NOW.isoformat(), "policy": {"version": 1},
                   "source": {"kind": "desktop_window", "app_id": "fixture.app", "title": "SYNTHETIC-COMPANY"},
                   "evidence": {"text": "Literal source"}, "classification": {"state": "company", "reason": "supported", "context_version": 2}, "development_fixture": True}
        transport.send_observations([payload])
        self.assertEqual(brain.values[0]["content"]["text"], "Literal source")
        self.assertEqual(brain.values[0]["classification"]["context_version"], 2)
        self.assertNotIn("actor", brain.values[0])
        payload["event_id"] = str(uuid.uuid4()); payload["classification"]["state"] = "uncertain"
        transport.send_observations([payload])
        self.assertEqual(set(brain.gap), {"schema_version", "event_id", "device_id", "occurred_at", "app", "surface_kind", "synthetic"})
        self.assertNotIn("Literal source", json.dumps(brain.gap))

    def test_production_configure_uses_github_adapter_and_no_fixture_or_shared_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            class GitHub:
                def probe(self): return {"online": True, "authenticated": True, "can_contribute": True, "actor": "fixture-own-account"}
                def policy(self): return policy(False)
            with patch("company_pilot.setup.CompanyEntry.guard"), patch("company_pilot.desktop.__main__.make_transport", return_value=GitHub()), contextlib.redirect_stdout(io.StringIO()):
                result = main(["configure", "--config", str(path), "--repo", "fixture/company"])
            self.assertEqual(result, 0)
            config = json.loads(path.read_text())
            self.assertEqual(config["transport"]["kind"], "github")
            self.assertFalse(config["capture_enabled"])
            self.assertNotIn("secret", path.read_text().casefold())
            self.assertNotIn("token", path.read_text().casefold())


if __name__ == "__main__": unittest.main()
