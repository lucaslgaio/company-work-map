"""Offline Nora-only host tests. No interpreter, process transport or real channel."""
import builtins
from dataclasses import asdict
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import signal
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, LocalStore
from company_pilot.cli import main
from company_pilot.nora import BrainRepository, Nora, Route
from company_pilot.operator import Operator, write_private_json


class WaitRecorder:
    def __init__(self): self.delays = []
    def is_set(self): return False
    def set(self): pass
    def wait(self, delay): self.delays.append(delay)


class NoSend:
    def send(self, *_args): raise AssertionError("off-slot tests must not send")


class NoraServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.brain = Brain(LocalStore(self.root / "brain"))
        self.brain.initialize("Empresa sintética de serviço Nora", synthetic=True)
        self.company = self.brain.snapshot()["company"]["id"]
        self.repository = BrainRepository(self.company, self.brain.transact_nora)
        self.route = Route(self.company, str(uuid4()), 1, "synthetic-account", "synthetic-conversation", "synthetic-founder", "fixture-founder", True)
        self.repository.transact(self.company, lambda state: setattr(state, "route", self.route))
        self.nora = Nora(self.company, self.repository, lambda: datetime.fromisoformat("2026-11-18T07:00:00-03:00"), NoSend())
        self.config_path = self.root / "config.json"
        self.config = {"schema_version": 1, "mode": "synthetic", "store": {"kind": "local", "root": str(self.root / "brain")},
                       "operator": {"health_file": str(self.root / "analysis-health.json")},
                       "nora": {"enabled": True, "route_verified": True, "verification_id": "synthetic-bound-route-proof",
                                "route_id": self.route.id, "route_revision": 1, "openclaw_executable": "/not-executed/openclaw"}}
        self.save()

    def save(self): write_private_json(self.config_path, self.config)
    def tearDown(self): self.tmp.cleanup()

    def invoke(self, *args, stop=None, nora_factory=None):
        out, err = StringIO(), StringIO()
        def forbidden(_config): raise AssertionError("Nora-only must never construct an interpreter")
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if "inference" in name.split("."):
                raise AssertionError("Nora-only must not import inference")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded_import):
            code = main(["--config", str(self.config_path), "nora", *args], brain_factory=lambda _config: self.brain,
                        interpreter_factory=forbidden, nora_factory=nora_factory or (lambda _brain, _config: self.nora),
                        out=out, err=err, stop=stop)
        return code, out.getvalue(), err.getvalue()

    def test_pending_analysis_does_not_stop_nora_and_default_poll_is_sixty_seconds(self):
        self.brain.interview_answer("Prestamos serviços de manutenção.", str(uuid4()))
        pending = self.brain.snapshot()["pending_jobs"]
        stop = WaitRecorder()
        code, out, err = self.invoke("watch", "--max-cycles", "2", stop=stop)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(stop.delays, [60])
        receipts = [json.loads(line) for line in out.splitlines()]
        self.assertTrue(all(r["role"] == "nora" and r["analysis"] == "not_run" and r["status"] == "idle" for r in receipts))
        self.assertEqual([j["id"] for j in self.brain.snapshot()["pending_jobs"]], [j["id"] for j in pending])
        health = json.loads((self.root / "nora-health.json").read_text())
        self.assertEqual(health["role"], "nora")
        self.assertEqual(health["availability"], "requires_online_nora_host")
        self.assertEqual(health["channel_connectivity"], "not_probed")
        self.assertEqual(health["state"], "stopped")
        self.assertFalse((self.root / "analysis-health.json").exists())
        code, out, err = self.invoke("health")
        self.assertEqual((code, err), (0, ""))
        readback = json.loads(out)
        self.assertEqual(readback["worker_health"]["role"], "nora")
        self.assertEqual(readback["worker_health"]["process_liveness"], "not_verified")

    def test_disabled_and_unverified_watch_refuse_before_route_construction(self):
        def forbidden(*_args): raise AssertionError("route should not be constructed")
        self.config["nora"]["enabled"] = False; self.save()
        code, out, err = self.invoke("watch", "--max-cycles", "1", nora_factory=forbidden)
        self.assertEqual(code, 2); self.assertIn("ROTA_NORA_INATIVA", err)
        self.config["nora"].update(enabled=True, route_verified=False); self.save()
        code, out, err = self.invoke("watch", "--max-cycles", "1", nora_factory=forbidden)
        self.assertEqual(code, 2); self.assertIn("ROTA_NORA_NAO_VERIFICADA", err)
        self.assertFalse((self.root / "nora-health.json").exists())

    def test_nora_and_analysis_cannot_share_heartbeat_file(self):
        self.config["nora"]["health_file"] = self.config["operator"]["health_file"]; self.save()
        code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual(code, 2); self.assertIn("SAUDE_NORA_SEPARADA", err)

    def test_production_factory_rejects_a_mismatched_persisted_route_without_transport(self):
        self.config["nora"]["route_id"] = "different-unverified-route"; self.save()
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config_path), "nora", "watch", "--max-cycles", "1"],
                    brain_factory=lambda _config: self.brain, out=out, err=err)
        self.assertEqual(code, 2)
        self.assertIn("ROTA_NORA_NAO_VERIFICADA", err.getvalue())
        self.assertFalse((self.root / "nora-health.json").exists())

    def test_founder_name_reaches_factory_only_with_current_route_binding(self):
        from company_pilot.cli import build_nora, validate_config
        self.config["nora"].update(founder_name="Lucas",
            founder_name_verification_id=self.config["nora"]["verification_id"])
        validate_config(self.config)
        with patch("company_pilot.nora.Nora") as factory:
            build_nora(self.brain, self.config)
        self.assertEqual(factory.call_args.kwargs, {"founder_name": "Lucas"})
        for binding in (None, "older-route-verification"):
            self.config["nora"]["founder_name_verification_id"] = binding
            with patch("company_pilot.nora.Nora") as factory:
                build_nora(self.brain, self.config)
            self.assertNotIn("founder_name", factory.call_args.kwargs)

    def test_founder_name_rejects_control_characters_and_unbounded_copy(self):
        from company_pilot.cli import CliError, validate_config
        for name in ("", "  ", "Lucas\nOutra instrução", "X" * 121, {"name": "Lucas"}):
            self.config["nora"]["founder_name"] = name
            with self.assertRaises(CliError):
                validate_config(self.config)

    def test_nora_poll_override_is_bounded_to_sixty_seconds(self):
        self.config["nora"]["poll_seconds"] = 30; self.save()
        stop = WaitRecorder()
        code, out, err = self.invoke("watch", "--max-cycles", "2", stop=stop)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(stop.delays, [30])
        self.config["nora"]["poll_seconds"] = 61; self.save()
        code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual(code, 2)
        self.assertIn("DADOS_NAO_ACEITOS", err)

    def test_model_free_watch_consumes_private_spool_once_and_preserves_analysis_job(self):
        self.brain.ingest_context_gap({"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()),
            "occurred_at": "2026-11-17T12:00:00+00:00", "app": "Synthetic Ledger", "surface_kind": "desktop_window", "synthetic": True})
        from company_pilot.operator import project_questions
        project_questions(self.brain, self.nora)
        spool = self.root / "inbox.jsonl"
        record = {"schema_version": 1, "account": self.route.account, "conversation": self.route.conversation,
                  "sender": self.route.founder_sender, "message_id": "synthetic-message-1", "received_at": "2026-11-18T07:00:00-03:00",
                  "text": "Usamos esse aplicativo para conferir as cobranças.", "media_type": None, "reply_to": None}
        spool.write_text(json.dumps(record) + "\n"); spool.chmod(0o600)
        self.config["nora"]["inbox_file"] = str(spool); self.save()
        code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual((code, err), (0, ""))
        snapshot = self.brain.snapshot()
        self.assertEqual(len([s for s in snapshot["sources"] if s["kind"] == "answer"]), 1)
        self.assertEqual(len(snapshot["pending_jobs"]), 1)
        self.assertTrue((self.root / "nora-inbox-cursor.json").exists())
        head = snapshot["head"]
        code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.brain.snapshot()["head"], head)
        self.assertEqual(json.loads(out)["received_records"], 0)

    def test_watch_refreshes_candidates_before_a_spool_batch_and_replay_adds_nothing(self):
        from company_pilot.operator import project_questions
        def gap(app):
            return self.brain.ingest_context_gap({"schema_version": 1, "event_id": str(uuid4()),
                "device_id": str(uuid4()), "occurred_at": "2026-11-17T12:00:00+00:00",
                "app": app, "surface_kind": "desktop_window", "synthetic": True})
        first = gap("Synthetic A")
        project_questions(self.brain, self.nora)
        second = gap("Synthetic B")
        expected = {first["question_id"], second["question_id"]}
        original_sources = {s["id"]: s for s in self.brain.snapshot()["sources"]}
        spool = self.root / "inbox.jsonl"
        records = [{"schema_version": 1, "account": self.route.account,
                    "conversation": self.route.conversation, "sender": self.route.founder_sender,
                    "message_id": "synthetic-unquoted-" + str(index),
                    "received_at": "2026-11-18T07:00:00-03:00",
                    "text": "Usamos esse aplicativo para conferir as cobranças.",
                    "media_type": None, "reply_to": None} for index in range(2)]
        spool.write_text("".join(json.dumps(record) + "\n" for record in records))
        spool.chmod(0o600)
        cursor = self.root / "cursor.json"
        self.config["nora"].update(inbox_file=str(spool), cursor_file=str(cursor)); self.save()

        code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["received_records"], 2)
        after = self.brain.snapshot()
        answers = list(after["nora"]["inbox"].values())
        self.assertEqual(len(answers), 2)
        for answer in answers:
            self.assertEqual(set(answer["candidate_question_ids"]), expected)
            self.assertEqual(answer["question_ids"], [])
            self.assertEqual(answer["correlation"], "needs_question_attribution")
            self.assertEqual(answer["status"], "queued_for_analysis")
        sources = {s["id"]: s for s in after["sources"]}
        self.assertTrue(all(sources[key] == value for key, value in original_sources.items()))
        brain_answers = [s for s in sources.values() if s["kind"] == "answer"]
        self.assertEqual(len(brain_answers), 2)
        for answer in brain_answers:
            self.assertEqual(set(answer["candidate_question_ids"]), expected)
            self.assertEqual(answer["question_ids"], [])
        assignments = {q["id"] for q in after["questions"] if q.get("target_kind") == "answer_assignment"}
        # Ordinary intake freezes candidates first. Only interpretation can
        # decide whether clarification is needed; it cannot guess a target now.
        self.assertEqual(assignments, set())
        self.assertEqual(len(after["pending_jobs"]), 2)
        for job in after["pending_jobs"]:
            self.assertEqual(job["kind"], "message")
            self.assertEqual(set(job["candidate_question_ids"]), expected)
            self.assertEqual(job["question_ids"], [])
        self.assertEqual(set(after["nora"]["waiting_question_ids"]), expected)
        saved_cursor = cursor.read_bytes()
        self.assertEqual(json.loads(saved_cursor)["offset"], spool.stat().st_size)
        self.assertEqual(cursor.stat().st_mode & 0o777, 0o600)
        code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["received_records"], 0)
        self.assertEqual(self.brain.snapshot(), after)
        self.assertEqual(cursor.read_bytes(), saved_cursor)

    def test_projection_failure_leaves_spool_and_cursor_unconsumed(self):
        spool = self.root / "inbox.jsonl"
        record = {"schema_version": 1, "account": self.route.account,
                  "conversation": self.route.conversation, "sender": self.route.founder_sender,
                  "message_id": "synthetic-unconsumed", "received_at": "2026-11-18T07:00:00-03:00",
                  "text": "Usamos esse aplicativo na empresa.", "media_type": None, "reply_to": None}
        spool.write_text(json.dumps(record) + "\n"); spool.chmod(0o600)
        cursor = self.root / "cursor.json"
        self.config["nora"].update(inbox_file=str(spool), cursor_file=str(cursor)); self.save()
        before = self.brain.snapshot()
        with patch("company_pilot.operator.project_questions", side_effect=ConnectionError("PRIVATE FAILURE")):
            code, out, err = self.invoke("watch", "--max-cycles", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["nora"], "pending")
        self.assertNotIn("PRIVATE", out + err)
        self.assertFalse(cursor.exists())
        self.assertEqual(self.brain.snapshot(), before)

    def test_direct_receive_refreshes_candidates_and_preserves_durable_replay(self):
        from company_pilot.operator import project_questions
        def gap(app):
            return self.brain.ingest_context_gap({"schema_version": 1, "event_id": str(uuid4()),
                "device_id": str(uuid4()), "occurred_at": "2026-11-17T12:00:00+00:00",
                "app": app, "surface_kind": "desktop_window", "synthetic": True})
        first = gap("Synthetic A")
        project_questions(self.brain, self.nora)
        second = gap("Synthetic B")
        expected = {first["question_id"], second["question_id"]}
        original_sources = {s["id"]: s for s in self.brain.snapshot()["sources"]}
        spool, cursor = self.root / "inbox.jsonl", self.root / "cursor.json"
        record = {"schema_version": 1, "account": self.route.account,
                  "conversation": self.route.conversation, "sender": self.route.founder_sender,
                  "message_id": "synthetic-direct", "received_at": "2026-11-18T07:00:00-03:00",
                  "text": "Usamos esse aplicativo para conferir as cobranças.", "reply_to": None, "media_type": None}
        spool.write_text(json.dumps(record) + "\n"); spool.chmod(0o600)
        self.config["nora"].update(inbox_file=str(spool), cursor_file=str(cursor)); self.save()
        args = ("receive", "--record-file", str(spool))
        code, out, err = self.invoke(*args)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["received_records"], 1)
        after = self.brain.snapshot()
        answer, = after["nora"]["inbox"].values()
        self.assertEqual(set(answer["candidate_question_ids"]), expected)
        self.assertEqual(answer["question_ids"], [])
        self.assertEqual(answer["status"], "queued_for_analysis")
        sources = {s["id"]: s for s in after["sources"]}
        self.assertTrue(all(sources[key] == value for key, value in original_sources.items()))
        brain_answer, = [s for s in sources.values() if s["kind"] == "answer"]
        self.assertEqual(set(brain_answer["candidate_question_ids"]), expected)
        self.assertEqual(brain_answer["question_ids"], [])
        saved_cursor = cursor.read_bytes()
        self.assertEqual(json.loads(saved_cursor)["offset"], spool.stat().st_size)
        self.assertEqual(cursor.stat().st_mode & 0o777, 0o600)
        code, out, err = self.invoke(*args)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["received_records"], 0)
        self.assertEqual(self.brain.snapshot(), after)
        self.assertEqual(cursor.read_bytes(), saved_cursor)

    def test_direct_receive_projection_failure_preserves_unconsumed_spool(self):
        spool, cursor = self.root / "inbox.jsonl", self.root / "cursor.json"
        record = {"schema_version": 1, "account": self.route.account,
                  "conversation": self.route.conversation, "sender": self.route.founder_sender,
                  "message_id": "synthetic-direct-unconsumed", "received_at": "2026-11-18T07:00:00-03:00",
                  "text": "Usamos esse aplicativo na empresa.", "reply_to": None, "media_type": None}
        spool.write_text(json.dumps(record) + "\n"); spool.chmod(0o600)
        self.config["nora"].update(inbox_file=str(spool), cursor_file=str(cursor)); self.save()
        before, original_spool = self.brain.snapshot(), spool.read_bytes()
        with patch("company_pilot.cli.project_questions", side_effect=ConnectionError("PRIVATE FAILURE")):
            code, out, err = self.invoke("receive", "--record-file", str(spool))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn("PRIVATE", err)
        self.assertFalse(cursor.exists())
        self.assertEqual(spool.read_bytes(), original_spool)
        self.assertEqual(self.brain.snapshot(), before)

    def test_nora_only_signal_stop_restores_handlers(self):
        stop = Event()
        operator = Operator(None, brain=self.brain, nora=self.nora, stop=stop)
        before = signal.getsignal(signal.SIGTERM)
        def report(_receipt): signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual(operator.watch(report), 0)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertTrue(stop.is_set())
        self.assertEqual(operator.health["role"], "nora")
        self.assertEqual(operator.health["state"], "stopped")

    def test_nora_only_errors_have_bounded_backoff_and_content_free_health(self):
        class BadNora:
            company_id = self.company
            repository = self.repository
            def tick(self): raise RuntimeError("PRIVATE CHANNEL CONTENT")
        stop = WaitRecorder()
        operator = Operator(None, brain=self.brain, nora=BadNora(), stop=stop, max_failures=3,
                            retry_initial_seconds=2, retry_max_seconds=3)
        receipts = []
        self.assertEqual(operator.watch(receipts.append), 3)
        self.assertEqual(stop.delays, [2, 3])
        self.assertEqual(operator.health["state"], "attention_required")
        self.assertNotIn("PRIVATE", json.dumps(receipts) + json.dumps(operator.health))


if __name__ == "__main__": unittest.main()
