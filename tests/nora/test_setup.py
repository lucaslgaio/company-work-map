"""Owner binding tests use only an in-memory company/ledger and private temp files."""
from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

from company_pilot.nora.setup import bind_route, main, read_private_json, reconcile_binding, SetupError


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class CompanyFixture:
    def __init__(self):
        self.company = {"id": str(uuid4()), "synthetic": True, "founders": ["owner", "founder"], "nora_actors": ["owner"]}
        self.actor = {"id": "owner", "permissions": ["read", "write", "admin"], "provider": "local_fixture"}
        self.nora, self.ledger, self.calls = {}, {}, 0
        self.fail_before = self.fail_after = False
        self.store = SimpleNamespace(authenticated_actor=lambda: deepcopy(self.actor),
                                     read=lambda: SimpleNamespace(files=deepcopy(self.ledger)))

    def snapshot(self):
        return {"company": deepcopy(self.company), "nora": deepcopy(self.nora)}

    def transact_nora(self, operation, event_id=None):
        self.calls += 1
        if self.fail_before:
            raise TimeoutError("PRIVATE TRANSPORT DATA")
        next_state = deepcopy(self.nora)
        result = operation(next_state)
        self.nora = next_state
        self.ledger[f"ledger/00000001-{event_id}.json"] = json.dumps({"id": event_id, "actor": self.actor["id"], "result": result})
        if self.fail_after:
            raise TimeoutError("PRIVATE TRANSPORT DATA")
        return result


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.json"
        self.config = {"schema_version": 1, "mode": "synthetic", "store": {"kind": "local", "root": str(self.root / "brain")},
                       "nora": {"enabled": False, "route_verified": False, "openclaw_executable": "/not-executed/openclaw"}}
        write_private(self.config_path, self.config)
        self.brain = CompanyFixture()
        self.route = {"company_id": self.brain.company["id"], "id": str(uuid4()), "revision": 1,
                      "account": "synthetic-account", "conversation": "100000000000@g.us",
                      "founder_sender": "sensitive-synthetic-native-sender", "founder_actor_id": "founder"}
        self.event = str(uuid4())

    def bind(self, **kwargs):
        return bind_route(self.brain, self.config_path, read_private_json(self.config_path),
                          self.route, "verified-request:fixture", self.event, writer=write_private, **kwargs)

    def test_default_records_exact_inactive_route_and_same_id_does_not_write_twice(self):
        receipt = self.bind()
        self.assertEqual(receipt["state"], "prepared_inactive")
        self.assertFalse(self.brain.nora["route"]["enabled"])
        config = read_private_json(self.config_path)
        self.assertEqual(config["nora"]["route_id"], self.route["id"])
        self.assertFalse(config["nora"]["route_verified"])
        self.assertEqual(self.bind()["state"], "already_bound")
        self.assertEqual(self.brain.calls, 1)
        self.assertNotIn(self.route["founder_sender"], json.dumps(receipt))
        self.assertFalse(receipt["message_sent"])

    def test_enable_requires_verification_and_completed_replay_cannot_undo_pause(self):
        with self.assertRaises(SetupError):
            self.bind(enable=True)
        result = self.bind(enable=True, verification_reference="binding-proof:fixture")
        self.assertEqual(result["state"], "bound")
        self.assertTrue(read_private_json(self.config_path)["nora"]["enabled"])
        config = read_private_json(self.config_path)
        config["nora"].update(enabled=False, route_verified=False)
        write_private(self.config_path, config)
        replay = self.bind(enable=True, verification_reference="binding-proof:fixture")
        self.assertEqual(replay["state"], "already_bound")
        self.assertFalse(replay["local_enabled"])
        self.assertFalse(read_private_json(self.config_path)["nora"]["enabled"])
        self.assertEqual(self.brain.calls, 1)

    def test_same_event_changed_route_is_a_conflict_without_second_mutation(self):
        self.bind()
        self.route["founder_sender"] = "changed-synthetic-sender"
        with self.assertRaises(SetupError):
            self.bind()
        self.assertEqual(self.brain.calls, 1)

    def test_team_writer_and_unconfigured_nora_actor_cannot_bind(self):
        self.brain.actor.update(id="team", permissions=["read", "write"])
        with self.assertRaises(SetupError):
            self.bind()
        self.brain.actor.update(id="founder")
        with self.assertRaises(SetupError):
            self.bind()
        self.assertEqual(self.brain.calls, 0)

    def test_target_founder_and_company_must_already_match_canonical_setup(self):
        for changes in ({"founder_actor_id": "not-enrolled"}, {"company_id": str(uuid4())},
                        {"conversation": "private-chat"}, {"revision": True}, {"token": "must-not-be-accepted"}):
            original = self.route
            self.route = {**original, **changes}
            with self.subTest(changes=list(changes)), self.assertRaises(SetupError):
                self.bind()
            self.route = original
        self.assertEqual(self.brain.calls, 0)

    def test_unknown_before_commit_never_retries_remote_and_keeps_local_disabled(self):
        self.brain.fail_before = True
        self.assertEqual(self.bind(enable=True, verification_reference="binding:proof")["state"], "unknown")
        self.assertEqual(self.bind(enable=True, verification_reference="binding:proof")["state"], "unknown_requires_reconcile")
        result = reconcile_binding(self.brain, self.config_path, read_private_json(self.config_path), self.event, writer=write_private)
        self.assertEqual(result["state"], "unknown_requires_review")
        self.assertEqual(self.brain.calls, 1)
        self.assertFalse(read_private_json(self.config_path)["nora"]["enabled"])

    def test_unknown_after_commit_reconciles_exact_ledger_without_remote_retry(self):
        self.brain.fail_after = True
        self.assertEqual(self.bind(enable=True, verification_reference="binding:proof")["state"], "unknown")
        self.assertFalse(read_private_json(self.config_path)["nora"]["enabled"])
        result = reconcile_binding(self.brain, self.config_path, read_private_json(self.config_path), self.event, writer=write_private)
        self.assertEqual(result["state"], "bound")
        self.assertEqual(self.brain.calls, 1)
        self.assertTrue(read_private_json(self.config_path)["nora"]["enabled"])

    def test_prepared_local_reconcile_requires_this_request_receipt_before_enabling(self):
        self.bind(enable=True, verification_reference="binding:proof")
        paused = read_private_json(self.config_path)
        paused["nora"].update(enabled=False, route_verified=False)
        write_private(self.config_path, paused)
        self.event = str(uuid4())
        before_calls = self.brain.calls
        def fail_applying(path, value):
            if Path(path).parent.name == "nora-bindings" and value.get("state") == "applying":
                raise OSError("Synthetic local write failure before remote mutation")
            write_private(path, value)
        with self.assertRaises(OSError):
            bind_route(self.brain, self.config_path, paused, self.route, "new:request", self.event,
                       enable=True, verification_reference="binding:proof", writer=fail_applying)
        journal = read_private_json(self.root / "nora-bindings" / (self.event + ".json"))
        self.assertEqual(journal["state"], "prepared_local")
        self.assertFalse(any(name.endswith("-" + self.event + ".json") for name in self.brain.ledger))
        result = reconcile_binding(self.brain, self.config_path, read_private_json(self.config_path),
                                   self.event, writer=write_private)
        self.assertEqual(result["state"], "unknown_requires_review")
        self.assertFalse(read_private_json(self.config_path)["nora"]["enabled"])
        self.assertFalse(read_private_json(self.config_path)["nora"]["route_verified"])
        self.assertEqual(self.brain.calls, before_calls)

    def test_stale_revision_is_rejected_without_overwriting_existing_route(self):
        self.bind()
        before = deepcopy(self.brain.nora)
        self.event = str(uuid4())
        self.route["founder_sender"] = "new-synthetic-sender"
        self.assertEqual(self.bind()["state"], "rejected")
        self.assertEqual(self.brain.nora, before)

    def test_tampered_journal_cannot_enable_configuration_during_reconcile(self):
        self.brain.fail_after = True
        self.bind()
        path = self.root / "nora-bindings" / (self.event + ".json")
        journal = read_private_json(path)
        journal["enable"] = True
        journal["verification_reference"] = "forged:proof"
        journal["route"]["enabled"] = True
        write_private(path, journal)
        with self.assertRaises(SetupError):
            reconcile_binding(self.brain, self.config_path, read_private_json(self.config_path), self.event, writer=write_private)
        self.assertFalse(read_private_json(self.config_path)["nora"]["enabled"])

    def test_optional_name_requires_verified_binding_and_hashes_exact_request(self):
        self.route["founder_name"] = "Lucas"
        self.bind()
        self.assertNotIn("founder_name", read_private_json(self.config_path)["nora"])
        self.event = str(uuid4())
        self.route["revision"] = 2
        self.bind(enable=True, verification_reference="verified:recipient")
        config = read_private_json(self.config_path)["nora"]
        self.assertEqual(config["founder_name"], "Lucas")
        self.assertEqual(config["founder_name_verification_id"], config["verification_id"])
        self.route["founder_name"] = "Outro nome"
        with self.assertRaisesRegex(SetupError, "EVENTO_REUTILIZADO"):
            self.bind(enable=True, verification_reference="verified:recipient")

    def test_invalid_name_denied_before_mutation_and_never_echoed(self):
        for name in ("", "   ", "x" * 121, "Lucas\n", "Lucas\u200b", 42):
            self.route["founder_name"] = name
            with self.subTest(name=repr(name)), self.assertRaisesRegex(SetupError, "NOME_VERIFICADO_INVALIDO"):
                self.bind(enable=True, verification_reference="verified:recipient")
        self.assertEqual(self.brain.calls, 0)

    def test_same_route_name_request_has_own_unknown_receipt_before_enabling(self):
        self.bind(enable=True, verification_reference="verified:first")
        self.event = str(uuid4())
        self.route["founder_name"] = "Lucas"
        self.brain.fail_after = True
        result = self.bind(enable=True, verification_reference="verified:recipient")
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(read_private_json(self.config_path)["nora"]["enabled"])
        self.assertEqual(self.brain.nora["binding_request"]["event_id"], self.event)
        self.brain.fail_after = False
        calls = self.brain.calls
        result = reconcile_binding(self.brain, self.config_path, read_private_json(self.config_path), self.event, writer=write_private)
        self.assertEqual(result["state"], "bound")
        self.assertEqual(self.brain.calls, calls)
        self.assertEqual(read_private_json(self.config_path)["nora"]["founder_name"], "Lucas")

    def test_main_uses_injected_no_model_factories_and_never_echoes_identity(self):
        route_file = self.root / "route.json"
        write_private(route_file, self.route)
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config_path), "bind", "--route-file", str(route_file),
                     "--request-reference", "request:fixture", "--event-id", self.event],
                    brain_factory=lambda _config: self.brain, config_loader=read_private_json,
                    writer=write_private, out=out, err=err)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertNotIn(self.route["founder_sender"], out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["state"], "prepared_inactive")

    def test_route_file_must_be_private_and_help_never_constructs_brain(self):
        path = self.root / "public.json"
        path.write_text("{}")
        path.chmod(0o644)
        with self.assertRaises(SetupError):
            read_private_json(path)
        def forbidden(*_args):
            raise AssertionError("Help must not load Brain/auth/model")
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["--help"], brain_factory=forbidden, config_loader=forbidden), 0)


if __name__ == "__main__":
    unittest.main()
