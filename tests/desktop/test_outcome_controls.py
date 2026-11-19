"""Actual I022/I012-shaped local proofs. No OS or remote calls."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.desktop.collector import Collector, flush
from company_pilot.desktop.outbox import Outbox
from company_pilot.desktop.policy import Policy
from company_pilot.desktop.config import replace_config
from company_pilot.desktop.privacy import bind_private_policy, configure_private_policy
from test_collector import DEVICE, NOW, NativeStub, TransportStub, policy


class OutcomeControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = Outbox(Path(self.tmp.name) / "queue.sqlite")
        self.native = NativeStub()
        self.current = policy(False)
        self.now = NOW
        self.collector = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: self.now, episode_seconds=0)

    def tearDown(self):
        self.box.close()
        self.tmp.cleanup()

    def test_missing_new_controls_does_not_authorize_real_brain_capture(self):
        raw = {"company_id": "fixture", "role": "team", "policy_version": 1, "context_version": 1,
               "expires_at": (NOW + timedelta(hours=1)).isoformat(), "work_signals": ["Fatura"]}
        self.assertFalse(Policy.from_brain(raw).valid(NOW))

    def test_grounded_marker_not_description_recognizes_i012(self):
        raw = {"company_id": "fixture", "role": "team", "policy_version": 1, "context_version": 1,
               "expires_at": (NOW + timedelta(hours=1)).isoformat(),
               "work_signals": ["O cronograma organiza todas as obras e seus pagamentos."],
               "recognition_markers": [{"value": "Cronograma Obra", "kind": "record_name", "operation": "add", "evidence": ["source-1"]}],
               "controls": {"capture_allowed": True, "reason": "ready", "participation": {}}}
        self.assertEqual(replace(Policy.from_brain(raw), local_exclusions_bound=True).classify({"title": "Cronograma Obra AVM 82 - Planilhas Google"}, NOW).state, "company")

    def private_policy(self, participating=True, screenshots=False):
        config_path = Path(self.tmp.name) / "config.json"
        config = {}
        receipt = configure_private_policy(config_path, config,
                    {"participating": participating, "screenshots": screenshots, "reviewed_defaults": True,
                     "personal_exclusions": ["Meu arquivo privado"]}, 1)
        raw = {"company_id": "fixture", "role": "team", "policy_version": 1, "context_version": 1,
               "expires_at": (NOW + timedelta(hours=1)).isoformat(), "work_signals": ["Fatura", "Inter Empresas"],
               "screenshots_allowed": True, "screenshots": {"team": True},
               "controls": {"capture_allowed": True, "reason": "ready", "participation": receipt["participation"]}}
        return config, receipt, Policy.from_brain(raw)

    def test_i022_exclusion_precedes_fatura_and_company_banking_survives(self):
        config, receipt, remote = self.private_policy()
        self.current = bind_private_policy(remote, config)
        self.native.surface.update(app_id="com.google.Chrome", title="Nubank - Fatura do cartão pessoal",
                                   record_ref="", text="Fatura atual R$ 2.310,44 | Supermercado | Farmácia | Streaming",
                                   window_id="nubank-pessoal", process_id="p-com.google.Chrome")
        self.assertEqual(self.collector.tick()["code"], "personal_context")
        self.assertEqual(self.box.status(), {})
        self.assertEqual(self.box.quarantine_pending(NOW.timestamp()), [])
        self.assertNotIn(b"Nubank", (Path(self.tmp.name) / "queue.sqlite").read_bytes())
        self.assertEqual((self.native.text_calls, self.native.image_calls), (0, 0))
        self.native.surface.update(title="Inter Empresas - Fatura de fornecedor", text="Pagamento empresarial AVM 82")
        self.assertEqual(self.collector.tick()["classification"], "company")
        target = TransportStub()
        self.assertEqual(flush(self.box, target, lambda: self.current, lambda: NOW)["delivered"], 1)
        self.assertNotIn("Nubank", json.dumps(target.events))
        self.assertEqual(self.native.image_calls, 0)
        self.assertNotIn("Meu arquivo privado", Path(receipt["participation_choices_file"]).read_text())

    def test_missing_changed_stale_or_declined_local_choices_block_before_read(self):
        config, receipt, remote = self.private_policy()
        for candidate in (bind_private_policy(remote, {}),
                          bind_private_policy(replace(remote, version=2), config)):
            self.current = candidate
            self.assertEqual(self.collector.tick()["status"], "policy_unavailable")
        local = Path(receipt["private_policy_file"])
        raw = json.loads(local.read_text())
        raw["personal_exclusions"].append("Changed")
        replace_config(local, raw)
        self.current = bind_private_policy(remote, config)
        self.assertEqual(self.collector.tick()["status"], "policy_unavailable")
        config, _, remote = self.private_policy(participating=False)
        self.current = bind_private_policy(remote, config)
        self.assertEqual(self.collector.tick()["status"], "policy_unavailable")
        self.assertEqual((self.native.context_calls, self.native.text_calls, self.native.image_calls), (0, 0, 0))

    def test_participant_and_company_image_choices_both_required(self):
        for member_images, company_images in ((False, True), (True, False), (True, True)):
            config, _, remote = self.private_policy(screenshots=member_images)
            effective = bind_private_policy(replace(remote, screenshot_allowed=company_images and member_images), config)
            self.assertEqual(effective.permits_image(NOW), member_images and company_images)

    def test_two_unknown_windows_keep_count_without_titles(self):
        for index in range(2):
            self.native.surface.update(title="UNKNOWN PRIVATE " + str(index), window_id=str(index), privacy_disposition="withheld")
            self.collector.tick()
        rows = [json.loads(r[0]) for r in self.box.db.execute("SELECT payload FROM events ORDER BY created_at")]
        self.assertEqual(rows[-1]["coverage"]["distinct_windows"], 2)
        self.assertEqual(rows[-1]["coverage"]["observed_samples"], 2)
        self.assertNotIn("PRIVATE", json.dumps(rows))
        self.assertEqual(len({r["context_key"] for r in rows}), 2)

    def test_reclassification_reobserves_current_content_at_current_time(self):
        surface = dict(self.native.surface, title="Cronograma Obra AVM 82")
        self.box.quarantine_metadata(DEVICE, surface, self.now.isoformat(), self.now.timestamp())
        self.now += timedelta(minutes=10)
        self.native.surface.update(title="Cronograma Obra AVM 82", text="CURRENT PERMITTED CONTENT")
        self.current = replace(self.current, version=2, work_markers=("Cronograma Obra",))
        self.collector.reclassify_quarantine()
        rows = [json.loads(r[0]) for r in self.box.db.execute("SELECT payload FROM events")]
        current = [r for r in rows if r["evidence"]["text"] == "CURRENT PERMITTED CONTENT"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["observed_at"], self.now.isoformat())
        self.assertNotIn("OLD UNREAD", json.dumps(rows))

    def test_clock_hour_change_alone_retains_unknown_context_and_period_identity(self):
        self.current = replace(self.current, expires_at=NOW + timedelta(days=1))
        self.native.surface.update(title="UNKNOWN", privacy_disposition="withheld")
        self.collector.tick()
        first = json.loads(self.box.db.execute("SELECT payload FROM events").fetchone()[0])
        self.now += timedelta(hours=2)
        self.collector.tick()
        latest = json.loads(self.box.db.execute("SELECT payload FROM events ORDER BY created_at DESC LIMIT 1").fetchone()[0])
        self.assertEqual(first["context_key"], latest["context_key"])
        self.assertEqual(first["coverage"]["period_start"], latest["coverage"]["period_start"])
        self.assertEqual(latest["coverage"]["observed_samples"], 2)

    def test_reclassification_of_unavailable_window_does_not_read_another_body(self):
        surface = dict(self.native.surface, title="Cronograma Obra AVM 82")
        self.box.quarantine_metadata(DEVICE, surface, self.now.isoformat(), self.now.timestamp())
        self.native.surface.update(title="Personal screen", window_id="different", text="NEVER READ")
        self.current = replace(self.current, version=2, work_markers=("Cronograma Obra",))
        self.collector.reclassify_quarantine()
        self.assertEqual(self.native.text_calls, 0)
        self.assertNotIn("NEVER READ", json.dumps([json.loads(r[0]) for r in self.box.db.execute("SELECT payload FROM events")]))

    def test_expiry_visible_and_personal_is_not_coverage_loss(self):
        self.native.surface.update(title="UNKNOWN", privacy_disposition="withheld")
        self.collector.tick()
        self.box.quarantine_pending((NOW + timedelta(days=2)).timestamp())
        self.assertEqual(self.box.coverage_status()["expired_unresolved"], 1)
        self.assertEqual(self.box.coverage_status()["expiry_updates_pending"], 1)
        self.now += timedelta(days=2)
        self.current = replace(self.current, personal_markers=("cartão pessoal",), expires_at=self.now + timedelta(hours=1))
        self.native.surface["title"] = "Nubank - Fatura do cartão pessoal"
        self.collector.tick()
        self.assertEqual(self.box.coverage_status()["observed_samples"], 1)
        expired = [json.loads(r[0]) for r in self.box.db.execute("SELECT payload FROM events") if "unknown_metadata_expired" in str(r[0])]
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["coverage"]["expired_unresolved"], 1)
        self.assertNotIn("Nubank", json.dumps(expired))
        self.assertEqual(self.box.coverage_status()["expiry_updates_pending"], 0)


if __name__ == "__main__": unittest.main()
