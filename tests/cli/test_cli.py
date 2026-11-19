import copy
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from threading import Event
import types
import unittest
from unittest.mock import patch

from company_pilot.cli import CliError, build_brain, consume_spool, enroll_founder, main, validate_config
from company_pilot.operator import Operator, drain_nora_inbox, project_questions, write_private_json


class FakeBrain:
    def __init__(self):
        self.calls = []
        self.data = {"company": {"id": "company-1", "synthetic": True}, "sources": [{"text": "PRIVATE SOURCE"}],
                     "tasks": [], "pending_jobs": [], "questions": [], "nora": {}}

    def initialize(self, name, **kwargs):
        self.calls.append(("init", name, kwargs))
        return {"company_id": "company-1", "state": "ready"}

    def snapshot(self):
        return copy.deepcopy(self.data)

    def interview_next(self):
        return {"state": "question", "question": "O que a empresa entrega ao cliente?"}

    def interview_answer(self, text, event_id):
        self.calls.append(("interview", text, event_id))
        return {"state": "pending_analysis"}

    def update_policy(self, changes, event_id):
        self.calls.append(("policy", changes, event_id))
        return {"policy_version": 2}

    def device_policy(self):
        return {"screenshots": {"founder": False, "team": True}}

    def access_policy(self):
        return {"state": "pending", "requests": [], "effective": [], "blockers": []}


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.json"
        self.brain = FakeBrain()

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, *args, **kwargs):
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config), *args], brain_factory=lambda _config: self.brain,
                    out=out, err=err, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def configure(self):
        config = {"schema_version": 1, "mode": "synthetic", "store": {"kind": "local", "root": str(self.root / "data")},
                  "operator": {"health_file": str(self.root / "health.json")}, "nora": {"enabled": False}}
        write_private_json(self.config, config)
        return config

    def test_init_private_config_and_strict_screenshot_decision(self):
        choices = self.root / "choices.json"
        choices.write_text('{"founder": false, "team": "false"}')
        code, _out, err = self.invoke("init", "--name", "Empresa teste", "--mode", "synthetic", "--store", "local", "--screenshots-file", str(choices))
        self.assertEqual(code, 2)
        self.assertIn("POLITICA_INVALIDA", err)
        self.assertEqual(self.brain.calls, [])
        choices.write_text('{"founder": true, "team": false}')
        code, out, err = self.invoke("init", "--name", "Empresa teste", "--mode", "synthetic", "--store", "local", "--screenshots-file", str(choices))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.brain.calls[0][2]["screenshots"], {"founder": True, "team": False})
        if os.name != "nt":
            self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertFalse(json.loads(self.config.read_text())["nora"]["enabled"])

    def test_company_data_cannot_use_local_fixture_and_config_cannot_hold_credentials(self):
        code, _, err = self.invoke("init", "--name", "Empresa", "--mode", "company", "--store", "local")
        self.assertEqual(code, 2)
        self.assertIn("LOCAL_APENAS_SINTETICO", err)
        config = self.configure()
        config["store"]["github_token"] = "DO-NOT-PRINT"
        write_private_json(self.config, config)
        code, out, err = self.invoke("status")
        self.assertEqual(code, 2)
        self.assertIn("SEGREDO_NA_CONFIGURACAO", err)
        self.assertNotIn("DO-NOT-PRINT", out + err)

    def test_github_branches_isolate_synthetic_and_company_initialization(self):
        code, out, err = self.invoke("init", "--name", "Empresa teste", "--mode", "synthetic", "--store", "github", "--repo", "owner/private")
        self.assertEqual((code, err), (0, ""))
        config = json.loads(self.config.read_text())
        self.assertEqual(config["store"]["branch"], "company-state-development")
        code, out, err = self.invoke("init", "--name", "Empresa teste", "--mode", "synthetic", "--store", "github", "--repo", "owner/private", "--branch", "company-state")
        self.assertEqual(code, 2)
        self.assertIn("BRANCH_INCOMPATIVEL", err)
        self.config = self.root / "company-config.json"
        code, out, err = self.invoke("init", "--name", "Empresa", "--mode", "company", "--store", "github", "--repo", "owner/private")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(self.config.read_text())["store"]["branch"], "company-state")
        code, out, err = self.invoke("init", "--name", "Empresa", "--mode", "company", "--store", "github", "--repo", "owner/private", "--branch", "company-state-development-proof")
        self.assertEqual(code, 2)
        self.assertIn("BRANCH_INCOMPATIVEL", err)

    def test_configured_synthetic_branch_is_passed_to_github_store_without_actor_override(self):
        captured = {}
        module = types.ModuleType("company_pilot.brain")
        class Store:
            def __init__(inner, repo, **kwargs): captured.update(repo=repo, **kwargs)
        module.GitHubStore = Store
        module.LocalStore = Store
        module.Brain = lambda store: store
        config = {"schema_version": 1, "mode": "synthetic", "store": {"kind": "github", "repo": "owner/private", "mirror": str(self.root / "mirror"), "branch": "company-state-development-proof-20261116"}}
        validate_config(config)
        with patch.dict(sys.modules, {"company_pilot.brain": module}):
            build_brain(config)
        self.assertEqual(captured["branch"], "company-state-development-proof-20261116")
        self.assertTrue(captured["development"])
        self.assertNotIn("actor", captured)

    def test_status_and_health_do_not_expose_sources_or_invoke_an_interpreter(self):
        self.configure()
        def forbidden(_config):
            raise AssertionError("interpreter must not be constructed")
        code, out, err = self.invoke("status", interpreter_factory=forbidden)
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("PRIVATE SOURCE", out)
        self.assertEqual(json.loads(out)["pending_job_count"], 0)
        code, out, err = self.invoke("operator", "health", interpreter_factory=forbidden)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["availability"], "not_established")

    def test_answer_and_policy_use_files_and_no_raw_exception_is_printed(self):
        self.configure()
        answer = self.root / "answer.txt"
        answer.write_text("Prestamos serviços técnicos.", encoding="utf-8")
        code, out, err = self.invoke("interview", "answer", "--text-file", str(answer))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.brain.calls[-1][1], "Prestamos serviços técnicos.")
        bad = FakeBrain()
        bad.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("TOKEN=DO-NOT-PRINT"))
        out, err = StringIO(), StringIO()
        self.assertEqual(main(["--config", str(self.config), "status"], brain_factory=lambda _c: bad, out=out, err=err), 2)
        self.assertNotIn("DO-NOT-PRINT", out.getvalue() + err.getvalue())

    def test_disabled_nora_never_constructs_sender_or_reads_arbitrary_inbox(self):
        self.configure()
        for command in (("nora", "health"), ("nora", "tick")):
            code, out, err = self.invoke(*command)
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["state"], "inactive")
        code, out, err = self.invoke("nora", "receive", "--record-file", str(self.root / "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("ROTA_NORA_INATIVA", err)

    def test_operator_uses_injected_interpreter_and_hides_receipt_content(self):
        self.configure()
        self.brain.store = types.SimpleNamespace(authenticated_actor=lambda: {"id": "fixture-founder"})
        self.brain.data["company"]["semantic_workers"] = ["fixture-founder"]
        sentinel = object()
        class Worker:
            def __init__(inner, brain, interpreter):
                self.assertIs(brain, self.brain)
                self.assertIs(interpreter, sentinel)
            def run_once(inner):
                return {"status": "applied", "receipt": {"private": "DO-NOT-PRINT"}}
        module = types.ModuleType("company_pilot.inference.worker")
        module.SemanticWorker = Worker
        with patch.dict(sys.modules, {"company_pilot.inference.worker": module}):
            code, out, err = self.invoke("operator", "once", interpreter_factory=lambda _config: sentinel)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["status"], "applied")
        self.assertNotIn("DO-NOT-PRINT", out)
        self.assertEqual(json.loads((self.root / "health.json").read_text())["state"], "stopped")

    def test_health_receipts_cannot_echo_arbitrary_backend_strings(self):
        self.configure()
        write_private_json(self.root / "health.json", {"schema_version": 1, "pid": 123, "state": "running",
                           "last_tick_at": "2026-11-18T12:00:00+00:00", "last_success_at": None,
                           "consecutive_failures": 1, "error_code": "TOKEN=DO-NOT-PRINT", "availability": "PRIVATE SOURCE"})
        code, out, err = self.invoke("operator", "health")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("DO-NOT-PRINT", out)
        self.assertNotIn("PRIVATE SOURCE", out)
        self.assertEqual(json.loads(out)["process_liveness"], "not_verified")

    def test_actor_override_is_not_a_supported_configuration_field(self):
        config = self.configure(); config["store"]["actor"] = "pretend-founder"
        write_private_json(self.config, config)
        code, out, err = self.invoke("status")
        self.assertEqual(code, 2)
        self.assertIn("CONFIGURACAO_INVALIDA", err)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.brain = FakeBrain()
        self.brain.data["company"]["founders"] = ["owner"]
        self.config = {"store": {"kind": "github", "repo": "owner/private"}}
        self.plan = {"repository_id": 42, "operator": "owner", "owner": "owner",
                     "requests": [{"login": "founder", "actual": "write"}]}

    def enroll(self):
        factory = lambda _repo: types.SimpleNamespace(plan=lambda _requests, request_reference: copy.deepcopy(self.plan))
        return enroll_founder(self.brain, self.config, "founder", "verified-request", "event", access_factory=factory)

    def test_owner_adds_founder_without_replacing_existing_owner_or_changing_access(self):
        result = self.enroll()
        self.assertEqual(self.brain.calls, [("policy", {"founders": ["owner", "founder"]}, "event")])
        self.assertFalse(result["access_changed"])
        self.assertEqual(result["repository_id"], 42)

    def test_pending_or_read_access_and_nonowner_cannot_enroll(self):
        for actual in ("none", "read", "unknown"):
            self.plan["requests"][0]["actual"] = actual
            with self.assertRaises(CliError):
                self.enroll()
        self.plan["requests"][0]["actual"] = "write"
        self.plan["operator"] = "someone-else"
        with self.assertRaises(CliError):
            self.enroll()
        self.assertEqual(self.brain.calls, [])

    def test_existing_founder_is_noop_and_local_fixture_is_not_remote_authority(self):
        self.brain.data["company"]["founders"].append("founder")
        self.assertEqual(self.enroll()["receipt"]["state"], "unchanged")
        self.config["store"]["kind"] = "local"
        with self.assertRaises(CliError):
            self.enroll()
        self.assertEqual(self.brain.calls, [])


class OperatorTests(unittest.TestCase):
    def test_scheduled_clarification_wait_does_not_exhaust_operator_failure_budget(self):
        class Worker:
            def run_once(self):
                return {"status": "awaiting_clarification", "job_ids": ["job-1"], "question_ids": ["question-1"]}
        operator = Operator(Worker(), brain=FakeBrain(), max_failures=2)
        for _ in range(5):
            result = operator.once()
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["error_code"], "awaiting_clarification")
            self.assertEqual(operator.failures, 0)
            self.assertEqual(operator.health["state"], "running")

    def test_watch_has_bounded_backoff_and_stops_after_the_failure_limit(self):
        delays = []
        class Stop:
            def is_set(self): return False
            def wait(self, delay): delays.append(delay)
            def set(self): pass
        class Worker:
            def run_once(self): return {"status": "pending", "error_code": "model_timeout", "private": "secret"}
        receipts = []
        operator = Operator(Worker(), stop=Stop(), max_failures=4, retry_initial_seconds=2, retry_max_seconds=5)
        self.assertEqual(operator.watch(receipts.append), 3)
        self.assertEqual(delays, [2, 4, 5])
        self.assertEqual(operator.health["state"], "attention_required")
        self.assertEqual(len(receipts), 4)
        self.assertNotIn("secret", json.dumps(receipts))

    def test_deferred_jobs_remain_visible_and_do_not_reset_failed_attempts(self):
        class Worker:
            outcomes = iter([{"status": "pending", "error_code": "model_timeout"}, {"status": "idle"}, {"status": "pending", "error_code": "model_timeout"}])
            def run_once(self): return next(self.outcomes)
        brain = FakeBrain(); brain.data["pending_jobs"] = [{"id": "job", "status": "pending", "next_attempt_at": "2099-01-01T00:00:00+00:00"}]
        operator = Operator(Worker(), brain=brain, max_failures=2)
        self.assertEqual(operator.once()["status"], "pending")
        self.assertEqual(operator.failures, 1)
        deferred = operator.once()
        self.assertEqual(deferred["error_code"], "retry_deferred")
        self.assertEqual(operator.failures, 1)
        operator.once()
        self.assertEqual(operator.health["state"], "attention_required")

    def test_watch_signal_stops_and_restores_handlers(self):
        stop, seen = Event(), []
        class Worker:
            def run_once(self): return {"status": "idle"}
        previous = signal.getsignal(signal.SIGTERM)
        def report(receipt):
            seen.append(receipt)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        operator = Operator(Worker(), stop=stop)
        self.assertEqual(operator.watch(report), 0)
        self.assertTrue(stop.is_set())
        self.assertEqual(operator.health["state"], "stopped")
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_intake_retries_same_id_after_cursor_failure_without_guessing_target(self):
        class Brain(FakeBrain):
            def __init__(self):
                super().__init__()
                self.fail = True
                self.records = {}
                self.data["questions"] = [{"id": qid, "status": "pending"} for qid in ("q1", "q2")]
                self.data["nora"] = {"route": {"id": "route"}, "inbox": {"key": {
                    "company_id": "company-1", "source_hash": "hash", "status": "awaiting_interpretation",
                    "question_ids": [], "candidate_question_ids": ["q1", "q2"], "question_versions": [],
                    "event": {"message_id": "m1", "account": "a", "conversation": "g", "sender": "f", "text": "Resposta ambígua",
                              "received_at": "2026-11-18T12:00:00+00:00"}}}}
            def message_targets(self, _text):
                return {"open_question_ids": [q["id"] for q in self.data["questions"]], "correction_targets": []}
            def record_message(self, text, event_id, **kwargs):
                self.records[event_id] = (kwargs["question_ids"], text, kwargs)
                return {"state": "pending"}
            def transact_nora(self, operation, **kwargs):
                if self.fail and "event_id" not in kwargs:
                    self.fail = False
                    raise RuntimeError("commit unconfirmed")
                return operation(self.data["nora"])
        brain = Brain()
        self.assertEqual(drain_nora_inbox(brain)["pending_intake"], 1)
        self.assertEqual(brain.data["nora"]["inbox"]["key"]["status"], "awaiting_interpretation")
        brain.data["questions"].append({"id": "q3", "status": "pending"})
        self.assertEqual(drain_nora_inbox(brain)["queued_answers"], 1)
        self.assertEqual(len(brain.records), 1)
        self.assertEqual(next(iter(brain.records.values()))[0], [])
        self.assertEqual(next(iter(brain.records.values()))[2]["candidate_question_ids"], ["q1", "q2"])
        self.assertEqual(brain.data["nora"]["inbox"]["key"]["candidate_question_ids"], ["q1", "q2"])


class ProjectionTests(unittest.TestCase):
    def test_context_gaps_are_projected_and_ordinary_new_observations_do_not_become_revisit_triggers(self):
        from company_pilot.nora.models import NoraState
        brain = FakeBrain()
        brain.store = types.SimpleNamespace(inspect_url=lambda path: "local-fixture:" + path)
        brain.data["sources"] = [{"id": "safe-source-1", "kind": "coverage_gap"}]
        brain.data["questions"] = [{"id": "context-gap", "task_id": "company-1", "target_kind": "context",
            "context_key": "context:app", "activity_summary": "Aplicativo ainda sem contexto", "prompt": "Este aplicativo é usado em qual atividade?",
            "status": "pending", "created_at": "2026-11-18T12:00:00+00:00", "task_version": 1,
            "revision": 1, "observation_ids": ["safe-source-1"], "evidence_ids": ["safe-source-1"],
            "missing_links": ["placement"], "revisit_evidence_ids": []}]
        state = NoraState()
        nora = types.SimpleNamespace(company_id="company-1", repository=types.SimpleNamespace(transact=lambda _id, callback: callback(state)),
                                     now=lambda: datetime.fromisoformat("2026-11-18T12:00:00+00:00"))
        self.assertEqual(project_questions(brain, nora), 1)
        self.assertEqual(state.questions["context-gap"].missing_links, ("placement",))
        brain.data["questions"][0]["observation_ids"].append("safe-source-2")
        project_questions(brain, nora)
        self.assertEqual(state.questions["context-gap"].observation_ids, ("safe-source-1", "safe-source-2"))
        self.assertEqual(state.questions["context-gap"].revisit_evidence_ids, ())


class SpoolTests(unittest.TestCase):
    def test_cursor_waits_for_durable_answer_handoff_and_replay_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spool, cursor = root / "inbox.jsonl", root / "cursor.json"
            record = {"message_id": "m1", "text": "Resposta do fundador"}
            spool.write_text(json.dumps(record) + "\n"); spool.chmod(0o600)
            class Brain(FakeBrain):
                def __init__(self):
                    super().__init__(); self.fail = True; self.records = {}
                    self.data["nora"] = {"route": {"id": "route"}, "inbox": {}}
                def message_targets(self, _text):
                    return {"open_question_ids": [], "correction_targets": []}
                def record_message(self, text, event_id, **kwargs):
                    if self.fail:
                        self.fail = False
                        raise RuntimeError("unconfirmed")
                    self.records[event_id] = text
                    return {"state": "pending"}
                def transact_nora(self, callback, **_kwargs):
                    return callback(self.data["nora"])
            brain = Brain()
            class Nora:
                calls = 0
                def receive(self, event):
                    self.calls += 1
                    brain.data["nora"]["inbox"].setdefault("key", {
                        "company_id": "company-1", "source_hash": "hash", "status": "awaiting_interpretation",
                        "question_ids": [], "question_versions": [], "event": {"message_id": event["message_id"],
                        "account": "a", "conversation": "g", "sender": "f", "text": event["text"],
                        "received_at": "2026-11-18T12:00:00+00:00"}})
                    return types.SimpleNamespace(status="awaiting_interpretation", source_key="key")
            nora = Nora(); setting = {"inbox_file": str(spool)}
            with self.assertRaises(CliError) as error:
                consume_spool(brain, nora, spool, setting, cursor, decoder=lambda record: record)
            self.assertEqual(error.exception.code, "RESPOSTA_PENDENTE")
            self.assertFalse(cursor.exists())
            self.assertEqual(consume_spool(brain, nora, spool, setting, cursor, decoder=lambda record: record)["received_records"], 1)
            self.assertEqual(len(brain.records), 1)
            self.assertEqual(consume_spool(brain, nora, spool, setting, cursor, decoder=lambda record: record)["received_records"], 0)
            self.assertEqual(nora.calls, 2)
            self.assertEqual(cursor.stat().st_mode & 0o777, 0o600)

    def test_invalid_and_incomplete_records_are_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spool, cursor = root / "inbox.jsonl", root / "cursor.json"
            spool.write_bytes(b'{"text":"incomplete"}'); spool.chmod(0o600)
            self.assertEqual(consume_spool(FakeBrain(), None, spool, {"inbox_file": str(spool)}, cursor, decoder=lambda value: value)["received_records"], 0)
            self.assertFalse(cursor.exists())
            spool.write_bytes(b'not JSON\n')
            with self.assertRaises(CliError) as error:
                consume_spool(FakeBrain(), None, spool, {"inbox_file": str(spool)}, cursor, decoder=lambda value: value)
            self.assertEqual(error.exception.code, "ENTRADA_EM_QUARENTENA")
            quarantine = root / "nora-inbox-quarantine.json"
            self.assertTrue(quarantine.exists())
            self.assertEqual(quarantine.stat().st_mode & 0o777, 0o600)
            self.assertFalse(cursor.exists())


if __name__ == "__main__":
    unittest.main()
