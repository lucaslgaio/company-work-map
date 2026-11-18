"""Capture orchestration; no business-task interpretation or backend assumptions."""
from datetime import datetime
from hashlib import sha256
import json
import uuid
from typing import Callable, Protocol

from .outbox import CapacityError, LeaseLost, Outbox, canonical
from .policy import Policy, utcnow


class NativeFailure(RuntimeError):
    pass


class Native(Protocol):
    def context(self) -> dict: ...
    def text(self, expected: dict) -> dict: ...
    def screenshot(self, expected: dict) -> bytes: ...


class Transport(Protocol):
    def upload_attachment(self, event_id: str, data: bytes, digest: str) -> dict: ...
    def send_observation(self, payload: dict) -> dict: ...


class PermanentDeliveryError(RuntimeError):
    """Invalid credential/role/payload: recovery requires configuration, not retry."""


class PolicyRefreshRequired(RuntimeError):
    pass


def _identity(surface: dict):
    return tuple(surface.get(key) for key in ("window_id", "process_id", "title", "record_ref"))


class Collector:
    def __init__(self, device_id: str, native: Native, outbox: Outbox,
                 policy: Callable[[], Policy], clock: Callable[[], datetime] = utcnow,
                 episode_seconds: int = 300):
        self.device_id = str(uuid.UUID(device_id))
        self.native, self.outbox, self.policy, self.clock = native, outbox, policy, clock
        self.episode_seconds = episode_seconds

    def reclassify_quarantine(self):
        current, now = self.policy(), self.clock()
        if not current.valid(now):
            return 0
        count = 0
        for item in self.outbox.quarantine_pending(now.timestamp()):
            current = self.policy()
            if not current.valid(self.clock()):
                return count
            surface = item["surface"]
            decision = current.classify(surface, now)
            if decision.state == "excluded":
                self.outbox.exclude_quarantine(item["id"])
                continue
            if decision.state != "company":
                continue
            text = "Metadados observados antes de estabelecer o contexto da empresa:\nTítulo: " + str(surface["title"]) + "\nReferência: " + str(surface["record_ref"])
            payload = {"schema_version": 1, "event_id": str(uuid.uuid5(uuid.UUID(item["id"]), "promoted")), "device_id": self.device_id,
                       "observed_at": item["observed_at"], "source": {"kind": "desktop_window_metadata", **surface},
                       "evidence": {"text": text, "text_sha256": sha256(text.encode()).hexdigest()},
                       "classification": {"state": "company", "signals": list(decision.signals), "reason": "founder_context_resolved_local_metadata", "context_version": current.context_version},
                       "policy": {"id": current.policy_id, "version": current.version},
                       "capture_gaps": ["metadata_only_no_original_body_or_image"], "development_fixture": surface.get("evidence_level") == "synthetic"}
            latest = self.policy()
            if not latest.valid(self.clock()) or latest.classify(surface, self.clock()).state != "company":
                continue
            self.outbox.promote(item["id"], payload)
            count += 1
            # The old body was never read. Re-observe only the same, still focused
            # permitted surface; the resulting event bears the current timestamp.
            self.tick(force_finalize=True, _reclassify=False, expected_surface=surface)
        return count

    def tick(self, force_finalize=False, *, _reclassify=True, expected_surface=None):
        now = self.clock()
        self.outbox.quarantine_pending(now.timestamp())
        policy = self.policy()
        if not policy.valid(now):
            return {"status": "policy_unavailable", "code": policy.control_reason}
        try:
            promoted = self.reclassify_quarantine() if _reclassify else 0
            self.outbox.enqueue_expiry_updates(policy)
            finalized = self.outbox.finalize_episodes(now.timestamp(), max_seconds=self.episode_seconds)
        except CapacityError:
            return {"status": "local_storage_capacity_reached"}
        try:
            surface = self.native.context()
        except NativeFailure as error:
            return {"status": "capture_unavailable", "code": str(error)}
        if expected_surface is not None and _identity(surface) != _identity(expected_surface):
            return {"status": "focus_changed"}
        policy = self.policy()
        decision = policy.classify(surface, now)
        if decision.state == "excluded":
            return {"status": "excluded", "code": decision.reason}
        gaps, image = [], None
        if decision.state == "uncertain":
            summary = self.outbox.observe_unknown(self.device_id, surface, now.isoformat(), now.timestamp())
            if summary is None:
                return {"status": "local_metadata_capacity_reached", "classification": "uncertain", "coverage_incomplete": True}
            # No title, record ref, document text, screenshot, or private term goes online.
            source = {"kind": "coverage_gap", "surface_kind": "desktop_window", "app_id": str(surface.get("app_id", "unknown"))[:160]}
            evidence = {"text": "An observed desktop context has no established company connection."}
            signals = []
        else:
            try:
                details = self.native.text(surface)
                if _identity(details) != _identity(surface):
                    return {"status": "focus_changed"}
            except NativeFailure as error:
                if str(error) == "focus_changed":
                    return {"status": "focus_changed"}
                details = surface
                gaps.append(str(error))
            if details.get("secure_context"):
                return {"status": "excluded", "code": "protected_surface"}
            # Re-classify after text extraction: a document may have changed in place.
            if policy.classify(details, now).state != "company":
                return {"status": "focus_changed"}
            source = {"kind": "desktop_window", **{key: surface.get(key, "") for key in ("app_id", "title", "record_ref", "window_id", "process_id")}}
            evidence = {"text": str(details.get("text", ""))[:16000]}
            signals = list(decision.signals)
            current = self.policy()
            if (current.policy_id != policy.policy_id or current.version != policy.version or not current.valid(self.clock())
                    or current.classify(surface, self.clock()).state != "company"):
                return {"status": "policy_changed"}
            if current.permits_image(self.clock()):
                try:
                    image = self.native.screenshot(surface)
                    if not 0 < len(image) <= 2 * 1024 * 1024:
                        raise NativeFailure("image_size_invalid")
                    # Defense beyond the helper's own pre/post focus checks.
                    if _identity(self.native.context()) != _identity(surface):
                        return {"status": "focus_changed"}
                except NativeFailure as error:
                    if str(error) == "focus_changed":
                        return {"status": "focus_changed"}
                    image = None
                    gaps.append(str(error))
            after = self.policy()
            if (after.policy_id != policy.policy_id or after.version != policy.version or not after.valid(self.clock())
                    or after.classify(surface, self.clock()).state != "company"):
                return {"status": "policy_changed"}
            if image is not None and not after.permits_image(self.clock()):
                image = None
                gaps.append("image_permission_changed_during_capture")
        evidence["text_sha256"] = sha256(evidence["text"].encode()).hexdigest()
        if image is not None:
            evidence["image_sha256"] = sha256(image).hexdigest()
        payload = {
            "schema_version": 1, "event_id": str(uuid.uuid4()), "device_id": self.device_id,
            "observed_at": now.isoformat(), "source": source, "evidence": evidence,
            "classification": {"state": decision.state, "signals": signals, "reason": decision.reason, "context_version": policy.context_version},
            "policy": {"id": policy.policy_id, "version": policy.version},
            "capture_gaps": gaps,
            "development_fixture": surface.get("evidence_level") == "synthetic",
        }
        if decision.state == "uncertain":
            payload.update(summary)
            # A missing authorized title is not evidence of a personal activity.
            # Preserve an unresolved sanitized gap under the shared core seam.
            payload["privacy_disposition"] = "withheld"
        digest = sha256(canonical({key: payload[key] for key in ("source", "evidence", "classification", "policy", "capture_gaps")})).hexdigest()
        try:
            if decision.state == "company" and self.episode_seconds > 0:
                key = self.device_id + ":window:" + sha256(canonical({key: source.get(key) for key in ("app_id", "window_id", "process_id", "title", "record_ref")})).hexdigest()
                self.outbox.buffer_episode(key, payload, image, now.timestamp())
                finalized += self.outbox.finalize_episodes(now.timestamp(), force=force_finalize, max_seconds=self.episode_seconds)
                return {"status": "queued" if finalized or promoted else ("unchanged" if force_finalize else "buffered"), "classification": "company"}
            if decision.state == "uncertain":
                # Distinct contexts remain distinct. Cumulative count refreshes are
                # bounded to five minutes, plus explicit one-shot fixture endpoints.
                key = self.device_id + ":gap:" + summary["context_key"]
                digest = sha256(canonical({"context": summary["context_key"], "period": int(now.timestamp() // 300),
                                          "windows": summary["coverage"]["distinct_windows"], "policy": policy.version,
                                          "samples": summary["coverage"]["observed_samples"] if force_finalize else None})).hexdigest()
            else:
                key = self.device_id
            changed = self.outbox.record_changed(key, digest, payload, image)
        except CapacityError:
            return {"status": "local_storage_capacity_reached"}
        return {"status": "queued" if changed else "unchanged", "classification": decision.state}


def flush(outbox: Outbox, transport: Transport, policy: Callable[[], Policy],
          clock: Callable[[], datetime] = utcnow, limit: int = 25):
    counts = {"delivered": 0, "retry": 0, "blocked": 0}
    ready = []

    def excluded_receipt(receipt):
        return receipt.get("state") == "excluded" and receipt.get("privacy_disposition") in ("personal", "excluded")

    def valid_receipt(receipt):
        return excluded_receipt(receipt) or bool(receipt.get("observation_id") and receipt.get("inspect_url"))

    def acknowledge(item, receipt):
        if excluded_receipt(receipt):
            outbox.update_claim(item, state="blocked_policy", receipt=json.dumps(receipt), last_error="private_context_not_ingested")
            counts["blocked"] += 1
        else:
            outbox.update_claim(item, state="delivered", receipt=json.dumps(receipt), last_error=None)
            counts["delivered"] += 1

    def authorized_for_send(item, payload, current):
        now = clock()
        if not current.valid(now):
            outbox.update_claim(item, state="pending", next_attempt=now.timestamp() + 60, last_error="policy_unavailable")
            counts["retry"] += 1
            return False
        if payload["classification"]["state"] == "company" and current.classify(payload["source"], now).state != "company":
            outbox.update_claim(item, state="blocked_policy", last_error="company_context_no_longer_authorized")
            counts["blocked"] += 1
            return False
        if payload.get("attachments") and not current.permits_image(now):
            outbox.suppress_image(item)
            counts["blocked"] += 1
            return False
        return True

    def failed(item, error):
        try:
            if isinstance(error, PolicyRefreshRequired):
                current = policy()
                if current.version == item["payload"]["policy"]["version"]:
                    outbox.update_claim(item, state="pending", next_attempt=clock().timestamp() + 60, last_error="policy_refresh_pending")
                    counts["retry"] += 1
                    return
                decision = current.classify(item["payload"]["source"], clock())
                if item["payload"]["classification"]["state"] == "uncertain" or decision.state == "company":
                    outbox.renew_policy(item, current)
                else:
                    if decision.state == "uncertain":
                        outbox.quarantine_metadata(item["payload"]["device_id"], item["payload"]["source"], item["payload"]["observed_at"], clock().timestamp())
                    outbox.update_claim(item, state="blocked_policy", last_error="company_context_requires_review")
                counts["retry"] += 1
            elif isinstance(error, PermanentDeliveryError):
                outbox.update_claim(item, state="blocked_delivery", last_error="credential_role_or_payload_rejected")
                counts["blocked"] += 1
            elif not isinstance(error, LeaseLost):
                attempts = item["attempts"] + 1
                outbox.update_claim(item, state="pending", attempts=attempts, next_attempt=clock().timestamp() + min(3600, 2 ** min(attempts, 11)), last_error="delivery_retry_required")
                counts["retry"] += 1
        except LeaseLost:
            counts["retry"] += 1

    for _ in range(limit):
        now = clock()
        item = outbox.claim(now.timestamp(), lease_seconds=600)
        if item is None:
            break
        try:
            current = policy()
            if not current.valid(now):
                outbox.update_claim(item, state="pending", next_attempt=now.timestamp() + 60, last_error="policy_unavailable")
                counts["retry"] += 1
                break
            if item["payload"]["classification"]["state"] == "company" and current.classify(item["payload"]["source"], now).state != "company":
                outbox.update_claim(item, state="blocked_policy", last_error="company_context_no_longer_authorized")
                counts["blocked"] += 1
                continue
            if item["image"] is not None and not current.permits_image(now):
                outbox.suppress_image(item)
                counts["blocked"] += 1
                continue
            payload = item["payload"]
            if item["image"] is not None:
                attachment = json.loads(item["attachment_receipt"]) if item["attachment_receipt"] else transport.upload_attachment(item["event_id"], item["image"], item["image_hash"])
                if not attachment.get("id") or attachment.get("sha256") != item["image_hash"]:
                    raise PermanentDeliveryError("invalid_attachment_receipt")
                outbox.update_claim(item, attachment_receipt=json.dumps(attachment))
                payload = dict(payload, attachments=[{"id": attachment["id"], "sha256": item["image_hash"]}])
            # Policy can be revoked during an upload; never send a now-forbidden image link.
            latest = policy()
            if not latest.valid(clock()):
                raise ConnectionError("policy_unavailable")
            if item["image"] is not None and not latest.permits_image(clock()):
                outbox.suppress_image(item)
                counts["blocked"] += 1
                continue
            outbound = canonical(payload)
            if item["outbound"] and item["outbound"] != outbound:
                raise PermanentDeliveryError("outbound_payload_conflict")
            outbox.update_claim(item, outbound=outbound)
            ready.append((item, payload))
        except Exception as error:
            failed(item, error)
    if ready:
        if hasattr(transport, "send_observations"):
            sendable = []
            try:
                # Preparation may have uploaded later attachments or learned a refusal.
                # Check the entire prepared set using the final policy at this call boundary.
                final_policy = policy()
            except Exception as error:
                for item, _ in ready:
                    failed(item, error)
            else:
                for item, payload in ready:
                    try:
                        if authorized_for_send(item, payload, final_policy):
                            sendable.append((item, payload))
                    except Exception as error:
                        failed(item, error)
                if sendable:
                    try:
                        receipts = transport.send_observations([payload for _, payload in sendable])
                        if len(receipts) != len(sendable) or any(not valid_receipt(r) for r in receipts):
                            raise ConnectionError("missing_observation_receipt")
                        for (item, _), receipt in zip(sendable, receipts):
                            acknowledge(item, receipt)
                    except Exception as error:
                        for item, _ in sendable:
                            failed(item, error)
        else:
            for item, payload in ready:
                try:
                    # A non-batch sender has a separate policy boundary for every call.
                    if not authorized_for_send(item, payload, policy()):
                        continue
                    receipt = transport.send_observation(payload)
                    if not valid_receipt(receipt):
                        raise ConnectionError("missing_observation_receipt")
                    acknowledge(item, receipt)
                except Exception as error:
                    failed(item, error)
    return counts
