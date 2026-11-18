"""The review package records the real commit. The git file can still say HEAD."""
import json
import unittest
from pathlib import Path

from scripts.review_package import stamped_release

ROOT = Path(__file__).resolve().parents[2]


class ReleaseStampTests(unittest.TestCase):
    def test_package_records_the_real_sha_and_git_keeps_head(self):
        raw = (ROOT / "founder" / "pilot-release.json").read_bytes()
        stored = json.loads(raw)
        self.assertEqual(stored["code_commit"], "HEAD")
        commit = "a" * 40
        stamped = json.loads(stamped_release(raw, commit))
        self.assertEqual(stamped["code_commit"], commit)
        self.assertIn("SHA gravado", stamped["code_commit_resolution"])
        self.assertEqual(json.loads((ROOT / "founder" / "pilot-release.json").read_text(encoding="utf-8"))["code_commit"], "HEAD")
        with self.assertRaises(ValueError):
            stamped_release(raw, "HEAD")


if __name__ == "__main__":
    unittest.main()
