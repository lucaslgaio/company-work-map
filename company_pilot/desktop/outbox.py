"""Durable immutable observations, restart-safe deduplication and leased delivery."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class CapacityError(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class Outbox:
    def __init__(self, path: Path, max_content_bytes: int = 128 * 1024 * 1024):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.max_content_bytes = max_content_bytes
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=DELETE;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY, payload BLOB NOT NULL, content_hash TEXT NOT NULL,
            image BLOB, image_hash TEXT, attachment_receipt TEXT, outbound BLOB,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
            last_error TEXT, receipt TEXT, created_at REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, digest TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS episodes (
            source TEXT PRIMARY KEY, payload BLOB NOT NULL, image BLOB,
            first_text TEXT NOT NULL, started REAL NOT NULL, updated REAL NOT NULL
          );
          CREATE TABLE IF NOT EXISTS quarantine (
            id TEXT PRIMARY KEY, surface BLOB NOT NULL, observed_at TEXT NOT NULL,
            expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', surface_key TEXT
          );
          CREATE TABLE IF NOT EXISTS coverage (
            period_key TEXT PRIMARY KEY, app_id TEXT NOT NULL, device_id TEXT NOT NULL,
            period_start TEXT NOT NULL, period_end TEXT NOT NULL,
            samples INTEGER NOT NULL DEFAULT 0, expired INTEGER NOT NULL DEFAULT 0
          );
          CREATE TABLE IF NOT EXISTS coverage_windows (
            period_key TEXT NOT NULL, window_key TEXT NOT NULL,
            PRIMARY KEY(period_key,window_key)
          );
          CREATE TABLE IF NOT EXISTS coverage_totals (
            name TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0
          );
          CREATE TABLE IF NOT EXISTS coverage_expirations (
            context_key TEXT PRIMARY KEY, period_key TEXT NOT NULL, occurred_at TEXT NOT NULL
          );
        """)
        with self.transaction():
            if "surface_key" not in {row["name"] for row in self.db.execute("PRAGMA table_info(quarantine)")}:
                self.db.execute("ALTER TABLE quarantine ADD COLUMN surface_key TEXT")
                # Legacy IDs identified a surface forever. Assign occurrence IDs without
                # rewriting any already-promoted immutable observation.
                for row in list(self.db.execute("SELECT id FROM quarantine")):
                    self.db.execute("UPDATE quarantine SET id=?,surface_key=? WHERE id=?", (str(uuid.uuid4()), row["id"], row["id"]))
            self.db.execute("CREATE INDEX IF NOT EXISTS quarantine_surface ON quarantine(surface_key,state,expires)")
            if "coverage_period" not in {row["name"] for row in self.db.execute("PRAGMA table_info(quarantine)")}:
                self.db.execute("ALTER TABLE quarantine ADD COLUMN coverage_period TEXT")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _insert(self, payload: dict, image: bytes | None = None):
        uuid.UUID(payload["event_id"])
        body = canonical(payload)
        image_hash = sha256(image).hexdigest() if image is not None else None
        digest = sha256(body + (image_hash or "").encode()).hexdigest()
        existing = self.db.execute("SELECT content_hash FROM events WHERE event_id=?", (payload["event_id"],)).fetchone()
        if existing:
            if existing["content_hash"] != digest:
                raise ValueError("event_id_payload_conflict")
            return False
        used = self.storage_size()
        if used + len(body) + len(image or b"") > self.max_content_bytes:
            raise CapacityError("local_storage_capacity_reached")
        self.db.execute("INSERT INTO events(event_id,payload,content_hash,image,image_hash,created_at) VALUES(?,?,?,?,?,?)",
                        (payload["event_id"], body, digest, image, image_hash, time.time()))
        return True

    def enqueue(self, payload: dict, image: bytes | None = None):
        with self.transaction():
            return self._insert(payload, image)

    def record_changed(self, source: str, digest: str, payload: dict, image: bytes | None = None):
        with self.transaction():
            previous = self.db.execute("SELECT digest FROM cursors WHERE source=?", (source,)).fetchone()
            if previous and previous["digest"] == digest:
                return False
            self._insert(payload, image)
            self.db.execute("INSERT INTO cursors VALUES(?,?) ON CONFLICT(source) DO UPDATE SET digest=excluded.digest", (source, digest))
            return True

    def quarantine_metadata(self, device_id: str, surface: dict, observed_at: str, now: float):
        safe = {key: surface.get(key, "") for key in ("app_id", "window_id", "process_id", "title", "record_ref", "evidence_level")}
        if surface.get("privacy_disposition"):
            safe["privacy_disposition"] = surface["privacy_disposition"]
        for key in ("title", "record_ref"):
            safe[key] = str(safe[key])[:4096]
        identity = sha256(canonical(safe)).hexdigest()
        surface_key = str(uuid.uuid5(uuid.UUID(device_id), "quarantine:" + identity))
        with self.transaction():
            # This expires only this collector's temporary unknown metadata, never user files.
            self.expire_quarantine(now)
            if self.db.execute("SELECT id FROM quarantine WHERE surface_key=? AND state='pending'", (surface_key,)).fetchone():
                return True
            if self.db.execute("SELECT count(*) FROM quarantine WHERE state='pending'").fetchone()[0] >= 500:
                return False
            if self.storage_size() + len(canonical(safe)) > self.max_content_bytes:
                return False
            self.db.execute("INSERT INTO quarantine(id,surface,observed_at,expires,surface_key) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), canonical(safe), observed_at, now + 24 * 3600, surface_key))
        return True

    def quarantine_open(self):
        """Pending rows, including those already due to expire. Does not delete."""
        return [dict(row) | {"surface": json.loads(row["surface"])} for row in self.db.execute("SELECT * FROM quarantine WHERE state='pending' ORDER BY observed_at")]

    def quarantine_pending(self, now: float):
        with self.transaction():
            self.expire_quarantine(now)
        return [dict(row) | {"surface": json.loads(row["surface"])} for row in self.db.execute("SELECT * FROM quarantine WHERE state='pending' AND expires>? ORDER BY observed_at", (now,))]

    def expire_quarantine(self, now):
        # Caller owns transaction. Keep only anonymous counts when temporary metadata expires.
        for row in list(self.db.execute("SELECT id,coverage_period FROM quarantine WHERE expires<=? AND state='pending'", (now,))):
            self.db.execute("UPDATE coverage SET expired=expired+1 WHERE period_key=?", (row["coverage_period"],))
            self.db.execute("INSERT INTO coverage_totals VALUES('expired_unresolved',1) ON CONFLICT(name) DO UPDATE SET count=count+1")
            if row["coverage_period"]:
                self.db.execute("INSERT OR IGNORE INTO coverage_expirations VALUES(?,?,?)", (sha256(row["id"].encode()).hexdigest(), row["coverage_period"], datetime.fromtimestamp(now, timezone.utc).isoformat()))
        self.db.execute("DELETE FROM quarantine WHERE expires<=?", (now,))

    def exclude_quarantine(self, key):
        # Only collector-owned temporary metadata. A personal context is not a missed company task.
        self.db.execute("DELETE FROM quarantine WHERE id=?", (key,))

    def observe_unknown(self, device_id, surface, observed_at, now):
        if not self.quarantine_metadata(device_id, surface, observed_at, now):
            return None
        safe = {key: surface.get(key, "") for key in ("app_id", "window_id", "process_id", "title", "record_ref", "evidence_level")}
        if surface.get("privacy_disposition"):
            safe["privacy_disposition"] = surface["privacy_disposition"]
        for key in ("title", "record_ref"): safe[key] = str(safe[key])[:4096]
        surface_key = str(uuid.uuid5(uuid.UUID(device_id), "quarantine:" + sha256(canonical(safe)).hexdigest()))
        item = self.db.execute("SELECT id,observed_at FROM quarantine WHERE surface_key=? AND state='pending'", (surface_key,)).fetchone()
        app = str(surface.get("app_id", "unknown"))[:160]
        # Keep a surviving occurrence stable across clock-hour changes; another
        # sample alone must not reopen an already answered context.
        first = datetime.fromisoformat(item["observed_at"]).timestamp()
        start = datetime.fromtimestamp(int(first // 3600) * 3600, timezone.utc).isoformat()
        period_key = sha256(canonical([device_id, app, start])).hexdigest()
        window_key = sha256(canonical([surface.get("window_id"), surface.get("process_id")])).hexdigest()
        with self.transaction():
            self.db.execute("INSERT INTO coverage(period_key,app_id,device_id,period_start,period_end,samples) VALUES(?,?,?,?,?,1) ON CONFLICT(period_key) DO UPDATE SET samples=samples+1,period_end=excluded.period_end", (period_key, app, device_id, start, observed_at))
            self.db.execute("INSERT OR IGNORE INTO coverage_windows VALUES(?,?)", (period_key, window_key))
            self.db.execute("UPDATE quarantine SET coverage_period=coalesce(coverage_period,?) WHERE id=?", (period_key, item["id"]))
            self.db.execute("INSERT INTO coverage_totals VALUES('observed_samples',1) ON CONFLICT(name) DO UPDATE SET count=count+1")
            # Bound count-only period history; all-time totals remain visible.
            cutoff = datetime.fromtimestamp(now - 7 * 86400, timezone.utc).isoformat()
            self.db.execute("DELETE FROM coverage_windows WHERE period_key IN (SELECT period_key FROM coverage WHERE period_end<? AND period_key NOT IN (SELECT period_key FROM coverage_expirations))", (cutoff,))
            self.db.execute("DELETE FROM coverage WHERE period_end<? AND period_key NOT IN (SELECT period_key FROM coverage_expirations)", (cutoff,))
        row = self.db.execute("SELECT * FROM coverage WHERE period_key=?", (period_key,)).fetchone()
        windows = self.db.execute("SELECT count(*) FROM coverage_windows WHERE period_key=?", (period_key,)).fetchone()[0]
        return {"context_key": sha256(item["id"].encode()).hexdigest(),
                "coverage": {"period_start": start, "period_end": observed_at, "observed_samples": row["samples"],
                             "distinct_windows": windows, "expired_unresolved": row["expired"]}}

    def coverage_status(self):
        totals = {row["name"]: row["count"] for row in self.db.execute("SELECT * FROM coverage_totals")}
        periods = [{"app_id": r["app_id"], "device_id": r["device_id"], "period_start": r["period_start"], "period_end": r["period_end"],
                    "observed_samples": r["samples"], "expired_unresolved": r["expired"],
                    "distinct_windows": self.db.execute("SELECT count(*) FROM coverage_windows WHERE period_key=?", (r["period_key"],)).fetchone()[0]}
                   for r in self.db.execute("SELECT * FROM coverage ORDER BY period_start DESC")]
        return {"observed_samples": totals.get("observed_samples", 0), "expired_unresolved": totals.get("expired_unresolved", 0),
                "expiry_updates_pending": self.db.execute("SELECT count(*) FROM coverage_expirations").fetchone()[0],
                "periods": periods, "meaning": "Só o que ficou explicitamente sem conteúdo, ou metadado antigo ainda não relido. Uma janela de trabalho entra no mapa. Contagens não comprovam inatividade. Metadados locais sem conteúdo expiram em 24 horas; o que é pessoal não entra nestes totais."}

    def enqueue_expiry_updates(self, policy):
        with self.transaction():
            for update in list(self.db.execute("SELECT * FROM coverage_expirations")):
                period = self.db.execute("SELECT * FROM coverage WHERE period_key=?", (update["period_key"],)).fetchone()
                if not period:
                    continue
                windows = self.db.execute("SELECT count(*) FROM coverage_windows WHERE period_key=?", (update["period_key"],)).fetchone()[0]
                text = "Metadados locais sem vínculo empresarial confirmado expiraram; o conteúdo anterior não foi recuperado."
                self._insert({"schema_version": 1, "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "coverage-expiry:" + update["context_key"])),
                    "device_id": period["device_id"], "observed_at": update["occurred_at"],
                    "source": {"kind": "coverage_gap", "surface_kind": "desktop_window", "app_id": period["app_id"]},
                    "evidence": {"text": text, "text_sha256": sha256(text.encode()).hexdigest()},
                    "classification": {"state": "uncertain", "signals": [], "reason": "unknown_metadata_expired", "context_version": policy.context_version},
                    "policy": {"id": policy.policy_id, "version": policy.version}, "capture_gaps": ["unknown_metadata_expired"],
                    "development_fixture": policy.control_reason in {"legacy_local_fixture", "legacy_synthetic_fixture", "ready_synthetic_fixture"},
                    "privacy_disposition": "withheld",
                    "context_key": update["context_key"], "coverage": {"period_start": period["period_start"], "period_end": update["occurred_at"],
                        "observed_samples": period["samples"], "distinct_windows": windows, "expired_unresolved": period["expired"]}})
                self.db.execute("DELETE FROM coverage_expirations WHERE context_key=?", (update["context_key"],))

    def promote(self, key: str, payload: dict):
        with self.transaction():
            self._insert(payload)
            self.db.execute("UPDATE quarantine SET state='promoted' WHERE id=?", (key,))

    def buffer_episode(self, source: str, payload: dict, image: bytes | None, now: float):
        with self.transaction():
            previous = self.db.execute("SELECT * FROM episodes WHERE source=?", (source,)).fetchone()
            if previous:
                payload = dict(payload, event_id=json.loads(previous["payload"])["event_id"], observed_at=json.loads(previous["payload"])["observed_at"])
                first, started = previous["first_text"], previous["started"]
            else:
                first, started = payload["evidence"]["text"], now
            old_size = len(previous["payload"]) + len(previous["image"] or b"") + len(previous["first_text"].encode()) if previous else 0
            new_size = len(canonical(payload)) + len(image or b"") + len(first.encode())
            if self.storage_size() - old_size + new_size > self.max_content_bytes:
                raise CapacityError("local_storage_capacity_reached")
            self.db.execute("INSERT INTO episodes VALUES(?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,image=coalesce(excluded.image, episodes.image),updated=excluded.updated",
                            (source, canonical(payload), image, first, started, now))

    def finalize_episodes(self, now: float, *, force=False, max_seconds=300, idle_seconds=60):
        count = 0
        with self.transaction():
            rows = list(self.db.execute("SELECT * FROM episodes WHERE ? OR started<=? OR updated<=?", (force, now - max_seconds, now - idle_seconds)))
            # Release every selected source inside the same transaction, so one larger
            # destination can use space freed by another move in this atomic batch.
            for row in rows:
                self.db.execute("DELETE FROM episodes WHERE source=?", (row["source"],))
            for row in rows:
                body = json.loads(row["payload"])
                latest = body["evidence"]["text"]
                if latest != row["first_text"]:
                    body["evidence"]["text"] = "Primeira leitura observada:\n" + row["first_text"][:8000] + "\nLeitura mais recente observada:\n" + latest[:8000]
                    body["evidence"]["text_sha256"] = sha256(body["evidence"]["text"].encode()).hexdigest()
                body["source"]["observed_until"] = row["updated"]
                # Compare with this window's last finalized episode, not the last device event.
                stable = {"source": {k: v for k, v in body["source"].items() if k != "observed_until"}, "evidence": body["evidence"], "policy": body["policy"]}
                digest = sha256(canonical(stable)).hexdigest()
                cursor = self.db.execute("SELECT digest FROM cursors WHERE source=?", (row["source"],)).fetchone()
                # A failed destination insertion restores all source rows and cursors.
                if not cursor or cursor["digest"] != digest:
                    self._insert(body, row["image"])
                    self.db.execute("INSERT INTO cursors VALUES(?,?) ON CONFLICT(source) DO UPDATE SET digest=excluded.digest", (row["source"], digest))
                    count += 1
        return count

    def claim(self, now: float, lease_seconds: float = 60):
        with self.transaction():
            row = self.db.execute("SELECT * FROM events WHERE (state='pending' AND next_attempt<=?) OR (state='leased' AND lease_until<=?) ORDER BY created_at,event_id LIMIT 1", (now, now)).fetchone()
            if not row:
                return None
            token = str(uuid.uuid4())
            self.db.execute("UPDATE events SET state='leased',lease_token=?,lease_until=? WHERE event_id=?", (token, now + lease_seconds, row["event_id"]))
            return dict(row) | {"lease_token": token, "payload": json.loads(row["payload"])}

    def update_claim(self, item: dict, **fields):
        allowed = {"state", "last_error", "attempts", "next_attempt", "attachment_receipt", "outbound", "receipt", "lease_until"}
        if not fields.keys() <= allowed:
            raise ValueError("invalid_outbox_update")
        sql = ",".join(key + "=?" for key in fields)
        changed = self.db.execute("UPDATE events SET " + sql + " WHERE event_id=? AND lease_token=? AND state='leased'", (*fields.values(), item["event_id"], item["lease_token"])).rowcount
        if changed != 1:
            raise LeaseLost("delivery_lease_lost")

    def suppress_image(self, item: dict):
        # Never rewrite an event under its old id: a prior send may have succeeded.
        body = item["payload"].copy()
        body["event_id"] = str(uuid.uuid5(uuid.UUID(body["event_id"]), "image-withheld"))
        body["evidence"] = dict(body["evidence"])
        body["evidence"].pop("image_sha256", None)
        body["capture_gaps"] = list(body.get("capture_gaps", [])) + ["image_withheld_by_current_policy"]
        with self.transaction():
            self.update_claim(item, state="blocked_policy", last_error="image_permission_revoked")
            self._insert(body)

    def renew_policy(self, item: dict, policy):
        body = json.loads(canonical(item["payload"]))
        body["event_id"] = str(uuid.uuid5(uuid.UUID(body["event_id"]), "policy:" + str(policy.version)))
        body["policy"] = {"id": policy.policy_id, "version": policy.version}
        body["capture_gaps"] = list(body.get("capture_gaps", [])) + ["policy_changed_before_delivery"]
        body["classification"]["context_version"] = policy.context_version
        image = item["image"] if policy.screenshot_allowed else None
        if image is None:
            body["evidence"].pop("image_sha256", None)
        with self.transaction():
            self.update_claim(item, state="blocked_policy", last_error="superseded_by_current_policy")
            self._insert(body, image)

    def status(self):
        result = {row["state"]: row["n"] for row in self.db.execute("SELECT state,count(*) n FROM events GROUP BY state")}
        buffered = self.db.execute("SELECT count(*) FROM episodes").fetchone()[0]
        if buffered: result["buffered"] = buffered
        return result

    def storage_size(self):
        total = self.db.execute("SELECT coalesce(sum(length(payload)+coalesce(length(image),0)),0) FROM events").fetchone()[0]
        total += self.db.execute("SELECT coalesce(sum(length(payload)+length(CAST(first_text AS BLOB))+coalesce(length(image),0)),0) FROM episodes").fetchone()[0]
        total += self.db.execute("SELECT coalesce(sum(length(surface)),0) FROM quarantine").fetchone()[0]
        return total
