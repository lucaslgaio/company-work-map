"""Combined actual CLI/core/desktop/Nora paths, with explicit synthetic decisions."""
import copy
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.access import Response
from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.cli import main
from company_pilot.desktop.policy import Policy
from company_pilot.desktop.privacy import bind_private_policy, configure_private_policy
from company_pilot.inference import SemanticWorker
from company_pilot.nora import BrainRepository, Nora, Route
from company_pilot.nora.models import InboundEvent, SendReceipt
from company_pilot.operator import drain_nora_inbox, project_questions, write_private_json
from company_pilot.watchdog.__main__ import main as watchdog_main


def uid():
    return str(uuid4())


class NativeAccessFixture:
    """All access operations must remain reads; identities are fictitious."""
    def __init__(self):
        self.calls = []

    def __call__(self, route, method="GET", body=None):
        self.calls.append((route, method))
        if method != "GET":
            raise AssertionError("verification must not mutate native access")
        if route == "user":
            return Response(200, {"login": "owner", "id": 1})
        if route == "repos/owner/private":
            return Response(200, {"id": 3, "private": True, "permissions": {"admin": True},
                                  "owner": {"login": "owner", "id": 1, "type": "User"}})
        if "/invitations?" in route:
            return Response(200, [])
        if "/collaborators?" in route:
            return Response(200, [{"login": "member", "id": 2}])
        if route.startswith("users/"):
            login = route.split("/")[-1]
            return Response(200, {"login": login, "id": 1 if login == "owner" else 2, "type": "User"})
        if route.endswith("/permission"):
            return Response(200, {"permission": "admin" if "/owner/permission" in route else "write"})
        raise AssertionError(route)


class OutcomeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.brain = Brain(LocalStore(self.root / "brain", actor="owner"))
        self.brain.initialize("Oficina Lume — fixture", synthetic=True)
        self.config = self.root / "company.json"
        # Config says GitHub solely to exercise the actual owner command. The
        # injected store is LocalStore and verification is labelled fixture E1.
        write_private_json(self.config, {"schema_version": 1, "mode": "synthetic",
                           "store": {"kind": "github", "repo": "owner/private", "branch": "company-state-development",
                                     "mirror": str(self.root / "unused-mirror")}})

    def invoke(self, *args, brain=None):
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config), *args], brain_factory=lambda _config: brain or self.brain,
                    out=out, err=err, interpreter_factory=lambda _: self.fail("no real interpreter"))
        return code, json.loads(out.getvalue() or err.getvalue())

    def policy(self):
        requests = self.root / "requests.json"
        requests.write_text(json.dumps([{"actor_id": who, "read_scope": "all", "modify_scope": "all", "record_categories": []}
                                       for who in ("owner", "member")]))
        self.assertEqual(self.invoke("policy", "request-access", "--json-file", str(requests),
                                     "--request-reference", "fixture-confirmed-choice", "--event-id", uid())[0], 0)
        api = NativeAccessFixture()
        with patch("company_pilot.access.GitHubAPI", return_value=api):
            code, result = self.invoke("owner", "verify-access", "--event-id", uid())
        self.assertEqual((code, result["state"]), (0, "verified"))
        self.assertTrue(result["verification"]["fixture_only"])
        self.assertTrue(all(method == "GET" for _, method in api.calls))
        code, readback = self.invoke("policy", "show")
        self.assertEqual(code, 0)
        self.assertEqual(readback["access_policy"]["requests"], result["requests"])
        self.assertEqual(readback["access_policy"]["effective"], result["effective"])
        self.assertEqual(readback["access_policy"]["blockers"], [])

    def test_requested_effective_access_individual_control_and_unsupported_choice(self):
        self.policy()
        version = self.brain.device_policy()["policy_version"]
        config = {}
        receipt = configure_private_policy(self.root / "desktop.json", config,
                    {"participating": True, "screenshots": False, "reviewed_defaults": True,
                     "personal_exclusions": ["arquivo pessoal da pessoa"]}, version)
        self.assertEqual(self.invoke("policy", "participation", "--json-file", receipt["participation_choices_file"],
                                     "--event-id", uid())[0], 0)
        policy = bind_private_policy(Policy.from_brain(self.brain.device_policy()), config)
        self.assertTrue(policy.valid(datetime.now(policy.expires_at.tzinfo)))
        self.assertFalse(policy.permits_image(datetime.now(policy.expires_at.tzinfo)))
        self.assertNotIn("arquivo pessoal da pessoa", "\n".join(self.brain.store.read().files.values()))
        verified = copy.deepcopy(self.brain.access_policy())
        member = Brain(LocalStore(self.root / "brain", actor="member", permissions={"read", "write"}))
        requests = self.root / "restricted.json"
        requests.write_text(json.dumps([{"actor_id": "member", "read_scope": "selected", "modify_scope": "own",
                                        "record_categories": ["financeiro"]}]))
        self.assertEqual(self.invoke("policy", "request-access", "--json-file", str(requests), "--request-reference", "fixture",
                                     "--event-id", uid(), brain=member)[0], 2)
        code, result = self.invoke("policy", "request-access", "--json-file", str(requests),
                                   "--request-reference", "fixture-restricted-choice", "--event-id", uid())
        self.assertEqual((code, result["state"]), (0, "unsupported"))
        self.assertFalse(self.brain.device_policy()["controls"]["capture_allowed"])
        self.proof = {"evidence_level": "E1_actual_CLI_and_LocalStore_with_read_only_access_fixture",
                      "verified": verified, "unsupported": result, "blocked_controls": self.brain.device_policy()["controls"]}

    def test_source_versions_actual_brain_scheduled_question_and_answer_to_map(self):
        self.policy()
        roles = self.root / "watchdog-actors.json"
        roles.write_text('["owner"]')
        self.assertEqual(self.invoke("policy", "watchdog-actors", "--json-file", str(roles), "--event-id", uid())[0], 0)
        company = self.root / "company-files"
        company.mkdir()
        file = company / "ordem.txt"
        body = ("OS-17. Começa com orçamento aprovado; recebe bicicleta; ajusta freios; entrega bicicleta revisada. "
                "Finalidade: freios seguros. Processo: revisão. Repasse: cliente. Controle: Financeiro Oficina. Executor não definido.")
        file.write_text(body)
        settings = self.root / "watchdog.json"
        write_private_json(settings, {"schema_version": 1, "company_id": self.brain.snapshot()["company"]["id"],
            "company_config": str(self.config), "catalog": str(self.root / "catalog.db"), "enabled": True,
            "archive": {"kind": "local", "root": str(self.root / "archive")},
            "sources": [{"id": "company-files", "kind": "directory", "root": str(company)}]})
        def sync():
            out, err = StringIO(), StringIO()
            code = watchdog_main(["--config", str(settings), "sync"], brain_factory=lambda _: self.brain, out=out, err=err)
            self.assertEqual((code, err.getvalue()), (0, ""))
        sync()
        file.write_text(body.replace("bicicleta revisada.", "bicicleta revisada com laudo."))
        sync()
        class Decisions:
            def interpret(inner, job, context):
                source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
                ref = [{"source_id": source["id"], "quote": source["text"]}]
                if job["kind"] == "company_record":
                    values = dict(zip(FIELDS, ["orçamento aprovado", None, "bicicleta", "ajustar freios", "bicicleta revisada com laudo",
                                              "freios seguros", "revisão", "cliente", "Financeiro Oficina"]))
                    fields = {key: {"value": value, "basis": "observed" if value else "inferred", "evidence": ref if value else []}
                              for key, value in values.items()}
                    return {"tasks": [{"task_id": None, "name": "Ajustar freios OS-17", "fields": fields, "links": []}],
                            "nodes": [], "context_updates": [], "next_question": None,
                            "record_extraction": {"complete": source["extract_complete"], "limitations": []}}
                task = copy.deepcopy(context["tasks"][0])
                task["fields"]["performer"] = {"value": "Lia", "basis": "reported", "evidence": ref}
                questions = [q["id"] for q in context["questions"] if q["task_id"] == task["id"]]
                return {"tasks": [{"task_id": task["id"], "name": task["name"], "fields": task["fields"], "links": []}],
                        "nodes": [], "context_updates": [], "next_question": None,
                        "message_intent": {"kind": "answer", "task_ids": [task["id"]], "question_ids": questions,
                                           "delegate_to": None, "disposition": None, "evidence": ref}}
        worker = SemanticWorker(self.brain, Decisions())
        # Queue order is not part of this contract. Both revisions must finish,
        # and only the current one may publish work regardless of processing order.
        receipts = [worker.run_once(), worker.run_once()]
        self.assertEqual({result["receipt"]["state"] for result in receipts}, {"superseded_source_revision", "applied"})
        self.assertTrue(all(result["status"] == "applied" for result in receipts))
        task = self.brain.snapshot()["tasks"][0]
        self.assertIsNone(task["fields"]["performer"]["value"])
        self.assertEqual(task["status"], "provisional")
        repository = BrainRepository(self.brain.snapshot()["company"]["id"], self.brain.transact_nora)
        route = Route(self.brain.snapshot()["company"]["id"], uid(), 1, "fixture-account", "fixture-room", "fixture-founder", "owner", True)
        repository.transact(route.company_id, lambda state: setattr(state, "route", route))
        messages = []
        class Sender:
            def send(inner, target, text):
                messages.append(text)
                return SendReceipt(target.account, target.conversation, "fixture-sent")
        at = datetime.fromisoformat("2026-11-18T10:00:00-03:00")
        nora = Nora(route.company_id, repository, lambda: at, Sender())
        project_questions(self.brain, nora)
        nora.tick()
        self.assertEqual(len(messages), 1)
        # E5/E6, G3/G5: the real composed flow must be answerable without
        # founder-facing protocol codes, not merely produce a queued round.
        self.assertIn("OS-17", messages[0])
        self.assertEqual(messages[0].count("?"), 1)
        self.assertLessEqual(len(messages[0]), 400)
        self.assertNotRegex(messages[0], r"Q-[A-F0-9]{8}")
        self.assertNotIn("Registro:", messages[0])
        self.assertNotIn("amostras", messages[0])
        self.assertNotIn(" de owner.", messages[0])
        nora.receive(InboundEvent(route.account, route.conversation, route.founder_sender, "fixture-answer", at,
                                 "A Lia executa o ajuste de freios da OS-17."))
        self.assertEqual(drain_nora_inbox(self.brain)["queued_answers"], 1)
        self.assertEqual(worker.run_once()["status"], "applied")
        final = self.brain.snapshot()
        self.assertEqual(final["tasks"][0]["status"], "established")
        self.assertEqual(final["tasks"][0]["fields"]["performer"]["value"], "Lia")
        self.assertTrue(all(q["status"] == "resolved" for q in final["questions"]))
        head = self.brain.store.read().head
        sync()
        self.assertEqual(self.brain.store.read().head, head)
        file.unlink()
        sync()
        final = self.brain.snapshot()
        self.assertEqual(final["company_records"][0]["state"], "removed")
        self.assertEqual(len(final["company_records"][0]["history"]), 3)
        self.assertEqual(final["tasks"][0]["status"], "established")
        self.proof = {"evidence_level": "E1_actual_source_CLI_Brain_Nora_with_explicit_interpretation_and_sender_fixtures",
                      "revision_receipts": receipts, "nora_message": messages[0], "task": final["tasks"][0],
                      "company_records": final["company_records"], "source_ids": [source["id"] for source in final["sources"]],
                      "task_markdown": self.brain.store.read().files["views/tasks/" + final["tasks"][0]["id"] + ".md"],
                      "unchanged_replay_preserved_head": True, "real_model_native_network_channel_calls": 0}


if __name__ == "__main__":
    unittest.main()
