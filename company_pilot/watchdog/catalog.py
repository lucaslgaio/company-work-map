"""Durable cursors and immutable revisions, including explicit coverage gaps."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid5

from .archive import SyncError, canonical, sha

NAMESPACE = UUID("d3578526-9c90-4cfe-bacc-6ba25f75834c")


def now():
    return datetime.now(timezone.utc).isoformat()


class Catalog:
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise SyncError("catalog_symlink_refused")
        self.db = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=DELETE;
          CREATE TABLE IF NOT EXISTS identity(company_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS source_errors(source_id TEXT PRIMARY KEY,code TEXT NOT NULL,at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS checkpoints(
            source TEXT PRIMARY KEY, cursor TEXT, inventory_complete INTEGER NOT NULL DEFAULT 0,
            last_success TEXT, last_attempt TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS revisions(
            source TEXT NOT NULL, item TEXT NOT NULL, revision TEXT NOT NULL,
            record TEXT NOT NULL, seen_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(source,item,revision));
          CREATE TABLE IF NOT EXISTS current(
            source TEXT NOT NULL, item TEXT NOT NULL, revision TEXT NOT NULL,
            PRIMARY KEY(source,item));
          CREATE TABLE IF NOT EXISTS gaps(
            source TEXT NOT NULL, item TEXT NOT NULL, code TEXT NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY(source,item));
        ''')

    def close(self):
        self.db.close()

    def bind_company(self, company_id):
        if not isinstance(company_id, str) or not company_id:
            raise SyncError("catalog_company_identity_required")
        with self.db:
            rows = self.db.execute("SELECT company_id FROM identity").fetchall()
            if rows and (len(rows) != 1 or rows[0][0] != company_id):
                raise SyncError("catalog_company_identity_mismatch")
            if not rows:
                self.db.execute("INSERT INTO identity VALUES(?)", (company_id,))

    @contextmanager
    def lease(self):
        # One central writer per catalogue. Never let a slower concurrent scan
        # rewind a newer cursor or infer removals from a different inventory.
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with lock.open("a+b") as stream:
            os.chmod(lock, 0o600)
            try:
                if os.name == "nt":
                    import msvcrt
                    if stream.tell() == 0:
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise SyncError("catalog_sync_already_running") from None
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def checkpoint(self, source):
        row = self.db.execute("SELECT * FROM checkpoints WHERE source=?", (source,)).fetchone()
        return dict(row) if row else {"source": source, "cursor": None, "inventory_complete": 0}

    def current(self, source, item):
        row = self.db.execute('''SELECT r.record FROM current c JOIN revisions r
             USING(source,item,revision) WHERE source=? AND item=?''', (source, item)).fetchone()
        return json.loads(row[0]) if row else None

    def _record(self, source, entry, archive=None):
        # Discovery time does not participate in revision identity, so retries
        # can reuse the exact frozen record/event even after process failure.
        record = {"source_key": source, **entry, "archive": archive}
        current = self.current(source, entry["id"])
        if current and all(current.get(key) == value for key, value in record.items()):
            return current
        # Content/provider version may reappear after removal. Bind a new state
        # transition to its predecessor; retries of the current state reuse it.
        if current:
            record["previous_revision"] = current["revision_id"]
        version = sha(canonical(record))
        record.update(revision_id=version,
                      event_id=str(uuid5(NAMESPACE, source + ":" + entry["id"] + ":" + version)))
        with self.db:
            previous = self.db.execute("SELECT record FROM revisions WHERE source=? AND item=? AND revision=?",
                                       (source, entry["id"], version)).fetchone()
            if previous:
                record = json.loads(previous[0])
            else:
                record["observed_at"] = now()
                self.db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,0)",
                                (source, entry["id"], version, canonical(record).decode(), record["observed_at"]))
            self.db.execute("INSERT OR REPLACE INTO current VALUES(?,?,?)", (source, entry["id"], version))
            self.db.execute("DELETE FROM gaps WHERE source=? AND item=?", (source, entry["id"]))
        return record

    def gap(self, source, item, code):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO gaps VALUES(?,?,?,?)", (source, item, code, now()))

    def reconcile_discovered_sources(self, prefix, members):
        """Complete discovery can establish scope unavailability, not deletion.

        Keep the last archived records and cursor for recovery. Only this
        authenticated account's exact namespace participates in reconciliation.
        """
        members = set(members)
        with self.db:
            for row in self.db.execute("SELECT source FROM checkpoints").fetchall():
                key = row["source"]
                if not key.startswith(prefix + "/"):
                    continue
                if key not in members:
                    self.db.execute("UPDATE checkpoints SET inventory_complete=0,error='source_scope_not_discovered',last_attempt=? WHERE source=?", (now(), key))
                else:
                    self.db.execute("UPDATE checkpoints SET error=NULL WHERE source=? AND error='source_scope_not_discovered'", (key,))

    def source_discovery_result(self, source_id, code):
        with self.db:
            if code is None:
                self.db.execute("DELETE FROM source_errors WHERE source_id=?", (source_id,))
            else:
                self.db.execute("INSERT OR REPLACE INTO source_errors VALUES(?,?,?)", (source_id, code, now()))

    def pending(self, source):
        rows = self.db.execute("SELECT record FROM revisions WHERE source=? AND delivered=0 ORDER BY seen_at,rowid", (source,))
        return [json.loads(row[0]) for row in rows]

    def deliver(self, source, consumer):
        count = 0
        for record in self.pending(source):
            # Consumer's event-id contract handles a crash after its own commit
            # but before this acknowledgement. No cursor relies on an effect
            # that has not been durably represented in this catalogue.
            consumer(record)
            with self.db:
                self.db.execute("UPDATE revisions SET delivered=1 WHERE source=? AND item=? AND revision=?",
                                (source, record["id"], record["revision_id"]))
            count += 1
        return count

    def revision(self, source, item, revision):
        row = self.db.execute("SELECT record FROM revisions WHERE source=? AND item=? AND revision=?",
                              (source, item, revision)).fetchone()
        if not row:
            raise SyncError("company_source_revision_unknown")
        return json.loads(row[0])

    def history(self, source=None, *, after=0, limit=None):
        query = "SELECT record,seen_at,delivered,rowid FROM revisions WHERE rowid>?"
        args = [after]
        if source is not None:
            query += " AND source=?"
            args.append(source)
        query += " ORDER BY rowid"
        if limit is not None:
            query += " LIMIT ?"
            args.append(limit)
        return [{**json.loads(row[0]), "seen_at": row[1], "delivered": bool(row[2]), "sequence": row[3]}
                for row in self.db.execute(query, args)]

    def status(self):
        discovery_errors = [dict(row) for row in self.db.execute("SELECT * FROM source_errors ORDER BY source_id")]
        sources = [dict(row) for row in self.db.execute("SELECT * FROM checkpoints ORDER BY source")]
        for source in sources:
            source["gaps"] = [dict(row) for row in self.db.execute("SELECT item,code,at FROM gaps WHERE source=? ORDER BY item", (source["source"],))]
            if source["error"] == "source_scope_not_discovered":
                source["gaps"].append({"item": None, "code": "source_scope_unavailable", "at": source["last_attempt"]})
            for error in discovery_errors:
                if source["source"] == error["source_id"] or source["source"].startswith(error["source_id"] + "/"):
                    source["gaps"].append({"item": None, "code": "source_discovery_unavailable", "at": error["at"]})
            source["pending_map_records"] = self.db.execute("SELECT count(*) FROM revisions WHERE source=? AND delivered=0", (source["source"],)).fetchone()[0]
            source["content_complete"] = bool(source["inventory_complete"] and not source["gaps"] and not source["error"])
            source["availability"] = ("unavailable" if any(gap["code"] in ("source_scope_unavailable", "source_discovery_unavailable") for gap in source["gaps"])
                                      else "available" if source.get("last_success") else "unknown")
        return {"sources": sources, "source_errors": discovery_errors, "scope": "configured_company_sources_only"}


def synchronize(source, catalog, archive, *, consumer=None):
    with catalog.lease():
        return _synchronize(source, catalog, archive, consumer=consumer)


def _synchronize(source, catalog, archive, *, consumer=None):
    """Read a provider; persist each result/gap before advancing its cursor.

    A provider's generator returns its terminal cursor. Listing failures never
    turn unseen files into deletions. Failed content reads become retryable gaps
    rather than disappearing behind a successful pagination checkpoint.
    """
    discovery = getattr(source, "discovered_scope", None)
    if discovery:
        catalog.reconcile_discovered_sources(discovery["prefix"], discovery["members"])
    key, before = source.key, catalog.checkpoint(source.key)
    counts = {"archived": 0, "unchanged": 0, "removed": 0, "gaps": 0}
    with catalog.db:
        catalog.db.execute("INSERT OR IGNORE INTO checkpoints(source) VALUES(?)", (key,))
        catalog.db.execute("UPDATE checkpoints SET last_attempt=?,error=NULL WHERE source=?", (now(), key))
    seen = set()

    def process(entry):
        item = entry["id"]
        seen.add(item)
        current = catalog.current(key, item)
        if current and all(current.get(field) == value for field, value in entry.items()):
            counts["unchanged"] += 1
            with catalog.db:
                catalog.db.execute("DELETE FROM gaps WHERE source=? AND item=?", (key, item))
            return
        if entry.get("state") in ("removed", "metadata_only"):
            catalog._record(key, entry)
            counts["removed"] += entry["state"] == "removed"
            return
        try:
            with source.open(entry) as stream:
                reference = archive.put(stream)
            # The provider verifies its source version around the read.
            source.verify(entry)
            catalog._record(key, entry, reference)
            counts["archived"] += 1
        except (OSError, SyncError) as error:
            code = error.args[0] if isinstance(error, SyncError) else "source_read_failed"
            catalog.gap(key, item, code)
            counts["gaps"] += 1

    try:
        # Retry previously acknowledged download gaps even when no new provider
        # change exists. A missing/denied item remains visible, not inferred gone.
        pending = [row[0] for row in catalog.db.execute("SELECT item FROM gaps WHERE source=?", (key,))]
        for item in pending:
            try:
                process(source.get(item))
            except (OSError, SyncError):
                counts["gaps"] += 1
        iterator = iter(source.scan(before["cursor"]))
        while True:
            try:
                process(next(iterator))
            except StopIteration as completed:
                cursor = completed.value
                break
        if before["cursor"] is None:
            # Only a complete full inventory can establish a missing item.
            previous = list(catalog.db.execute("SELECT item FROM current WHERE source=?", (key,)))
            for row in previous:
                if row[0] not in seen:
                    old = catalog.current(key, row[0])
                    if old.get("state") != "removed":
                        process({"id": row[0], "state": "removed", "provider_revision": "absent_from_complete_inventory"})
        with catalog.db:
            catalog.db.execute("UPDATE checkpoints SET cursor=?,inventory_complete=1,last_success=?,error=NULL WHERE source=?",
                               (cursor, now(), key))
    except (OSError, SyncError):
        with catalog.db:
            catalog.db.execute("UPDATE checkpoints SET error='source_listing_incomplete' WHERE source=?", (key,))
        raise SyncError("source_listing_incomplete") from None
    delivered = catalog.deliver(key, consumer) if consumer else 0
    return {"source": key, **counts, "delivered": delivered,
            **next(row for row in catalog.status()["sources"] if row["source"] == key)}
