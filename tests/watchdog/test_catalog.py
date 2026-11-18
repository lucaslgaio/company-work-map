from contextlib import contextmanager
import io
from pathlib import Path
import tempfile
import unittest

from company_pilot.watchdog import Archive, Catalog, LocalObjects, synchronize
from company_pilot.watchdog.archive import SyncError


class Source:
    key = "synthetic-drive"

    def __init__(self):
        self.files = {"one": {"name": "Orders.csv", "provider_revision": "1", "data": b"order,amount\nA,10\n"},
                      "two": {"name": "Policy.txt", "provider_revision": "1", "data": b"Owner approves invoices."}}
        self.removed = []
        self.fail = set()
        self.partial = False
        self.token = "checkpoint-1"

    def get(self, item):
        value = self.files[item]
        return {"id": item, "name": value["name"], "provider_revision": value["provider_revision"],
                "original_url": "https://drive.google.com/file/d/" + item,
                "mime_type": "text/plain", "state": "present", "synthetic": True}

    def scan(self, cursor):
        for item in self.files:
            yield self.get(item)
            if self.partial:
                raise SyncError("incomplete_provider_page")
        for item in self.removed:
            yield {"id": item, "state": "removed", "provider_revision": self.token}
        return self.token

    @contextmanager
    def open(self, entry):
        if entry["id"] in self.fail:
            raise SyncError("source_download_denied")
        yield io.BytesIO(self.files[entry["id"]]["data"])

    def verify(self, entry):
        if entry["provider_revision"] != self.files[entry["id"]]["provider_revision"]:
            raise SyncError("source_changed_during_read")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / "catalog.db")
        self.addCleanup(self.catalog.close)
        self.archive = Archive(LocalObjects(self.root / "objects"))
        self.source = Source()

    def test_initial_import_edit_rename_removal_and_replay_preserve_versions(self):
        delivered = {}
        def consume(record):
            delivered[record["event_id"]] = record
        first = synchronize(self.source, self.catalog, self.archive, consumer=consume)
        self.assertEqual((first["archived"], first["content_complete"]), (2, True))
        old = self.catalog.current(self.source.key, "one")
        self.source.files["one"].update(name="Paid orders.csv", provider_revision="2", data=b"order,amount\nA,12\n")
        del self.source.files["two"]
        self.source.removed = ["two"]
        self.source.token = "checkpoint-2"
        second = synchronize(self.source, self.catalog, self.archive, consumer=consume)
        self.assertEqual((second["archived"], second["removed"]), (1, 1))
        current = self.catalog.current(self.source.key, "one")
        self.assertNotEqual(old["revision_id"], current["revision_id"])
        with self.archive.open(old["archive"]) as stream:
            self.assertEqual(stream.read(), b"order,amount\nA,10\n")
        with self.archive.open(current["archive"]) as stream:
            self.assertEqual(stream.read(), b"order,amount\nA,12\n")
        self.assertEqual(current["original_url"], old["original_url"])
        self.assertEqual(len(self.catalog.history()), 4)
        replay = synchronize(self.source, self.catalog, self.archive, consumer=consume)
        self.assertEqual((replay["archived"], replay["delivered"]), (0, 0))
        self.assertEqual(len(delivered), 4)

    def test_failed_content_is_visible_and_retried_without_new_change(self):
        self.source.fail.add("two")
        result = synchronize(self.source, self.catalog, self.archive)
        self.assertFalse(result["content_complete"])
        self.assertEqual(result["gaps"][0]["code"], "source_download_denied")
        self.assertEqual(self.catalog.checkpoint(self.source.key)["cursor"], "checkpoint-1")
        self.source.fail.clear()
        repaired = synchronize(self.source, self.catalog, self.archive)
        self.assertTrue(repaired["content_complete"])
        self.assertEqual(len(self.catalog.history()), 2)

    def test_listing_failure_never_commits_complete_cursor_or_deletes_unseen_file(self):
        synchronize(self.source, self.catalog, self.archive)
        # Explicitly simulate a required full inventory after a provider cursor
        # reset. Only its first page succeeds; the old second file must remain.
        with self.catalog.db:
            self.catalog.db.execute("UPDATE checkpoints SET cursor=NULL WHERE source=?", (self.source.key,))
        self.source.partial = True
        with self.assertRaises(SyncError):
            synchronize(self.source, self.catalog, self.archive)
        self.assertIsNone(self.catalog.checkpoint(self.source.key)["cursor"])
        self.assertEqual(self.catalog.current(self.source.key, "two")["state"], "present")
        self.assertFalse(self.catalog.status()["sources"][0]["content_complete"])

    def test_analysis_crash_retries_same_event_after_archive_and_cursor_commit(self):
        calls = []
        def failed_after_remote_commit(record):
            calls.append(record["event_id"])
            raise RuntimeError("simulated local acknowledgement failure")
        with self.assertRaises(RuntimeError):
            synchronize(self.source, self.catalog, self.archive, consumer=failed_after_remote_commit)
        self.assertEqual(len(self.catalog.pending(self.source.key)), 2)
        synchronize(self.source, self.catalog, self.archive, consumer=lambda record: calls.append(record["event_id"]))
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.catalog.pending(self.source.key), [])
        self.assertEqual(len(self.catalog.history()), 2)

    def test_source_version_race_does_not_publish_mixed_revision(self):
        def changed(_entry):
            raise SyncError("source_changed_during_read")
        self.source.verify = changed
        result = synchronize(self.source, self.catalog, self.archive)
        self.assertFalse(result["content_complete"])
        self.assertEqual(self.catalog.history(), [])
        self.assertEqual(len(result["gaps"]), 2)

    def test_second_catalogue_writer_cannot_advance_while_sync_holds_lease(self):
        second = Catalog(self.catalog.path)
        self.addCleanup(second.close)
        with self.catalog.lease():
            with self.assertRaisesRegex(SyncError, "catalog_sync_already_running"):
                synchronize(self.source, second, self.archive)
        self.assertIsNone(self.catalog.checkpoint(self.source.key)["cursor"])


if __name__ == "__main__":
    unittest.main()
