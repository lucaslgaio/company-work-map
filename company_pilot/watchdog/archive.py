"""Immutable content-addressed archives; full bytes are separate from the map."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CHUNK_SIZE = 4 * 1024 * 1024


class SyncError(ValueError):
    """Only a fixed diagnostic code, never a remote response body."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SyncError("unexpected_http_redirect")


class LocalObjects:
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key):
        if not re.fullmatch(r"(?:chunks|manifests)/[a-f0-9]{64}", key):
            raise SyncError("archive_key_invalid")
        path = self.root / key
        if path.is_symlink() or path.parent.is_symlink():
            raise SyncError("archive_symlink_refused")
        return path

    def put(self, key, data):
        path = self._path(key)
        path.parent.mkdir(exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self.get(key) != data:
                    raise SyncError("archive_content_conflict") from None
        finally:
            os.unlink(temporary)

    def get(self, key):
        return self._path(key).read_bytes()


class SupabaseObjects:
    """An already provisioned private bucket; never creates or changes policies.

    Credentials are runtime callbacks, not serialized fields. A server worker
    may use an operator JWT/secret; that credential must never reach a client.
    """
    def __init__(self, url, bucket, *, api_key, bearer, prefix, opener=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise SyncError("supabase_origin_invalid")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", bucket) or not re.fullmatch(r"[a-zA-Z0-9_-]+", prefix):
            raise SyncError("supabase_archive_scope_invalid")
        self.url, self.bucket, self.prefix = url.rstrip("/"), bucket, prefix
        self.api_key, self.bearer = api_key, bearer
        self.opener = opener or build_opener(NoRedirect())

    def _request(self, route, method="GET", data=None):
        request = Request(self.url + "/storage/v1/" + route, data=data, method=method,
                          headers={"apikey": self.api_key(), "Authorization": "Bearer " + self.bearer(),
                                   "Content-Type": "application/octet-stream", "x-upsert": "false"})
        try:
            return self.opener.open(request, timeout=60)
        except HTTPError as error:
            code = error.code
            error.close()
            if method == "POST" and code in (400, 409):
                raise SyncError("archive_object_may_exist") from None
            raise SyncError("archive_http_" + str(code)) from None
        except SyncError:
            raise
        except (OSError, TimeoutError):
            raise SyncError("archive_connection_unconfirmed") from None

    def _check(self):
        # Bucket visibility can change while this long-lived worker is running.
        # Check every immutable object operation, including successive chunks.
        with self._request("bucket/" + quote(self.bucket, safe="")) as response:
            try:
                value = json.loads(response.read(1024 * 1024))
            except (ValueError, UnicodeError):
                raise SyncError("archive_bucket_unverified") from None
        if not isinstance(value, dict) or value.get("public") is not False or value.get("id") != self.bucket:
            raise SyncError("private_archive_bucket_required")

    def _route(self, key):
        if not re.fullmatch(r"(?:chunks|manifests)/[a-f0-9]{64}", key):
            raise SyncError("archive_key_invalid")
        return "object/" + quote(self.bucket, safe="") + "/" + self.prefix + "/" + key

    def put(self, key, data):
        self._check()
        try:
            with self._request(self._route(key), "POST", data) as response:
                response.read(1024 * 1024)
        except SyncError as error:
            # A conflict is only idempotent after reading and checking bytes.
            if error.args != ("archive_object_may_exist",) or self.get(key) != data:
                raise

    def get(self, key):
        self._check()
        with self._request(self._route(key)) as response:
            data = response.read(CHUNK_SIZE + 1024 * 1024)
            if len(data) > CHUNK_SIZE:
                raise SyncError("archive_object_oversize")
            return data


class Archive:
    def __init__(self, objects):
        self.objects = objects

    def put(self, stream):
        digest, size, chunks = hashlib.sha256(), 0, []
        while data := stream.read(CHUNK_SIZE):
            if not isinstance(data, bytes):
                raise SyncError("binary_source_stream_required")
            digest.update(data)
            size += len(data)
            key = "chunks/" + sha(data)
            self.objects.put(key, data)
            chunks.append({"key": key, "size": len(data)})
        manifest = {"schema_version": 1, "sha256": digest.hexdigest(), "size": size, "chunks": chunks}
        data = canonical(manifest)
        key = "manifests/" + sha(data)
        self.objects.put(key, data)
        return {"key": key, "sha256": manifest["sha256"], "size": size}

    @contextmanager
    def open(self, reference):
        data = self.objects.get(reference["key"])
        if "manifests/" + sha(data) != reference["key"]:
            raise SyncError("archive_manifest_changed")
        manifest = json.loads(data)
        digest, size = hashlib.sha256(), 0
        # Spooled file bounds memory while making ZIP/document readers possible.
        with tempfile.SpooledTemporaryFile(max_size=CHUNK_SIZE) as out:
            for part in manifest["chunks"]:
                chunk = self.objects.get(part["key"])
                if "chunks/" + sha(chunk) != part["key"] or len(chunk) != part["size"]:
                    raise SyncError("archive_chunk_changed")
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
            if (digest.hexdigest() != reference["sha256"] or size != reference["size"] or
                    manifest["sha256"] != reference["sha256"] or manifest["size"] != size):
                raise SyncError("archive_content_changed")
            out.seek(0)
            yield out
