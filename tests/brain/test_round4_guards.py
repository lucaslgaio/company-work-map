"""Report-derived round4 public-core guards; no full-run replay or live I/O."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, FIELDS, LocalStore
import test_engine as fixtures


def uid():
    return str(uuid4())


def refs(source):
    return [{"source_id": source["id"], "quote": source["text"]}]


def claim(source, value):
    return {"value": value, "basis": "observed" if source["kind"] == "observation" else "reported", "evidence": refs(source)}


def empty():
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None}


class Round4PublicGuards(unittest.TestCase):
    def setUp(self):
        self.fresh_brain()

    def fresh_brain(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética round4 guards", synthetic=True)

    def source(self, sid):
        return next(source for source in self.brain.snapshot()["sources"] if source["id"] == sid)

    def task(self, tid):
        return next(task for task in self.brain.snapshot()["tasks"] if task["id"] == tid)

    def proposal(self, source, tid=None, changes=None, name="Atividade sintética"):
        fields = (copy.deepcopy(self.task(tid)["fields"]) if tid else
                  {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS})
        for field, value in (changes or {}).items():
            fields[field] = claim(source, value)
        return {"task_id": tid, "name": name, "fields": fields, "links": []}

    def observe(self, text, *, tid=None, changes=None, record="REC-A", when="2026-11-23T12:00:00+00:00", update=None, issues=None, name="Atividade sintética", apply=True):
        payload = fixtures.observation(text)
        payload["occurred_at"] = when
        if record is None:
            payload["source"].pop("record_ref", None)
        else:
            payload["source"]["record_ref"] = record
        raw = self.brain.ingest_observation(payload)
        source = self.source(raw["observation_id"])
        candidate = self.proposal(source, tid, changes, name)
        if update:
            candidate["field_updates"] = update(source)
        if issues is not None:
            candidate["consistency_issues"] = [{"description": issue, "evidence": refs(source)} for issue in issues]
        output = {**empty(), "tasks": [candidate]}
        if not apply:
            return raw, source, output
        receipt = self.brain.apply_analysis(raw["job_id"], output)
        return receipt["tasks"][0]["task_id"], source

    def scope(self):
        snapshot = self.brain.snapshot()
        return {**{group: [{"id": row["id"], "version": row["version"]} for row in snapshot[group]]
                   for group in ("tasks", "nodes", "edges")},
                "source_ids": [source["id"] for source in snapshot["sources"]]}

    def intent(self, source, kind, tasks=(), questions=()):
        return {"kind": kind, "task_ids": list(tasks), "question_ids": list(questions),
                "delegate_to": None, "disposition": None, "evidence": refs(source)}

    def equivalent(self, field, prior):
        return lambda source: {field: {"mode": "equivalent", "effective_at": None,
                                      "evidence": refs(source), "prior_evidence": copy.deepcopy(prior)}}

    def progress(self, field="result", effective_at=None):
        return lambda source: {field: {"mode": "progress", "effective_at": effective_at, "evidence": refs(source)}}

    def founder_result(self):
        tid, _ = self.observe("Registro REC-A: executado 80%.", changes={"result": "Executado 80%"})
        with patch("company_pilot.brain.engine.now", return_value="2026-11-24T12:00:00+00:00"):
            self.brain.change_map([{"op": "correct", "id": tid, "fields": {"result": "Executado 80%, conferido Bia"}}],
                                  uid(), "Executado 80%, conferido Bia na terça.")
        return tid

    def test_F3_equivalence_requires_current_and_complete_prior_raw_refs(self):
        tid, _ = self.observe("Preparar o relatório semanal.", changes={"purpose": "Preparar o relatório semanal"})
        prior = copy.deepcopy(self.task(tid)["fields"]["purpose"]["evidence"])
        raw, source, output = self.observe("Elaborar o relatório da semana.", tid=tid,
            changes={"purpose": "Elaborar o relatório da semana"}, apply=False)
        cases = (({"mode": "equivalent", "effective_at": None, "evidence": refs(source)}, "complete_prior_field_evidence_required"),
                 ({"mode": "equivalent", "effective_at": None, "evidence": prior, "prior_evidence": prior}, "current_field_update_evidence_required"),
                 ({"mode": "equivalent", "effective_at": None, "evidence": [{"source_id": source["id"], "quote": "ELABORAR O RELATÓRIO"}], "prior_evidence": prior}, "source_quote_not_found"))
        before = copy.deepcopy(self.task(tid))
        for update, reason in cases:
            with self.subTest(reason=reason):
                candidate = copy.deepcopy(output)
                candidate["tasks"][0]["field_updates"] = {"purpose": update}
                with self.assertRaisesRegex(BrainError, reason):
                    self.brain.apply_analysis(raw["job_id"], candidate)
                self.assertEqual(self.task(tid), before)
        output["tasks"][0]["field_updates"] = self.equivalent("purpose", prior)(source)
        self.brain.apply_analysis(raw["job_id"], output)
        self.assertNotIn("purpose", self.task(tid)["conflicts"])
        self.assertEqual(self.task(tid)["fields"]["purpose"]["evidence"], prior + refs(source))

    def test_F3_equivalence_requires_all_conflicting_support_and_preserves_business_issue(self):
        tid, first = self.observe("Preparar o relatório semanal; falta saber quem aprova compras.",
            changes={"purpose": "Preparar o relatório semanal"}, issues=["Ainda não foi confirmado quem aprova compras."])
        _, second = self.observe("Elaborar o relatório da semana.", tid=tid, changes={"purpose": "Elaborar o relatório da semana"})
        issue = copy.deepcopy(self.task(tid)["consistency_issues"])
        raw, source, output = self.observe("Produzir o relatório semanal.", tid=tid,
            changes={"purpose": "Produzir o relatório semanal"}, update=self.equivalent("purpose", refs(first)), apply=False)
        with self.assertRaisesRegex(BrainError, "complete_prior_field_evidence_required"):
            self.brain.apply_analysis(raw["job_id"], output)
        output["tasks"][0]["field_updates"] = self.equivalent("purpose", refs(first) + refs(second))(source)
        self.brain.apply_analysis(raw["job_id"], output)
        self.assertNotIn("purpose", self.task(tid)["conflicts"])
        self.assertEqual(self.task(tid)["consistency_issues"], issue)
        self.assertEqual(self.task(tid)["fields"]["purpose"]["evidence"], refs(first) + refs(second) + refs(source))
        question = next(q for q in self.brain.snapshot()["questions"] if q.get("gap_kind") == "business_consistency")
        self.assertEqual(question["status"], "pending")

    def test_F3_quantities_negation_and_known_performer_identity_stay_conflicts(self):
        for field, old, new, reason in (("purpose", "Comprar 90 peças", "Comprar 94 peças", "different_quantities"),
                                       ("purpose", "Bia aprova compras", "Bia não aprova compras", "different_constraint_or_state"),
                                       ("performer", "Bia", "Ana", "different_or_unknown_people")):
            with self.subTest(field=field, new=new):
                self.fresh_brain()
                known = self.brain.interview_answer("Bia confere a obra. Ana atua no PMO.", uid())
                source = self.source(known["source_id"])
                self.brain.apply_analysis(known["job_id"], {**empty(), "nodes": [
                    {"kind": "person", "name": name, "evidence": [{"source_id": source["id"], "quote": name}]}
                    for name in ("Bia", "Ana")]})
                tid, _ = self.observe(old, changes={field: old})
                prior = copy.deepcopy(self.task(tid)["fields"][field]["evidence"])
                self.observe(new, tid=tid, changes={field: new}, update=self.equivalent(field, prior))
                task = self.task(tid)
                self.assertEqual(task["fields"][field]["value"], old)
                self.assertEqual(task["conflicts"][field]["reported_candidate"]["value"], new)
                self.assertEqual(task["equivalence_history"][-1]["reason"], reason)

    def test_F6_newer_same_record_advances_founder_report_with_history(self):
        tid = self.founder_result()
        before = copy.deepcopy(self.task(tid)["fields"]["result"])
        self.observe("Registro REC-A: executado 100%, conferido Bia.", tid=tid,
            changes={"result": "Executado 100%, conferido Bia"}, when="2026-11-26T12:00:00+00:00", update=self.progress())
        task = self.task(tid)
        self.assertEqual(task["fields"]["result"]["value"], "Executado 100%, conferido Bia")
        self.assertNotIn("result", task["conflicts"])
        self.assertEqual(task["progress_history"][-1]["previous"], before)
        self.assertEqual(task["progress_history"][-1]["prior_authority"], "founder_report")

    def test_F6_same_time_older_other_record_and_timestamp_only_do_not_replace(self):
        cases = (("same", "2026-11-24T12:00:00+00:00", "REC-A", True, True),
                 ("older", "2026-11-23T12:00:00+00:00", "REC-A", True, False),
                 ("different_record", "2026-11-26T12:00:00+00:00", "REC-B", True, True),
                 ("no_record", "2026-11-26T12:00:00+00:00", None, True, True),
                 ("timestamp_only", "2026-11-26T12:00:00+00:00", "REC-A", False, True))
        for label, when, record, declared_progress, conflict in cases:
            with self.subTest(case=label):
                self.fresh_brain()
                tid = self.founder_result()
                before = copy.deepcopy(self.task(tid)["fields"]["result"])
                self.observe("Executado 100%, conferido Bia.", tid=tid, changes={"result": "Executado 100%, conferido Bia"},
                    when=when, record=record, update=self.progress() if declared_progress else None)
                self.assertEqual(self.task(tid)["fields"]["result"], before)
                self.assertEqual("result" in self.task(tid)["conflicts"], conflict)

    def test_F6_unknown_prior_clock_and_unsupported_field_do_not_gain_authority(self):
        raw = self.brain.interview_answer("Foi executado 80% e a finalidade é informar o cliente.", uid())
        source = self.source(raw["source_id"])
        receipt = self.brain.apply_analysis(raw["job_id"], {**empty(), "tasks": [self.proposal(source, changes={"result": "Executado 80%", "purpose": "Informar o cliente"})]})
        tid = receipt["tasks"][0]["task_id"]
        self.observe("Executado 100%.", tid=tid, changes={"result": "Executado 100%"},
                     when="2026-11-26T12:00:00+00:00", update=self.progress())
        self.assertEqual(self.task(tid)["fields"]["result"]["value"], "Executado 80%")
        self.assertIn("result", self.task(tid)["conflicts"])
        pending, _, output = self.observe("A finalidade mudou para cobrar o cliente.", tid=tid,
            changes={"purpose": "Cobrar o cliente"}, when="2026-11-27T12:00:00+00:00", update=self.progress("purpose"), apply=False)
        before = copy.deepcopy(self.task(tid))
        with self.assertRaisesRegex(BrainError, "mutable_observation_progress_required"):
            self.brain.apply_analysis(pending["job_id"], output)
        self.assertEqual(self.task(tid), before)

    def test_F6_priority_hint_does_not_authorize_other_record_progress(self):
        tid = self.founder_result()
        self.observe("Relato alternativo: executado 85%.", tid=tid, changes={"result": "Executado 85%"})
        question = next(q for q in self.brain.snapshot()["questions"] if q["task_id"] == tid and q["missing_links"] == ["result"])
        raw = self.brain.record_message("Essa dúvida merece atenção primeiro.", uid(), candidate_question_ids=[question["id"]])
        source = self.source(raw["answer_id"])
        output = {**empty(), "message_intent": self.intent(source, "chatter"),
                  "attention_requests": [{"question_id": question["id"], "reason": claim(source, source["text"])}]}
        self.brain.apply_analysis(raw["job_id"], output)
        self.assertTrue(next(q for q in self.brain.snapshot()["questions"] if q["id"] == question["id"])["priority_hint"])
        self.observe("Registro B informa execução de 100%.", tid=tid, record="REC-B", when="2026-11-28T12:00:00+00:00",
                     changes={"result": "Executado 100%"}, update=self.progress())
        self.assertEqual(self.task(tid)["fields"]["result"]["value"], "Executado 80%, conferido Bia")
        self.assertIn("result", self.task(tid)["conflicts"])

    def test_F8_delegate_refresh_retains_owner_without_fabricated_route(self):
        delegated = self.brain.interview_defer("tracking", "Ana sabe onde o andamento é acompanhado.", uid(), delegate_to="Ana")
        qid = delegated["question_id"]
        first = next(q for q in self.brain.snapshot()["questions"] if q["id"] == qid)
        self.assertEqual(first["status"], "awaiting_person")
        self.assertEqual(set(first["response_disposition"]), {"kind", "delegate_to", "disposition", "source_id", "uncertainty_basis"})
        self.assertNotIn(qid, self.brain.message_targets()["open_question_ids"])
        self.brain.interview_defer("tracking", "Ainda precisamos esclarecer o acompanhamento.", uid())
        refreshed = next(q for q in self.brain.snapshot()["questions"] if q["id"] == qid)
        self.assertEqual(refreshed["status"], "awaiting_person")
        self.assertEqual(refreshed["response_disposition"], first["response_disposition"])
        deferred = self.brain.snapshot()["interview"]["deferred_topics"]["tracking"]
        self.assertEqual((deferred["status"], deferred["delegate_to"]), ("awaiting_person", "Ana"))

    def test_F8_interview_next_does_not_offer_delegated_question_to_founder(self):
        delegated = self.brain.interview_defer("tracking", "Ana sabe onde o andamento é acompanhado.", uid(), delegate_to="Ana")
        self.assertNotIn(delegated["question_id"], self.brain.interview_next()["pending_context_question_ids"])

    def test_F8_supported_founder_answer_can_resolve_delegated_context(self):
        delegated = self.brain.interview_defer("tracking", "Ana sabe informar.", uid(), delegate_to="Ana")
        qid = delegated["question_id"]
        raw = self.brain.record_message("Ana confirmou: acompanhamos na Planilha Obras A.", uid(), question_ids=[qid])
        source = self.source(raw["answer_id"])
        output = {**empty(), "message_intent": self.intent(source, "context", questions=[qid]),
                  "context_updates": [{"topic": "tracking", **claim(source, "Planilha Obras A")}]}
        self.brain.apply_analysis(raw["job_id"], output)
        question = next(q for q in self.brain.snapshot()["questions"] if q["id"] == qid)
        self.assertEqual(question["status"], "resolved")
        self.assertEqual(question["resolved_by"], source["id"])
        self.assertEqual(self.brain.snapshot()["context"]["tracking"]["value"], "Planilha Obras A")
        self.assertEqual(self.brain.snapshot()["interview"]["deferred_topics"]["tracking"]["status"], "resolved")

    def gap(self, disposition=None, **extra):
        payload = {"schema_version": 1, "event_id": uid(), "device_id": uid(), "occurred_at": "2026-11-23T12:00:00+00:00",
                   "app": "PRIVATE ORIGINAL APP" if disposition else "Synthetic Unclassified Work Tool", "surface_kind": "desktop_window", "synthetic": True}
        if disposition:
            payload["privacy_disposition"] = disposition
        return {**payload, **extra}

    def test_F11_withheld_has_only_safe_inspectable_coverage_without_personal_inference(self):
        payload = self.gap("withheld")
        receipt = self.brain.ingest_context_gap(payload)
        self.assertEqual(receipt["state"], "coverage_only")
        self.assertEqual(self.brain.ingest_context_gap(payload), receipt)
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["questions"], [])
        self.assertEqual(snapshot["tasks"], [])
        self.assertEqual(len(snapshot["sources"]), 1)
        source = self.source(receipt["source_id"])
        self.assertEqual(source["safe_metadata"]["privacy_disposition"], "withheld")
        self.assertIs(source["safe_metadata"]["founder_question_eligible"], False)
        self.assertNotIn("classification", source)
        self.assertNotIn("PRIVATE ORIGINAL APP", str(self.brain.store.read().files))
        self.assertEqual(snapshot["coverage"][0]["reason"], "withheld")
        self.assertIn("não declara atividade pessoal", self.brain.store.read().files["views/cobertura.md"])

    def test_F11_unclassified_is_genuine_pending_but_personal_and_excluded_are_absent(self):
        for disposition in ("personal", "excluded"):
            with self.subTest(disposition=disposition):
                before = self.brain.store.read()
                result = self.brain.ingest_context_gap(self.gap(disposition))
                self.assertEqual(result["state"], "excluded")
                self.assertEqual(self.brain.store.read().files, before.files)
                self.assertEqual(self.brain.snapshot()["sources"], [])
        receipt = self.brain.ingest_context_gap(self.gap())
        self.assertEqual(receipt["state"], "pending_context")
        question = next(q for q in self.brain.snapshot()["questions"] if q["id"] == receipt["question_id"])
        self.assertEqual(question["status"], "pending")
        self.assertIn(question["id"], self.brain.message_targets()["open_question_ids"])
        self.assertNotIn("privacy_disposition", self.source(receipt["source_id"])["safe_metadata"])

    def test_F11_private_body_title_image_cannot_enter_safe_coverage_source(self):
        for extra in ({"text": "PRIVATE BODY"}, {"title": "PRIVATE TITLE"}, {"screenshot": "PRIVATE IMAGE"}):
            with self.subTest(extra=extra):
                before = self.brain.store.read()
                with self.assertRaisesRegex(BrainError, "safe_context_gap_schema_required"):
                    self.brain.ingest_context_gap(self.gap("withheld", **extra))
                self.assertEqual(self.brain.store.read().files, before.files)

    def test_F13_default_message_receipt_keeps_unrelated_tasks_out(self):
        tid, _ = self.observe("A equipe concilia os depósitos bancários.", changes={"action": "Conciliar depósitos bancários"}, name="Conciliar pagamentos bancários")
        deferred = self.brain.interview_defer("tracking", "Ainda não sei onde pagamentos e saldos são acompanhados.", uid())
        text = "Correção do relatório semanal: revise as fotos da vistoria."
        offered = self.brain.message_targets(text)
        self.assertEqual(offered["open_question_ids"], [])
        self.assertEqual(offered["correction_targets"], [])
        raw = self.brain.record_message(text, uid())
        self.assertEqual(raw["candidate_question_ids"], [])
        self.assertEqual(raw["candidate_task_versions"], [])
        related = self.brain.message_targets("pagamentos bancários")
        self.assertIn(tid, [item["task_id"] for item in related["correction_targets"]])
        self.assertIn(deferred["question_id"], related["open_question_ids"])

    def test_F13_explicit_receipt_scope_is_honored_but_never_widens_to_new_tasks(self):
        first, _ = self.observe("Preparar relatório semanal.", changes={"action": "Preparar relatório semanal"}, name="Preparar relatório semanal")
        second, _ = self.observe("Conciliar pagamentos bancários.", changes={"action": "Conciliar pagamentos bancários"}, name="Conciliar pagamentos")
        question = next(q for q in self.brain.snapshot()["questions"] if q["task_id"] == first and q["missing_links"] == ["purpose"])
        raw = self.brain.record_message("Correção: a finalidade é informar o cliente sobre a vistoria.", uid(),
            question_ids=[question["id"]], candidate_question_ids=[],
            candidate_task_versions=[{"id": first, "version": self.task(first)["version"]}])
        self.assertEqual([item["id"] for item in raw["candidate_task_versions"]], [first])
        self.assertEqual(raw["candidate_question_ids"], [question["id"]])
        third, _ = self.observe("Comprar ferramentas.", changes={"action": "Comprar ferramentas"}, name="Comprar ferramentas")
        source = self.source(raw["answer_id"])
        bad = {**empty(), "message_intent": self.intent(source, "correction", tasks=[third]),
               "tasks": [self.proposal(source, third, {"purpose": "Informar o cliente"})]}
        with self.assertRaisesRegex(BrainError, "message_selection_outside_receipt_scope"):
            self.brain.apply_analysis(raw["job_id"], bad, map_scope=self.scope())
        unselected = [copy.deepcopy(self.task(tid)) for tid in (second, third)]
        good = {**empty(), "message_intent": self.intent(source, "correction", tasks=[first], questions=[question["id"]]),
                "tasks": [self.proposal(source, first, {"purpose": "Informar o cliente sobre a vistoria"})]}
        self.brain.apply_analysis(raw["job_id"], good, map_scope=self.scope())
        self.assertEqual(self.task(first)["fields"]["purpose"]["value"], "Informar o cliente sobre a vistoria")
        self.assertEqual([self.task(tid) for tid in (second, third)], unselected)

    def test_F13_nonempty_anchorless_text_is_not_empty_discovery(self):
        tid, _ = self.observe("Conciliar pagamentos bancários.", changes={"action": "Conciliar pagamentos bancários"}, name="Conciliar pagamentos")
        deferred = self.brain.interview_defer("tracking", "Não sei onde pagamentos e saldos são acompanhados.", uid())
        question = deferred["question_id"]
        self.assertTrue(self.brain.message_targets("")["correction_targets"])
        self.assertIn(question, self.brain.message_targets("")["open_question_ids"])
        for text in ("isso", "ISSO", "essa com uma", "???", "   "):
            with self.subTest(text=text):
                targets = self.brain.message_targets(text)
                self.assertEqual(targets["correction_targets"], [])
                self.assertEqual(targets["open_question_ids"], [])
                if text.strip():
                    raw = self.brain.record_message(text, uid())
                    self.assertEqual(raw["candidate_question_ids"], [])
                    self.assertEqual(raw["candidate_task_versions"], [])
        scoped = self.brain.record_message("isso", uid(), question_ids=[question],
            candidate_task_versions=[{"id": tid, "version": self.task(tid)["version"]}])
        self.assertEqual(scoped["candidate_question_ids"], [question])
        self.assertEqual(scoped["candidate_task_versions"], [{"id": tid, "version": self.task(tid)["version"]}])

    def test_F13_frozen_correction_version_rejects_task_changed_after_receipt(self):
        tid, _ = self.observe("Preparar relatório semanal.", changes={"action": "Preparar relatório semanal"}, name="Preparar relatório semanal")
        raw = self.brain.record_message("Correção do relatório semanal: informar o cliente.", uid(),
            candidate_task_versions=[{"id": tid, "version": self.task(tid)["version"]}])
        source = self.source(raw["answer_id"])
        self.observe("O relatório deve ser entregue à revisão.", tid=tid, changes={"handoff": "Entregar à revisão"})
        before = copy.deepcopy(self.task(tid))
        output = {**empty(), "message_intent": self.intent(source, "correction", tasks=[tid]),
                  "tasks": [self.proposal(source, tid, {"purpose": "Informar o cliente"})]}
        with self.assertRaisesRegex(BrainError, "stale_message_correction"):
            self.brain.apply_analysis(raw["job_id"], output, map_scope=self.scope())
        self.assertEqual(self.task(tid), before)


if __name__ == "__main__":
    unittest.main()
