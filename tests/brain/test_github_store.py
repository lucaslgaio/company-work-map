"""E1 Git data API simulation, including rewrite/role/credential boundaries."""
import base64
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from company_pilot.brain import AccessDenied, Brain, ConcurrentWrite, GitHubError, GitHubStore, IntegrityError
from company_pilot.brain.stores import BINARY_PREFIX, canonical, digest


class FakeGitHub(GitHubStore):
    def __init__(self, root):
        super().__init__("synthetic-owner/synthetic-company", development=True, mirror_dir=root)
        self.head = None
        self.history = {}
        self.trees = {}
        self.blobs = {}
        self.calls = []
        self.serial = 0
        self.conflict_next = False
        self.login = "authenticated-operator"
        self.permissions = {"pull": True, "push": True, "admin": True}
        self.private = True

    def _request(self, path, method="GET", data=None, raw=False):
        self.calls.append((path, method, copy.deepcopy(data)))
        prefix = "repos/" + self.repo
        if path == "user":
            return {"login": self.login}
        if path == prefix:
            return {"private": self.private, "owner": {"type": "User", "login": "synthetic-owner"}, "permissions": self.permissions}
        if "/collaborators/" in path:
            raise GitHubError(404)
        if path == prefix + "/git/ref/heads/company-state":
            if self.head is None:
                raise GitHubError(404)
            return {"object": {"sha": self.head}}
        if "/compare/" in path:
            old, new = path.rsplit("/", 1)[1].split("...")
            cursor = new
            while cursor in self.history:
                if cursor == old:
                    return {"status": "ahead", "merge_base_commit": {"sha": old}}
                parents = self.history[cursor]["parents"]
                cursor = parents[0] if parents else None
            return {"status": "diverged", "merge_base_commit": {"sha": "unknown"}}
        if "/tarball/" in path:
            head = path.rsplit("/", 1)[1]
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for key, value in self.history[head]["files"].items():
                    content = base64.b64decode(value[len(BINARY_PREFIX):]) if value.startswith(BINARY_PREFIX) else value.encode()
                    item = tarfile.TarInfo("synthetic-root/" + key)
                    item.size = len(content)
                    archive.addfile(item, io.BytesIO(content))
            return stream.getvalue()
        if path == prefix + "/git/blobs" and method == "POST":
            sha = digest(data["content"])
            self.blobs[sha] = BINARY_PREFIX + data["content"]
            return {"sha": sha}
        if path == prefix + "/git/trees" and method == "POST":
            files = dict(self.trees.get(data.get("base_tree"), {}))
            for entry in data["tree"]:
                if "content" in entry:
                    files[entry["path"]] = entry["content"]
                elif entry.get("sha") is None:
                    files.pop(entry["path"], None)
                else:
                    files[entry["path"]] = self.blobs[entry["sha"]]
            sha = digest(canonical(files))
            self.trees[sha] = files
            return {"sha": sha}
        if path == prefix + "/git/commits" and method == "POST":
            self.serial += 1
            sha = f"synthetic-commit-{self.serial}"
            self.history[sha] = {"tree": {"sha": data["tree"]}, "parents": data["parents"], "files": self.trees[data["tree"]]}
            return {"sha": sha}
        if "/git/commits/" in path:
            return self.history[path.rsplit("/", 1)[1]]
        if path == prefix + "/git/refs" and method == "POST":
            if self.head is not None:
                raise GitHubError(422)
            self.head = data["sha"]
            return {}
        if path == prefix + "/git/refs/heads/company-state" and method == "PATCH":
            if self.conflict_next:
                self.conflict_next = False
                raise GitHubError(422)
            if data.get("force") is not False:
                raise AssertionError("force:false is required")
            self.head = data["sha"]
            return {}
        raise AssertionError((path, method))


class GitHubAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FakeGitHub(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_tree_commit_ref_and_actor_are_provider_derived(self):
        brain = Brain(self.store)
        brain.initialize("Empresa de teste", synthetic=True)
        first = self.store.head
        brain.update_policy({"tool_hints": ["ferramenta sintética"]}, "f5112a03-90ea-454a-9e86-683f0064fb91")
        self.assertNotEqual(first, self.store.head)
        self.assertEqual(brain.snapshot()["company"]["founders"], [self.store.login])
        updates = [data for path, method, data in self.store.calls if method == "PATCH"]
        self.assertTrue(updates)
        self.assertTrue(all(data["force"] is False for data in updates))
        self.assertTrue((Path(self.temp.name) / "verified.json").exists())
        self.assertFalse(any("contents" in path for path, _, _ in self.store.calls))

    def test_final_ref_race_is_retried_without_losing_state(self):
        brain = Brain(self.store)
        brain.initialize("Empresa de teste", synthetic=True)
        self.store.conflict_next = True
        brain.update_policy({"tool_hints": ["estado após corrida"]}, "336917a9-eb5b-46f5-9fbd-fef37a10c31b")
        self.assertEqual(brain.snapshot()["company"]["tool_hints"], ["estado após corrida"])
        self.assertEqual(len([x for x in self.store.history.values() if x["parents"]]), 2)

    def test_force_rewrite_halts_and_keeps_verified_mirror(self):
        brain = Brain(self.store)
        brain.initialize("Empresa de teste", synthetic=True)
        verified = (Path(self.temp.name) / "verified.json").read_text()
        self.store.history["rewritten"] = {"tree": {"sha": "bad"}, "parents": [], "files": {}}
        self.store.head = "rewritten"
        with self.assertRaisesRegex(IntegrityError, "non_fast_forward"):
            self.store.read()
        self.assertEqual((Path(self.temp.name) / "verified.json").read_text(), verified)
        self.assertEqual(json.loads((Path(self.temp.name) / "integrity-alert.json").read_text())["state"], "halted")
        trusted_head = json.loads(verified)["head"]
        self.store.head = trusted_head
        with self.assertRaisesRegex(IntegrityError, "requires_operator"):
            self.store.read()
        self.assertEqual(self.store.reconcile_mirror()["state"], "resolved")
        self.assertEqual(self.store.read().head, trusted_head)

    def test_branch_disappearance_and_public_repo_are_denied(self):
        Brain(self.store).initialize("Empresa de teste", synthetic=True)
        self.store.head = None
        with self.assertRaises(IntegrityError):
            self.store.read()
        self.store.private = False
        with self.assertRaises(AccessDenied):
            self.store.read()

    def test_read_only_cannot_write_and_native_roles_are_not_invented(self):
        brain = Brain(self.store)
        brain.initialize("Empresa de teste", synthetic=True)
        plan = brain.access_plan([{"login": "founder", "role": "admin"}, {"login": "lucas-reader", "role": "read"}, {"login": "member", "role": "write"}])
        self.assertEqual([x["supported"] for x in plan["requests"]], [False, False, True])
        self.assertTrue(all(x["actual"] == "none" for x in plan["requests"]))
        self.assertFalse(plan["applied"])
        self.assertFalse(any(method != "GET" and "/collaborators/" in path for path, method, _ in self.store.calls))
        self.store.permissions = {"pull": True, "push": False, "admin": False}
        self.assertTrue(brain.snapshot())
        with self.assertRaises(AccessDenied):
            brain.update_policy({"tool_hints": []}, "d5be06a7-bd44-40b1-bd4b-8c37cefbbe62")

    def test_remote_store_exposes_no_actor_override(self):
        with self.assertRaises(TypeError):
            GitHubStore("owner/repo", mirror_dir=self.temp.name, actor="forged")

    def test_binary_attachment_is_a_private_real_git_blob(self):
        import hashlib
        brain = Brain(self.store)
        brain.initialize("Empresa de teste", synthetic=True, screenshots={"founder": True, "team": False})
        content = b"\x89PNG\r\n\x1a\nSYNTHETIC-ONLY"
        result = brain.upload_attachment("9dc4272e-ec39-4259-8106-84152a7931b4", content, hashlib.sha256(content).hexdigest())
        self.assertTrue(result["inspect_url"].startswith("https://github.com/"))
        self.assertTrue(any(path.endswith("/git/blobs") for path, _, _ in self.store.calls))
        self.assertEqual(len([path for path in self.store.read().files if path.startswith("attachments/")]), 1)


if __name__ == "__main__":
    unittest.main()
