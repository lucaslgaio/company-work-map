"""0.1.2: false context conflicts can be repaired, and recognition names stay opt-in."""
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from company_pilot.brain import AccessDenied, Brain, BrainError, LocalStore
from company_pilot.brain.engine import identifier
from company_pilot.brain.stores import canonical
from company_pilot.cli import main
from company_pilot.desktop.policy import Policy
from company_pilot.operator import Operator, queue_status, write_private_json
from datetime import datetime, timezone


def uid():
    return str(uuid.uuid4())


def claim(source, value):
    return {"value": value, "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}


class ContextRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brain = Brain(LocalStore(self.root))
        self.brain.initialize("Empresa fictícia", synthetic=True)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, source_id):
        return next(source for source in self.brain.snapshot()["sources"] if source["id"] == source_id)

    def answer(self, text, topic, value):
        raw = self.brain.interview_answer(text, uid())
        source = self.source(raw["source_id"])
        self.brain.apply_analysis(raw["job_id"], {"tasks": [], "nodes": [], "next_question": None, "context_updates": [
            {"topic": topic, "value": value, "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}]})
        return source

    def inject_legacy(self, source, topic, previous, candidate, update):
        def apply(state, actor):
            result = {"tasks": [], "nodes": [], "context_updates": [update], "next_question": None}
            self.brain._source(state, actor, identifier(source["id"], "legacy-analysis"), "analysis", canonical(result),
                               input_source_ids=[source["id"]])
            self.brain._context_conflict(state, topic, previous, candidate, source["id"])
            return {"injected": True}
        self.brain._transaction("legacy_context_conflict", uid(), {"source": source["id"], "topic": topic, "value": candidate["value"]}, apply)

    def test_repair_keeps_explicit_and_stale_conflicts_and_is_idempotent(self):
        kept = self.answer("Traduzimos documentos.", "activities", "Tradução")
        later = self.brain.interview_answer("Também revisamos os textos.", uid())
        later_source = self.source(later["source_id"])
        self.brain.apply_analysis(later["job_id"], {"tasks": [], "nodes": [], "context_updates": [], "next_question": None})
        self.inject_legacy(later_source, "activities", self.brain.snapshot()["context"]["activities"],
                           claim(later_source, "Revisão"),
                           {"topic": "activities", "value": "Revisão", "basis": "reported",
                            "evidence": [{"source_id": later_source["id"], "quote": later_source["text"]}]})
        self.answer("Recebemos, traduzimos e entregamos.", "stages", "Receber e entregar")
        fresh = self.brain.interview_answer("A etapa passou a ser só a cobrança.", uid())
        fresh_source = self.source(fresh["source_id"])
        self.brain.apply_analysis(fresh["job_id"], {"tasks": [], "nodes": [], "next_question": None, "context_updates": [{
            "topic": "stages", "value": "Cobrança", "basis": "reported",
            "evidence": [{"source_id": fresh_source["id"], "quote": fresh_source["text"]}], "mode": "conflict"}]})
        self.answer("Guardamos o contrato assinado.", "documents", "Contrato assinado")
        legacy = self.brain.interview_answer("O documento passou a ser só o rascunho.", uid())
        legacy_source = self.source(legacy["source_id"])
        self.brain.apply_analysis(legacy["job_id"], {"tasks": [], "nodes": [], "context_updates": [], "next_question": None})
        self.inject_legacy(legacy_source, "documents", self.brain.snapshot()["context"]["documents"],
                           claim(legacy_source, "Rascunho"),
                           {"topic": "documents", "value": "Rascunho", "basis": "reported", "mode": "conflict",
                            "evidence": [{"source_id": legacy_source["id"], "quote": legacy_source["text"]}]})
        gap = self.brain.interview_defer("people", "Ainda não confirmei a equipe.", uid())
        old = self.brain.record_answer([gap["question_id"]], "Ana traduz.", uid())
        correction = self.brain.interview_correct("people", "Correção posterior: Bia traduz.", uid())
        self.brain.apply_analysis(correction["job_id"], {"tasks": [], "nodes": [], "next_question": None, "context_updates": [{
            "topic": "people", "value": "Bia traduz", "basis": "reported",
            "evidence": [{"source_id": correction["source_id"], "quote": self.source(correction["source_id"])["text"]}]}]})
        self.brain.apply_analysis(old["job_id"], {"tasks": [], "nodes": [], "next_question": None, "context_updates": [{
            "topic": "people", "value": "Ana traduz", "basis": "reported",
            "evidence": [{"source_id": old["answer_id"], "quote": self.source(old["answer_id"])["text"]}]}]})
        queued = self.brain.interview_correct("tracking", "O acompanhamento é o diário da obra.", uid())
        self.assertIsNone(self.source(queued["source_id"]).get("previous_context"))
        tracking_text = "Acompanhamos no cronograma geral."

        def fill(state, actor):
            raw = self.brain._source(state, actor, identifier(uid(), "tracking-fill"), "interview", tracking_text, speaker=actor["id"])
            from company_pilot.brain.knowledge import apply_context
            apply_context(self.brain, state, "tracking", claim(raw, "Cronograma geral"), raw, "add")
            return {"source_id": raw["id"]}

        filled_id = self.brain._transaction("tracking_fill", uid(), {"text": tracking_text}, fill)["source_id"]
        correction_source = self.source(queued["source_id"])
        self.inject_legacy(correction_source, "tracking", self.brain.snapshot()["context"]["tracking"],
                           claim(correction_source, "Diário da obra"),
                           {"topic": "tracking", "value": "Diário da obra", "basis": "reported", "mode": "conflict",
                            "evidence": [{"source_id": correction_source["id"], "quote": correction_source["text"]}]})
        head = self.brain.store.read().head
        ledger = {path: data for path, data in self.brain.store.read().files.items() if path.startswith("ledger/")}
        preview = self.brain.repair_context_conflicts(dry_run=True)
        self.assertEqual(self.brain.store.read().head, head)
        self.assertEqual({item["reason"] for item in preview["repairable"]}, {"missing_mode", "empty_previous_context"})
        self.assertIn("explicit_conflict", {item["reason"] for item in preview["skipped"]})
        self.assertIn("stale_answer", {item["reason"] for item in preview["skipped"]})
        receipt = self.brain.repair_context_conflicts(uid())
        self.assertEqual(receipt["state"], "repaired")
        self.assertEqual({item["reason"] for item in receipt["repaired"]}, {"missing_mode", "empty_previous_context"})
        final = self.brain.snapshot()
        active = {record["claim"]["value"] for record in final["context_claims"] if record["topic"] == "activities" and record["status"] == "active"}
        self.assertEqual(active, {"Tradução", "Revisão"})
        self.assertIn(kept["id"], {ref["source_id"] for record in final["context_claims"] if record["claim"]["value"] == "Tradução" for ref in record["claim"]["evidence"]})
        self.assertIn(later_source["id"], {ref["source_id"] for record in final["context_claims"] if record["claim"]["value"] == "Revisão" for ref in record["claim"]["evidence"]})
        tracking = {record["claim"]["value"] for record in final["context_claims"] if record["topic"] == "tracking" and record["status"] == "active"}
        self.assertEqual(tracking, {"Cronograma geral", "Diário da obra"})
        self.assertIn(filled_id, {ref["source_id"] for record in final["context_claims"] if record["claim"]["value"] == "Cronograma geral" for ref in record["claim"]["evidence"]})
        explicit_conflict = next(conflict for conflict in final["interview"]["context_conflicts"] if conflict["topic"] == "stages" and conflict["reported_candidate"]["value"] == "Cobrança")
        self.assertEqual(explicit_conflict["status"], "pending")
        self.assertEqual(explicit_conflict["origin"], "explicit_mode")
        legacy_conflict = next(conflict for conflict in final["interview"]["context_conflicts"] if conflict["topic"] == "documents")
        self.assertEqual(legacy_conflict["status"], "pending")
        self.assertNotIn("origin", legacy_conflict)
        self.assertNotIn("Cobrança", {record["claim"]["value"] for record in final["context_claims"] if record["status"] == "active"})
        self.assertNotIn("Rascunho", {record["claim"]["value"] for record in final["context_claims"] if record["status"] == "active"})
        stale = next(conflict for conflict in final["interview"]["context_conflicts"] if conflict["topic"] == "people")
        self.assertEqual(stale["status"], "pending")
        self.assertEqual(final["context"]["people"]["value"], "Bia traduz")
        self.assertIn("não era uma contradição", self.brain.store.read().files["views/empresa.md"])
        files = self.brain.store.read().files
        for path, data in ledger.items():
            self.assertEqual(files[path], data)
        self.assertGreater(sum(path.startswith("ledger/") for path in files), len(ledger))
        again = self.brain.store.read().head
        second = self.brain.repair_context_conflicts(uid())
        self.assertEqual(second["state"], "nothing_to_repair")
        self.assertEqual(self.brain.store.read().head, again)
        self.assertEqual(self.brain.repair_context_conflicts(dry_run=True)["repairable"], [])
        self.assertIn("explicit_conflict", {item["reason"] for item in self.brain.repair_context_conflicts(dry_run=True)["skipped"]})


class RecognitionNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brain = Brain(LocalStore(self.root))
        self.brain.initialize("Monte Vita", synthetic=True)
        self.config = self.root / "company.json"
        write_private_json(self.config, {"schema_version": 1, "mode": "synthetic",
                                          "store": {"kind": "local", "root": str(self.root)},
                                          "operator": {"health_file": str(self.root / "health.json")},
                                          "nora": {"enabled": False}})

    def tearDown(self):
        self.temp.cleanup()

    def test_named_nodes_stay_unconfirmed_until_the_founder_command(self):
        text = "A empresa Monte Vita acompanha a Obra Apartamento LM no Cronograma de Gestão de Obra."
        raw = self.brain.interview_answer(text, uid())
        source = next(item for item in self.brain.snapshot()["sources"] if item["id"] == raw["source_id"])
        evidence = [{"source_id": source["id"], "quote": source["text"]}]
        self.brain.apply_analysis(raw["job_id"], {"tasks": [], "context_updates": [], "next_question": None, "nodes": [
            {"kind": "organization", "name": "Monte Vita", "evidence": evidence},
            {"kind": "project", "name": "Obra Apartamento LM", "evidence": evidence},
            {"kind": "tracking_record", "name": "Cronograma de Gestão de Obra", "evidence": evidence},
        ]})
        self.assertEqual(self.brain.snapshot()["recognition_markers"], [])
        gap = self.brain.interview_next()["recognition"]
        self.assertEqual(gap["confirmed_count"], 0)
        self.assertEqual(gap["capture_effect"], "uncertain_until_names_confirmed")
        self.assertIn("confirm-names", gap["command"])
        self.assertEqual({(item["name"], item["kind"]) for item in gap["candidates"]}, {
            ("Monte Vita", "company_alias"),
            ("Obra Apartamento LM", "record_name"),
            ("Cronograma de Gestão de Obra", "record_name"),
        })
        out = StringIO()
        self.assertEqual(main(["--config", str(self.config), "setup", "status"], out=out, err=StringIO()), 0)
        status = json.loads(out.getvalue())
        self.assertEqual(status["recognition"]["confirmed_count"], 0)
        self.assertIn("Obra Apartamento LM", self.brain.store.read().files["views/reconhecimento.md"])
        self.assertIn("ainda não confirmado", self.brain.store.read().files["views/reconhecimento.md"])
        health = queue_status(self.brain.snapshot(), datetime(2026, 11, 19, tzinfo=timezone.utc))
        self.assertFalse(health["recognition_names_confirmed"])
        self.assertIn("setup confirm-names", health["summary"])
        self.assertNotIn("Obra Apartamento LM", health["summary"])
        operator = Operator(type("Idle", (), {"run_once": lambda self: {"status": "idle"}})(), brain=self.brain, health_file=self.root / "health.json")
        operator.once()
        health_out = StringIO()
        self.assertEqual(main(["--config", str(self.config), "operator", "health"], out=health_out, err=StringIO()), 0)
        reported = json.loads(health_out.getvalue())
        self.assertFalse(reported["processing"]["recognition_names_confirmed"])
        self.assertIn("setup confirm-names", reported["processing"]["summary"])
        self.assertNotIn(text, health_out.getvalue())
        at = datetime.now(timezone.utc)
        from dataclasses import replace
        before = replace(Policy.from_brain(self.brain.device_policy()), capture_allowed=True, local_exclusions_bound=True)
        self.assertEqual(before.classify({"title": "Obra Apartamento LM"}, at).state, "uncertain")
        head = self.brain.store.read().head
        with self.assertRaisesRegex(BrainError, "marker_name_must_be_literal_in_source"):
            self.brain.confirm_recognition_names([{"value": "Outra Obra", "kind": "record_name"}], uid())
        self.assertEqual(self.brain.store.read().head, head)
        with self.assertRaisesRegex(BrainError, "app_only_or_generic_marker_not_authorized"):
            self.brain.confirm_recognition_names([{"value": "Planilha", "kind": "record_name"}], uid())
        team = Brain(LocalStore(self.root, actor="fixture-team", permissions={"read", "write"}))
        with self.assertRaises(AccessDenied):
            team.confirm_recognition_names([{"value": "Monte Vita", "kind": "company_alias"}], uid())
        code, err = StringIO(), StringIO()
        self.assertEqual(main(["--config", str(self.config), "setup", "confirm-names",
                               "--name", "Obra Apartamento LM", "--kind", "record_name",
                               "--name", "Cronograma de Gestão de Obra", "--kind", "record_name",
                               "--name", "Monte Vita", "--kind", "company_alias"], out=code, err=err), 0)
        confirmed = json.loads(code.getvalue())
        self.assertEqual(confirmed["state"], "confirmed")
        self.assertEqual(self.brain.snapshot()["recognition_markers"] and len(self.brain.snapshot()["recognition_markers"]), 3)
        self.assertNotIn("recognition", self.brain.interview_next())
        after = replace(Policy.from_brain(self.brain.device_policy()), capture_allowed=True, local_exclusions_bound=True)
        self.assertEqual(after.classify({"title": "Obra Apartamento LM — medições"}, at).state, "company")
        self.assertEqual(after.classify({"title": "Cronograma de Gestão de Obra"}, at).state, "company")
        self.assertEqual(after.classify({"title": "Receita de bolo"}, at).state, "uncertain")
        self.assertEqual(after.classify({"title": "Obra Apartamento LM", "privacy_disposition": "withheld"}, at).state, "uncertain")
        self.brain.update_policy({"personal_exclusions": ["conta pessoal"]}, uid())
        personal = replace(Policy.from_brain(self.brain.device_policy()), capture_allowed=True, local_exclusions_bound=True)
        self.assertEqual(personal.classify({"title": "conta pessoal Monte Vita"}, at).state, "excluded")
        again = self.brain.store.read().head
        repeat = self.brain.confirm_recognition_names([
            {"value": "Obra Apartamento LM", "kind": "record_name"},
            {"value": "Cronograma de Gestão de Obra", "kind": "record_name"},
            {"value": "Monte Vita", "kind": "company_alias"},
        ], uid())
        self.assertEqual(repeat["state"], "already_confirmed")
        self.assertEqual(self.brain.store.read().head, again)
        operator.once()
        health_out = StringIO()
        self.assertEqual(main(["--config", str(self.config), "operator", "health"], out=health_out, err=StringIO()), 0)
        self.assertTrue(json.loads(health_out.getvalue())["processing"]["recognition_names_confirmed"])
        self.assertNotIn("Nenhum nome de reconhecimento foi confirmado", health_out.getvalue())


if __name__ == "__main__":
    unittest.main()
