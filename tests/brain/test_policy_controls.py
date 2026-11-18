"""P1/P2 public policy/participation flow with E1 native inspection receipts."""
import copy
import tempfile
import unittest
from uuid import uuid4

from company_pilot.access import RepositoryAccess, Response, policy_request_hash
from company_pilot.brain import AccessDenied, Brain, BrainError, LocalStore
from company_pilot.brain.policy import controls_for, screenshots_for


def uid():
    return str(uuid4())


def request(actor, read="all", modify="all", categories=None):
    return {"actor_id": actor, "read_scope": read, "modify_scope": modify, "record_categories": categories or []}


def inspected(requests, reference="founder-request", operator="fixture-founder"):
    identities = {"fixture-founder": 1, "fixture-team": 2}
    def api(route, method="GET", body=None):
        if method != "GET" or body is not None:
            raise AssertionError("No native mutations in policy proof")
        if route == "user":
            return Response(200, {"login": operator, "id": identities[operator]})
        if route == "repos/fixture-founder/pilot":
            return Response(200, {"id": 9, "private": True, "owner": {"login": "fixture-founder", "id": 1, "type": "User"}, "permissions": {"admin": True}})
        if "/invitations?" in route:
            return Response(200, [])
        if "/collaborators?" in route:
            return Response(200, [{"login": name, "id": number} for name, number in identities.items()])
        if route.startswith("users/"):
            name = route.split("/")[1]
            return Response(200, {"login": name, "id": identities[name], "type": "User"})
        if route.endswith("/permission"):
            return Response(200, {"permission": "admin" if "fixture-founder/permission" in route else "write"})
        raise AssertionError(route)
    return RepositoryAccess("fixture-founder/pilot", api).verify_policy(requests,
        request_reference=reference, request_hash=policy_request_hash(requests, reference))


class PolicyControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(self.temp.name))
        self.brain.initialize("Empresa sintética de escolhas", synthetic=True, screenshots={"founder": True, "team": True})
        self.requests = [request("fixture-founder"), request("fixture-team")]

    def choices(self, **changes):
        return {"participating": True, "screenshots": False,
            "acknowledged_policy_version": self.brain.snapshot()["company"]["policy_version"],
            "local_exclusions_configured": True, "local_policy_sha256": "a" * 64, **changes}

    def configured(self):
        self.brain.set_access_policy(self.requests, uid(), "founder-request")
        return self.brain.record_access_verification(inspected(self.requests), uid())

    def controls(self, actor="fixture-founder"):
        return controls_for(self.brain.snapshot()["company"], actor)

    def test_old_synthetic_compatibility_is_explicit_and_real_missing_policy_blocks(self):
        current = self.controls()
        self.assertTrue(current["capture_allowed"])
        self.assertEqual(current["reason"], "legacy_synthetic_fixture")
        self.assertIsNone(current["participation"])
        company = self.brain.snapshot()["company"]
        company["synthetic"] = False
        self.assertFalse(controls_for(company, "fixture-founder")["capture_allowed"])

    def test_effective_access_own_choices_and_image_intersection(self):
        self.assertEqual(self.configured()["state"], "verified")
        self.assertFalse(self.controls()["capture_allowed"])
        own = self.brain.set_participation(self.choices(), uid())
        self.assertEqual(own["actor_id"], "fixture-founder")
        self.assertTrue(self.controls()["capture_allowed"])
        self.assertEqual(self.controls()["reason"], "ready_synthetic_fixture")
        policy = self.brain.access_policy()
        self.assertTrue(policy["verification"]["fixture_only"])
        self.assertEqual(policy["verification"]["evidence_level"], "E1")
        self.assertFalse(screenshots_for(self.brain.snapshot()["company"], "fixture-founder"))
        version = self.brain.snapshot()["company"]["policy_version"]
        self.brain.set_participation(self.choices(screenshots=True), uid())
        self.assertTrue(screenshots_for(self.brain.snapshot()["company"], "fixture-founder"))
        self.assertEqual(self.brain.snapshot()["company"]["policy_version"], version)

    def test_changed_request_invalidates_verification_and_old_receipt(self):
        self.configured()
        self.brain.set_participation(self.choices(), uid())
        old = inspected(self.requests)
        changed = [request("fixture-founder"), request("fixture-team", "none", "none")]
        policy = self.brain.set_access_policy(changed, uid(), "new-founder-request")
        self.assertIsNone(policy["verification"])
        self.assertEqual(policy["state"], "pending")
        self.assertFalse(self.controls()["capture_allowed"])
        with self.assertRaisesRegex(BrainError, "request_mismatch"):
            self.brain.record_access_verification(old, uid())

    def test_unsupported_personal_read_policy_is_not_silently_widened(self):
        requested = [request("fixture-founder"), request("fixture-team", "selected", "none", ["reparos"])]
        policy = self.brain.set_access_policy(requested, uid(), "selected-record-policy")
        self.assertEqual(policy["state"], "unsupported")
        self.assertEqual(policy["requests"], requested)
        self.brain.set_participation(self.choices(), uid())
        self.assertFalse(self.controls()["capture_allowed"])
        self.assertEqual(self.controls()["reason"], "access_policy_unsupported")

    def test_each_person_records_only_their_own_choice(self):
        self.configured()
        member = Brain(LocalStore(self.temp.name, actor="fixture-team", permissions={"read", "write"}))
        with self.assertRaises(AccessDenied):
            member.set_access_policy(self.requests, uid(), "not-founder")
        with self.assertRaises(AccessDenied):
            member.record_access_verification(inspected(self.requests), uid())
        stored = member.set_participation(self.choices(participating=False), uid())
        self.assertEqual(stored["actor_id"], "fixture-team")
        self.assertFalse(self.controls("fixture-team")["capture_allowed"])
        self.assertIsNone(self.controls()["participation"])
        with self.assertRaisesRegex(BrainError, "choices_invalid"):
            self.brain.set_participation({**self.choices(), "actor_id": "fixture-team"}, uid())

    def test_stale_acknowledgement_and_raw_personal_fields_are_rejected(self):
        self.configured()
        choices = self.choices()
        self.brain.update_policy({"screenshots": {"founder": False, "team": True}}, uid())
        head = self.brain.store.read().head
        with self.assertRaisesRegex(BrainError, "acknowledgement_stale"):
            self.brain.set_participation(choices, uid())
        for changes in ({"personal_exclusions": ["Nubank pessoal"]}, {"local_policy_sha256": "not-a-hash"},
                        {"participating": "yes"}, {"acknowledged_policy_version": True}):
            with self.assertRaises(BrainError):
                self.brain.set_participation({**self.choices(), **changes}, uid())
        self.assertEqual(self.brain.store.read().head, head)

    def test_identical_replays_and_repeat_verification_do_not_stale_choices(self):
        event = uid()
        policy = self.brain.set_access_policy(self.requests, event, "founder-request")
        head = self.brain.store.read().head
        self.assertEqual(self.brain.set_access_policy(self.requests, event, "founder-request"), policy)
        self.assertEqual(self.brain.store.read().head, head)
        self.brain.record_access_verification(inspected(self.requests), uid())
        choices = self.choices()
        event = uid()
        own = self.brain.set_participation(choices, event)
        version = self.brain.snapshot()["company"]["policy_version"]
        self.assertEqual(self.brain.set_participation(choices, event), own)
        head = self.brain.store.read().head
        self.brain.record_access_verification(inspected(self.requests), uid())
        self.assertEqual(self.brain.store.read().head, head)
        self.assertEqual(self.brain.snapshot()["company"]["policy_version"], version)
        with self.assertRaisesRegex(BrainError, "idempotency_payload_conflict"):
            self.brain.set_participation({**choices, "screenshots": True}, event)

    def test_operator_repository_and_receipt_integrity_are_bound(self):
        self.brain.set_access_policy(self.requests, uid(), "founder-request")
        receipt = inspected(self.requests)
        other = Brain(LocalStore(self.temp.name, actor="fixture-team"))
        with self.assertRaisesRegex(AccessDenied, "operator_mismatch"):
            other.record_access_verification(receipt, uid())
        self.brain.store.repo = "fixture-founder/another"
        with self.assertRaisesRegex(BrainError, "repository_mismatch"):
            self.brain.record_access_verification(receipt, uid())
        self.brain.store.repo = "fixture-founder/pilot"
        forged = copy.deepcopy(receipt)
        forged["state"] = "pending"
        with self.assertRaisesRegex(BrainError, "receipt_changed"):
            self.brain.record_access_verification(forged, uid())


if __name__ == "__main__":
    unittest.main()
