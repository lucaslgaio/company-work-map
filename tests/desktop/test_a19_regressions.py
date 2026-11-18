"""Regression coverage for the independently reproduced p22 desktop failures."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from company_pilot.desktop.collector import Collector, PermanentDeliveryError, flush
from company_pilot.desktop.github_transport import BrainTransport
from company_pilot.desktop.outbox import CapacityError, Outbox, canonical
from company_pilot.desktop.policy import Policy
from test_collector import DEVICE, NOW, NativeStub, TransportStub, policy


class AccessDenied(Exception):
    pass


class IntegrityError(Exception):
    pass


class GitHubError(Exception):
    def __init__(self, status):
        self.status = status


class PolicyBrain:
    error = None

    def device_policy(self):
        if self.error is not None:
            raise self.error
        return {"company_id": "fixture-company", "policy_version": 1, "context_version": 1,
                "expires_at": (NOW + timedelta(hours=1)).isoformat(), "role": "team",
                "screenshots": {"team": True}, "screenshots_allowed": True,
                "work_signals": ["SYNTHETIC-COMPANY"]}


class AuthorizationRegressions(unittest.TestCase):
    def test_transient_offline_cache_stays_valid_but_explicit_denials_stop_native_capture(self):
        network_shaped_denial = type("AccessDenied", (PermissionError,), {})("private backend detail")
        for refusal in (AccessDenied("private backend detail"), IntegrityError("private backend detail"), GitHubError(401), GitHubError(403), network_shaped_denial):
            with self.subTest(refusal=type(refusal).__name__), tempfile.TemporaryDirectory() as directory:
                brain = PolicyBrain()
                transport = BrainTransport(brain)
                transport.cache_path = Path(directory) / "policy.json"
                with patch("company_pilot.desktop.github_transport.utcnow", return_value=NOW):
                    original = transport.policy(force=True)
                    brain.error = ConnectionError("offline")
                    self.assertEqual(transport.policy(force=True), original)
                    brain.error = refusal
                    with self.assertRaises(PermanentDeliveryError): transport.policy(force=True)
                    self.assertFalse(transport._policy.current(NOW))
                    saved = Policy.parse(json.loads(transport.cache_path.read_text()))
                    self.assertTrue(saved.revoked)
                    self.assertNotIn("private backend detail", transport.cache_path.read_text())
                    brain.error = ConnectionError("still offline")
                    box = Outbox(Path(directory) / "outbox.sqlite")
                    try:
                        native = NativeStub()
                        collector = Collector(DEVICE, native, box, transport.policy, lambda: NOW, episode_seconds=0)
                        with self.assertRaises(ConnectionError): collector.tick()
                        self.assertEqual((native.context_calls, native.text_calls, native.image_calls), (0, 0, 0))
                    finally: box.close()

    def test_known_refusal_from_sender_also_invalidates_capture_policy(self):
        brain = PolicyBrain()
        transport = BrainTransport(brain)
        with patch("company_pilot.desktop.github_transport.utcnow", return_value=NOW):
            transport.policy(force=True)
            def denied(): raise IntegrityError("do not expose this")
            with self.assertRaises(PermanentDeliveryError): transport._invoke(denied)
            self.assertFalse(transport._policy.current(NOW))

    def test_read_only_identity_cannot_refresh_itself_back_into_capture_permission(self):
        brain = PolicyBrain()
        class Store:
            permissions = ["read", "write"]
            def authenticated_actor(self): return {"permissions": self.permissions}
        brain.store = Store()
        transport = BrainTransport(brain)
        with patch("company_pilot.desktop.github_transport.utcnow", return_value=NOW):
            self.assertTrue(transport.policy(force=True).current(NOW))
            brain.store.permissions = ["read"]
            with self.assertRaises(PermanentDeliveryError): transport.policy(force=True)
            self.assertFalse(transport._policy.current(NOW))


class DeliveryRegressions(unittest.TestCase):
    def test_final_batch_drops_prepared_image_links_after_later_upload_revokes(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "outbox.sqlite")
            current = [policy(True)]
            native = NativeStub()
            collector = Collector(DEVICE, native, box, lambda: current[0], lambda: NOW, episode_seconds=0)
            collector.tick()
            native.surface["text"] = "Second literal sample"
            collector.tick()
            class Remote(TransportStub):
                batches = 0
                def upload_attachment(self, *args):
                    receipt = super().upload_attachment(*args)
                    if len(self.uploads) == 2:
                        current[0] = replace(current[0], version=2, screenshot_allowed=False)
                    return receipt
                def send_observations(self, payloads):
                    self.batches += 1
                    assert all(not p.get("attachments") for p in payloads)
                    return [self.send_observation(p) for p in payloads]
            remote = Remote()
            try:
                result = flush(box, remote, lambda: current[0], lambda: NOW)
                self.assertEqual(result["blocked"], 2)
                self.assertGreaterEqual(remote.batches, 1)
                self.assertTrue(all(not p.get("attachments") for p in remote.events.values()))
            finally: box.close()

    def test_sequential_sender_rechecks_policy_before_each_separate_call(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "outbox.sqlite")
            current = [policy(True)]
            native = NativeStub()
            collector = Collector(DEVICE, native, box, lambda: current[0], lambda: NOW, episode_seconds=0)
            collector.tick()
            native.surface["text"] = "Second sample"
            collector.tick()
            class Remote(TransportStub):
                image_calls = 0
                def send_observation(self, payload):
                    if payload.get("attachments"):
                        assert current[0].permits_image(NOW)
                        self.image_calls += 1
                    receipt = super().send_observation(payload)
                    current[0] = replace(current[0], screenshot_allowed=False)
                    return receipt
            remote = Remote()
            try:
                flush(box, remote, lambda: current[0], lambda: NOW)
                self.assertEqual(remote.image_calls, 1)
                self.assertEqual(box.status(), {"blocked_policy": 1, "delivered": 1, "pending": 1})
            finally: box.close()


class QuarantineRegressions(unittest.TestCase):
    def test_new_occurrence_after_promotion_has_its_own_id_even_before_retention_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite"
            box = Outbox(path)
            current = [replace(policy(False), expires_at=NOW + timedelta(days=10), work_markers=())]
            native = NativeStub("TEST-900 unknown document")
            clock = [NOW]
            collector = Collector(DEVICE, native, box, lambda: current[0], lambda: clock[0])
            try:
                collector.tick()
                first = box.quarantine_pending(NOW.timestamp())[0]
                clock[0] += timedelta(seconds=15)
                collector.tick()
                self.assertEqual(box.quarantine_pending(clock[0].timestamp())[0]["id"], first["id"])
                box.close()
                box = Outbox(path); collector.outbox = box
                current[0] = replace(current[0], version=2, work_markers=("TEST-900",))
                self.assertEqual(collector.reclassify_quarantine(), 1)
                current[0] = replace(current[0], version=3, work_markers=())
                clock[0] += timedelta(seconds=15)
                collector.tick()
                later = box.quarantine_pending(clock[0].timestamp())[0]
                self.assertNotEqual(first["id"], later["id"])
                current[0] = replace(current[0], version=4, work_markers=("TEST-900",))
                self.assertEqual(collector.reclassify_quarantine(), 1)
                self.assertEqual(collector.reclassify_quarantine(), 0)
            finally: box.close()

    def test_legacy_quarantine_migration_cannot_reuse_historical_promoted_event_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite"
            legacy_id = str(uuid.uuid4())
            raw = NativeStub("TEST-900 unknown document").surface
            surface = {key: raw[key] for key in ("app_id", "window_id", "process_id", "title", "record_ref", "evidence_level")}
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE quarantine(id TEXT PRIMARY KEY,surface BLOB NOT NULL,observed_at TEXT NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL DEFAULT 'pending')")
            db.execute("INSERT INTO quarantine(id,surface,observed_at,expires) VALUES(?,?,?,?)", (legacy_id, canonical(surface), NOW.isoformat(), (NOW + timedelta(hours=1)).timestamp()))
            db.commit(); db.close()
            box = Outbox(path)
            historical = {"event_id": str(uuid.uuid5(uuid.UUID(legacy_id), "promoted")), "history": "unchanged"}
            box.enqueue(historical)
            try:
                migrated = box.quarantine_pending(NOW.timestamp())[0]
                self.assertNotEqual(migrated["id"], legacy_id)
                current = replace(policy(False), work_markers=("TEST-900",))
                collector = Collector(DEVICE, NativeStub(), box, lambda: current, lambda: NOW)
                self.assertEqual(collector.reclassify_quarantine(), 1)
                preserved = box.db.execute("SELECT payload FROM events WHERE event_id=?", (historical["event_id"],)).fetchone()[0]
                self.assertEqual(json.loads(preserved), historical)
            finally: box.close()


class CapacityRegressions(unittest.TestCase):
    def test_atomic_multi_episode_move_uses_space_freed_by_other_selected_buffers(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "outbox.sqlite")
            native = NativeStub()
            collector = Collector(DEVICE, native, box, lambda: policy(False), lambda: NOW)
            try:
                native.surface.update(title="SYNTHETIC-COMPANY short", text="a")
                collector.tick()
                native.surface.update(title="SYNTHETIC-COMPANY long", text="b" * 400)
                collector.tick()
                box.max_content_bytes = box.storage_size()
                self.assertEqual(box.finalize_episodes(NOW.timestamp(), force=True), 2)
                self.assertLessEqual(box.storage_size(), box.max_content_bytes)
                self.assertEqual(box.status(), {"pending": 2})
            finally: box.close()

    def test_failed_atomic_move_restores_buffer_and_cursor_then_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "outbox.sqlite")
            native = NativeStub()
            collector = Collector(DEVICE, native, box, lambda: policy(False), lambda: NOW)
            try:
                collector.tick()
                before = dict(box.db.execute("SELECT * FROM episodes").fetchone())
                box.max_content_bytes = 1
                with self.assertRaises(CapacityError): box.finalize_episodes(NOW.timestamp(), force=True)
                self.assertEqual(dict(box.db.execute("SELECT * FROM episodes").fetchone()), before)
                self.assertEqual(box.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
                self.assertEqual(box.db.execute("SELECT count(*) FROM cursors").fetchone()[0], 0)
                box.max_content_bytes = 785
                self.assertEqual(box.finalize_episodes(NOW.timestamp(), force=True), 1)
                self.assertLessEqual(box.storage_size(), box.max_content_bytes)
                self.assertEqual(box.status(), {"pending": 1})
            finally: box.close()

    def test_initial_multibyte_buffer_is_fully_counted_and_update_fits_same_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "outbox.sqlite")
            native = NativeStub(); native.surface["text"] = "é" * 200
            collector = Collector(DEVICE, native, box, lambda: policy(False), lambda: NOW)
            try:
                collector.tick()
                size = box.storage_size()
                self.assertGreaterEqual(size, len(canonical(json.loads(box.db.execute("SELECT payload FROM episodes").fetchone()[0]))) + 400)
                box.max_content_bytes = size
                self.assertEqual(collector.tick()["status"], "buffered")
                self.assertEqual(box.storage_size(), size)
            finally: box.close()


if __name__ == "__main__": unittest.main()
