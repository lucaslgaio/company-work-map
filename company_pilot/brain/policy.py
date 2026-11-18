"""Founder-requested access and participant-owned capture choices.

This module never calls GitHub or changes native access. The owner-side command
must obtain a read-only native receipt before recording its authenticated result.
"""
from __future__ import annotations

import copy
import re
import uuid

from ..access import AccessError, policy_request_hash, validate_verification_receipt
from .stores import AccessDenied, BrainError, canonical


def _identifier(namespace, value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(namespace) + ":" + str(value)))


def controls_for(company, actor_id):
    """Pure readback/gate; old synthetic fixtures are explicitly not consent."""
    policy = company.get("access_policy")
    participation = copy.deepcopy(company.get("participation", {}).get(actor_id))
    result = {"capture_allowed": False, "reason": "access_policy_not_configured",
              "access_state": (policy or {}).get("state", "pending"),
              "participation": participation, "policy_revision": company.get("policy_version", 1)}
    if not policy:
        if company.get("synthetic") is True and participation is None:
            result.update(capture_allowed=True, reason="legacy_synthetic_fixture")
        return result
    if policy.get("state") != "verified":
        result["reason"] = "access_policy_unsupported" if policy.get("state") == "unsupported" else "access_verification_pending"
        return result
    receipt = (policy.get("verification") or {}).get("receipt")
    try:
        validate_verification_receipt(receipt)
        current = policy_request_hash(policy.get("requests"), policy.get("request_reference"))
    except AccessError:
        result.update(access_state="pending", reason="access_verification_pending")
        return result
    if (receipt["request_hash"] != current or policy.get("request_hash") != current
            or receipt["state"] != "verified" or policy.get("effective") != receipt["effective"]):
        result.update(access_state="pending", reason="access_verification_pending")
        return result
    effective = next((item for item in policy.get("effective", []) if item.get("actor_id", "").casefold() == actor_id.casefold()), None)
    if not effective or effective.get("state") != "verified" or effective.get("read_scope") != "all" or effective.get("modify_scope") != "all":
        result["reason"] = "participant_repository_write_unavailable"
        return result
    if not participation:
        result["reason"] = "participation_not_recorded"
    elif participation.get("participating") is not True:
        result["reason"] = "participation_declined"
    elif participation.get("acknowledged_policy_version") != company.get("policy_version", 1):
        result["reason"] = "participant_policy_acknowledgement_stale"
    elif participation.get("local_exclusions_configured") is not True or not isinstance(participation.get("local_policy_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", participation["local_policy_sha256"]):
        result["reason"] = "local_personal_policy_not_configured"
    else:
        result.update(capture_allowed=True, reason="ready_synthetic_fixture" if company.get("synthetic") is True else "ready")
    return result


def screenshots_for(company, actor_id):
    controls = controls_for(company, actor_id)
    role = "founder" if actor_id in company.get("founders", []) else "team"
    allowed = company.get("screenshots", {}).get(role) is True
    if controls["reason"] == "legacy_synthetic_fixture":
        return allowed
    return controls["capture_allowed"] and allowed and (controls["participation"] or {}).get("screenshots") is True


class PolicyMixin:
    def access_policy(self):
        policy = self.snapshot()["company"].get("access_policy")
        return copy.deepcopy(policy or {"revision": 0, "requests": [], "request_reference": None,
            "request_hash": None, "state": "pending", "effective": [], "verification": None,
            "blockers": [{"actor_id": "company", "code": "access_policy_not_configured"}]})

    def set_access_policy(self, requests, event_id, request_reference):
        try:
            policy_request_hash(requests, request_reference)
        except AccessError as error:
            raise BrainError(str(error)) from error
        requests = copy.deepcopy(requests)
        request_reference = request_reference.strip()
        payload = {"requests": requests, "request_reference": request_reference}
        def apply(state, actor):
            return self._apply_requested_access(state, actor, requests, event_id, request_reference)
        return self._transaction("access_policy_request", event_id, payload, apply)

    def set_person_access_policy(self, request, event_id, request_reference):
        """Merge one explicit human intent against each current CAS snapshot.

        The idempotency payload contains that person only. An unrelated person's
        intervening choice must neither change retry identity nor be replaced.
        set_access_policy remains the deliberate full-list replacement API.
        """
        request = copy.deepcopy(request)
        try:
            policy_request_hash([request], request_reference)
        except AccessError as error:
            raise BrainError(str(error)) from error
        request_reference = request_reference.strip()
        payload = {"request": request, "request_reference": request_reference}
        def apply(state, actor):
            previous = (state.company.get("access_policy") or {}).get("requests", [])
            requests = [copy.deepcopy(item) for item in previous
                        if item["actor_id"].casefold() != request["actor_id"].casefold()] + [copy.deepcopy(request)]
            return self._apply_requested_access(state, actor, requests, event_id, request_reference)
        return self._transaction("access_policy_person_request", event_id, payload, apply)

    def _apply_requested_access(self, state, actor, requests, event_id, request_reference):
        self._founder(state, actor)
        try:
            request_hash = policy_request_hash(requests, request_reference)
        except AccessError as error:
            raise BrainError(str(error)) from error
        previous = state.company.get("access_policy") or {}
        if previous.get("request_hash") == request_hash:
            return copy.deepcopy(previous)
        blockers = []
        for item in requests:
            pair = (item["read_scope"], item["modify_scope"])
            if ("selected" in pair or "own" in pair or (pair[0] == "none" and pair[1] != "none")
                    or (actor.get("owner_type", "User") == "User" and pair == ("all", "none"))):
                blockers.append({"actor_id": item["actor_id"], "code": "unsupported_native_scope"})
        source = self._source(state, actor, _identifier(event_id, "access-policy"), "access_policy_request",
            canonical({"requests": requests, "request_reference": request_reference}), speaker=actor["id"])
        policy = {"revision": previous.get("revision", 0) + 1, "requests": requests,
            "request_reference": request_reference, "request_hash": request_hash,
            "state": "unsupported" if blockers else "pending", "effective": [], "verification": None,
            "blockers": blockers, "source_id": source["id"]}
        state.company["access_policy"] = policy
        state.company["policy_version"] += 1
        return copy.deepcopy(policy)

    def record_access_verification(self, receipt, event_id):
        try:
            validate_verification_receipt(receipt)
        except AccessError as error:
            raise BrainError(str(error)) from error
        receipt = copy.deepcopy(receipt)
        def apply(state, actor):
            if "admin" not in actor["permissions"]:
                raise AccessDenied("native_access_admin_attestor_required")
            if actor.get("provider") not in ("github", "local_fixture"):
                raise AccessDenied("native_access_attestor_provider_required")
            policy = state.company.get("access_policy")
            if not policy:
                raise BrainError("access_policy_request_required")
            if (receipt["request_hash"] != policy["request_hash"] or receipt["request_reference"] != policy["request_reference"]
                    or receipt["requests"] != policy["requests"]):
                raise BrainError("access_verification_request_mismatch")
            repository = getattr(self.store, "repo", None)
            if repository is not None and receipt["repository"].casefold() != repository.casefold():
                raise BrainError("access_verification_repository_mismatch")
            if receipt["operator"]["actor_id"].casefold() != actor["id"].casefold():
                raise AccessDenied("access_verification_operator_mismatch")
            previous = policy.get("verification") or {}
            if previous.get("receipt", {}).get("receipt_hash") == receipt["receipt_hash"] and previous.get("attested_by") == actor["id"]:
                return copy.deepcopy(policy)
            source = self._source(state, actor, _identifier(event_id, "access-verification"), "access_verification",
                canonical(receipt), speaker=actor["id"])
            fixture = actor.get("provider") == "local_fixture"
            policy.update(state=receipt["state"], effective=copy.deepcopy(receipt["effective"]), blockers=copy.deepcopy(receipt["blockers"]),
                verification={"receipt": receipt, "attested_by": actor["id"], "provider": actor.get("provider"),
                    "fixture_only": fixture, "evidence_level": "E1" if fixture else "native_inspection", "source_id": source["id"]})
            state.company["policy_version"] += 1
            return copy.deepcopy(policy)
        return self._transaction("access_verification", event_id, {"receipt": receipt}, apply)

    def set_participation(self, choices, event_id):
        expected = {"participating", "screenshots", "acknowledged_policy_version", "local_exclusions_configured", "local_policy_sha256"}
        if not isinstance(choices, dict) or set(choices) != expected:
            raise BrainError("participant_choices_invalid")
        if (any(type(choices[key]) is not bool for key in ("participating", "screenshots", "local_exclusions_configured"))
                or type(choices["acknowledged_policy_version"]) is not int or choices["acknowledged_policy_version"] < 1
                or not isinstance(choices["local_policy_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", choices["local_policy_sha256"])):
            raise BrainError("participant_choices_invalid")
        choices = copy.deepcopy(choices)
        def apply(state, actor):
            if choices["acknowledged_policy_version"] != state.company["policy_version"]:
                raise BrainError("participant_policy_acknowledgement_stale")
            own = {"actor_id": actor["id"], **choices}
            if state.company.get("participation", {}).get(actor["id"]) == own:
                return copy.deepcopy(own)
            state.company.setdefault("participation", {})[actor["id"]] = own
            self._source(state, actor, _identifier(event_id, "participation"), "participation_choice", canonical(choices), speaker=actor["id"])
            return copy.deepcopy(own)
        return self._transaction("participation", event_id, choices, apply)
