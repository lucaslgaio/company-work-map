"""Atomic repository snapshots and retained, verified mirrors.

LocalStore is a deterministic development adapter. GitHubStore obtains identity
from GitHub and publishes a whole transaction using Git tree/commit/ref APIs.
No token is read, stored or included in diagnostics by this module.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.parse import quote

BINARY_PREFIX = "binary-base64:"
_BOOTSTRAP_PATH = ".company-pilot-bootstrap"
_BOOTSTRAP_TEXT = "Company Pilot repository bootstrap. Company records use the configured state branch.\n"


class BrainError(ValueError):
    pass


class ConcurrentWrite(BrainError):
    pass


class IntegrityError(BrainError):
    pass


class AccessDenied(BrainError):
    pass


class GitHubError(BrainError):
    def __init__(self, status=0, *, code=None):
        self.status = status
        self.code = "empty_repository" if status == 409 and code == "empty_repository" else None
        super().__init__(f"github_request_failed:{status}")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    head: str | None
    files: dict[str, str]


def validate_files(files, previous=None):
    for path, content in files.items():
        if path.startswith("/") or ".." in Path(path).parts or "\\" in path:
            raise IntegrityError("unsafe_repository_path")
        if not isinstance(content, str):
            raise IntegrityError("non_text_record")
    for path, content in (previous or {}).items():
        if path.startswith(("sources/", "records/", "ledger/", "attachments/")) and files.get(path) != content:
            raise IntegrityError("retained_evidence_changed:" + path)
    previous_hash = None
    for number, path in enumerate(sorted(p for p in files if p.startswith("ledger/")), 1):
        event = json.loads(files[path])
        claimed = event.pop("hash", None)
        if event.get("sequence") != number or event.get("previous_hash") != previous_hash:
            raise IntegrityError("broken_event_chain")
        if digest(canonical(event)) != claimed:
            raise IntegrityError("event_hash_mismatch")
        previous_hash = claimed


def _verified_files(files, previous=None):
    """Verify content and the business-state hash before treating it as cached."""
    try:
        if not isinstance(files, dict):
            raise IntegrityError("verified_files_required")
        validate_files(files, previous)
        ledgers = sorted(path for path in files if path.startswith("ledger/"))
        if ledgers:
            expected = json.loads(files[ledgers[-1]]).get("state_sha256")
            if expected is not None:
                actual = digest(canonical({key: value for key, value in files.items()
                    if not key.startswith(("ledger/", "views/", "notes/")) and key != "README.md"}))
                if actual != expected:
                    raise IntegrityError("cached_state_not_supported_by_ledger")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        if isinstance(error, IntegrityError):
            raise
        raise IntegrityError("verified_snapshot_invalid") from error


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(canonical(value))
        temporary = Path(stream.name)
    os.replace(temporary, path)


class VerifiedMirror:
    """Keeps the last trusted full snapshot and halts on suspicious history."""

    def __init__(self, root):
        self.root = Path(root)

    def _read_record(self):
        path = self.root / "verified.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or "head" not in data or data["head"] is not None and not isinstance(data["head"], str)
                    or digest(canonical(data["files"])) != data["manifest_sha256"]):
                raise IntegrityError("local_mirror_changed")
            _verified_files(data["files"])
            cache = data.get("cache")
            if cache is not None:
                keys = {"schema_version", "repo", "branch", "actor_id", "actor_native_id", "repository_id", "head", "tree", "manifest_sha256"}
                if (not isinstance(cache, dict) or set(cache) != keys or type(cache["schema_version"]) is not int or cache["schema_version"] != 1
                        or any(not isinstance(cache[key], str) or not cache[key] for key in ("repo", "branch", "actor_id", "head", "tree"))
                        or any(cache[key] is not None and (type(cache[key]) is not int or cache[key] <= 0) for key in ("actor_native_id", "repository_id"))
                        or cache["head"] != data["head"] or cache["manifest_sha256"] != data["manifest_sha256"]
                        or data.get("cache_sha256") != digest(canonical(cache))):
                    raise IntegrityError("local_verified_cache_changed")
            elif "cache_sha256" in data:
                raise IntegrityError("local_verified_cache_changed")
            return data
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            if isinstance(error, IntegrityError):
                raise
            raise IntegrityError("local_mirror_changed") from error

    def read(self):
        data = self._read_record()
        return Snapshot(data["head"], data["files"]) if data else None

    def accept(self, snapshot, *, cache=None):
        previous = self.read()
        _verified_files(snapshot.files, previous.files if previous else None)
        data = {
            "head": snapshot.head, "files": snapshot.files,
            "manifest_sha256": digest(canonical(snapshot.files)),
        }
        if cache is not None:
            data["cache"] = {**cache, "head": snapshot.head, "manifest_sha256": data["manifest_sha256"]}
            data["cache_sha256"] = digest(canonical(data["cache"]))
        _write_json(self.root / "verified.json", data)

    def alert(self, reason, observed_head):
        _write_json(self.root / "integrity-alert.json", {
            "state": "halted", "reason": reason, "observed_head": observed_head,
            "recovery": "Preserve verified.json; reconcile history with the operating owner before resuming.",
        })


class LocalStore:
    """E1 fixture identity only; never represents a remote authentication test."""

    development = True

    def __init__(self, root, actor="fixture-founder", permissions=None):
        self.root = Path(root)
        self.actor = actor
        self.permissions = set(permissions if permissions is not None else ("read", "write", "admin"))

    def authenticated_actor(self):
        return {"id": self.actor, "permissions": sorted(self.permissions), "provider": "local_fixture"}

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "LOCK").open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def read(self):
        if "read" not in self.permissions:
            raise AccessDenied("repository_read_required")
        head_path = self.root / "HEAD"
        if not head_path.exists():
            return Snapshot(None, {})
        head = json.loads(head_path.read_text())["head"]
        data = json.loads((self.root / "snapshots" / (head + ".json")).read_text())
        if digest(canonical(data["files"])) != data["manifest_sha256"]:
            raise IntegrityError("local_snapshot_changed")
        validate_files(data["files"])
        return Snapshot(head, data["files"])

    def commit(self, expected_head, files, message):
        if "write" not in self.permissions:
            raise AccessDenied("repository_write_required")
        with self._lock():
            before = self.read()
            if before.head != expected_head:
                raise ConcurrentWrite("snapshot_advanced")
            validate_files(files, before.files)
            head = digest(canonical({"parent": expected_head, "files": files}))
            _write_json(self.root / "snapshots" / (head + ".json"), {
                "files": files, "manifest_sha256": digest(canonical(files)),
                "parent": expected_head, "message": message,
            })
            _write_json(self.root / "HEAD", {"head": head})
            return Snapshot(head, files)

    def inspect_url(self, path):
        return "local-fixture:" + path


class GitHubStore:
    """Uses the user's authenticated gh provider; actor overrides are forbidden.

    A personal repository is accepted only in explicitly synthetic development
    mode. Branch separation is organizational, never an access-control boundary.
    A mirror detects rewrites after first trust; this is not tamper-proof storage.
    """

    def __init__(self, repo, branch="company-state", development=False, mirror_dir=None, gh="gh", allow_personal_collaborators=True):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise BrainError("invalid_repository")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", branch) or ".." in branch:
            raise BrainError("invalid_state_branch")
        if mirror_dir is None:
            raise BrainError("retained_mirror_directory_required")
        self.repo, self.branch, self.development, self.gh = repo, branch, development, gh
        self.allow_personal_collaborators = allow_personal_collaborators
        self.mirror = VerifiedMirror(mirror_dir)
        self._trees = {}

    def _request(self, path, method="GET", data=None, raw=False):
        command = [self.gh, "api", "--hostname", "github.com", path, "--method", method]
        if data is not None:
            command += ["--input", "-"]
        env = dict(os.environ, GH_PROMPT_DISABLED="1")
        try:
            result = subprocess.run(command, input=canonical(data).encode() if data is not None else None,
                                    capture_output=True, timeout=45, env=env)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubError() from error
        if result.returncode:
            match = re.search(rb"HTTP (\d{3})", result.stderr)
            status = int(match.group(1)) if match else 0
            empty = False
            if status == 409:
                message = None
                try:
                    body = json.loads(result.stdout)
                    message = body.get("message") if isinstance(body, dict) else None
                except (ValueError, UnicodeDecodeError):
                    pass
                empty = message in ("Git Repository is empty", "Git Repository is empty.") if isinstance(message, str) else bool(re.fullmatch(rb"gh: Git Repository is empty\.? \(HTTP 409\)\s*", result.stderr))
            raise GitHubError(status, code="empty_repository" if empty else None)
        if raw:
            return result.stdout
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise GitHubError() from error

    def authenticated_actor(self):
        user = self._request("user")
        repo = self._request("repos/" + self.repo)
        if not repo.get("private"):
            raise AccessDenied("private_repository_required")
        if not self.development and not self.allow_personal_collaborators and repo.get("owner", {}).get("type") != "Organization":
            raise AccessDenied("client_requires_company_organization_and_native_roles")
        permissions = repo.get("permissions", {})
        granted = [name for name, native in (("read", "pull"), ("write", "push"), ("admin", "admin")) if permissions.get(native) is True]
        if (not isinstance(user.get("login"), str) or not user["login"] or "read" not in granted
                or any(value is not None and (type(value) is not int or value <= 0) for value in (user.get("id"), repo.get("id")))):
            raise AccessDenied("authenticated_repository_identity_required")
        return {"id": user["login"], "permissions": granted, "provider": "github",
                "native_user_id": user.get("id"), "repository_id": repo.get("id"),
                "default_branch": repo.get("default_branch"),
                "owner_type": repo.get("owner", {}).get("type"),
                "owner": repo.get("owner", {}).get("login"),
                "access_boundary": "repository", "native_founder_admin": repo.get("owner", {}).get("type") == "Organization"}

    def access_plan(self, requested):
        actor = self.authenticated_actor()
        result = []
        for entry in requested:
            login, desired = entry.get("login"), entry.get("role")
            if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]+", login) or desired not in ("read", "write", "admin"):
                raise BrainError("invalid_access_request")
            supported = actor["owner_type"] == "Organization" or (desired == "write" and login != actor["owner"]) or (login == actor["owner"] and desired == "admin")
            try:
                actual = self._request(f"repos/{self.repo}/collaborators/{login}/permission").get("permission", "none")
            except GitHubError as error:
                if error.status != 404:
                    raise
                actual = "none"
            result.append({"login": login, "desired": desired, "actual": actual, "supported": supported,
                           "action": "owner_operator_review" if supported else "unsupported_native_role"})
        plan = {"repository": self.repo, "operator": actor["owner"], "requests": result,
                "applied": False, "credentials_distributed": False}
        return {**plan, "plan_hash": digest(canonical(plan))}

    def _head(self):
        return self._head_info()[0]

    def _head_info(self):
        try:
            return self._request(f"repos/{self.repo}/git/ref/heads/{quote(self.branch)}")["object"]["sha"], False
        except GitHubError as error:
            if error.status == 404:
                return None, False
            if error.status == 409 and error.code == "empty_repository":
                return None, True
            raise

    def _bootstrap_empty(self, actor):
        """Create only a non-data first commit through Contents-empty semantics.

        Git refs cannot initialize a genuinely zero-commit repository. This
        create-only sentinel initializes its default branch; no company snapshot
        is accepted until the configured ref is published normally. A race may
        leave this harmless seed, but never overwrites a competitor's file/ref.
        """
        fresh = self.authenticated_actor()
        if "write" not in fresh["permissions"]:
            raise AccessDenied("repository_write_required")
        if self._content_identity(fresh) != self._content_identity(actor):
            raise ConcurrentWrite("authenticated_repository_identity_changed")
        head, empty = self._head_info()
        if head is not None or not empty:
            raise ConcurrentWrite("repository_no_longer_empty")
        branch = fresh.get("default_branch")
        if not isinstance(branch, str) or not branch or "\n" in branch or "\r" in branch:
            raise BrainError("empty_repository_default_branch_required")
        try:
            created = self._request(f"repos/{self.repo}/contents/{_BOOTSTRAP_PATH}", "PUT", {
                "message": "Initialize Company Pilot repository",
                "content": base64.b64encode(_BOOTSTRAP_TEXT.encode()).decode("ascii"),
            })  # No sha: creation only, never update an existing file.
        except GitHubError as error:
            if error.status in (409, 422):
                raise ConcurrentWrite("empty_repository_initialization_race") from error
            raise
        seed = created.get("commit", {}).get("sha") if isinstance(created, dict) else None
        if not isinstance(seed, str) or not seed:
            raise GitHubError()
        commit = self._request(f"repos/{self.repo}/git/commits/{seed}")
        if commit.get("parents") != []:
            raise ConcurrentWrite("empty_repository_initialization_race")
        current = self._head()
        if current != (seed if self.branch == branch else None):
            raise ConcurrentWrite("remote_advanced")
        return seed, seed if self.branch == branch else None

    def _content_identity(self, actor):
        return {"repo": self.repo, "branch": self.branch, "actor_id": actor["id"],
                "actor_native_id": actor.get("native_user_id"), "repository_id": actor.get("repository_id")}

    def _tree_key(self, actor, head):
        identity = self._content_identity(actor)
        return tuple(identity.values()) + (head,)

    def _cache(self, actor, head, tree):
        return {"schema_version": 1, **self._content_identity(actor), "head": head, "tree": tree}

    def read(self, *, _reconcile=False):
        return self.read_with_actor(_reconcile=_reconcile)[0]

    def read_with_actor(self, *, _reconcile=False):
        """Return this read's fresh authority and content, without retaining rights.

        A caller may authorize its one read operation with this actor instead of
        fetching the same state/permissions again inside the same operation.
        Neither this actor nor permissions enter the content mirror or a cache.
        Every invocation checks the authenticated user, native rights and HEAD.
        """
        actor = self.authenticated_actor()
        snapshot = self._read_verified(actor, self._head(), _reconcile=_reconcile)
        return snapshot, actor

    def _read_verified(self, actor, head, *, _reconcile=False):
        alert_path = self.mirror.root / "integrity-alert.json"
        if alert_path.exists() and json.loads(alert_path.read_text()).get("state") == "halted" and not _reconcile:
            raise IntegrityError("integrity_alert_requires_operator_reconciliation")
        try:
            record = self.mirror._read_record()
            trusted = Snapshot(record["head"], record["files"]) if record else None
            cache = record.get("cache") if record else None
            if cache and (cache["repo"] != self.repo or cache["branch"] != self.branch):
                raise IntegrityError("retained_mirror_scope_mismatch")
            if cache and cache["repository_id"] is not None and actor.get("repository_id") is not None and cache["repository_id"] != actor["repository_id"]:
                raise IntegrityError("retained_repository_identity_changed")
            if trusted and head != trusted.head:
                if head is None:
                    raise IntegrityError("remote_branch_disappeared")
                try:
                    compare = self._request(f"repos/{self.repo}/compare/{trusted.head}...{head}")
                except GitHubError as error:
                    if error.status == 404:
                        raise IntegrityError("trusted_history_unavailable") from error
                    raise
                if compare.get("status") != "ahead" or compare.get("merge_base_commit", {}).get("sha") != trusted.head:
                    raise IntegrityError("remote_non_fast_forward")
            if head is None:
                return Snapshot(None, {})
            identity = self._content_identity(actor)
            if not _reconcile and cache and cache["head"] == head and all(cache[key] == value for key, value in identity.items()):
                self._trees[self._tree_key(actor, head)] = cache["tree"]
                return Snapshot(head, dict(trusted.files))
            commit = self._request(f"repos/{self.repo}/git/commits/{head}")
            tree = commit["tree"]["sha"]
            self._trees[self._tree_key(actor, head)] = tree
            archive = self._request(f"repos/{self.repo}/tarball/{head}", raw=True)
            files, total = {}, 0
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
                for entry in bundle:
                    if entry.isdir():
                        continue
                    if not entry.isfile() or "/" not in entry.name:
                        raise IntegrityError("unsafe_archive_entry")
                    path = entry.name.split("/", 1)[1]
                    total += entry.size
                    if total > 64 * 1024 * 1024:
                        raise IntegrityError("state_snapshot_size_limit")
                    content = bundle.extractfile(entry).read()
                    files[path] = BINARY_PREFIX + base64.b64encode(content).decode("ascii") if path.startswith("attachments/") else content.decode("utf-8")
            _verified_files(files, trusted.files if trusted else None)
            snapshot = Snapshot(head, files)
            self.mirror.accept(snapshot, cache=self._cache(actor, head, tree))
            return snapshot
        except (IntegrityError, UnicodeDecodeError, tarfile.TarError) as error:
            self.mirror.alert(type(error).__name__ + ":" + str(error), head)
            if isinstance(error, IntegrityError):
                raise
            raise IntegrityError("remote_history_cannot_be_verified") from error

    def reconcile_mirror(self):
        """Explicit owner action after history repair; never force-pushes/deletes."""
        actor = self.authenticated_actor()
        if "admin" not in actor["permissions"]:
            raise AccessDenied("owner_operator_reconciliation_required")
        snapshot = self.read(_reconcile=True)
        _write_json(self.mirror.root / "integrity-alert.json", {
            "state": "resolved", "head": snapshot.head, "verified_by": actor["id"],
        })
        return {"state": "resolved", "head": snapshot.head}

    def commit(self, expected_head, files, message):
        actor = self.authenticated_actor()
        if "write" not in actor["permissions"]:
            raise AccessDenied("repository_write_required")
        head, empty_repository = self._head_info()
        if head != expected_head:
            raise ConcurrentWrite("remote_advanced")
        before = self._read_verified(actor, head)
        if before.head != expected_head:
            raise ConcurrentWrite("verified_snapshot_required")
        previous = before.files
        _verified_files(files, previous)
        bootstrap, write_head = None, expected_head
        if expected_head is None and empty_repository:
            bootstrap, write_head = self._bootstrap_empty(actor)
        changes = []
        for path, content in files.items():
            if previous.get(path) == content:
                continue
            entry = {"path": path, "mode": "100644", "type": "blob"}
            if content.startswith(BINARY_PREFIX):
                blob = self._request(f"repos/{self.repo}/git/blobs", "POST", {"content": content[len(BINARY_PREFIX):], "encoding": "base64"})
                entry["sha"] = blob["sha"]
            else:
                entry["content"] = content
            changes.append(entry)
        changes += [{"path": path, "mode": "100644", "type": "blob", "sha": None}
                    for path in previous if path not in files]
        body = {"tree": changes}
        if expected_head:
            body["base_tree"] = self._trees.get(self._tree_key(actor, expected_head)) or self._request(f"repos/{self.repo}/git/commits/{expected_head}")["tree"]["sha"]
        tree = self._request(f"repos/{self.repo}/git/trees", "POST", body)
        commit = self._request(f"repos/{self.repo}/git/commits", "POST", {
            "message": message, "tree": tree["sha"], "parents": [bootstrap] if bootstrap else [expected_head] if expected_head else [],
        })
        try:
            if write_head:
                self._request(f"repos/{self.repo}/git/refs/heads/{quote(self.branch)}", "PATCH", {"sha": commit["sha"], "force": False})
            else:
                self._request(f"repos/{self.repo}/git/refs", "POST", {"ref": "refs/heads/" + self.branch, "sha": commit["sha"]})
        except GitHubError as error:
            if error.status in (409, 422):
                raise ConcurrentWrite("remote_advanced") from error
            raise
        result = Snapshot(commit["sha"], dict(files))
        self._trees[self._tree_key(actor, result.head)] = tree["sha"]
        self.mirror.accept(result, cache=self._cache(actor, result.head, tree["sha"]))
        return result

    def inspect_url(self, path):
        return f"https://github.com/{self.repo}/blob/{self.branch}/{quote(path, safe='/')}"
