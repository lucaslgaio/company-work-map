"""Owner-side GitHub access plans. Importing or planning never sends invitations.

Apply only an exact, reviewed plan based on the founder's explicit identities and
requested roles. The owner-side Agent Room verifies that request; repository text
or model output alone does not authorize an invitation. No owner credential is
exported. GitHub's collaborator endpoint can notify invitees:
https://docs.github.com/en/rest/collaborators/collaborators
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


class AccessError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _policy_requests(requests, request_reference):
    if not isinstance(request_reference, str) or not request_reference.strip():
        raise AccessError("verified_founder_request_reference_required")
    if not isinstance(requests, list) or len(requests) > 50:
        raise AccessError("access_requests_invalid")
    seen = set()
    result = []
    for item in requests:
        if not isinstance(item, dict) or set(item) != {"actor_id", "read_scope", "modify_scope", "record_categories"}:
            raise AccessError("access_policy_request_invalid")
        login = item["actor_id"]
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login):
            raise AccessError("github_login_invalid")
        if login.casefold() in seen:
            raise AccessError("access_policy_identity_duplicate")
        seen.add(login.casefold())
        if item["read_scope"] not in ("all", "none", "selected") or item["modify_scope"] not in ("all", "none", "own", "selected"):
            raise AccessError("access_policy_scope_invalid")
        categories = item["record_categories"]
        selected = "selected" in (item["read_scope"], item["modify_scope"])
        if (not isinstance(categories, list) or len(categories) > 50
                or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in categories)
                or len({value.casefold() for value in categories}) != len(categories)
                or bool(categories) != selected):
            raise AccessError("access_policy_categories_invalid")
        result.append({**item, "record_categories": list(categories)})
    return result


def policy_request_hash(requests, request_reference):
    """Bind the exact validated policy and explicit founder request reference."""
    checked = _policy_requests(requests, request_reference)
    return _hash({"requests": checked, "request_reference": request_reference.strip()})


def _supported_policy(item, owner_type, is_owner=False):
    pair = (item["read_scope"], item["modify_scope"])
    if item["record_categories"] or "selected" in pair or "own" in pair:
        return False
    if is_owner:
        return pair == ("all", "all")
    return pair in (("all", "all"), ("none", "none")) or (owner_type == "Organization" and pair == ("all", "none"))


def validate_verification_receipt(receipt):
    """Validate a native-inspection receipt; its hash is not an identity proof.

    The caller must separately authenticate the owner/admin attestor, compare the
    current request/repository and reject caller-supplied 'verified' assertions.
    """
    keys = {"schema_version", "kind", "repository", "repository_id", "request_hash", "request_reference",
            "owner", "operator", "requests", "effective", "state", "blockers", "attestation", "receipt_hash"}
    if not isinstance(receipt, dict) or set(receipt) != keys or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1 or receipt["kind"] != "repository_access_verification":
        raise AccessError("access_verification_receipt_invalid")
    if (not isinstance(receipt["repository"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", receipt["repository"])
            or type(receipt["repository_id"]) is not int or receipt["repository_id"] <= 0
            or receipt["request_hash"] != policy_request_hash(receipt["requests"], receipt["request_reference"])):
        raise AccessError("access_verification_request_mismatch")
    for key, expected in (("owner", {"actor_id", "id", "type", "read_scope", "modify_scope"}),
                          ("operator", {"actor_id", "id"})):
        identity = receipt[key]
        if not isinstance(identity, dict) or set(identity) != expected or not isinstance(identity["actor_id"], str) or not identity["actor_id"] or type(identity["id"]) is not int or identity["id"] <= 0:
            raise AccessError("access_verification_identity_invalid")
    if receipt["owner"]["type"] not in ("User", "Organization") or receipt["owner"]["read_scope"] != "all" or receipt["owner"]["modify_scope"] != "all":
        raise AccessError("access_verification_owner_invalid")
    if receipt["attestation"] != {"provider": "github", "method": "read_only_native_inspection"}:
        raise AccessError("access_verification_attestation_invalid")
    if receipt["state"] not in ("verified", "unsupported", "pending") or not isinstance(receipt["blockers"], list) or not isinstance(receipt["effective"], list):
        raise AccessError("access_verification_state_invalid")
    seen = set()
    for effective in receipt["effective"]:
        if (not isinstance(effective, dict) or set(effective) != {"actor_id", "user_id", "role", "read_scope", "modify_scope", "record_categories", "state", "blockers"}
                or not isinstance(effective["actor_id"], str) or not effective["actor_id"]
                or effective["actor_id"].casefold() in seen or type(effective["user_id"]) is not int or effective["user_id"] <= 0
                or not isinstance(effective["role"], str) or effective["read_scope"] not in ("all", "none", "unknown")
                or effective["modify_scope"] not in ("all", "none", "unknown") or effective["record_categories"] != []
                or effective["state"] not in ("verified", "unsupported", "pending") or not isinstance(effective["blockers"], list)
                or any(not isinstance(code, str) or not code for code in effective["blockers"])):
            raise AccessError("access_verification_effective_invalid")
        seen.add(effective["actor_id"].casefold())
    for blocker in receipt["blockers"]:
        if not isinstance(blocker, dict) or set(blocker) != {"actor_id", "code"} or any(not isinstance(value, str) or not value for value in blocker.values()):
            raise AccessError("access_verification_blocker_invalid")
    if receipt["state"] == "verified" and (receipt["blockers"] or any(item["state"] != "verified" for item in receipt["effective"])):
        raise AccessError("access_verification_false_success")
    if receipt["state"] == "verified":
        requested = {item["actor_id"].casefold(): item for item in receipt["requests"]}
        actual = {item["actor_id"].casefold(): item for item in receipt["effective"]}
        required = {receipt["operator"]["actor_id"].casefold()}
        if receipt["owner"]["type"] == "User":
            required.add(receipt["owner"]["actor_id"].casefold())
        if not (set(requested) | required) <= set(actual):
            raise AccessError("access_verification_false_success")
        for key, effective in actual.items():
            desired = requested.get(key)
            if desired is None:
                if effective["role"] != "none":
                    raise AccessError("access_verification_false_success")
            elif (not _supported_policy(desired, receipt["owner"]["type"], key == receipt["owner"]["actor_id"].casefold())
                  or effective["read_scope"] != desired["read_scope"] or effective["modify_scope"] != desired["modify_scope"]
                  or effective["role"] not in ("none", "read", "write", "admin")):
                raise AccessError("access_verification_false_success")
    if receipt["receipt_hash"] != _hash({key: value for key, value in receipt.items() if key != "receipt_hash"}):
        raise AccessError("access_verification_receipt_changed")
    return receipt


@dataclass(frozen=True)
class Response:
    status: int
    data: object = None


class GitHubAPI:
    def __init__(self, executable="gh"):
        self.executable = executable

    def __call__(self, route, method="GET", body=None):
        command = [self.executable, "api", "--hostname", "github.com", "--include",
                   "--method", method, "-H", "X-GitHub-Api-Version: 2026-03-10", route]
        if body is not None:
            command += ["--input", "-"]
        try:
            result = subprocess.run(command, input=_canonical(body) if body is not None else None,
                                    capture_output=True, text=True, timeout=45,
                                    env=dict(os.environ, GH_PROMPT_DISABLED="1"))
        except (OSError, subprocess.TimeoutExpired) as error:
            # A timed-out mutation may have reached GitHub. Never retry blindly.
            raise AccessError("github_result_unknown" if method != "GET" else "github_unavailable") from error
        header, _, content = result.stdout.replace("\r\n", "\n").partition("\n\n")
        first = header.splitlines()[0] if header else ""
        match = re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", first)
        if not match:
            raise AccessError("github_result_unknown" if method != "GET" else "github_response_invalid")
        status = int(match[1])
        if result.returncode and 200 <= status < 300:
            raise AccessError("github_result_unknown")
        try:
            data = json.loads(content) if content.strip() else None
        except json.JSONDecodeError as error:
            raise AccessError("github_result_unknown" if method != "GET" else "github_response_invalid") from error
        return Response(status, data)


class RepositoryAccess:
    def __init__(self, repository, api=None):
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise AccessError("repository_invalid")
        self.repository = repository
        self.api = api or GitHubAPI()

    def _get(self, route, *, absent=False):
        result = self.api(route)
        if result.status == 404 and absent:
            return None
        if result.status != 200 or not isinstance(result.data, dict):
            raise AccessError("github_read_denied")
        return result.data

    @staticmethod
    def _role(permission, owner_type):
        if permission is None:
            return "none"
        # Organization base permission collapses triage/custom roles to read
        # and maintain/custom roles to write. Only the effective native role
        # can establish an exact requested role.
        return permission.get("role_name", "unknown") if owner_type == "Organization" else permission.get("permission", "unknown")

    def _list(self, route, code):
        records = []
        for page in range(1, 11):
            response = self.api(route + ("&" if "?" in route else "?") + f"per_page=100&page={page}")
            if response.status != 200 or not isinstance(response.data, list) or any(not isinstance(item, dict) for item in response.data):
                raise AccessError(code + "_unavailable")
            records.extend(response.data)
            if len(response.data) < 100:
                return records, True
        return records, False

    def verify_policy(self, requests, *, request_reference, request_hash):
        """Inspect native access; never create/update an invitation or permission.

        A caller must generate this through the owner-side command, not accept a
        founder-supplied 'verified' object. The receipt binds the exact request;
        its hash alone is not evidence that any HTTP inspection took place.
        """
        requests = _policy_requests(requests, request_reference)
        if request_hash != policy_request_hash(requests, request_reference):
            raise AccessError("access_policy_request_hash_mismatch")
        operator = self._get("user")
        repo = self._get("repos/" + self.repository)
        owner = repo.get("owner") or {}
        if not repo.get("private") or repo.get("permissions", {}).get("admin") is not True:
            raise AccessError("private_repository_admin_required")
        if (owner.get("type") not in ("User", "Organization")
                or not isinstance(owner.get("login"), str) or not owner["login"]
                or not isinstance(operator.get("login"), str) or not operator["login"]
                or any(type(value) is not int or value <= 0 for value in (repo.get("id"), owner.get("id"), operator.get("id")))
                or self.repository.split("/", 1)[0].casefold() != owner["login"].casefold()):
            raise AccessError("repository_identity_invalid")
        if owner["type"] == "User" and (operator["login"].casefold() != owner["login"].casefold() or operator["id"] != owner["id"]):
            raise AccessError("personal_repository_owner_required")
        invitations, invitations_complete = self._list(f"repos/{self.repository}/invitations", "pending_invitations")
        collaborators, collaborators_complete = self._list(f"repos/{self.repository}/collaborators?affiliation=all", "collaborators")
        native_owners, owners_complete = ([{"login": owner["login"], "id": owner["id"]}], True) if owner["type"] == "User" else self._list(f"orgs/{owner['login']}/members?role=admin", "organization_owners")
        if owner["type"] == "Organization" and not native_owners:
            raise AccessError("organization_owners_unavailable")
        profiles = {}
        listed = set()
        owner_ids = set()
        for item in collaborators + native_owners:
            login = item.get("login")
            if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login) or type(item.get("id")) is not int or item["id"] <= 0:
                raise AccessError("collaborator_identity_invalid")
            key = login.casefold()
            if key in profiles and profiles[key]["id"] != item["id"]:
                raise AccessError("collaborator_identity_changed")
            profiles[key] = {"login": login, "id": item["id"]}
        listed.update(item["login"].casefold() for item in collaborators)
        owner_ids.update(item["login"].casefold() for item in native_owners)
        operator_key = operator["login"].casefold()
        if operator_key in profiles and profiles[operator_key]["id"] != operator["id"]:
            raise AccessError("collaborator_identity_changed")
        profiles[operator_key] = {"login": operator["login"], "id": operator["id"]}
        by_request = {item["actor_id"].casefold(): item for item in requests}
        for item in requests:
            login = item["actor_id"]
            profile = self._get("users/" + login)
            if (profile.get("type") != "User" or type(profile.get("id")) is not int or profile["id"] <= 0
                    or not isinstance(profile.get("login"), str) or profile["login"].casefold() != login.casefold()):
                raise AccessError("invitee_identity_invalid")
            key = login.casefold()
            if key in profiles and profiles[key]["id"] != profile["id"]:
                raise AccessError("collaborator_identity_changed")
            profiles[key] = {"login": profile["login"], "id": profile["id"]}
        pending = {}
        for invitation in invitations:
            invitee = invitation.get("invitee") or {}
            login = invitee.get("login")
            if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login) or type(invitee.get("id")) is not int or invitee["id"] <= 0:
                raise AccessError("pending_invitation_identity_invalid")
            key = login.casefold()
            if key in pending:
                raise AccessError("ambiguous_pending_invitation")
            pending[key] = invitation
            if key in profiles and profiles[key]["id"] != invitee["id"]:
                raise AccessError("collaborator_identity_changed")
            profiles.setdefault(key, {"login": login, "id": invitee["id"]})
        effective, blockers = [], []
        for complete, code in ((invitations_complete, "pending_invitations_incomplete"),
                               (collaborators_complete, "collaborators_incomplete"),
                               (owners_complete, "organization_owners_incomplete")):
            if not complete:
                blockers.append({"actor_id": owner["login"], "code": code})
        unsupported = False
        for key, profile in sorted(profiles.items()):
            desired = by_request.get(key)
            is_owner = key in owner_ids
            permission = self._get(f"repos/{self.repository}/collaborators/{profile['login']}/permission", absent=True)
            role = "admin" if is_owner or key == operator_key else self._role(permission, owner["type"])
            rights = {"none": ("none", "none"), "read": ("all", "none"), "write": ("all", "all"), "admin": ("all", "all")}
            read, modify = rights.get(role, ("unknown", "unknown"))
            codes = []
            supported = desired is not None and _supported_policy(desired, owner["type"], is_owner)
            if desired is None:
                if is_owner:
                    codes.append("owner_visibility_not_acknowledged")
                elif role != "none":
                    codes.append("unrequested_repository_access")
            elif not supported:
                codes.append("unsupported_native_scope")
                unsupported = True
            if role not in rights:
                codes.append("native_role_not_verified")
            if key in pending:
                codes.append("invitation_pending")
            if key in listed and role == "none":
                codes.append("permission_listing_disagrees")
            if desired and supported and (read != desired["read_scope"] or modify != desired["modify_scope"]):
                codes.append("effective_access_differs")
            state = "unsupported" if "unsupported_native_scope" in codes else "pending" if codes else "verified"
            effective.append({"actor_id": profile["login"], "user_id": profile["id"], "role": role,
                              "read_scope": read, "modify_scope": modify, "record_categories": [], "state": state, "blockers": codes})
            blockers.extend({"actor_id": profile["login"], "code": code} for code in codes)
        result = {"schema_version": 1, "kind": "repository_access_verification", "repository": self.repository,
                  "repository_id": repo["id"], "request_hash": request_hash, "request_reference": request_reference.strip(),
                  "owner": {"actor_id": owner["login"], "id": owner["id"], "type": owner["type"], "read_scope": "all", "modify_scope": "all"},
                  "operator": {"actor_id": operator["login"], "id": operator["id"]}, "requests": requests,
                  "effective": effective, "state": "unsupported" if unsupported else "pending" if blockers else "verified",
                  "blockers": blockers, "attestation": {"provider": "github", "method": "read_only_native_inspection"}}
        result["receipt_hash"] = _hash(result)
        return validate_verification_receipt(result)

    def plan(self, requests, *, request_reference):
        if not isinstance(request_reference, str) or not request_reference.strip():
            raise AccessError("verified_founder_request_reference_required")
        if not isinstance(requests, list) or len(requests) > 50:
            raise AccessError("access_requests_invalid")
        seen = set()
        for item in requests:
            if not isinstance(item, dict) or set(item) != {"login", "role"}:
                raise AccessError("access_request_invalid")
            if not isinstance(item["login"], str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", item["login"]):
                raise AccessError("github_login_invalid")
            if item["role"] not in ("read", "write", "admin") or item["login"].casefold() in seen:
                raise AccessError("access_request_duplicate_or_role_invalid")
            seen.add(item["login"].casefold())
        user = self._get("user")
        repo = self._get("repos/" + self.repository)
        owner = repo.get("owner", {})
        if not repo.get("private") or not repo.get("permissions", {}).get("admin"):
            raise AccessError("private_repository_admin_required")
        owner_type = owner.get("type")
        if (owner_type not in ("User", "Organization") or not user.get("login")
                or any(type(value) is not int or value <= 0 for value in (repo.get("id"), owner.get("id"), user.get("id")))):
            raise AccessError("repository_identity_invalid")
        if owner_type == "User" and user["login"].casefold() != str(owner.get("login", "")).casefold():
            raise AccessError("personal_repository_owner_required")
        invitations = []
        if requests:
            for page in range(1, 11):
                response = self.api(f"repos/{self.repository}/invitations?per_page=100&page={page}")
                if response.status != 200 or not isinstance(response.data, list):
                    raise AccessError("pending_invitations_unavailable")
                invitations.extend(response.data)
                if len(response.data) < 100:
                    break
            else:
                raise AccessError("pending_invitations_incomplete")
        items = []
        for item in requests:
            login, desired = item["login"], item["role"]
            profile = self._get("users/" + login)
            if profile.get("type") != "User" or not profile.get("id") or profile.get("login", "").casefold() != login.casefold():
                raise AccessError("invitee_identity_invalid")
            permission = self._get(f"repos/{self.repository}/collaborators/{login}/permission", absent=True)
            actual = self._role(permission, owner_type)
            is_owner = owner_type == "User" and login.casefold() == owner["login"].casefold()
            supported = (is_owner and desired == "admin") or (not is_owner and (owner_type == "Organization" or desired == "write"))
            pending = [entry for entry in invitations if str((entry.get("invitee") or {}).get("login", "")).casefold() == login.casefold()]
            if len(pending) > 1:
                raise AccessError("ambiguous_pending_invitation")
            action = "unsupported" if not supported else "unchanged" if actual == desired or is_owner else "invite_or_update"
            if pending and supported:
                action = "invitation_pending" if pending[0].get("permissions") == desired else "pending_invitation_needs_review"
            items.append({"login": profile["login"], "user_id": profile["id"], "desired": desired,
                          "actual": "admin" if is_owner else actual, "supported": supported, "action": action,
                          "base_permission": (permission or {}).get("permission", "none"),
                          "invitation_id": pending[0].get("id") if pending else None})
        plan = {"schema_version": 1, "repository": self.repository, "repository_id": repo["id"],
                "owner": owner["login"], "owner_id": owner["id"], "operator_id": user["id"],
                "owner_type": owner_type, "operator": user["login"],
                "request_reference": request_reference.strip(), "requests": items}
        return {**plan, "plan_hash": _hash(plan), "applied": False}

    def apply(self, plan, *, approved_plan_hash):
        if not isinstance(plan, dict) or not approved_plan_hash or approved_plan_hash != plan.get("plan_hash"):
            raise AccessError("exact_reviewed_plan_required")
        fields = {key: value for key, value in plan.items() if key not in ("plan_hash", "applied")}
        if _hash(fields) != approved_plan_hash or plan.get("repository") != self.repository or plan.get("applied") is not False:
            raise AccessError("access_plan_changed")
        requests = [{"login": item["login"], "role": item["desired"]} for item in plan["requests"]]
        fresh = self.plan(requests, request_reference=plan["request_reference"])
        if fresh["plan_hash"] != approved_plan_hash:
            raise AccessError("access_plan_stale_reinspect")
        if any(not item["supported"] for item in fresh["requests"]):
            raise AccessError("unsupported_native_role_no_access_changed")
        results = []
        for item in fresh["requests"]:
            if item["action"] in ("invitation_pending", "pending_invitation_needs_review"):
                results.append({"login": item["login"], "status": item["action"], "actual": "not_confirmed"})
                continue
            if item["action"] == "unchanged":
                results.append({"login": item["login"], "status": "unchanged", "actual": item["actual"]})
                continue
            # Role parameter applies only to organization-owned repositories.
            body = ({"permission": {"read": "pull", "write": "push", "admin": "admin"}[item["desired"]]}
                    if fresh["owner_type"] == "Organization" else {})
            try:
                response = self.api(f"repos/{self.repository}/collaborators/{item['login']}", "PUT", body)
            except AccessError:
                results.append({"login": item["login"], "status": "unknown_reinspect_before_retry"})
                break
            if response.status == 201:
                results.append({"login": item["login"], "status": "invitation_pending", "actual": "not_confirmed"})
            elif response.status == 204:
                try:
                    actual = self._get(f"repos/{self.repository}/collaborators/{item['login']}/permission", absent=True)
                except AccessError:
                    actual = None
                effective = self._role(actual, fresh["owner_type"])
                results.append({"login": item["login"], "status": "verified" if effective == item["desired"] else "verification_pending",
                                "actual": effective})
            else:
                status = "refused" if response.status in (401, 403, 404, 422) else "unknown_reinspect_before_retry"
                results.append({"login": item["login"], "status": status, "http_status": response.status})
                break
        return {"plan_hash": approved_plan_hash, "repository": self.repository,
                "results": results, "credentials_distributed": False,
                "complete": len(results) == len(fresh["requests"]) and all(r["status"] in ("unchanged", "verified") for r in results)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Planejar ou aplicar os acessos pedidos pelo fundador usando a conta do operador.")
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--file", required=True, help="JSON de pedidos (plan) ou plano revisado (apply)")
    parser.add_argument("--request-reference", help="Referência ao pedido explícito e verificado do fundador")
    parser.add_argument("--approved-plan-hash", help="Hash exato do plano revisado pelo operador")
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        access = RepositoryAccess(args.repo)
        result = (access.plan(data, request_reference=args.request_reference)
                  if args.action == "plan" else access.apply(data, approved_plan_hash=args.approved_plan_hash))
    except (AccessError, OSError, ValueError, KeyError, TypeError) as error:
        code = str(error) if isinstance(error, AccessError) else "access_input_invalid"
        print(json.dumps({"status": "not_confirmed", "error_code": code}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.action == "plan" or result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
