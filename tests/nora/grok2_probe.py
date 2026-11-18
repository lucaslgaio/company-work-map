"""E1/E5/E6/E9, G3/G4/G5: NEW report-derived actual-path regression fixture.

python3 THIS_FILE PRODUCT_ROOT OUTPUT_DIRECTORY
Not a replay of truncated Grok-2 requests or the original Brain database.
"""
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

sys.dont_write_bytecode = True
product, output = [Path(value).resolve() for value in sys.argv[1:3]]
sys.path.insert(0, str(product))
from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.nora import BrainRepository, InboundEvent, Nora, Route, SendReceipt
from company_pilot.operator import project_questions


class Fixture:
    def __init__(self, root):
        self.now = datetime.fromisoformat("2026-11-23T00:00:00-03:00")
        self.patch = patch("company_pilot.brain.engine.now", side_effect=lambda: self.now.isoformat())
        self.patch.start()
        self.brain = Brain(LocalStore(Path(root) / "brain", actor="Lucas"))
        self.brain.initialize("Empresa sintética de medição e relatórios", synthetic=True)
        company = self.brain.snapshot()["company"]["id"]
        self.repo = BrainRepository(company, self.brain.transact_nora)
        self.route = Route(company, str(uuid4()), 1, "fixture-account", "100000000000@g.us", "fixture-native-founder", "Lucas", True)
        self.repo.transact(company, lambda state: setattr(state, "route", self.route))
        self.messages = []
        owner = self
        class Sender:
            def send(self, route, text):
                owner.messages.append({"at": owner.now.isoformat(), "text": text})
                return SendReceipt(route.account, route.conversation, "fixture-message-" + str(len(owner.messages)))
        self.nora = Nora(company, self.repo, lambda: self.now, Sender())
        self.nora.founder_name = "Lucas"  # Explicit fixture identity, never inferred from a login.

    def close(self): self.patch.stop()
    def task(self, name, material=False):
        text = ("Ana preparou a medição 2 da Obra Um para cobrar a etapa do contrato e enviou a Caio. "
                "A medição registra R$ 18.400; o contrato da mesma etapa registra R$ 16.900. "
                "O acompanhamento é no registro Medição 2 Obra Um.") if material else (name + ": revisão interna de relatório de trabalho.")
        record = "Medição 2 Obra Um" if material else name
        receipt = self.brain.ingest_observation({"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()),
            "occurred_at": self.now.isoformat(), "policy_version": 1,
            "source": {"kind": "active_window", "app": "Planilha", "record_ref": record},
            "classification": {"label": "company", "reason": "explicit report-derived business fixture", "context_version": 1},
            "content": {"text": text, "sha256": sha256(text.encode()).hexdigest()}, "synthetic": True})
        evidence = [{"source_id": receipt["observation_id"], "quote": text}]
        values = {"trigger": "Preparação da cobrança", "performer": "Ana", "input": "Medição 2",
            "action": "Preparar medição", "result": "Medição enviada a Caio", "purpose": "Cobrar etapa do contrato",
            "placement": "Cobrança de etapas", "handoff": "Caio", "tracking_record": "Registro Medição 2 Obra Um"}
        fields = {field: {"value": values[field] if material else None,
            "basis": "observed" if material else "inferred", "evidence": evidence if material else []} for field in FIELDS}
        if not material:
            fields["action"] = {"value": "Revisar relatório", "basis": "observed", "evidence": evidence}
        candidate = {"task_id": None, "name": name, "fields": fields, "links": []}
        if material:
            candidate["consistency_issues"] = [{"description": "A medição registra R$ 18.400 e o contrato da mesma etapa R$ 16.900", "evidence": evidence}]
        self.brain.apply_analysis(receipt["job_id"], {"tasks": [candidate], "nodes": [], "context_updates": [], "next_question": None})


output.mkdir(parents=True, exist_ok=False)
failures = []
with TemporaryDirectory(prefix="grok2-nora-priority-") as root:
    f = Fixture(root)
    try:
        f.now = f.now.replace(hour=10)
        f.task("Revisar relatório interno")
        f.now = datetime.fromisoformat("2026-11-25T18:00:00-03:00")
        f.task("Preparar medição 2 da Obra Um", material=True)
        expected = next(q for q in f.brain.snapshot()["questions"] if q.get("gap_kind") == "business_consistency")
        project_questions(f.brain, f.nora)
        tick = f.nora.tick()
        round = f.repo.transact(f.route.company_id, lambda state: state.rounds[tick.round_id])
        text = round.text
        item = asdict(round.items[0])
        checks = {"material_conflict_selected": len(round.items) == 1 and round.items[0].question.id == expected["id"],
            "one_semantic_question": len(round.items) == 1 and text.count("?") == 1,
            "human_message": all(word not in text for word in ("Q-", "Registro:", "views/", "amostras", "Referências desta atividade")) and len(text) <= 400,
            "actual_selection_provenance": bool(item.get("why_chosen")) and bool(item.get("evidence")),
            "recognizable_material_context": "18.400" in text and "16.900" in text and "medição" in text.casefold()}
        reply = f.nora.receive(InboundEvent(f.route.account, f.route.conversation, f.route.founder_sender,
            "fixture-code-free-reply", f.now + timedelta(minutes=5), "O valor correto para essa cobrança é R$ 16.900."))
        checks["code_free_conversation_scope"] = reply.question_ids == (expected["id"],)
        failures += [name for name, ok in checks.items() if not ok]
        (output / "selected-message.txt").write_text(text + "\n")
        (output / "selection.json").write_text(json.dumps({"fixture_kind": "new_report_derived_actual_Brain_Nora_paths",
            "checks": checks, "tick": asdict(tick), "round": asdict(round), "received": asdict(reply),
            "pending_question_count": len([q for q in f.brain.snapshot()["questions"] if q["status"] == "pending"])}, ensure_ascii=False, indent=2, default=str) + "\n")
    finally: f.close()

with TemporaryDirectory(prefix="grok2-nora-week-") as root:
    f = Fixture(root)
    try:
        for day in range(5):
            f.now = datetime.fromisoformat("2026-11-23T00:00:00-03:00") + timedelta(days=day)
            for number in range(3): f.task(f"Relatório sintético {day}-{number}")
            project_questions(f.brain, f.nora)
            for hour in (6, 10, 14, 18, 22):
                f.now = f.now.replace(hour=hour)
                f.nora.tick(); f.nora.tick()
        counts = {}
        for message in f.messages: counts[message["at"][:10]] = counts.get(message["at"][:10], 0) + 1
        rounds = f.repo.transact(f.route.company_id, lambda state: [asdict(r) for r in state.rounds.values() if r.items])
        checks = {"three_per_day": max(counts.values(), default=0) <= 3,
            "approved_slots_only": all(datetime.fromisoformat(m["at"]).hour in (10, 14, 18) for m in f.messages),
            "one_item_per_message": all(len(r["items"]) == 1 for r in rounds),
            "backlog_remains_visible": len([q for q in f.brain.snapshot()["questions"] if q["status"] == "pending"]) > len(f.messages)}
        failures += [name for name, ok in checks.items() if not ok]
        (output / "week.json").write_text(json.dumps({"checks": checks, "daily_counts": counts,
            "messages": f.messages, "pending": len([q for q in f.brain.snapshot()["questions"] if q["status"] == "pending"])}, ensure_ascii=False, indent=2) + "\n")
    finally: f.close()
receipt = {"evidence_level": "E1_new_report_derived_fixtures", "error_ids": ["E1", "E5", "E6", "E9"],
    "goals": ["G3", "G4", "G5"], "failures": failures, "real_model_native_network_channel_actions": 0,
    "source_sha256": {name: sha256((product/name).read_bytes()).hexdigest() for name in
        ("company_pilot/nora/engine.py", "company_pilot/nora/schedule.py", "company_pilot/operator.py")}}
(output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
raise SystemExit(bool(failures))
