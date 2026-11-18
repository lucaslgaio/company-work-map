from contextlib import contextmanager
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from company_pilot.watchdog import Archive, Catalog, LocalObjects, synchronize
from company_pilot.watchdog.archive import SyncError
from company_pilot.watchdog.drive import DriveSource, discover_shared_drives
from company_pilot.watchdog.local import DirectorySource, SQLiteSource


def file(item, name, version="1", mime="text/plain"):
    return {"id": item, "name": name, "version": version, "mimeType": mime,
            "capabilities": {"canDownload": True}}


class FakeDrive:
    def __init__(self):
        self.calls = []
        self.incomplete = False
        self.changed = False
        self.payload = b"a,b\n1,2\n"

    def json(self, route, **kwargs):
        self.calls.append((route, kwargs))
        query = kwargs.get("query", {})
        if route == "drives":
            return ({"drives": [{"id": "second"}]} if query.get("pageToken") else
                    {"drives": [{"id": "first"}], "nextPageToken": "more-drives"})
        if route == "changes/startPageToken":
            return {"startPageToken": "before-inventory"}
        if route == "files":
            return ({"files": [file("b", "Ledger.csv")]} if query.get("pageToken") else
                    {"files": [file("a", "Orders.csv")], "nextPageToken": "more-files", "incompleteSearch": self.incomplete})
        if route == "changes":
            return {"changes": [{"fileId": "a", "file": file("a", "Paid orders.csv", "2")},
                                {"fileId": "c", "removed": True}], "newStartPageToken": "after-catchup"}
        if route.endswith("/download"):
            return {"done": True, "response": {"downloadUri": "https://drive.usercontent.google.com/download?id=native"}}
        if route.startswith("files/"):
            item = route.removeprefix("files/")
            if item == "a":
                return file("a", "Paid orders.csv", "3" if self.changed else "2")
            return file(item, "Ledger.csv")
        raise AssertionError(route)

    @contextmanager
    def open(self, route, **kwargs):
        self.calls.append((route, kwargs))
        yield io.BytesIO(self.payload)


class SourceTests(unittest.TestCase):
    def test_drive_all_pages_then_changes_catch_inventory_race(self):
        api = FakeDrive()
        source = DriveSource("company-drive", api, synthetic=True)
        scan = source.scan(None)
        entries = []
        while True:
            try:
                entries.append(next(scan))
            except StopIteration as complete:
                self.assertEqual(complete.value, "after-catchup")
                break
        self.assertEqual(api.calls[0][0], "changes/startPageToken")
        self.assertEqual([entry["id"] for entry in entries], ["a", "b", "a", "c"])
        self.assertEqual(entries[2]["provider_revision"], "2")
        self.assertEqual(entries[-1]["removal_meaning"], "removed_or_access_lost")
        self.assertEqual(len(discover_shared_drives(api)), 2)

    def test_incomplete_drive_search_never_claims_complete(self):
        api = FakeDrive()
        api.incomplete = True
        with self.assertRaisesRegex(SyncError, "drive_inventory_incomplete"):
            list(DriveSource("company-drive", api).scan(None))

    def test_native_download_does_not_use_ten_megabyte_export(self):
        api = FakeDrive()
        api.payload = b"x" * (11 * 1024 * 1024)
        source = DriveSource("company-drive", api)
        entry = source._entry(file("native", "All tabs", mime="application/vnd.google-apps.spreadsheet"))
        with source.open(entry) as stream:
            self.assertEqual(len(stream.read()), len(api.payload))
        self.assertTrue(any(route == "files/native/download" for route, _ in api.calls))
        self.assertFalse(any("export" in route for route, _ in api.calls))

    def test_version_verification_rejects_edit_during_read(self):
        api = FakeDrive()
        source = DriveSource("company-drive", api)
        entry = source._entry(file("a", "Paid orders.csv", "2"))
        api.changed = True
        with self.assertRaisesRegex(SyncError, "source_changed_during_read"):
            source.verify(entry)

    def test_company_directory_full_rescan_tracks_removal_and_refuses_symlink_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            company = root / "company"
            company.mkdir()
            path = company / "orders.txt"
            path.write_text("Order A approved")
            catalog = Catalog(root / "catalog.db")
            self.addCleanup(catalog.close)
            archive = Archive(LocalObjects(root / "archive"))
            source = DirectorySource("company-directory", company, synthetic=True)
            synchronize(source, catalog, archive)
            path.unlink()
            synchronize(source, catalog, archive)
            self.assertEqual(catalog.current(source.key, "orders.txt")["state"], "removed")
            (company / "private-link").symlink_to(root / "catalog.db")
            with self.assertRaises(SyncError):
                synchronize(source, catalog, archive)

    def test_sqlite_backup_includes_committed_wal_without_mutating_live_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "company.db"
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, total INTEGER)")
            db.execute("INSERT INTO orders VALUES(1,10)")
            db.commit()
            source = SQLiteSource("company-database", path, synthetic=True)
            self.addCleanup(source.close)
            entry = next(source.scan(None))
            with source.open(entry) as stream:
                snapshot = Path(folder) / "snapshot.db"
                snapshot.write_bytes(stream.read())
            with sqlite3.connect(snapshot) as copy:
                self.assertEqual(copy.execute("SELECT * FROM orders").fetchall(), [(1, 10)])
            source.verify(entry)
            self.assertEqual(db.execute("SELECT * FROM orders").fetchall(), [(1, 10)])


if __name__ == "__main__":
    unittest.main()
