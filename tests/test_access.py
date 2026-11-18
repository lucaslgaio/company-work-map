import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from company_pilot.access import AccessError, GitHubAPI, RepositoryAccess, Response


class API:
    def __init__(self, owner_type="User"):
        self.owner_type = owner_type
        self.operator = "owner"
        self.private = True
        self.actual = {}
        self.invites = []
        self.writes = []
        self.put_status = 201
        self.fail_put = False
        self.verify_failure = False
        self.effective_roles = {}
        self.repository_id = 10

    def __call__(self, path, method="GET", body=None):
        if method == "PUT":
            self.writes.append((path, copy.deepcopy(body)))
            if self.fail_put:
                raise AccessError("github_result_unknown")
            if self.put_status == 204:
                self.actual[path.rsplit("/", 1)[-1]] = {"pull": "read", "push": "write", "admin": "admin"}.get((body or {}).get("permission"), "write")
            return Response(self.put_status, {"id": 7} if self.put_status == 201 else None)
        if path == "user":
            return Response(200, {"login": self.operator, "id": 1})
        if path == "repos/owner/pilot":
            return Response(200, {"id": self.repository_id, "private": self.private, "owner": {"login": "owner", "id": 1, "type": self.owner_type}, "permissions": {"admin": True}})
        if "/invitations?" in path:
            return Response(200, copy.deepcopy(self.invites))
        if path.startswith("users/"):
            login = path.split("/", 1)[1]
            return Response(200, {"login": login, "id": 1 if login == "owner" else 2, "type": "User"})
        login = path.split("/")[-2]
        if self.verify_failure and self.writes:
            return Response(503)
        return Response(200, {"permission": self.actual[login], "role_name": self.effective_roles.get(login, self.actual[login])}) if login in self.actual else Response(404)


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.access = RepositoryAccess("owner/pilot", self.api)

    def plan(self, role="write", login="founder"):
        return self.access.plan([{"login": login, "role": role}], request_reference="verified-founder-request-42")

    def apply(self, plan):
        return self.access.apply(plan, approved_plan_hash=plan["plan_hash"])

    def test_personal_read_and_admin_are_unsupported_and_fail_before_any_change(self):
        for role in ("read", "admin"):
            plan = self.plan(role)
            self.assertFalse(plan["requests"][0]["supported"])
            with self.assertRaisesRegex(AccessError, "unsupported_native_role"):
                self.apply(plan)
        self.assertEqual(self.api.writes, [])

    def test_invitation_is_pending_not_granted_and_no_role_parameter_for_personal(self):
        result = self.apply(self.plan())
        self.assertEqual(result["results"][0]["status"], "invitation_pending")
        self.assertFalse(result["complete"])
        self.assertFalse(result["credentials_distributed"])
        self.assertEqual(self.api.writes[0][1], {})

    def test_organization_native_role_maps_and_is_verified(self):
        self.api.owner_type = "Organization"
        self.api.put_status = 204
        result = self.apply(self.plan("read"))
        self.assertTrue(result["complete"])
        self.assertEqual(result["results"][0]["actual"], "read")
        self.assertEqual(self.api.writes[0][1], {"permission": "pull"})

    def test_hash_tampering_and_stale_permissions_refuse_before_mutation(self):
        plan = self.plan()
        tampered = copy.deepcopy(plan)
        tampered["requests"][0]["login"] = "another"
        with self.assertRaisesRegex(AccessError, "access_plan_changed"):
            self.apply(tampered)
        self.api.actual["founder"] = "write"
        with self.assertRaisesRegex(AccessError, "access_plan_stale"):
            self.apply(plan)
        self.assertEqual(self.api.writes, [])

    def test_organization_elevated_and_custom_roles_are_never_reported_as_exact_base_access(self):
        self.api.owner_type = "Organization"
        self.api.put_status = 204
        for base, effective in (("read", "triage"), ("write", "maintain"), ("write", "custom-finance")):
            self.api.actual["founder"] = base
            self.api.effective_roles["founder"] = effective
            plan = self.plan(base)
            self.assertEqual(plan["requests"][0]["actual"], effective)
            self.assertEqual(plan["requests"][0]["base_permission"], base)
            self.assertEqual(plan["requests"][0]["action"], "invite_or_update")
            result = self.apply(plan)
            self.assertFalse(result["complete"])
            self.assertEqual(result["results"][0]["status"], "verification_pending")
            self.assertEqual(result["results"][0]["actual"], effective)

    def test_recreated_repository_under_same_name_is_not_the_reviewed_target(self):
        plan = self.plan()
        self.assertEqual(plan["repository_id"], 10)
        self.api.repository_id = 999
        with self.assertRaisesRegex(AccessError, "access_plan_stale"):
            self.apply(plan)
        self.assertEqual(self.api.writes, [])

    def test_owner_account_and_private_repository_required(self):
        self.api.operator = "someone"
        with self.assertRaisesRegex(AccessError, "owner_required"):
            self.plan()
        self.api.operator = "owner"
        self.api.private = False
        with self.assertRaisesRegex(AccessError, "private_repository_admin"):
            self.plan()
        self.assertEqual(self.api.writes, [])

    def test_existing_owner_and_collaborator_are_noop(self):
        self.assertTrue(self.apply(self.plan("admin", "owner"))["complete"])
        self.api.actual["founder"] = "write"
        self.assertTrue(self.apply(self.plan())["complete"])
        self.assertEqual(self.api.writes, [])

    def test_existing_pending_invitation_is_not_resent_or_called_granted(self):
        self.api.invites = [{"id": 123, "invitee": {"login": "founder"}, "permissions": "write"}]
        result = self.apply(self.plan())
        self.assertEqual(result["results"][0]["status"], "invitation_pending")
        self.assertFalse(result["complete"])
        self.assertEqual(self.api.writes, [])

    def test_unknown_delivery_stops_remaining_changes_and_verification_failure_is_visible(self):
        plan = self.access.plan([{"login": "first", "role": "write"}, {"login": "second", "role": "write"}], request_reference="verified-reference")
        self.api.fail_put = True
        result = self.apply(plan)
        self.assertFalse(result["complete"])
        self.assertEqual(result["results"][0]["status"], "unknown_reinspect_before_retry")
        self.assertEqual(len(self.api.writes), 1)
        self.api.fail_put = False
        self.api.put_status = 204
        self.api.verify_failure = True
        self.api.writes = []
        result = self.apply(self.plan())
        self.assertEqual(result["results"][0]["status"], "verification_pending")
        self.assertFalse(result["complete"])

    def test_invalid_identity_and_missing_reference_fail_without_requests(self):
        for requests in ([{"login": "-bad", "role": "write"}], [{"login": "one", "role": "write"}, {"login": "ONE", "role": "read"}]):
            with self.assertRaises(AccessError):
                self.access.plan(requests, request_reference="verified-reference")
        with self.assertRaises(AccessError):
            self.access.plan([], request_reference=None)
        self.assertEqual(self.api.writes, [])

    def test_server_failure_is_unknown_and_stops_remainder_while_documented_denial_is_refused(self):
        for status, expected in ((502, "unknown_reinspect_before_retry"), (403, "refused"), (422, "refused")):
            self.api.writes = []
            self.api.put_status = status
            plan = self.access.plan([{"login": "first", "role": "write"}, {"login": "second", "role": "write"}], request_reference="verified-reference")
            result = self.apply(plan)
            self.assertEqual(result["results"][0]["status"], expected)
            self.assertEqual(len(self.api.writes), 1)
            self.assertFalse(result["complete"])

    def test_native_gh_parser_uses_stdin_no_shell_and_never_exposes_error_body(self):
        result = SimpleNamespace(returncode=0, stdout="HTTP/2.0 201 Created\r\nContent-Type: application/json\r\n\r\n{\"id\": 9}\n")
        with patch("company_pilot.access.subprocess.run", return_value=result) as run:
            self.assertEqual(GitHubAPI()("repos/owner/pilot/collaborators/founder", "PUT", {}), Response(201, {"id": 9}))
            self.assertEqual(run.call_args.kwargs["input"], "{}")
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertIn("github.com", run.call_args.args[0])
        result.stdout = "TOKEN PRIVATE RESPONSE"
        with patch("company_pilot.access.subprocess.run", return_value=result):
            with self.assertRaisesRegex(AccessError, "^github_result_unknown$"):
                GitHubAPI()("repos/owner/pilot/collaborators/founder", "PUT", {})


if __name__ == "__main__":
    unittest.main()
