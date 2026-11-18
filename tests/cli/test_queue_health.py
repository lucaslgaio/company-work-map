from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.cli import main
from company_pilot.operator import write_private_json


class QueueHealthTests(unittest.TestCase):
    def test_saved_queue_age_is_recomputed_without_model_backend_or_private_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, health = root / "config.json", root / "health.json"
            write_private_json(config, {"schema_version": 1, "mode": "synthetic",
                "store": {"kind": "local", "root": str(root / "data")}, "operator": {"health_file": str(health)}})
            current = datetime.now(timezone.utc)
            old = (current - timedelta(hours=2)).isoformat()
            receipt = {"schema_version": 1, "pid": 123, "state": "stopped", "consecutive_failures": 0,
                       "last_tick_at": current.isoformat(), "last_success_at": current.isoformat(), "error_code": None,
                       "processing": {"pending_job_count": 3, "queued_answer_count": 1, "awaiting_clarification_count": 0,
                                      "oldest_pending_at": old, "oldest_queued_answer_at": old, "last_analysis_at": None,
                                      "oldest_pending_age_seconds": 0, "summary": "PRIVATE SOURCE TOKEN=SECRET"}}
            write_private_json(health, receipt)
            def forbidden(_config):
                self.fail("health must not construct a backend or model")
            out, err = StringIO(), StringIO()
            code = main(["--config", str(config), "operator", "health"], brain_factory=forbidden,
                        interpreter_factory=forbidden, out=out, err=err)
            self.assertEqual((code, err.getvalue()), (0, ""))
            result = json.loads(out.getvalue())
            self.assertEqual(result["processing"]["queued_answer_count"], 1)
            self.assertGreaterEqual(result["processing"]["oldest_pending_age_seconds"], 7200)
            self.assertNotIn("PRIVATE SOURCE", out.getvalue())
            self.assertNotIn("SECRET", out.getvalue())
            self.assertEqual(result["process_liveness"], "not_verified")
            receipt["processing"]["queued_answer_count"] = "TOKEN=SECRET"
            write_private_json(health, receipt)
            out = StringIO()
            main(["--config", str(config), "operator", "health"], brain_factory=forbidden, out=out, err=err)
            self.assertEqual(json.loads(out.getvalue())["processing"], {"state": "not_verified"})
            self.assertNotIn("SECRET", out.getvalue())


if __name__ == "__main__":
    unittest.main()
