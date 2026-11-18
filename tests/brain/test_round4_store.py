"""NEW report-derived F2/F16 fixtures, not the original onboarding export.

Actual Brain/GitHubStore code, injected Git-data API and subprocess responses.
No request reaches GitHub and fake request counts are not latency measurements.
"""
import copy
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from company_pilot.brain import AccessDenied, Brain, ConcurrentWrite, GitHubError, GitHubStore, IntegrityError
from company_pilot.brain.stores import canonical, digest
from tests.brain.test_github_store import FakeGitHub


class Round4GitHub(FakeGitHub):
    def __init__(self, root):
        super().__init__(root)
        self.empty_status = None
        self.head_failure = None
        self.auth_failure = None
        self.fail_tree = False
        self.default_branch = "main"
        self.extra_heads = {}
        self.seed_race = False
        self.seed_parent_race = False
        self.user_identity = 41
        self.repository_identity = 73

    def _request(self, path, method="GET", data=None, raw=False):
        prefix = "repos/" + self.repo
        if path == "user" and self.auth_failure:
            raise GitHubError(self.auth_failure)
        if path == prefix + "/git/ref/heads/" + self.branch:
            if self.head_failure:
                raise GitHubError(self.head_failure)
            if self.head is None and not self.history and self.empty_status:
                self.calls.append((path, method, copy.deepcopy(data)))
                return GitHubStore._request(self, path, method, data, raw)
            if self.branch != "company-state":
                self.calls.append((path, method, copy.deepcopy(data)))
                if self.head is None:
                    raise GitHubError(404)
                return {"object": {"sha": self.head}}
        if self.branch != "company-state" and path == prefix + "/git/refs/heads/" + self.branch and method == "PATCH":
            self.calls.append((path, method, copy.deepcopy(data)))
            if data.get("force") is not False:
                raise AssertionError("Never force a ref")
            self.head = data["sha"]
            return {}
        if path == prefix + "/contents/.company-pilot-bootstrap" and method == "PUT":
            self.calls.append((path, method, copy.deepcopy(data)))
            if self.history or self.seed_race:
                if self.seed_race:
                    self.history["competing-head"] = {"tree": {"sha": "competing-tree"}, "parents": [], "files": {"peer.txt": "Do not overwrite"}}
                    self.trees["competing-tree"] = {"peer.txt": "Do not overwrite"}
                    self.head = "competing-head"
                raise GitHubError(422)
            if "sha" in data:
                raise AssertionError("Seed must be create-only")
            files = {".company-pilot-bootstrap": base64.b64decode(data["content"]).decode()}
            parents = []
            if self.seed_parent_race:
                files["peer.txt"] = "Preserve competing default-branch content"
                parents = [{"sha": "competing-default-head"}]
            tree = digest(canonical(files))
            self.trees[tree] = files
            self.serial += 1
            seed = f"synthetic-seed-{self.serial}"
            self.history[seed] = {"tree": {"sha": tree}, "parents": parents, "files": files}
            if self.branch == self.default_branch:
                self.head = seed
            else:
                self.extra_heads[self.default_branch] = seed
            return {"commit": {"sha": seed}}
        if self.fail_tree and path == prefix + "/git/trees" and method == "POST":
            raise GitHubError(int(self.fail_tree))
        if path == prefix + "/git/trees" and method == "POST" and not self.history and self.empty_status:
            raise GitHubError(409, code="empty_repository")
        result = super()._request(path, method, data, raw)
        if path == "user":
            result["id"] = self.user_identity
        elif path == prefix:
            result.update(id=self.repository_identity, default_branch=self.default_branch)
        return result


def empty_response(message="Git Repository is empty.", status=409):
    return subprocess.CompletedProcess(["fake-gh"], 1,
        json.dumps({"message": message}).encode(), f"gh: {message} (HTTP {status})\n".encode())


def count(calls, suffix):
    return sum(suffix in path for path, _method, _data in calls)


class Round4StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Round4GitHub(self.temp.name)

    def initialize(self):
        return Brain(self.store).initialize("NEW synthetic F2/F16 fixture", synthetic=True)

    def test_F2_genuine_empty409_reads_empty_then_initializes_ready(self):
        self.store.empty_status = 409
        with patch("company_pilot.brain.stores.subprocess.run", return_value=empty_response()) as run:
            snapshot = self.store.read()
            self.assertEqual(snapshot.head, None)
            self.assertEqual(snapshot.files, {})
            receipt = self.initialize()
            self.assertEqual(receipt["state"], "ready")
            self.assertTrue(self.store.read().files["meta/index.json"])
            self.assertTrue(run.called)
            seeds = [data for path, method, data in self.store.calls if "/contents/" in path and method == "PUT"]
            self.assertEqual(len(seeds), 1)
            self.assertNotIn("sha", seeds[0])
            self.assertNotIn(".company-pilot-bootstrap", self.store.read().files)
        print(json.dumps({"fixture": "NEW report-derived, no original replay", "empty_http": 409,
                          "init_state": receipt["state"], "head_created": self.store.head is not None}))

    def test_F16_repeat_command_and_fresh_adapter_reuse_verified_content(self):
        self.initialize()
        expected = Brain(self.store).interview_next()
        self.store.calls.clear()
        actual = Brain(self.store).interview_next()
        calls = list(self.store.calls)
        self.assertEqual(actual, expected)
        measured = {"requests": len(calls), "tarball": count(calls, "/tarball/"),
                    "head": count(calls, "/git/ref/heads/"), "user": sum(path == "user" for path, _, _ in calls)}
        print(json.dumps({"fixture": "NEW report-derived fake request benchmark", "repeat_interview_next": measured}))
        self.assertEqual(measured["tarball"], 0)
        self.assertGreaterEqual(measured["head"], 1)
        self.assertGreaterEqual(measured["user"], 1)
        fresh = Round4GitHub(self.temp.name)
        fresh.head, fresh.history, fresh.trees = self.store.head, copy.deepcopy(self.store.history), copy.deepcopy(self.store.trees)
        self.assertEqual(Brain(fresh).interview_next(), expected)
        self.assertEqual(count(fresh.calls, "/tarball/"), 0)
        self.assertGreaterEqual(count(fresh.calls, "/git/ref/heads/"), 1)

    def test_F16_interview_next_uses_one_fresh_authorized_snapshot_per_call(self):
        self.initialize()
        expected = Brain(self.store).interview_next()
        for _ in range(2):
            self.store.calls.clear()
            self.assertEqual(Brain(self.store).interview_next(), expected)
            paths = [path for path, method, _ in self.store.calls if method == "GET"]
            self.assertEqual(paths.count("user"), 1)
            self.assertEqual(paths.count("repos/" + self.store.repo), 1)
            self.assertEqual(count(self.store.calls, "/git/ref/heads/"), 1)
            self.assertEqual(len(paths), 3)
            self.assertEqual(count(self.store.calls, "/tarball/"), 0)
        self.store.permissions = {"pull": False, "push": False, "admin": False}
        with self.assertRaises(AccessDenied):
            Brain(self.store).interview_next()
        self.store.permissions = {"pull": True, "push": True, "admin": True}
        self.store.login = "another-authenticated-member"
        with self.assertRaisesRegex(AccessDenied, "founder_authority_required"):
            Brain(self.store).interview_next()

    def test_F2_nonempty409_and_auth_or_network_errors_are_not_empty(self):
        self.store.empty_status = 409
        for status, text in ((409, "Another conflict"), (401, "Bad credentials"), (403, "Forbidden"), (500, "Unavailable")):
            with self.subTest(status=status), patch("company_pilot.brain.stores.subprocess.run", return_value=empty_response(text, status)):
                with self.assertRaises(GitHubError) as caught:
                    self.store.read()
                self.assertEqual(caught.exception.status, status)
                self.assertIsNone(caught.exception.code)
        contradictory = empty_response("Another conflict")
        contradictory.stderr = b"gh: Git Repository is empty. (HTTP 409)\n"
        with patch("company_pilot.brain.stores.subprocess.run", return_value=contradictory):
            with self.assertRaises(GitHubError) as caught:
                self.store.read()
            self.assertIsNone(caught.exception.code)
        with patch("company_pilot.brain.stores.subprocess.run", side_effect=subprocess.TimeoutExpired("fake-gh", 45)):
            with self.assertRaises(GitHubError) as caught:
                self.store.read()
            self.assertEqual(caught.exception.status, 0)
        self.assertFalse(any(method != "GET" for _, method, _ in self.store.calls))

    def test_F2_default_state_branch_seed_is_followed_by_nonforce_normal_commit(self):
        self.store.branch = self.store.default_branch
        self.store.empty_status = 409
        with patch("company_pilot.brain.stores.subprocess.run", return_value=empty_response()):
            self.assertEqual(self.initialize()["state"], "ready")
        snapshot = self.store.read()
        self.assertIn("meta/index.json", snapshot.files)
        self.assertNotIn(".company-pilot-bootstrap", snapshot.files)
        updates = [data for _, method, data in self.store.calls if method == "PATCH"]
        self.assertTrue(updates)
        self.assertTrue(all(data["force"] is False for data in updates))

    def test_F2_seed_race_preserves_competing_nonempty_branch(self):
        self.store.empty_status = 409
        self.store.seed_race = True
        with patch("company_pilot.brain.stores.subprocess.run", return_value=empty_response()):
            with self.assertRaises(ConcurrentWrite):
                self.store.commit(None, {"new-state.txt": "Do not publish over competitor"}, "fixture init")
        self.assertEqual(self.store.head, "competing-head")
        self.assertEqual(self.store.history[self.store.head]["files"], {"peer.txt": "Do not overwrite"})
        self.assertFalse((Path(self.temp.name) / "verified.json").exists())
        self.assertFalse(any(path.endswith("/git/trees") and method == "POST" for path, method, _ in self.store.calls))

    def test_F2_downstream409_does_not_return_ready_or_cache_incomplete_seed(self):
        self.store.empty_status = 409
        self.store.fail_tree = 409
        with patch("company_pilot.brain.stores.subprocess.run", return_value=empty_response()):
            with self.assertRaises(GitHubError) as caught:
                self.initialize()
        self.assertEqual(caught.exception.status, 409)
        self.assertIsNone(self.store.head)
        self.assertFalse((Path(self.temp.name) / "verified.json").exists())
        # The harmless first commit is not a ready company snapshot. Retry does
        # not seed again and only publishes the complete desired state.
        self.store.fail_tree = False
        self.assertEqual(self.initialize()["state"], "ready")
        self.assertEqual(sum("/contents/" in path for path, _, _ in self.store.calls), 1)

    def test_F2_competing_default_branch_parent_is_not_treated_as_empty_success(self):
        self.store.empty_status = 409
        self.store.seed_parent_race = True
        with patch("company_pilot.brain.stores.subprocess.run", return_value=empty_response()):
            with self.assertRaisesRegex(ConcurrentWrite, "initialization_race"):
                self.store.commit(None, {"company.txt": "Do not publish"}, "fixture init")
        self.assertIsNone(self.store.head)
        self.assertFalse((Path(self.temp.name) / "verified.json").exists())
        seeded = self.store.history[self.store.extra_heads[self.store.default_branch]]
        self.assertEqual(seeded["files"]["peer.txt"], "Preserve competing default-branch content")

    def test_F16_cached_content_does_not_cache_permissions_or_revoke_checks(self):
        self.initialize()
        self.store.read()
        self.store.permissions = {"pull": True, "push": False, "admin": False}
        self.assertIn("meta/index.json", self.store.read().files)
        with self.assertRaises(AccessDenied):
            self.store.commit(self.store.head, self.store.read().files, "forbidden")
        self.store.permissions = {"pull": False, "push": False, "admin": False}
        with self.assertRaises(AccessDenied):
            self.store.read()
        self.store.permissions = {"pull": True, "push": True, "admin": True}
        for status in (401, 403):
            self.store.auth_failure = status
            with self.subTest(status=status), self.assertRaises(GitHubError) as caught:
                self.store.read()
            self.assertEqual(caught.exception.status, status)

    def test_F16_legacy_mirror_refetches_once_before_becoming_identity_bound_cache(self):
        self.initialize()
        path = Path(self.temp.name) / "verified.json"
        record = json.loads(path.read_text())
        expected = copy.deepcopy(record["files"])
        record.pop("cache")
        record.pop("cache_sha256")
        path.write_text(json.dumps(record))
        self.store.calls.clear()
        self.assertEqual(self.store.read().files, expected)
        self.assertEqual(count(self.store.calls, "/tarball/"), 1)
        cache = json.loads(path.read_text())["cache"]
        self.assertEqual(cache["actor_id"], self.store.login)
        self.assertNotIn("permissions", cache)
        self.assertNotIn("default_branch", cache)
        self.store.calls.clear()
        self.store.read()
        self.assertEqual(count(self.store.calls, "/tarball/"), 0)

    def test_F16_fresh_head_failure_never_falls_back_to_cached_content(self):
        self.initialize()
        before = (Path(self.temp.name) / "verified.json").read_bytes()
        for status in (409, 500):
            self.store.head_failure = status
            with self.subTest(status=status), self.assertRaises(GitHubError) as caught:
                self.store.read()
            self.assertEqual(caught.exception.status, status)
            self.assertEqual((Path(self.temp.name) / "verified.json").read_bytes(), before)

    def test_F16_changed_actor_or_native_identity_refetches_content(self):
        self.initialize()
        expected = self.store.read().files
        for login, identity in (("another-authorized-reader", 99), ("another-authorized-reader", 100)):
            self.store.login, self.store.user_identity = login, identity
            self.store.calls.clear()
            self.assertEqual(self.store.read().files, expected)
            self.assertEqual(count(self.store.calls, "/tarball/"), 1)
            self.assertTrue(any(path == "user" for path, _, _ in self.store.calls))

    def test_F16_repo_branch_or_repository_identity_never_cross_uses_cache(self):
        for attribute, value in (("repo", "another-owner/another-company"), ("branch", "different-state"), ("repository_identity", 999)):
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as root:
                store = Round4GitHub(root)
                Brain(store).initialize("Synthetic scope control", synthetic=True)
                original = (Path(root) / "verified.json").read_bytes()
                setattr(store, attribute, value)
                with self.assertRaises(IntegrityError):
                    store.read()
                self.assertEqual((Path(root) / "verified.json").read_bytes(), original)

    def test_F16_remote_head_advance_fetches_and_verifies_new_snapshot(self):
        self.initialize()
        old = (Path(self.temp.name) / "verified.json").read_bytes()
        old_head = self.store.head
        Brain(self.store).update_policy({"tool_hints": ["new permitted tool"]}, "c41bd0ab-cd75-4671-adf8-6f7dc2f58df9")
        new_head = self.store.head
        self.assertNotEqual(old_head, new_head)
        (Path(self.temp.name) / "verified.json").write_bytes(old)
        self.store.calls.clear()
        snapshot = self.store.read()
        self.assertEqual(snapshot.head, new_head)
        self.assertEqual(count(self.store.calls, "/tarball/"), 1)
        self.assertTrue(any("/compare/" in path for path, _, _ in self.store.calls))

    def test_F16_corrupted_cache_and_state_halt_instead_of_being_served(self):
        for mutation in ("files", "metadata", "hash", "state"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                store = Round4GitHub(root)
                Brain(store).initialize("Synthetic corruption control", synthetic=True)
                path = Path(root) / "verified.json"
                data = json.loads(path.read_text())
                if mutation in ("files", "state"):
                    data["files"]["meta/index.json"] = "{}"
                elif mutation == "metadata":
                    data["cache"]["actor_id"] = "forged-cache-owner"
                else:
                    data["cache_sha256"] = "0" * 64
                if mutation == "state":
                    # Even a recomputed envelope does not override the ledger's
                    # retained business-state hash. No tamper-proof claim.
                    data["manifest_sha256"] = digest(canonical(data["files"]))
                    data["cache"]["manifest_sha256"] = data["manifest_sha256"]
                    data["cache_sha256"] = digest(canonical(data["cache"]))
                path.write_text(json.dumps(data))
                store.calls.clear()
                with self.assertRaises(IntegrityError):
                    store.read()
                self.assertEqual(count(store.calls, "/tarball/"), 0)
                self.assertEqual(json.loads((Path(root) / "integrity-alert.json").read_text())["state"], "halted")

    def test_F16_snapshot_mutation_and_failed_write_do_not_poison_verified_cache(self):
        self.initialize()
        before = self.store.read()
        before.files["meta/index.json"] = "caller mutation"
        self.assertNotEqual(self.store.read().files["meta/index.json"], "caller mutation")
        path = Path(self.temp.name) / "verified.json"
        original = path.read_bytes()
        self.store.fail_tree = 503
        event = "7c18055b-685c-4c35-ab48-57d105508782"
        with self.assertRaises(GitHubError):
            Brain(self.store).update_policy({"tool_hints": ["retry after failure"]}, event)
        self.assertEqual(path.read_bytes(), original)
        self.store.fail_tree = False
        Brain(self.store).update_policy({"tool_hints": ["retry after failure"]}, event)
        self.assertEqual(Brain(self.store).snapshot()["company"]["tool_hints"], ["retry after failure"])

    def test_F16_explicit_reconciliation_revalidates_archive_and_admin(self):
        self.initialize()
        self.store.calls.clear()
        self.assertEqual(self.store.reconcile_mirror()["state"], "resolved")
        self.assertEqual(count(self.store.calls, "/tarball/"), 1)
        self.store.permissions = {"pull": True, "push": True, "admin": False}
        with self.assertRaises(AccessDenied):
            self.store.reconcile_mirror()


if __name__ == "__main__":
    unittest.main()
