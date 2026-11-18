"""a36: independent a34 counterexamples at real local/provider seams, no network."""
from contextlib import contextmanager
import http.client
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from company_pilot.watchdog import Archive, Catalog, LocalObjects, SupabaseObjects, synchronize
from company_pilot.watchdog.archive import CHUNK_SIZE, SyncError
from company_pilot.watchdog.drive import CompleteDownload, DriveSource
from company_pilot.watchdog.local import DirectorySource
from company_pilot.watchdog import __main__ as cli


class FakeSocket:
    def __init__(self, body=b"abc", length=8): self.body, self.length = body, length
    def makefile(self, *args, **kwargs):
        return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(self.length).encode() + b"\r\n\r\n" + self.body)


class PartialDrive:
    metadata = dict(id="file-1", name="Orders.txt", version="1", mimeType="text/plain", size="8", capabilities=dict(canDownload=True))
    body = b"abc"
    def json(self, route, **kwargs):
        if route == "changes/startPageToken": return dict(startPageToken="before")
        if route == "files": return dict(files=[self.metadata])
        if route == "changes": return dict(changes=[], newStartPageToken="after")
        if route == "files/file-1": return self.metadata
        raise AssertionError(route)
    @contextmanager
    def open(self, *args, **kwargs):
        response = http.client.HTTPResponse(FakeSocket(self.body))
        response.begin()
        with response: yield response


class DiscoveredDrive:
    access = True
    metadata = dict(id="shared-file", name="Company ledger.txt", version="1", mimeType="text/plain", driveId="team-drive", capabilities=dict(canDownload=True))
    def json(self, route, **kwargs):
        query = kwargs.get("query", {})
        if route == "about": return dict(user=dict(permissionId="expected-account"))
        if route == "drives": return dict(drives=[dict(id="team-drive", name="Company team")] if self.access else [])
        if route == "changes/startPageToken": return dict(startPageToken="before")
        if route == "files": return dict(files=[self.metadata] if query.get("driveId") else [])
        if route == "changes": return dict(changes=[], newStartPageToken="after")
        if route == "files/shared-file":
            if not self.access: raise AssertionError("Unavailable scope must not silently be read")
            return self.metadata
        raise AssertionError(route)
    @contextmanager
    def open(self, *args, **kwargs): yield io.BytesIO(b"Company ledger")


class FakeHTTP:
    def __init__(self):
        self.public, self.contents, self.calls = False, {}, []
        self.flip_after_chunk_post = self.flip_after_chunk_get = False
    def open(self, request, timeout):
        route = urlsplit(request.full_url).path.removeprefix("/storage/v1/")
        method = request.get_method()
        self.calls.append((route, method, self.public))
        if route == "bucket/company": return io.BytesIO(json.dumps({"id": "company", "public": self.public}).encode())
        if method == "POST":
            self.contents[route] = request.data
            if self.flip_after_chunk_post and "/chunks/" in route: self.public = True
            return io.BytesIO(b"{}")
        data = self.contents[route]
        if self.flip_after_chunk_get and "/chunks/" in route: self.public = True
        return io.BytesIO(data)


def remote_objects(http):
    return SupabaseObjects("https://fixture.invalid", "company", prefix="company-a", api_key=lambda: "FAKE-KEY", bearer=lambda: "FAKE-JWT", opener=http)


class SourceRepairTests(unittest.TestCase):
    def test_native_export_partial_http_is_also_refused_without_blob_size(self):
        class NativePartial(PartialDrive):
            metadata = {**PartialDrive.metadata, "mimeType": "application/vnd.google-apps.document"}
            def json(self, route, **kwargs):
                if route == "files/file-1/download": return {"done": True, "response": {"downloadUri": "https://drive.usercontent.google.com/fake"}}
                return super().json(route, **kwargs)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog = Catalog(root / "catalog.db")
            try:
                result = synchronize(DriveSource("native-export", NativePartial(), synthetic=True), catalog, Archive(LocalObjects(root / "archive")))
                self.assertFalse(result["content_complete"])
                self.assertEqual(result["gaps"][0]["code"], "drive_download_incomplete")
                self.assertIsNone(catalog.current("native-export", "file-1"))
            finally: catalog.close()

    def test_unbounded_incomplete_http_error_contains_only_safe_code(self):
        response = http.client.HTTPResponse(FakeSocket(b"PRIVATE", 20))
        response.begin()
        with response, self.assertRaises(SyncError) as caught:
            CompleteDownload(response).read()
        self.assertEqual(str(caught.exception), "drive_download_incomplete")

    def test_partial_http_response_is_gap_and_retry_publishes_only_complete_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog, archive = Catalog(root / "catalog.db"), Archive(LocalObjects(root / "archive"))
            api = PartialDrive()
            source = DriveSource("company-drive", api, synthetic=True)
            try:
                result = synchronize(source, catalog, archive)
                self.assertFalse(result["content_complete"])
                self.assertIsNone(catalog.current(source.key, "file-1"))
                self.assertEqual(result["gaps"][0]["code"], "drive_download_incomplete")
                self.assertEqual(result["cursor"], "after")  # gap survives checkpoint and is retried
                self.assertEqual(list((root / "archive" / "manifests").glob("*")), [])
                api.body = b"abcdefgh"
                retried = synchronize(source, catalog, archive)
                self.assertTrue(retried["content_complete"])
                current = catalog.current(source.key, "file-1")
                with archive.open(current["archive"]) as stream: self.assertEqual(stream.read(), api.body)
                self.assertEqual(len(catalog.history()), 1)
            finally: catalog.close()

    def test_repeated_directory_removals_are_distinct_frozen_transitions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            company = root / "company"
            company.mkdir()
            path = company / "record.txt"
            source = DirectorySource("company", company, synthetic=True)
            catalog, archive = Catalog(root / "catalog.db"), Archive(LocalObjects(root / "archive"))
            received, delivered = [], []
            try:
                for state in ("present", "removed", "present", "removed"):
                    if state == "present": path.write_bytes(b"Same business record")
                    else: path.unlink()
                    delivered.append(synchronize(source, catalog, archive, consumer=received.append)["delivered"])
                self.assertEqual(delivered, [1, 1, 1, 1])
                self.assertEqual([r["state"] for r in received], ["present", "removed", "present", "removed"])
                self.assertEqual(len({r["event_id"] for r in received}), 4)
                self.assertEqual(len(catalog.history()), 4)
                self.assertEqual(synchronize(source, catalog, archive, consumer=received.append)["delivered"], 0)
                with archive.open(received[0]["archive"]) as stream: self.assertEqual(stream.read(), b"Same business record")
            finally: catalog.close()

    def test_same_provider_revision_reappearing_after_removal_is_new_event(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Catalog(Path(folder) / "catalog.db")
            try:
                entry = {"id": "record", "state": "present", "provider_revision": "unchanged-provider-version"}
                first = catalog._record("scope", entry)
                self.assertEqual(catalog._record("scope", entry), first)
                catalog._record("scope", {"id": "record", "state": "removed", "provider_revision": "missing"})
                again = catalog._record("scope", entry)
                self.assertNotEqual(again["event_id"], first["event_id"])
                self.assertEqual(catalog._record("scope", entry), again)
                self.assertEqual(catalog.revision("scope", "record", first["revision_id"]), first)
            finally: catalog.close()

    def test_disappeared_discovered_scope_is_unavailable_then_recovers_without_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog, archive = Catalog(root / "catalog.db"), Archive(LocalObjects(root / "archive"))
            api, received = DiscoveredDrive(), []
            settings = dict(sources=[dict(id="company-drive", kind="google_drive", account_id="expected-account", token_env="FAKE_TOKEN")])
            old = "company-drive/expected-account/shared/team-drive"
            try:
                with patch.object(cli, "DriveAPI", lambda credential: api):
                    for source in cli.sources_for(settings, False): synchronize(source, catalog, archive, consumer=received.append)
                    first = catalog.current(old, "shared-file")
                    api.access = False
                    for source in cli.sources_for(settings, False): synchronize(source, catalog, archive, consumer=received.append)
                    status = next(s for s in catalog.status()["sources"] if s["source"] == old)
                    self.assertFalse(status["content_complete"])
                    self.assertEqual(status["availability"], "unavailable")
                    self.assertEqual(catalog.current(old, "shared-file"), first)
                    self.assertFalse(any(r["state"] == "removed" for r in received))
                    api.access = True
                    for source in cli.sources_for(settings, False): synchronize(source, catalog, archive, consumer=received.append)
                    status = next(s for s in catalog.status()["sources"] if s["source"] == old)
                    self.assertTrue(status["content_complete"])
                    self.assertEqual(status["availability"], "available")
                    self.assertEqual(status["cursor"], "after")
                    self.assertEqual(len(received), 1)
            finally: catalog.close()


class ArchivePrivacyRepairTests(unittest.TestCase):
    def test_private_bucket_change_blocks_later_write_and_read_on_same_instance(self):
        http = FakeHTTP()
        archive = Archive(remote_objects(http))
        ref = archive.put(io.BytesIO(b"first private revision"))
        http.public = True
        before = len(http.calls)
        with self.assertRaisesRegex(SyncError, "private_archive_bucket_required"): archive.put(io.BytesIO(b"later revision"))
        with self.assertRaisesRegex(SyncError, "private_archive_bucket_required"):
            with archive.open(ref): self.fail("Must not return public-bucket data")
        self.assertFalse(any(method == "POST" and public for _, method, public in http.calls[before:]))

    def test_public_transition_between_chunks_blocks_remaining_writes(self):
        http = FakeHTTP()
        http.flip_after_chunk_post = True
        with self.assertRaisesRegex(SyncError, "private_archive_bucket_required"):
            Archive(remote_objects(http)).put(io.BytesIO(b"A" * CHUNK_SIZE + b"B"))
        self.assertFalse(any(method == "POST" and public for _, method, public in http.calls))
        self.assertFalse(any("/manifests/" in key for key in http.contents))

    def test_public_transition_between_read_chunks_never_returns_assembled_bytes(self):
        http = FakeHTTP()
        archive = Archive(remote_objects(http))
        ref = archive.put(io.BytesIO(b"A" * CHUNK_SIZE + b"B"))
        http.flip_after_chunk_get = True
        before = len(http.calls)
        with self.assertRaisesRegex(SyncError, "private_archive_bucket_required"):
            with archive.open(ref): self.fail("Must not expose partly checked content")
        self.assertFalse(any(method == "GET" and public and route.startswith("object/") for route, method, public in http.calls[before:]))


if __name__ == "__main__": unittest.main()
