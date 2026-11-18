from io import StringIO
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.operator import write_private_json
from company_pilot.watchdog.__main__ import main
from company_pilot.watchdog.catalog import Catalog


class Sink:
    def __init__(self, synthetic=True):
        self.synthetic, self.records = synthetic, {}

    def snapshot(self):
        return {"company": {"id": "synthetic-company", "synthetic": self.synthetic}}

    def ingest_company_record(self, payload):
        previous = self.records.setdefault(payload["event_id"], payload)
        if previous != payload:
            raise ValueError("payload_changed")
        return {"state": "pending_analysis", "source_id": payload["event_id"]}


class WatchdogCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.company = self.root / "company"
        self.company.mkdir()
        self.source_file = self.company / "orders.csv"
        self.source_file.write_text("order,total\nA,10\n")
        self.company_config = self.root / "company.json"
        write_private_json(self.company_config, {"schema_version": 1, "mode": "synthetic",
                           "store": {"kind": "local", "root": str(self.root / "brain")}})
        self.config = self.root / "watchdog.json"
        self.settings = {"schema_version": 1, "company_config": str(self.company_config),
                         "company_id": "synthetic-company", "catalog": str(self.root / "catalog.db"),
                         "enabled": True, "archive": {"kind": "local", "root": str(self.root / "archive")},
                         "sources": [{"id": "company-files", "kind": "directory", "root": str(self.company)}]}
        self.sink = Sink()

    def invoke(self, *args, sink=None):
        write_private_json(self.config, self.settings)
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config), *args], brain_factory=lambda _config: sink or self.sink, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_actual_directory_cli_to_typed_intake_and_private_before_after_export(self):
        started = datetime.now(timezone.utc)
        code, out, err = self.invoke("sync")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(json.loads(out)["status"]["sources"][0]["content_complete"])
        self.assertEqual(len(self.sink.records), 1)
        first = next(iter(self.sink.records.values()))
        self.assertIn("A,10", first["content"]["text"])
        self.assertLessEqual(started, datetime.fromisoformat(first["occurred_at"]))
        self.assertLessEqual(datetime.fromisoformat(first["occurred_at"]), datetime.now(timezone.utc))
        self.assertEqual(first["object"]["size_bytes"], len(self.source_file.read_bytes()))
        code, _, err = self.invoke("sync")
        self.assertEqual((code, err, len(self.sink.records)), (0, "", 1))
        self.source_file.write_text("order,total\nA,12\n")
        code, _, err = self.invoke("sync")
        self.assertEqual((code, err, len(self.sink.records)), (0, "", 2))
        _, out, _ = self.invoke("changes")
        versions = json.loads(out)["changes"]
        self.assertEqual(len(versions), 2)
        code, out, err = self.invoke("compare", "--source", versions[0]["source_key"], "--record", "orders.csv",
                                    "--before", versions[0]["revision_id"], "--after", versions[1]["revision_id"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("-A,10", json.loads(out)["diff"])
        self.assertIn("+A,12", json.loads(out)["diff"])
        _, out, _ = self.invoke("changes", "--limit", "1")
        page = json.loads(out)
        self.assertTrue(page["has_more"])
        _, out, _ = self.invoke("changes", "--after", str(page["next_after"]), "--limit", "1")
        self.assertFalse(json.loads(out)["has_more"])
        self.assertEqual(json.loads(out)["changes"][0]["revision_id"], versions[1]["revision_id"])
        target = self.root / "original-orders.csv"
        code, _, err = self.invoke("inspect", "--source", versions[0]["source_key"], "--record", "orders.csv",
                                    "--revision", versions[0]["revision_id"], "--output-file", str(target))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(target.read_text(), "order,total\nA,10\n")
        code, _, _ = self.invoke("inspect", "--source", versions[0]["source_key"], "--record", "orders.csv",
                                "--revision", versions[0]["revision_id"], "--output-file", str(target))
        self.assertEqual(code, 2)
        self.assertEqual(target.read_text(), "order,total\nA,10\n")

    def test_pending_release_and_disabled_worker_do_not_read_company_source(self):
        self.settings["enabled"] = False
        code, out, err = self.invoke("sync")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("watchdog_not_enabled", err)
        self.assertFalse((self.root / "archive").exists())
        self.settings["enabled"] = True
        manifest = self.root / "release.json"
        manifest.write_text('{"release_status":"review_candidate"}')
        self.settings["release_manifest"] = str(manifest)
        code, out, err = self.invoke("sync", sink=Sink(synthetic=False))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("watchdog_release_not_ready", err)
        self.assertFalse((self.root / "archive").exists())

    def test_status_is_local_and_catalogue_cannot_switch_companies(self):
        self.invoke("sync")
        def forbidden(_config):
            raise AssertionError("status must not contact Brain or credentials")
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config), "status"], brain_factory=forbidden, out=out, err=err)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.settings["company_id"] = "another-company"
        code, _, err = self.invoke("status")
        self.assertEqual(code, 2)
        self.assertIn("catalog_company_identity_mismatch", err)

    def test_source_unavailable_before_construction_invalidates_old_complete_readback(self):
        self.assertEqual(self.invoke("sync")[0], 0)
        self.source_file.unlink()
        self.company.rmdir()
        self.assertNotEqual(self.invoke("sync")[0], 0)
        _, out, _ = self.invoke("status")
        self.assertFalse(json.loads(out)["sources"][0]["content_complete"])
        self.company.mkdir()
        self.source_file.write_text("order,total\nA,10\n")
        self.assertEqual(self.invoke("sync")[0], 0)
        _, out, _ = self.invoke("status")
        self.assertTrue(json.loads(out)["sources"][0]["content_complete"])


if __name__ == "__main__":
    unittest.main()
