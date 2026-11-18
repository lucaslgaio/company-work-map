"""P2 effective policy inspection, E1 injected GETs only."""
import copy
import unittest

from company_pilot.access import (AccessError, RepositoryAccess, Response,
    policy_request_hash, validate_verification_receipt)


def request(actor_id, read="all", modify="all", categories=None):
    return {"actor_id": actor_id, "read_scope": read, "modify_scope": modify, "record_categories": categories or []}


class API:
    def __init__(self, organization=False):
        self.organization = organization
        self.operator = "operator" if organization else "owner"
        self.profiles = {"owner": 100 if organization else 1, "operator": 1, "member": 2, "silent": 3}
        self.roles = {self.operator: "admin", "member": "write"}
        self.invites = []
        self.calls = []
        self.incomplete = False
        self.fail_roster = False

    def __call__(self, route, method="GET", body=None):
        self.calls.append((route, method, body))
        if method != "GET" or body is not None:
            raise AssertionError("Policy inspection attempted a mutation")
        if route == "user":
            return Response(200, {"login": self.operator, "id": 1})
        if route == "repos/owner/pilot":
            return Response(200, {"id": 9, "private": True, "owner": {"login": "owner", "id": self.profiles["owner"],
                "type": "Organization" if self.organization else "User"}, "permissions": {"admin": True}})
        if "/invitations?" in route:
            return Response(200, copy.deepcopy(self.invites))
        if "/collaborators?" in route:
            if self.fail_roster:
                return Response(503)
            if self.incomplete:
                return Response(200, [{"login": "silent", "id": 3}] * 100)
            return Response(200, [{"login": name, "id": self.profiles[name]} for name in self.roles])
        if route.startswith("orgs/"):
            return Response(200, [{"login": self.operator, "id": 1}])
        if route.startswith("users/"):
            name = route.split("/")[1]
            return Response(200, {"login": name, "id": self.profiles[name], "type": "User"})
        if route.endswith("/permission"):
            name = route.split("/")[-2]
            return Response(200, {"permission": self.roles[name], "role_name": self.roles[name]}) if name in self.roles else Response(404)
        raise AssertionError(route)


class PolicyVerificationTests(unittest.TestCase):
    def inspect(self, api, requests):
        reference = "verified-founder-local-request"
        return RepositoryAccess("owner/pilot", api).verify_policy(requests, request_reference=reference,
            request_hash=policy_request_hash(requests, reference))

    def test_personal_broad_access_is_exact_and_read_only(self):
        api = API()
        result = self.inspect(api, [request("owner"), request("member")])
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["owner"]["read_scope"], "all")
        self.assertEqual(result["operator"], {"actor_id": "owner", "id": 1})
        self.assertIs(validate_verification_receipt(result), result)
        self.assertTrue(all(method == "GET" and body is None for route, method, body in api.calls))

    def test_owner_is_visible_and_omission_cannot_verify(self):
        result = self.inspect(API(), [request("member")])
        self.assertEqual(result["state"], "pending")
        self.assertTrue(any(b["code"] == "owner_visibility_not_acknowledged" for b in result["blockers"]))
        self.assertTrue(any(item["actor_id"] == "owner" and item["modify_scope"] == "all" for item in result["effective"]))
        excluded = self.inspect(API(), [request("owner", "none", "none"), request("member")])
        self.assertEqual(excluded["state"], "unsupported")

    def test_unlisted_current_collaborator_blocks_even_exact_requested_rights(self):
        api = API()
        api.roles["silent"] = "write"
        result = self.inspect(api, [request("owner"), request("member")])
        self.assertEqual(result["state"], "pending")
        self.assertIn({"actor_id": "silent", "code": "unrequested_repository_access"}, result["blockers"])

    def test_absence_can_be_verified_but_pending_invitation_cannot(self):
        api = API()
        api.roles.pop("member")
        requests = [request("owner"), request("member", "none", "none")]
        self.assertEqual(self.inspect(api, requests)["state"], "verified")
        api.invites = [{"id": 7, "invitee": {"login": "member", "id": 2}, "permissions": "write"}]
        result = self.inspect(api, requests)
        self.assertEqual(result["state"], "pending")
        self.assertIn({"actor_id": "member", "code": "invitation_pending"}, result["blockers"])

    def test_unsupported_selected_own_and_personal_read_only_never_widen(self):
        for desired in (request("member", "selected", "none", ["financeiro"]),
                        request("member", "all", "own"), request("member", "all", "none")):
            result = self.inspect(API(), [request("owner"), desired])
            self.assertEqual(result["state"], "unsupported")
            member = next(item for item in result["effective"] if item["actor_id"] == "member")
            self.assertEqual((member["read_scope"], member["modify_scope"]), ("all", "all"))

    def test_organization_whole_repository_read_scope_is_native(self):
        api = API(organization=True)
        api.roles["member"] = "read"
        result = self.inspect(api, [request("operator"), request("member", "all", "none")])
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["owner"]["type"], "Organization")
        api.roles["member"] = "custom-finance"
        self.assertEqual(self.inspect(api, [request("operator"), request("member", "all", "none")])["state"], "pending")

    def test_incomplete_pagination_is_pending_and_failed_inspection_never_verifies(self):
        api = API()
        api.incomplete = True
        result = self.inspect(api, [request("owner"), request("member")])
        self.assertEqual(result["state"], "pending")
        self.assertIn({"actor_id": "owner", "code": "collaborators_incomplete"}, result["blockers"])
        self.assertEqual(len([path for path, _, _ in api.calls if "/collaborators?" in path]), 10)
        api = API()
        api.fail_roster = True
        with self.assertRaisesRegex(AccessError, "collaborators_unavailable"):
            self.inspect(api, [request("owner"), request("member")])

    def test_hash_changed_request_and_forged_receipt_fail(self):
        requests = [request("owner"), request("member")]
        before = policy_request_hash(requests, "reference")
        changed = [request("owner"), request("member", "none", "none")]
        api = API()
        with self.assertRaisesRegex(AccessError, "request_hash_mismatch"):
            RepositoryAccess("owner/pilot", api).verify_policy(changed, request_reference="reference", request_hash=before)
        self.assertEqual(api.calls, [])
        result = self.inspect(API(), requests)
        tampered = copy.deepcopy(result)
        tampered["effective"][0]["modify_scope"] = "none"
        with self.assertRaises(AccessError):
            validate_verification_receipt(tampered)


if __name__ == "__main__":
    unittest.main()
