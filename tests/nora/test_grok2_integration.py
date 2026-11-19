"""G3/G4/G5; new report-derived fixtures on actual setup/Brain/Nora paths.

Only external channel and Claude process are faked. These are not original-run
replays. Optional NORA_GROK2_PROOF saves the actual messages/cards/receipts.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.cli import build_nora, validate_config
from company_pilot.inference import ClaudeInterpreter, SemanticWorker
from company_pilot.nora import InboundEvent, SendReceipt, RoundStatus
from company_pilot.nora.setup import bind_route, read_private_json
from company_pilot.nora.worker import health
from company_pilot.operator import Operator, drain_nora_inbox, project_questions, write_private_json
from tests.inference.test_claude import FakeRunner, stream
import test_nora as nora_fixtures


def evidence(source):
    return [{"source_id": source["id"], "quote": source["text"]}]


class Grok2NoraControls(unittest.TestCase):
    def fixture(self):
        case = nora_fixtures.NoraTests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_unknown_delivery_holds_new_projection_until_verified_reconcile(self):
        f = self.fixture()
        q = f.seed()[0]
        f.sender.outcomes.append(TimeoutError())
        sent = f.nora.tick()
        f.nora.put_question(replace(q, revision=2, issue_ids=("new-issue",)))
        f.clock.value += timedelta(hours=4)
        self.assertEqual(f.nora.tick().outcome, "empty")
        self.assertEqual(len(f.sender.calls), 1)
        f.nora.reconcile_unknown(sent.round_id, evidence_id="fixture-trusted-unsent", verified_unsent=True)
        f.clock.value += timedelta(hours=4)
        self.assertEqual(f.nora.tick().outcome, "accepted")

    def test_legacy_prepared_multi_question_message_cannot_send_after_upgrade(self):
        f = self.fixture()
        first, second = f.seed(2)
        f.sender.outcomes.append(__import__('company_pilot.nora', fromlist=['KnownUnsent']).KnownUnsent())
        sent = f.nora.tick()
        with f.repo.transaction(f.company) as state:
            old = state.rounds[sent.round_id]
            state.rounds[sent.round_id] = replace(old, items=old.items + (replace(old.items[0], question=second),),
                                                 text="Frozen historical multi-question message")
        f.clock.value += timedelta(seconds=30)
        self.assertEqual(f.nora.tick().outcome, "changed_before_send")
        self.assertEqual(len(f.sender.calls), 1)
        self.assertEqual(f.state().rounds[sent.round_id].text, "Frozen historical multi-question message")

    def test_legacy_accepted_batch_does_not_select_last_unresolved_item_by_guess(self):
        f = self.fixture()
        first, second = f.seed(2)
        sent = f.nora.tick()
        with f.repo.transaction(f.company) as state:
            old = state.rounds[sent.round_id]
            state.rounds[sent.round_id] = replace(old, items=old.items + (replace(old.items[0], question=second),),
                                                 text="Frozen historical batch with two questions")
        f.nora.put_question(replace(first, revision=2, status=nora_fixtures.QuestionStatus.RESOLVED))
        answer = f.nora.receive(f.event())
        self.assertEqual(answer.candidate_question_ids, (second.id,))
        self.assertEqual(answer.question_ids, ())
        self.assertEqual(len(answer.conversation_contexts), 2)

    def test_only_exact_route_only_legacy_codec_shape_migrates(self):
        from dataclasses import asdict
        from company_pilot.nora.repository import decode_state
        f = self.fixture()
        restored = decode_state({"route": asdict(f.route)}, f.company)
        self.assertEqual(restored.route, f.route)
        with self.assertRaises(ValueError):
            decode_state({"route": asdict(f.route), "rounds": {}}, f.company)
        with self.assertRaises(ValueError):
            decode_state({"route": asdict(f.route)}, "another-company")

    def test_field_conflict_copy_uses_actual_competing_values_not_full_source(self):
        f = self.fixture()
        q = f.question(missing_links=("performer",), activity_summary="Conferir conduítes RB-27",
            observed_context={"record_ref": "Cronograma RB-27", "actor": "lucas-de-sa-empresa-alfa", "occurred_at": f.clock.value.isoformat()},
            conflict_ids=("fixture-field-conflict",), conflict={"previous": {"value": "Lia"}, "reported_candidate": {"value": "Bia"}, "alternatives": []},
            evidence=({"source_id": "fixture", "quote": "MANY_UNRELATED_FIELD_VALUES Lia Bia"},))
        f.nora.put_question(q); f.nora.tick()
        text = f.sender.calls[0][1]
        for name in ("Lia", "Bia", "conduítes"):
            self.assertIn(name, text)
        self.assertNotIn("MANY_UNRELATED", text)
        self.assertNotIn("lucas-de-sa", text)
        self.assertEqual(text.count("?"), 1); self.assertLessEqual(len(text), 400)

    def test_related_fact_is_confirmation_with_frozen_source_not_automatic_closure(self):
        f = self.fixture()
        q = f.question(activity_summary="Encaminhar medição", known_facts=({"task_id": "other-task", "field": "purpose",
            "claim": {"value": "Permitir pagamento após liberação escrita", "basis": "observed",
                      "evidence": [{"source_id": "accepted-record", "quote": "Pagamento somente após liberação escrita"}]}},))
        f.nora.put_question(q); sent = f.nora.tick()
        item = f.state().rounds[sent.round_id].items[0]
        text = f.sender.calls[0][1]
        self.assertIn("Permitir pagamento após liberação escrita", text)
        self.assertIn("também se aplica", text)
        self.assertNotIn("Para que serve", text)
        self.assertEqual(item.why_chosen["context_fact"]["task_id"], "other-task")
        self.assertIn("accepted-record", item.why_chosen["source_ids"])
        self.assertEqual(f.state().questions[q.id].status.value, "pending")
        self.assertEqual(text.count("?"), 1)

    def test_unsupported_known_claim_and_opaque_actor_are_not_rendered(self):
        from company_pilot.nora.projection import known_fact_support
        claims = {"known_facts": [{"field": "purpose", "claim": {"basis": "observed", "value": "UNSUPPORTED", "evidence": [{"source_id": "missing", "quote": "not accepted"}]}}]}
        self.assertEqual(known_fact_support(claims, {}), ())
        f = self.fixture()
        q = f.question(observed_context={"record_ref": "Assunto: Pedido OS-17", "actor": "owner", "occurred_at": f.clock.value.isoformat()})
        f.nora.put_question(q); f.nora.tick()
        text = f.sender.calls[0][1]
        self.assertIn("Pedido OS-17", text); self.assertNotIn(" de owner.", text)

    def test_unasked_material_conflict_precedes_old_gap_and_due_reminder(self):
        f = self.fixture()
        old = f.seed()[0]
        f.nora.tick()
        f.clock.value += timedelta(days=3)
        f.nora.put_question(f.question(2))
        material = f.question(3, gap_kind="business_consistency", issue_ids=("price-issue",),
            evidence=({"source_id": "literal-fixture", "quote": "R$ 18.400 no pedido e R$ 16.900 no contrato"},))
        f.nora.put_question(material)
        sent = f.nora.tick()
        selected = f.state().rounds[sent.round_id].items[0]
        self.assertEqual(selected.question.id, material.id)
        self.assertFalse(selected.reminder)
        self.assertEqual(selected.why_chosen["tier"], "business_consistency")
        self.assertEqual(selected.why_chosen["eligible_count"], 3)
        self.assertEqual(f.state().questions[old.id].status.value, "pending")

    def test_no_sent_context_is_not_permission_to_guess_the_only_pending_question(self):
        f = self.fixture()
        q = f.seed()[0]
        answer = f.nora.receive(f.event(reply_to=None))
        self.assertEqual(answer.question_ids, ())
        self.assertEqual(answer.candidate_question_ids, (q.id,))
        self.assertEqual(answer.conversation_contexts, ())


class Grok2ActualSetupAndReply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.fromisoformat("2026-11-25T10:00:00-03:00")
        clock = patch("company_pilot.brain.engine.now", side_effect=lambda: self.now.isoformat())
        clock.start(); self.addCleanup(clock.stop)
        self.brain = Brain(LocalStore(self.root / "brain", actor="Lucas"))
        self.brain.initialize("Empresa sintética — Grok2 Nora", synthetic=True)
        company = self.brain.snapshot()["company"]["id"]
        self.path = self.root / "config.json"
        self.config = {"schema_version": 1, "mode": "synthetic", "store": {"kind": "local", "root": str(self.root / "brain")},
            "nora": {"enabled": False, "route_verified": False, "openclaw_executable": "/not-executed/openclaw"}}
        write_private_json(self.path, self.config)
        self.route = {"company_id": company, "id": str(uuid4()), "revision": 1, "account": "synthetic-account",
            "conversation": "100000000000@g.us", "founder_sender": "synthetic-native", "founder_actor_id": "Lucas",
            "founder_name": "Lucas"}
        result = bind_route(self.brain, self.path, self.config, self.route, "fixture:owner-request", str(uuid4()),
                            enable=True, verification_reference="fixture:verified-name-and-route")
        self.assertEqual(result["state"], "bound")
        self.config = read_private_json(self.path)
        validate_config(self.config)
        self.nora = build_nora(self.brain, self.config)
        self.assertEqual(self.nora.founder_name, "Lucas")
        self.nora.clock = lambda: self.now
        self.sent = []
        outer = self
        class FakeChannel:
            def send(self, route, text):
                outer.sent.append(text)
                return SendReceipt(route.account, route.conversation, "fixture-channel-" + str(len(outer.sent)))
        self.nora.sender = FakeChannel()

    def observe(self, text):
        result = self.brain.ingest_observation({"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()),
            "occurred_at": self.now.isoformat(), "policy_version": 1, "source": {"kind": "active_window", "app": "Planilha", "record_ref": "Pedido OS-17"},
            "classification": {"label": "company", "reason": "new report-derived fixture"},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True})
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == result["observation_id"])
        return result, source

    def task(self, text=None):
        raw, source = self.observe(text or "Ana confere o Pedido OS-17, recebe a planilha e entrega o pedido revisado a Renan. O motivo ainda não consta.")
        fields = {field: {"value": "Dado sintético " + field, "basis": "observed", "evidence": evidence(source)} for field in FIELDS}
        fields["purpose"] = {"value": None, "basis": "inferred", "evidence": []}
        self.brain.apply_analysis(raw["job_id"], {"tasks": [{"task_id": None, "name": "Revisar pedido OS-17", "fields": fields, "links": []}],
            "nodes": [], "context_updates": [], "next_question": None})
        project_questions(self.brain, self.nora)
        return self.brain.snapshot()["tasks"][0], self.brain.snapshot()["questions"][0]

    def save(self, name, **extra):
        target = os.environ.get("NORA_GROK2_PROOF")
        if not target: return
        folder = Path(target); folder.mkdir(parents=True, exist_ok=True)
        files = self.brain.store.read().files
        pages = {p: text for p, text in files.items() if p.startswith(("views/questions/", "views/tasks/")) or p == "views/perguntas.md"}
        (folder / (name + ".json")).write_text(json.dumps({"evidence_level": "new_report_derived_actual_components", "external_boundaries_faked": ["Claude process", "channel"],
            "actual_components": ["owner binding", "ledger", "CLI build_nora", "Nora", "Brain", "operator", "views"],
            "messages": self.sent, "snapshot": self.brain.snapshot(), "pages": pages, **extra}, ensure_ascii=False, indent=2, default=str) + "\n")

    def reply(self, text):
        return self.nora.receive(InboundEvent(self.route["account"], self.route["conversation"], self.route["founder_sender"],
            str(uuid4()), self.now + timedelta(minutes=1), text))

    def worker(self, analysis):
        runner = FakeRunner(output=stream(analysis))
        return SemanticWorker(self.brain, ClaudeInterpreter("/fake/claude", runner=runner)), runner

    def test_verified_setup_to_sent_question_to_supported_reply_to_updated_map(self):
        task, q = self.task()
        self.nora.tick()
        self.assertTrue(self.sent[0].startswith("Lucas, "))
        self.assertIn("OS-17", self.sent[0]); self.assertEqual(self.sent[0].count("?"), 1)
        answer = self.reply("A revisão evita uma compra duplicada.")
        self.assertEqual(answer.question_ids, (q["id"],))
        self.assertEqual(answer.conversation_contexts[0]["text"], self.sent[0])
        self.assertEqual(drain_nora_inbox(self.brain)["queued_answers"], 1)
        source = next(s for s in self.brain.snapshot()["sources"] if s["text"] == answer.event.text)
        fields = deepcopy(task["fields"])
        fields["purpose"] = {"value": "Evitar compra duplicada", "basis": "reported", "evidence": evidence(source)}
        decision = {"tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}], "nodes": [], "context_updates": [], "next_question": None,
            "message_intent": {"kind": "answer", "task_ids": [task["id"]], "question_ids": [q["id"]], "delegate_to": None, "disposition": None, "evidence": evidence(source)}}
        worker, runner = self.worker(decision)
        result = Operator(worker, brain=self.brain, nora=self.nora, clock=lambda: self.now).once()
        self.assertEqual(result["status"], "applied", result)
        current = self.brain.snapshot()
        self.assertEqual(current["tasks"][0]["fields"]["purpose"]["value"], "Evitar compra duplicada")
        self.assertEqual(current["questions"][0]["status"], "resolved")
        request = json.loads(runner.calls[-1][1]["input"])
        primary = next(s for s in request["base_context"]["sources"] if s["id"] == source["id"])
        self.assertEqual(primary["channel"]["sent_question_context"][0]["text"], self.sent[0])
        page = self.brain.store.read().files["views/questions/" + q["id"] + ".md"]
        selected = next(iter(current["nora"]["rounds"].values()))["items"][0]
        self.assertIn(selected["why_chosen"]["reason"], page)
        self.assertIn(selected["evidence"][0]["quote"], page)
        self.now += timedelta(hours=4)
        project_questions(self.brain, self.nora)
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(len(self.sent), 1)
        self.save("ordinary-reply-and-visible-provenance", operator=result, actual_request=request)

    def test_unresolved_semantic_completion_refreshes_visible_targeted_followup(self):
        task, q = self.task(); self.nora.tick()
        answer = self.reply("Não sei dizer a finalidade dessa revisão.")
        drain_nora_inbox(self.brain)
        source = next(s for s in self.brain.snapshot()["sources"] if s["text"] == answer.event.text)
        decision = {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
            "message_intent": {"kind": "answer", "task_ids": [], "question_ids": [q["id"]], "delegate_to": None, "disposition": None, "evidence": evidence(source)}}
        worker, _ = self.worker(decision)
        result = Operator(worker, brain=self.brain, nora=self.nora, clock=lambda: self.now).once()
        if result["status"] != "pending":
            self.save("unresolved-status-before-worker-fix", operator=result)
        self.assertEqual(result["status"], "pending", result)
        stored = self.nora.repository.transact(self.nora.company_id, lambda state: state.questions[q["id"]])
        self.assertEqual(stored.application["status"], "unresolved")
        self.assertEqual(health(self.nora)["questions_waiting_analysis"], 0)
        self.now += timedelta(hours=4)
        self.assertEqual(self.nora.tick().outcome, "accepted")
        self.assertIn("Sua resposta foi preservada", self.sent[1])
        self.assertEqual(self.sent[1].count("?"), 1)
        self.save("unresolved-semantic-completion", operator=result)

    def test_safe_context_identifiable_and_withheld_not_disclosed_or_falsely_closed(self):
        def gap(privacy=None):
            body = {"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()), "occurred_at": self.now.isoformat(),
                "app": "com.google.Chrome", "surface_kind": "desktop_window", "reason": "context_unresolved", "synthetic": True}
            if privacy: body["privacy_disposition"] = privacy
            return self.brain.ingest_context_gap(body)
        for privacy in ("personal", "excluded"):
            self.assertEqual(gap(privacy)["state"], "excluded")
        self.assertEqual(len(self.brain.snapshot()["questions"]), 0)
        withheld = gap("withheld")
        project_questions(self.brain, self.nora)
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(withheld["state"], "coverage_only")
        self.assertEqual(health(self.nora)["coverage_constraint_count"], 1)
        self.assertEqual(self.brain.snapshot()["questions"], [])
        self.now += timedelta(hours=4)
        safe = gap()
        project_questions(self.brain, self.nora)
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(self.sent, [])
        self.assertEqual(health(self.nora)["unaskable_pending_by_reason"], {"coverage_gap_not_asked": 1})
        self.assertEqual(health(self.nora)["coverage_constraint_count"], 1)
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == safe["question_id"])["status"], "pending")
        self.save("safe-and-withheld-context", safe=safe, withheld=withheld, health=health(self.nora))

    def test_accepted_relevant_source_answers_named_fact_before_another_message(self):
        task, q = self.task("Ana confere o Pedido OS-17. A revisão evita uma compra duplicada.")
        earlier = next(s for s in self.brain.snapshot()["sources"] if s["kind"] == "observation")
        self.now += timedelta(hours=4)
        raw, current = self.observe("Ana retomou a conferência do Pedido OS-17.")
        decision = {"tasks": [{"task_id": task["id"], "name": task["name"], "fields": deepcopy(task["fields"]), "links": []}],
            "nodes": [], "context_updates": [], "next_question": None,
            "question_answers": [{"question_id": q["id"], "claim": {"value": "Evitar uma compra duplicada", "basis": "observed", "evidence": evidence(earlier)}}]}
        worker, _ = self.worker(decision)
        result = worker.run_once()
        self.assertEqual(result["status"], "applied", result)
        project_questions(self.brain, self.nora)
        self.assertEqual(self.brain.snapshot()["questions"][0]["status"], "resolved")
        self.assertEqual(self.nora.tick().outcome, "empty")
        self.assertEqual(self.sent, [])
        self.save("accepted-relevant-source", operator=result)

    def test_partial_issue_answer_retains_remaining_same_source_issue(self):
        task, unused = self.task()
        raw, source = self.observe("Pedido OS-17: preço R$ 18.400 no pedido e R$ 16.900 no contrato; aprovação de Caio ainda sem confirmação.")
        candidate = {"task_id": task["id"], "name": task["name"], "fields": deepcopy(task["fields"]), "links": [],
            "consistency_issues": [{"description": label, "evidence": evidence(source)} for label in
                ("Preço: R$ 18.400 no pedido e R$ 16.900 no contrato", "A aprovação de Caio ainda precisa de confirmação")]}
        self.brain.apply_analysis(raw["job_id"], {"tasks": [candidate], "nodes": [], "context_updates": [], "next_question": None})
        project_questions(self.brain, self.nora)
        tick = self.nora.tick()
        round = self.nora.repository.transact(self.nora.company_id, lambda state: state.rounds[tick.round_id])
        item = round.items[0]
        self.assertEqual(item.question.gap_kind, "business_consistency")
        focus = item.why_chosen["focus_issue_ids"]
        self.assertEqual(len(focus), 1)
        self.assertIn("18.400", self.sent[0])
        answer = self.reply("Considere R$ 16.900 para o preço. A aprovação de Caio continua sem confirmação.")
        drain_nora_inbox(self.brain)
        primary = next(s for s in self.brain.snapshot()["sources"] if s["text"] == answer.event.text)
        current = next(t for t in self.brain.snapshot()["tasks"] if t["id"] == task["id"])
        claim = {"value": "Preço correto R$ 16.900", "basis": "reported", "evidence": evidence(primary)}
        decision = {"tasks": [{"task_id": current["id"], "name": current["name"], "fields": deepcopy(current["fields"]), "links": [],
            "issue_resolutions": [{"issue_ids": focus, "resolution": claim}]}], "nodes": [], "context_updates": [], "next_question": None,
            "message_intent": {"kind": "answer", "task_ids": [current["id"]], "question_ids": [item.question.id], "delegate_to": None, "disposition": None, "evidence": evidence(primary)}}
        worker, _ = self.worker(decision)
        result = Operator(worker, brain=self.brain, nora=self.nora, clock=lambda: self.now).once()
        q = next(q for q in self.brain.snapshot()["questions"] if q["id"] == item.question.id)
        self.assertEqual(q["status"], "pending")
        self.assertEqual(len(q["issue_ids"]), 1)
        self.assertNotIn(q["issue_ids"][0], focus)
        self.now += timedelta(hours=4)
        sent = self.nora.tick()
        selected = self.nora.repository.transact(self.nora.company_id, lambda state: state.rounds[sent.round_id].items[0])
        self.assertEqual(selected.why_chosen["focus_issue_ids"], q["issue_ids"])
        self.assertIn("Caio", self.sent[-1])
        self.assertNotIn("18.400", self.sent[-1])
        self.assertEqual(self.sent[-1].count("?"), 1)
        self.save("partial-same-source-issue", operator=result)

    def test_actual_factory_omits_absent_or_obsolete_name_binding(self):
        for binding in (None, "fixture:older-route"):
            config = deepcopy(self.config)
            config["nora"]["founder_name_verification_id"] = binding
            self.assertIsNone(build_nora(self.brain, config).founder_name)
        config = deepcopy(self.config); config["nora"].pop("founder_name")
        self.assertIsNone(build_nora(self.brain, config).founder_name)


if __name__ == "__main__": unittest.main()
