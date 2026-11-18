"""Explicit company directories and consistent read-only SQLite snapshots."""
from contextlib import contextmanager
import hashlib
import mimetypes
from pathlib import Path
import sqlite3
import tempfile

from .archive import SyncError, sha


class DirectorySource:
    def __init__(self, key, root, *, synthetic=False):
        self.key, self.root, self.synthetic = key, Path(root).expanduser().resolve(), synthetic
        if not self.root.is_dir():
            raise SyncError("company_directory_missing")

    def _path(self, item):
        path = self.root / item
        if path.is_symlink() or self.root not in path.resolve().parents:
            raise SyncError("company_directory_boundary_refused")
        return path

    def get(self, item):
        path = self._path(item)
        stat = path.stat()
        if not path.is_file():
            raise SyncError("company_file_unavailable")
        return {"id": item, "name": item, "state": "present", "synthetic": self.synthetic,
                "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "original_url": path.as_uri(),
                "provider_revision": f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"}

    def scan(self, cursor):
        # A directory has no durable change journal in this adapter. Reconcile
        # a full inventory each time instead of inventing an incremental token.
        def walk(directory):
            for path in sorted(directory.iterdir()):
                if path.is_symlink():
                    raise SyncError("company_directory_symlink_requires_explicit_source")
                if path.is_dir():
                    yield from walk(path)
                elif path.is_file():
                    yield self.get(path.relative_to(self.root).as_posix())
        yield from walk(self.root)
        return None

    @contextmanager
    def open(self, entry):
        with self._path(entry["id"]).open("rb") as stream:
            yield stream

    def verify(self, entry):
        if self.get(entry["id"]) != entry:
            raise SyncError("source_changed_during_read")


class SQLiteSource:
    """SQLite is a supported engine, not a claim to support all SQL products.

    backup() includes a consistent committed view, including WAL contents. The
    source connection is URI read-only and never runs file-supplied SQL.
    """
    def __init__(self, key, path, *, synthetic=False):
        self.key, self.path, self.synthetic = key, Path(path).expanduser().resolve(), synthetic
        if not self.path.is_file():
            raise SyncError("company_database_missing")
        self.snapshot = None

    def _snapshot(self):
        if self.snapshot:
            self.snapshot.close()
        self.snapshot = tempfile.TemporaryFile()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "snapshot.db"
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as source:
                source.execute("PRAGMA query_only=ON")
                with sqlite3.connect(target) as destination:
                    source.backup(destination)
            with target.open("rb") as stream:
                digest = hashlib.sha256()
                while chunk := stream.read(4 * 1024 * 1024):
                    digest.update(chunk)
                    self.snapshot.write(chunk)
        self.snapshot.seek(0)
        return digest.hexdigest()

    def scan(self, cursor):
        version = self._snapshot()
        yield {"id": "database", "name": self.path.name, "state": "present", "synthetic": self.synthetic,
               "mime_type": "application/vnd.sqlite3", "original_url": self.path.as_uri(), "provider_revision": version}
        return version

    def get(self, item):
        if item != "database":
            raise SyncError("company_database_record_unknown")
        return next(self.scan(None))

    @contextmanager
    def open(self, entry):
        if self.snapshot is None:
            raise SyncError("database_snapshot_missing")
        self.snapshot.seek(0)
        yield self.snapshot

    def verify(self, entry):
        self.snapshot.seek(0)
        digest = hashlib.sha256()
        while chunk := self.snapshot.read(4 * 1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != entry["provider_revision"]:
            raise SyncError("database_snapshot_changed")

    def close(self):
        if self.snapshot:
            self.snapshot.close()
