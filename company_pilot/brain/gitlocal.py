"""Local bare Git records. Tests and a simulated operator use this; production uses GitHubStore."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .stores import AccessDenied, BrainError, ConcurrentWrite, Snapshot, validate_files


class LocalGitStore:
    """One bare repository on branch company-state. Optional remote is fetched before read."""

    development = True

    def __init__(self, path, actor="fixture-founder", permissions=None, remote=None, branch="company-state"):
        self.path = Path(path)
        self.actor = actor
        self.permissions = set(permissions if permissions is not None else ("read", "write", "admin"))
        self.remote = str(Path(remote).resolve()) if remote else None
        self.branch = branch
        self._env = dict(os.environ)
        self._env.update(GIT_AUTHOR_NAME="company-pilot", GIT_AUTHOR_EMAIL="pilot@localhost",
                         GIT_COMMITTER_NAME="company-pilot", GIT_COMMITTER_EMAIL="pilot@localhost")

    def authenticated_actor(self):
        return {"id": self.actor, "permissions": sorted(self.permissions), "provider": "local_fixture"}

    def _git(self, *args, input=None, check=True, env=None):
        command = ["git", "-c", "safe.directory=*", "-c", "core.hooksPath=/dev/null",
                   "--git-dir", str(self.path), *args]
        result = subprocess.run(command, input=input, capture_output=True, env=env or self._env, timeout=30)
        if check and result.returncode:
            raise BrainError("git_local_failed")
        return result

    def _ensure(self):
        if (self.path / "HEAD").is_file():
            return
        self.path.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "init", "--bare", str(self.path)],
            capture_output=True, env=self._env, timeout=30)
        if result.returncode:
            raise BrainError("git_local_failed")

    def _ref(self):
        return "refs/heads/" + self.branch

    def _head(self):
        result = self._git("rev-parse", "--verify", "--quiet", self._ref(), check=False)
        if result.returncode:
            return None
        value = result.stdout.decode().strip()
        return value or None

    def _fetch(self):
        self._ensure()
        if not self.remote:
            return
        result = self._git("fetch", self.remote, "+" + self._ref() + ":" + self._ref(), check=False)
        if result.returncode and self._head() is None:
            return
        if result.returncode:
            raise BrainError("git_local_failed")

    def _lock(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path.with_name(self.path.name + ".lock"), "a+b")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    def sync(self):
        handle = self._lock()
        try:
            self._fetch()
            return {"head": self._head(), "synced": True, "remote": self.remote}
        finally:
            handle.close()

    def read(self):
        if "read" not in self.permissions:
            raise AccessDenied("repository_read_required")
        handle = self._lock()
        try:
            self._fetch()
            return self._read_unlocked()
        finally:
            handle.close()

    def _read_unlocked(self):
        self._ensure()
        head = self._head()
        if head is None:
            return Snapshot(None, {})
        listed = self._git("ls-tree", "-r", "-z", head)
        files = {}
        for entry in listed.stdout.split(b"\0"):
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            sha = meta.split()[2].decode()
            blob = self._git("cat-file", "blob", sha).stdout
            try:
                files[path.decode()] = blob.decode("utf-8")
            except UnicodeError:
                raise BrainError("git_local_failed") from None
        validate_files(files)
        return Snapshot(head, files)

    def commit(self, expected_head, files, message):
        if "write" not in self.permissions:
            raise AccessDenied("repository_write_required")
        handle = self._lock()
        try:
            self._fetch()
            current = self._head()
            if current != expected_head:
                raise ConcurrentWrite("snapshot_advanced")
            before = self._read_unlocked()
            validate_files(files, before.files)
            index = self.path.with_name(self.path.name + ".index")
            if index.exists():
                index.unlink()
            index_env = {**self._env, "GIT_INDEX_FILE": str(index)}
            for path, content in files.items():
                sha = self._git("hash-object", "-w", "--stdin", input=content.encode("utf-8")).stdout.decode().strip()
                self._git("update-index", "--add", "--cacheinfo", f"100644,{sha},{path}", env=index_env)
            tree = self._git("write-tree", env=index_env).stdout.decode().strip()
            index.unlink(missing_ok=True)
            args = ["commit-tree", tree, "-m", message]
            if expected_head:
                args.extend(["-p", expected_head])
            commit = self._git(*args).stdout.decode().strip()
            if expected_head:
                self._git("update-ref", self._ref(), commit, expected_head)
            else:
                self._git("update-ref", "--stdin", input=f"create {self._ref()} {commit}\n".encode())
            if self.remote:
                pushed = self._git("push", self.remote, self._ref() + ":" + self._ref(), check=False)
                if pushed.returncode:
                    if expected_head:
                        self._git("update-ref", self._ref(), expected_head, commit, check=False)
                    else:
                        self._git("update-ref", "-d", self._ref(), check=False)
                    raise ConcurrentWrite("snapshot_advanced")
            return Snapshot(commit, dict(files))
        finally:
            handle.close()

    def inspect_url(self, path):
        return "git-local:" + path
