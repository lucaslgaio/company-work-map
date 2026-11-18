"""o6/I7: unsupported native construction still yields actionable doctor checks."""
import contextlib
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from company_pilot.desktop.__main__ import main
from company_pilot.desktop.collector import NativeFailure
from company_pilot.desktop.github_transport import BrainTransport
from company_pilot.desktop.preflight import platform_check, runtime_check
from company_pilot.brain.stores import BrainError
from install.bootstrap import main as bootstrap
from test_collector import DEVICE, NOW, policy


class SetupDiagnosticsTests(unittest.TestCase):
    def test_unsupported_native_constructor_keeps_structured_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(replace(policy(False), expires_at=NOW + timedelta(days=10000)).as_mapping()))
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"schema_version": 1, "device_id": DEVICE,
                "policy_file": str(policy_path), "outbox_file": str(root / "outbox.sqlite"),
                "transport": {"kind": "directory-fixture", "path": str(root / "receiver")}}))
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("company_pilot.desktop.__main__.NativeAdapter", side_effect=NativeFailure("unsupported_platform")), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["doctor", "--config", str(config_path), "--development"])
            self.assertEqual(result, 2)
            self.assertTrue(stdout.getvalue().strip(), "I7: doctor omitted structured checks after unsupported native construction")
            output = json.loads(stdout.getvalue())
            self.assertIn("checks", output)
            self.assertFalse(output["ready"])
            raw = json.loads(config_path.read_text())
            raw["capture_enabled"] = True
            config_path.write_text(json.dumps(raw))
            with patch("company_pilot.desktop.__main__.NativeAdapter", side_effect=NativeFailure("unsupported_platform")), contextlib.redirect_stdout(io.StringIO()):
                result = main(["enable-capture", "--config", str(config_path), "--development"])
            self.assertEqual(result, 2)
            self.assertFalse(json.loads(config_path.read_text())["capture_enabled"])

    def test_os_and_python_checks_are_independent(self):
        with patch("company_pilot.desktop.preflight.platform.system", return_value="Linux"), patch("company_pilot.desktop.preflight.sys.version_info", (3, 11, 9)):
            self.assertEqual(platform_check()["code"], "unsupported_platform")
            self.assertEqual(runtime_check()["code"], "python_312_required")
        with patch("company_pilot.desktop.preflight.platform.system", return_value="Darwin"), patch("company_pilot.desktop.preflight.platform.mac_ver", return_value=("13.7", (), "")):
            self.assertEqual(platform_check()["code"], "macos_version_unsupported")

    def test_missing_build_tool_stops_before_installation_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "not-created"
            stderr = io.StringIO()
            with patch("install.bootstrap.platform_check", return_value={"supported": True}), patch("install.bootstrap.runtime_check", return_value={"supported": True}), patch("install.bootstrap.platform.system", return_value="Darwin"), patch("install.bootstrap.shutil.which", return_value=None), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught: bootstrap(["--prefix", str(prefix)])
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("Command Line Tools", stderr.getvalue())
            self.assertFalse(prefix.exists())

    def test_configure_distinguishes_missing_branch_empty_brain_and_wrong_mode(self):
        class Store:
            head = None
            def authenticated_actor(self): return {"provider": "local_fixture", "permissions": ["read", "write"], "id": "fixture"}
            def read(self): return SimpleNamespace(head=self.head)
        class Brain:
            def __init__(self): self.store = Store()
            def snapshot(self): raise BrainError("brain_not_initialized")
        for scenario in ("state_branch_missing", "brain_not_initialized", "synthetic_real_boundary_mismatch"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                brain = Brain()
                if scenario == "brain_not_initialized": brain.store.head = "existing-head"
                if scenario == "synthetic_real_boundary_mismatch": brain.snapshot = lambda: {"company": {"synthetic": True}}
                transport = BrainTransport(brain)
                transport.expected_synthetic = False
                config = Path(directory) / "config.json"
                stderr = io.StringIO()
                with patch("company_pilot.setup.CompanyEntry.guard"), patch("company_pilot.desktop.__main__.make_transport", return_value=transport), contextlib.redirect_stderr(stderr):
                    result = main(["configure", "--config", str(config), "--repo", "fixture/company", "--branch", "actual-state"])
                self.assertEqual(result, 2)
                self.assertEqual(json.loads(stderr.getvalue())["code"], scenario)
                self.assertFalse(config.exists())

    def test_development_clock_matches_fixture_time_and_is_rejected_for_native(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pp, cp, fp = root / "policy.json", root / "config.json", root / "surface.json"
            pp.write_text(json.dumps(policy(False).as_mapping()))
            cp.write_text(json.dumps({"schema_version": 1, "device_id": DEVICE, "policy_file": str(pp),
                                     "outbox_file": str(root / "outbox.sqlite"), "transport": {"kind": "directory-fixture", "path": str(root / "receiver")}}))
            fp.write_text(json.dumps({"app_id": "fixture", "title": "SYNTHETIC-COMPANY", "text": "Current fixture", "window_id": "1", "process_id": "1"}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["run", "--config", str(cp), "--fixture", str(fp), "--once", "--development", "--clock", NOW.isoformat()])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["clock_source"], "development_fixture")
            payload = json.loads(next((root / "receiver").glob("*.json")).read_text())
            self.assertEqual(payload["observed_at"], NOW.isoformat())
            stderr = io.StringIO()
            with patch("company_pilot.desktop.__main__.NativeAdapter") as native, contextlib.redirect_stderr(stderr):
                result = main(["run", "--config", str(cp), "--native", "--once", "--development", "--clock", NOW.isoformat()])
            self.assertEqual(result, 2)
            native.assert_not_called()
            self.assertEqual(json.loads(stderr.getvalue())["code"], "fixture_clock_requires_development_once")


if __name__ == "__main__": unittest.main()
