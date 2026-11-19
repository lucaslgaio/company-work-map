"""Adapter to the selected brain. No owner credential is stored or distributed."""
from hashlib import sha256
from dataclasses import replace
import json
import errno
from pathlib import Path
import time

from .collector import PermanentDeliveryError, PolicyRefreshRequired
from .policy import Policy, utcnow


def _authoritative_denial(error):
    kinds = {kind.__name__ for kind in type(error).__mro__}
    return (isinstance(error, PermanentDeliveryError)
            or bool(kinds & {"AccessDenied", "IntegrityError"})
            or ("GitHubError" in kinds and getattr(error, "status", 0) in {401, 403}))


def _transient_offline(error):
    if "GitHubError" in {kind.__name__ for kind in type(error).__mro__}:
        return getattr(error, "status", 0) in {0, 429} or getattr(error, "status", 0) >= 500
    return isinstance(error, (ConnectionError, TimeoutError)) or (isinstance(error, OSError) and error.errno in {
        errno.ENETDOWN, errno.ENETUNREACH, errno.ECONNRESET, errno.ETIMEDOUT,
        errno.EHOSTUNREACH, errno.ECONNREFUSED, errno.EPIPE})


class BrainTransport:
    def __init__(self, brain):
        self.brain = brain
        self._policy = None
        self._fetched = 0
        self.cache_path = None

    def _invalidate_policy(self):
        if self._policy is not None:
            self._policy = replace(self._policy, revoked=True)
            if self.cache_path:
                from .config import replace_config
                try:
                    replace_config(self.cache_path, self._policy.as_mapping())
                except OSError:
                    # Memory stays denied even when a broken local disk cannot save the cache.
                    pass
        self._fetched = 0

    def policy(self, force=False):
        if force or self._policy is None or time.monotonic() - self._fetched > 60 or not self._policy.current(utcnow()):
            try:
                if hasattr(self.brain, "store") and "write" not in self.brain.store.authenticated_actor()["permissions"]:
                    raise PermanentDeliveryError("repository_contribution_no_longer_permitted")
                current = Policy.from_brain(self.brain.device_policy())
                self._policy, self._fetched = current, time.monotonic()
                if self.cache_path:
                    from .config import replace_config
                    replace_config(self.cache_path, current.as_mapping())
            except Exception as error:
                if not _authoritative_denial(error) and _transient_offline(error):
                    if self._policy is None or not self._policy.current(utcnow()):
                        raise ConnectionError("device_policy_unavailable") from None
                else:
                    self._invalidate_policy()
                    raise PermanentDeliveryError("device_policy_refused_or_invalid") from None
        return self._policy

    def probe(self):
        actor = self._invoke(self.brain.store.authenticated_actor)
        def inspect_state():
            try:
                snapshot = self.brain.snapshot()
            except Exception as error:
                if str(error) == "brain_not_initialized" and hasattr(self, "expected_synthetic") and self.brain.store.read().head is None:
                    raise PermanentDeliveryError("state_branch_missing") from None
                raise
            if hasattr(self, "expected_synthetic") and snapshot["company"]["synthetic"] is not self.expected_synthetic:
                raise PermanentDeliveryError("synthetic_real_boundary_mismatch")
            return snapshot
        self._invoke(inspect_state)
        self.policy(force=True)
        return {"online": actor["provider"] == "github", "authenticated": True,
                "can_contribute": "write" in actor["permissions"], "actor": actor["id"],
                "permission_boundary": "whole_repository", "owner_credentials_distributed": False}

    def upload_attachment(self, event_id, data, digest):
        media = "image/jpeg" if data.startswith(b"\xff\xd8\xff") else "image/png"
        receipt = self._invoke(lambda: self.brain.upload_attachment(event_id, data, digest, media_type=media))
        return {"id": receipt["attachment_id"], "sha256": receipt["sha256"], "media_type": media}

    @staticmethod
    def wire(payload):
        source = payload["source"]
        text = payload["evidence"]["text"]
        if not text.strip():
            text = "Metadados de janela observados; texto indisponível.\nTítulo: " + str(source.get("title", ""))
        result = {"schema_version": 1, "event_id": payload["event_id"], "device_id": payload["device_id"],
                  "occurred_at": payload["observed_at"], "policy_version": payload["policy"]["version"],
                  "source": {"kind": source["kind"], "app": source.get("app_id", "unknown"),
                             "record_ref": source.get("record_ref") or None, "title": source.get("title"),
                             "origin": "synthetic_fixture" if payload.get("development_fixture") else "native",
                             "capture_gaps": payload.get("capture_gaps", []),
                             "observed_until": source.get("observed_until")},
                  "classification": {"label": payload["classification"]["state"], "reason": payload["classification"]["reason"],
                                     "context_version": payload["classification"].get("context_version", 1)},
                  "content": {"text": text, "sha256": sha256(text.encode()).hexdigest()},
                  "synthetic": payload.get("development_fixture") is True}
        if payload.get("attachments"):
            evidence = payload["attachments"][0]
            result["screenshot"] = {"attachment_id": evidence["id"], "sha256": evidence["sha256"], "media_type": evidence.get("media_type", "image/png")}
        return result

    def _invoke(self, operation):
        try:
            return operation()
        except Exception as error:
            name, code = type(error).__name__, str(error)
            if code == "refresh_device_policy":
                self.policy(force=True)
                raise PolicyRefreshRequired() from None
            if _authoritative_denial(error):
                self._invalidate_policy()
                if code in {"state_branch_missing", "synthetic_real_boundary_mismatch", "repository_write_access_required"}:
                    raise PermanentDeliveryError(code) from None
                raise PermanentDeliveryError("brain_access_or_integrity_denied") from None
            if name == "BrainError":
                if code in {"brain_not_initialized", "invalid_repository", "invalid_state_branch"}:
                    raise PermanentDeliveryError(code) from None
                raise PermanentDeliveryError("brain_access_integrity_or_payload_rejected") from None
            raise ConnectionError("brain_temporarily_unavailable") from None

    def send_observation(self, payload):
        return self.send_observations([payload])[0]

    def send_observations(self, payloads):
        receipts = []
        company = [item for item in payloads if item["classification"]["state"] == "company"]
        by_id = {}
        if company:
            returned = self._invoke(lambda: self.brain.send_observations([self.wire(item) for item in company]))
            by_id.update(zip((item["event_id"] for item in company), returned))
        for item in payloads:
            if item["classification"]["state"] == "uncertain":
                if not hasattr(self.brain, "ingest_context_gap"):
                    raise ConnectionError("coverage_gap_intake_pending")
                safe = {"schema_version": 1, "event_id": item["event_id"], "device_id": item["device_id"],
                        "occurred_at": item["observed_at"], "app": item["source"]["app_id"],
                        "surface_kind": "desktop_window", "synthetic": item.get("development_fixture") is True}
                for field in ("context_key", "coverage", "privacy_disposition"):
                    if field in item:
                        safe[field] = item[field]
                raw = self._invoke(lambda: self.brain.ingest_context_gap(safe))
                by_id[item["event_id"]] = {**raw, "observation_id": raw.get("observation_id") or raw.get("source_id")}
            receipts.append(by_id[item["event_id"]])
        return receipts


class GitHubTransport(BrainTransport):
    def __init__(self, config):
        from company_pilot.brain import Brain, GitHubStore
        store = GitHubStore(config["repo"], branch=config.get("branch", "company-state"),
                            mirror_dir=config["mirror_dir"], development=config.get("synthetic", False),
                            gh=config.get("gh", "gh"))
        super().__init__(Brain(store))
        self.expected_synthetic = config.get("synthetic", False) is True
        self.cache_path = Path(config["policy_cache"]) if config.get("policy_cache") else None
        if self.cache_path and self.cache_path.is_file():
            try:
                self._policy = Policy.parse(json.loads(self.cache_path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, OSError):
                pass
