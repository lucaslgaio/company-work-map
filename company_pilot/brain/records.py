"""Typed company-source revisions; no download, credentials or desktop fiction."""
import copy
from datetime import datetime
import hashlib
import re

from .stores import AccessDenied, BrainError, digest, canonical
from .policy import controls_for


class RecordMixin:
    def ingest_company_record(self, payload):
        from .engine import identifier, valid_uuid
        fields = {"schema_version", "event_id", "occurred_at", "synthetic", "source", "operation", "content", "object"}
        if not isinstance(payload, dict) or set(payload) != fields or type(payload["schema_version"]) is not int or payload["schema_version"] != 1 or type(payload["synthetic"]) is not bool or payload["operation"] not in ("upsert", "removed"):
            raise BrainError("typed_company_record_required")
        event_id = valid_uuid(payload["event_id"])
        source, content, obj = payload["source"], payload["content"], payload["object"]
        if not isinstance(source, dict) or set(source) != {"system", "record_id", "url", "revision"} or any(not isinstance(value, str) or not value.strip() or len(value) > 4000 for value in source.values()):
            raise BrainError("provider_record_identity_required")
        if not isinstance(content, dict) or set(content) - {"text", "sha256", "complete"} or not {"text", "sha256"} <= set(content) or not isinstance(content["text"], str) or len(content["text"].encode()) > 256 * 1024 or content["sha256"] != hashlib.sha256(content["text"].encode()).hexdigest() or type(content.get("complete", False)) is not bool:
            raise BrainError("bounded_record_extract_hash_required")
        if obj is not None and (not isinstance(obj, dict) or set(obj) != {"storage_ref", "sha256", "media_type", "size_bytes"} or not isinstance(obj["storage_ref"], str) or not 1 <= len(obj["storage_ref"]) <= 4000 or not isinstance(obj["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", obj["sha256"]) or not isinstance(obj["media_type"], str) or not 1 <= len(obj["media_type"]) <= 200 or type(obj["size_bytes"]) is not int or not 0 <= obj["size_bytes"] < 2**63):
            raise BrainError("typed_snapshot_locator_required")
        try:
            if datetime.fromisoformat(payload["occurred_at"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except (TypeError, AttributeError, ValueError) as error:
            raise BrainError("timestamp_with_timezone_required") from error
        def apply(state, actor):
            if actor["id"] not in state.company.get("watchdog_actors", []):
                raise AccessDenied("configured_watchdog_actor_required")
            if state.company["synthetic"] != bool(payload["synthetic"]):
                raise AccessDenied("synthetic_real_boundary_mismatch")
            policy = state.company.get("access_policy")
            if not policy and not state.company["synthetic"] or policy and controls_for(state.company, actor["id"])["access_state"] != "verified":
                raise AccessDenied("company_human_access_not_verified")
            logical = identifier(state.company["id"], "company-record:" + source["system"] + ":" + source["record_id"])
            sid = identifier(logical, source["revision"] + ":" + payload["operation"])
            previous = state.sources.get(sid)
            fingerprint = digest(canonical({key: payload[key] for key in ("source", "operation", "content", "object", "synthetic")}))
            if previous:
                if previous["record_fingerprint"] != fingerprint:
                    raise BrainError("provider_revision_changed_bytes")
                return previous["ingestion_receipt"]
            record = state.company_records.get(logical, {"id": logical, "source_system": source["system"], "record_id": source["record_id"], "history": []})
            raw = self._source(state, actor, sid, "company_record", content["text"] or ("Registro removido na fonte; as versões anteriores permanecem preservadas." if payload["operation"] == "removed" else "Registro arquivado sem extração textual; conteúdo não interpretado."),
                logical_record_id=logical, source_record=copy.deepcopy(source), record_operation=payload["operation"],
                extract_complete=content.get("complete", False), object=copy.deepcopy(obj), record_fingerprint=fingerprint, occurred_at=payload["occurred_at"])
            job_id = self._queue(state, raw, "company_record") if payload["operation"] == "upsert" and content["text"].strip() else None
            receipt = {"source_id": sid, "logical_record_id": logical, "revision": source["revision"], "job_id": job_id,
                "state": "removed_at_source" if payload["operation"] == "removed" else "pending_analysis" if job_id else "recorded_without_extract",
                "extract_complete": content.get("complete", False), "evidence_level": "E1" if state.company["synthetic"] else "company_source"}
            raw["ingestion_receipt"] = copy.deepcopy(receipt)
            record["history"].append(sid)
            record.update(current_source_id=sid, revision=source["revision"], state="removed" if payload["operation"] == "removed" else "active", url=source["url"])
            state.company_records[logical] = record
            return receipt
        return self._transaction("company_record", event_id, payload, apply)
