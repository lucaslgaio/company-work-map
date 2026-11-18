from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.watchdog import Archive, LocalObjects, SupabaseObjects
from company_pilot.watchdog.archive import CHUNK_SIZE, SyncError


class ArchiveTests(unittest.TestCase):
    def test_large_file_roundtrip_replay_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            objects = LocalObjects(folder)
            archive = Archive(objects)
            data = b"A" * CHUNK_SIZE + b"B" * CHUNK_SIZE + b"end"
            ref = archive.put(io.BytesIO(data))
            files = sorted(Path(folder).rglob("*"))
            self.assertEqual(archive.put(io.BytesIO(data)), ref)
            self.assertEqual(sorted(Path(folder).rglob("*")), files)
            with archive.open(ref) as restored:
                self.assertEqual(restored.read(), data)
            part = next((Path(folder) / "chunks").iterdir())
            part.write_bytes(b"changed outside the archive")
            with self.assertRaises(SyncError):
                with archive.open(ref):
                    self.fail("corrupt bytes must not be returned")

    def test_private_supabase_bucket_required_before_upload_and_conflict_rechecked(self):
        class Objects(SupabaseObjects):
            def __init__(self, public=False):
                super().__init__("https://example.supabase.co", "company", api_key=lambda: "SECRET",
                                 bearer=lambda: "JWT", prefix="synthetic-company")
                self.public, self.calls, self.contents = public, [], {}

            @contextmanager
            def _request(self, route, method="GET", data=None):
                self.calls.append((route, method))
                if route.startswith("bucket/"):
                    yield io.BytesIO(json.dumps({"id": "company", "public": self.public}).encode())
                elif method == "POST":
                    if route in self.contents:
                        raise SyncError("archive_object_may_exist")
                    self.contents[route] = data
                    yield io.BytesIO(b"{}")
                else:
                    yield io.BytesIO(self.contents[route])
        public = Objects(public=True)
        with self.assertRaisesRegex(SyncError, "private_archive_bucket_required"):
            Archive(public).put(io.BytesIO(b"company data"))
        self.assertFalse(any(method == "POST" for _, method in public.calls))
        private = Objects()
        archive = Archive(private)
        first = archive.put(io.BytesIO(b"company data"))
        self.assertEqual(archive.put(io.BytesIO(b"company data")), first)
        self.assertTrue(any(method == "GET" and route.startswith("object/") for route, method in private.calls))
        with archive.open(first) as stream:
            self.assertEqual(stream.read(), b"company data")


if __name__ == "__main__":
    unittest.main()
