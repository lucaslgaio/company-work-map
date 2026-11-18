"""Static Linux bundle checks; never systemd, gateway, GitHub or actual accounts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("nora_vps_bundle",
    Path(__file__).resolve().parents[2] / "deploy/nora-vps/render_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class DeploymentTests(unittest.TestCase):
    def test_protected_aliases_are_rejected_before_any_mkdir(self):
        for destination in ("/etc/company-pilot/stage", "/private/etc/company-pilot/stage",
                            "/var/lib/company-pilot/stage", "/private/var/lib/company-pilot/stage"):
            with self.subTest(destination=destination), patch.object(Path, "mkdir", side_effect=AssertionError("Protected mkdir reached")) as mkdir:
                with self.assertRaises(ValueError):
                    bundle.render_bundle(destination, openclaw_executable="/usr/bin/openclaw", code_commit="a" * 40)
                mkdir.assert_not_called()

    def test_bundle_is_private_inactive_isolated_and_contains_model_free_command(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "stage"
            manifest = bundle.render_bundle(destination, openclaw_executable="/usr/local/bin/openclaw",
                                            code_commit="a" * 40)
            self.assertFalse(manifest["activation_approved"])
            self.assertFalse(manifest["gateway_config_complete"])
            self.assertEqual(destination.stat().st_mode & 0o077, 0)
            for path in destination.iterdir():
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            config = json.loads((destination / "config.json").read_text())
            self.assertIs(config["nora"]["enabled"], False)
            self.assertIs(config["nora"]["route_verified"], False)
            self.assertNotEqual(config["operator"]["health_file"], config["nora"]["health_file"])
            self.assertEqual(config["nora"]["poll_seconds"], 60)
            fragment = json.loads((destination / "openclaw-plugin-fragment.json").read_text())
            entry = fragment["plugins"]["entries"]["company-nora-bridge"]
            self.assertIs(entry["enabled"], False)
            self.assertIs(entry["hooks"]["allowConversationAccess"], True)
            for name in ("company-pilot-gateway", "company-pilot-nora"):
                text = (destination / f"{name}.service").read_text()
                self.assertIn("User=company-pilot", text)
                self.assertIn("ProtectHome=true", text)
                self.assertIn("ConditionPathExists=/etc/company-pilot/", text)
                self.assertNotIn("/root/", text)
                self.assertNotIn("/Users/", text)
                self.assertNotIn("--force", text)
                self.assertNotIn("--allow-unconfigured", text)
                self.assertNotIn("@", text)
            self.assertIn(" nora watch", (destination / "company-pilot-nora.service").read_text())
            self.assertIn("/venv/bin/python -m company_pilot --config", (destination / "company-pilot-nora.service").read_text())
            self.assertNotIn(" operator watch", (destination / "company-pilot-nora.service").read_text())
            self.assertFalse((destination / "gateway-approved.json").exists())
            self.assertFalse((destination / "nora-approved.json").exists())
            self.assertFalse((destination / "openclaw.json").exists())

    def test_refuses_overwrite_personal_paths_and_unsafe_exec_or_port(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "stage"
            bundle.render_bundle(target, openclaw_executable="/usr/bin/openclaw", code_commit="a" * 40)
            with self.assertRaises(FileExistsError):
                bundle.render_bundle(target, openclaw_executable="/usr/bin/openclaw", code_commit="a" * 40)
            for executable in ("/root/.openclaw/openclaw", "/Users/operator/openclaw", "/usr/bin/openclaw %h", "openclaw", "/usr/../root/openclaw"):
                with self.subTest(executable=executable), self.assertRaises(ValueError):
                    bundle.render_bundle(Path(root) / "bad", openclaw_executable=executable, code_commit="a" * 40)
            for port in (18789, 18811, True, 0, 65536):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    bundle.render_bundle(Path(root) / "bad", openclaw_executable="/usr/bin/openclaw", code_commit="a" * 40, gateway_port=port)
            for destination in ("/etc/company-pilot/stage", "/etc/systemd/system/stage", "/var/lib/company-pilot/stage", "/opt/company-pilot/stage", str(Path(root) / ".openclaw" / "stage")):
                with self.subTest(destination=destination), self.assertRaises(ValueError):
                    bundle.render_bundle(destination, openclaw_executable="/usr/bin/openclaw", code_commit="a" * 40)


if __name__ == "__main__":
    unittest.main()
