"""Report-derived E2/E3/E4/E9/E14 public-core guards; not a run replay.

Inputs and semantic proposals are explicit synthetic fixtures. No provider,
network, native collector or channel is invoked by this file.
"""
import copy
import hashlib
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, FIELDS, LocalStore


def uid():
    return str(uuid4())


def empty():
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None}


def refs(source):
    return [{"source_id": source["id"], "quote": source["text"]}]


def claim(source, value, basis=None):
    return {"value": value, "basis": basis or ("observed" if source["kind"] == "observation" else "reported"), "evidence": refs(source)}


class PublicCoreGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(self.temp.name))
        self.brain.initialize("Empresa sintética — guardas derivadas do relatório Grok-2", synthetic=True)

    def source(self, source_id):
        return next(source for source in self.brain.snapshot()["sources"] if source["id"] == source_id)

    def task(self, task_id):
        return next(task for task in self.brain.snapshot()["tasks"] if task["id"] == task_id)

    def question(self, task_id, field=None, gap_kind=None):
        return next(q for q in self.brain.snapshot()["questions"] if q["task_id"] == task_id
                    and (gap_kind is None or q.get("gap_kind") == gap_kind)
                    and (field is None or q.get("missing_links") == [field] and not q.get("gap_kind")))

    def observe(self, text, *, task_id=None, changes=None, when="2026-11-23T12:00:00+00:00", record="REC-A", updates=None, issues=None):
        raw = self.brain.ingest_observation({"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": when, "policy_version": self.brain.snapshot()["company"]["policy_version"],
            "source": {"kind": "active_window", "app": "Synthetic Work", "record_ref": record},
            "classification": {"label": "company", "reason": "fixture permitida e sintética do relatório"},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True})
        source = self.source(raw["observation_id"])
        fields = (copy.deepcopy(self.task(task_id)["fields"]) if task_id else
                  {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS})
        if not task_id:
            fields["action"] = claim(source, text)
        for field, value in (changes if changes is not None else {"result": text}).items():
            fields[field] = claim(source, value)
        candidate = {"task_id": task_id, "name": "Atividade sintética no registro " + str(record), "fields": fields, "links": []}
        if updates is not None:
            candidate["field_updates"] = {field: {"mode": mode, "effective_at": effective, "evidence": refs(source)}
                                          for field, mode, effective in updates}
        if issues is not None:
            candidate["consistency_issues"] = [{"description": description, "evidence": refs(source)} for description in issues]
        receipt = self.brain.apply_analysis(raw["job_id"], {**empty(), "tasks": [candidate]})
        return receipt["tasks"][0]["task_id"], source

    def message(self, text, question_ids=()):
        # Explicit test context keeps authority/lifecycle checks independent of topic retrieval.
        raw = self.brain.record_message(text, uid(), question_ids=list(question_ids),
            candidate_task_versions=[{"id": t["id"], "version": t["version"]} for t in self.brain.snapshot()["tasks"]])
        return raw, self.source(raw["answer_id"])

    def apply_message(self, raw, source, *, task_id=None, changes=None, questions=(), kind="answer", issues=None, resolutions=None, answers=None):
        output = empty()
        if task_id:
            task = self.task(task_id)
            fields = copy.deepcopy(task["fields"])
            for field, value in (changes or {}).items():
                fields[field] = claim(source, value)
            candidate = {"task_id": task_id, "name": task["name"], "fields": fields, "links": []}
            if issues is not None:
                candidate["consistency_issues"] = copy.deepcopy(issues)
            if resolutions is not None:
                candidate["issue_resolutions"] = copy.deepcopy(resolutions)
            output["tasks"] = [candidate]
        output["message_intent"] = {"kind": kind, "task_ids": [task_id] if task_id else [], "question_ids": list(questions),
            "delegate_to": None, "disposition": None, "evidence": refs(source)}
        if answers is not None:
            output["question_answers"] = answers
        snapshot = self.brain.snapshot()
        scope = {group: [{"id": row["id"], "version": row["version"]} for row in snapshot[group]] for group in ("tasks", "nodes", "edges")}
        scope["source_ids"] = [row["id"] for row in snapshot["sources"]]
        return self.brain.apply_analysis(raw["job_id"], output, map_scope=scope)

    def seed_conflict(self):
        task_id, first = self.observe("Cronograma: Em andamento, 80%.", changes={"result": "Em andamento, 80%"})
        self.observe("Outro relato marcou Concluído.", task_id=task_id, changes={"result": "Concluído"}, when="2026-11-24T12:00:00+00:00")
        return task_id, first, self.question(task_id, "result")["id"]

    def test_E2_current_answer_changes_asked_field_but_unsolicited_other_change_conflicts(self):
        task_id, _, qid = self.seed_conflict()
        # Give the unasked purpose a supported existing value first.
        purpose = self.question(task_id, "purpose")["id"]
        raw, source = self.message("A finalidade é informar o cliente.", [purpose])
        self.apply_message(raw, source, task_id=task_id, changes={"purpose": "Informar o cliente"}, questions=[purpose])
        raw, source = self.message("O resultado correto é 60%; a finalidade seria outra.", [qid])
        self.apply_message(raw, source, task_id=task_id, changes={"result": "Em andamento, 60%", "purpose": "Outra finalidade"}, questions=[qid])
        current = self.task(task_id)
        self.assertEqual(current["fields"]["result"]["value"], "Em andamento, 60%")
        self.assertNotIn("result", current["conflicts"])
        self.assertEqual(current["fields"]["purpose"]["value"], "Informar o cliente")
        self.assertIn("purpose", current["conflicts"])
        self.assertEqual(self.question(task_id, "result")["status"], "resolved")

    def test_E2_unsolicited_contradictory_answer_cannot_replace_current_truth(self):
        task_id, _ = self.observe("Cronograma em 80%.", changes={"result": "80%"})
        raw, source = self.message("Talvez fosse só 60%.")
        self.apply_message(raw, source, task_id=task_id, changes={"result": "60%"})
        self.assertEqual(self.task(task_id)["fields"]["result"]["value"], "80%")
        self.assertEqual(self.task(task_id)["conflicts"]["result"]["reported_candidate"]["value"], "60%")

    def test_E2_stale_scoped_reply_cannot_clear_a_newer_conflict_generation(self):
        task_id, _, qid = self.seed_conflict()
        old, old_source = self.message("Eu tinha entendido 60%.", [qid])
        self.observe("Nova vistoria informa 40% e contradiz o encerramento.", task_id=task_id,
                     changes={"result": "40%"}, when="2026-11-25T12:00:00+00:00")
        before = copy.deepcopy(self.task(task_id))
        self.apply_message(old, old_source, task_id=task_id, changes={"result": "60%"}, questions=[qid])
        current = self.task(task_id)
        self.assertEqual(current["fields"]["result"], before["fields"]["result"])
        self.assertIn("result", current["conflicts"])
        self.assertEqual(self.question(task_id, "result")["status"], "pending")

    def test_E2_empty_context_answer_reports_unresolved_and_partial_reply_is_partial(self):
        gap = self.brain.ingest_context_gap({"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": "2026-11-23T12:00:00+00:00", "app": "Synthetic Context", "surface_kind": "desktop_window", "synthetic": True})
        raw, source = self.message("Ainda não expliquei isso.", [gap["question_id"]])
        receipt = self.apply_message(raw, source, questions=[gap["question_id"]])
        self.assertEqual(receipt["state"], "awaiting_clarification")
        self.assertEqual(receipt["question_outcomes"][0]["status"], "unresolved")
        self.assertEqual(receipt["question_outcomes"][0]["reason"], "selected_question_not_substantively_resolved")
        task_id, _ = self.observe("Atividade com entrada ainda desconhecida.")
        purpose, handoff = [self.question(task_id, field)["id"] for field in ("purpose", "handoff")]
        raw, source = self.message("Serve para preparar o relatório.", [purpose, handoff])
        receipt = self.apply_message(raw, source, task_id=task_id, changes={"purpose": "Preparar relatório"}, questions=[purpose, handoff])
        self.assertEqual(receipt["state"], "partially_applied")
        self.assertEqual({row["question_id"]: row["status"] for row in receipt["question_outcomes"]}, {purpose: "resolved", handoff: "unresolved"})

    def test_E2_unrelated_field_refresh_does_not_invalidate_the_asked_uncertainty(self):
        task_id, _, result_qid = self.seed_conflict()
        old, old_source = self.message("Confirmo que o resultado correto era 60%.", [result_qid])
        purpose = self.question(task_id, "purpose")["id"]
        raw, source = self.message("A finalidade é preparar o relatório.", [purpose])
        self.apply_message(raw, source, task_id=task_id, changes={"purpose": "Preparar relatório"}, questions=[purpose])
        self.apply_message(old, old_source, task_id=task_id, changes={"result": "60%"}, questions=[result_qid])
        self.assertEqual(self.task(task_id)["fields"]["result"]["value"], "60%")
        self.assertNotIn("result", self.task(task_id)["conflicts"])
        self.assertEqual(self.task(task_id)["fields"]["purpose"]["value"], "Preparar relatório")

    def test_E2_partial_named_context_reply_leaves_the_other_app_pending(self):
        questions = []
        for app in ("Synthetic Meeting Tool", "Synthetic Email Tool"):
            gap = self.brain.ingest_context_gap({"schema_version": 1, "event_id": uid(), "device_id": uid(),
                "occurred_at": "2026-11-23T12:00:00+00:00", "app": app, "surface_kind": "desktop_window", "synthetic": True})
            questions.append(gap["question_id"])
        raw, source = self.message("O aplicativo de reunião transcreve as reuniões.", questions)
        receipt = self.apply_message(raw, source, questions=questions,
            answers=[{"question_id": questions[0], "claim": claim(source, "Transcrição das reuniões")}])
        self.assertEqual(receipt["state"], "partially_applied")
        self.assertEqual({row["question_id"]: row["status"] for row in receipt["question_outcomes"]},
                         {questions[0]: "resolved", questions[1]: "unresolved"})

    def test_E3_explicit_progress_needs_same_record_and_later_timestamp_alone_is_not_authority(self):
        for update, record, expected in ((None, "REC-A", "80%"), ([("result", "progress", None)], "REC-B", "80%"),
                                         ([("result", "progress", None)], "REC-A", "100%")):
            with self.subTest(update=update, record=record):
                task_id, _ = self.observe("Cronograma em 80%.", record="REC-A", changes={"result": "80%"})
                self.observe("Cronograma em 100%.", task_id=task_id, record=record,
                             changes={"result": "100%"}, when="2026-11-26T12:00:00+00:00", updates=update)
                self.assertEqual(self.task(task_id)["fields"]["result"]["value"], expected)
                self.assertEqual("result" in self.task(task_id)["conflicts"], expected == "80%")

    def test_E3_same_time_disagreement_and_reported_founder_fact_stay_conflicts(self):
        task_id, _ = self.observe("Cronograma em 80%.", changes={"result": "80%"})
        self.observe("Outra tela diz 60% no mesmo instante.", task_id=task_id, changes={"result": "60%"},
                     updates=[("result", "progress", None)])
        self.assertEqual(self.task(task_id)["fields"]["result"]["value"], "80%")
        qid = self.question(task_id, "result")["id"]
        raw, source = self.message("Confirmo que era 70%.", [qid])
        self.apply_message(raw, source, task_id=task_id, changes={"result": "70%"}, questions=[qid])
        self.observe("Tela posterior diz 100%.", task_id=task_id, changes={"result": "100%"}, when="2026-11-27T12:00:00+00:00",
                     updates=[("result", "progress", None)])
        self.assertEqual(self.task(task_id)["fields"]["result"]["value"], "70%")
        self.assertEqual(self.task(task_id)["fields"]["result"]["basis"], "reported")
        self.assertIn("result", self.task(task_id)["conflicts"])

    def test_E3_delayed_older_progress_is_history_and_does_not_regress_current(self):
        task_id, _ = self.observe("Cronograma em 30%.", changes={"result": "30%"})
        self.observe("Cronograma avançou para 100%.", task_id=task_id, changes={"result": "100%"}, when="2026-11-27T12:00:00+00:00",
                     updates=[("result", "progress", None)])
        self.observe("Registro antigo de 60% chegou atrasado.", task_id=task_id, changes={"result": "60%"}, when="2026-11-25T12:00:00+00:00",
                     updates=[("result", "progress", None)])
        self.assertEqual(self.task(task_id)["fields"]["result"]["value"], "100%")
        self.assertNotIn("result", self.task(task_id)["conflicts"])
        self.assertTrue(any(row["claim"]["value"] == "60%" for row in self.task(task_id)["progress_history"]))

    def test_E3_effective_order_wins_over_newer_ingestion_and_future_effective_time_is_rejected(self):
        task_id, _ = self.observe("Cronograma em 30%.", changes={"result": "30%"})
        self.observe("Cronograma avançou para 100%.", task_id=task_id, changes={"result": "100%"}, when="2026-11-25T12:00:00+00:00",
                     updates=[("result", "progress", None)])
        self.observe("Documento recebido hoje descreve 60% de ontem.", task_id=task_id, changes={"result": "60%"},
                     when="2026-11-27T12:00:00+00:00", updates=[("result", "progress", "2026-11-24T12:00:00+00:00")])
        self.assertEqual(self.task(task_id)["fields"]["result"]["value"], "100%")
        before = copy.deepcopy(self.task(task_id))
        with self.assertRaisesRegex(BrainError, "effective_time_after_observation"):
            self.observe("Proposta indevida de fato futuro.", task_id=task_id, changes={"result": "Futuro"},
                         when="2026-11-27T12:00:00+00:00", updates=[("result", "progress", "2026-11-28T12:00:00+00:00")])
        self.assertEqual(self.task(task_id), before)

    def test_E4_empty_issue_list_even_in_correction_is_not_resolution(self):
        task_id, _ = self.observe("Cronograma e vistoria divergem.", issues=["Conclusão sem conferência."])
        before = copy.deepcopy(self.task(task_id)["consistency_issues"])
        qid = self.question(task_id, gap_kind="business_consistency")["id"]
        raw, source = self.message("Corrijo só a finalidade, para orientar a equipe.", [qid])
        self.apply_message(raw, source, task_id=task_id, changes={"purpose": "Orientar equipe"}, questions=[qid], kind="correction", issues=[])
        self.assertEqual(self.task(task_id)["consistency_issues"], before)
        self.assertEqual(self.question(task_id, gap_kind="business_consistency")["status"], "pending")

    def test_E4_only_named_current_issue_and_current_founder_quote_can_resolve(self):
        task_id, source = self.observe("Há duas divergências: conclusão e entrega.", issues=["Conclusão sem conferência.", "Entrega sem responsável."])
        issues = copy.deepcopy(self.task(task_id)["consistency_issues"])
        qid = self.question(task_id, gap_kind="business_consistency")["id"]
        raw, founder = self.message("Bia confirmou a conclusão.", [qid])
        before = copy.deepcopy(self.task(task_id))
        for resolution in ({"issue_ids": [uid()], "resolution": claim(founder, "Confirmado")},
                           {"issue_ids": [issues[0]["id"]], "resolution": claim(source, "Confirmado", "reported")}):
            with self.subTest(resolution=resolution), self.assertRaises(BrainError):
                self.apply_message(raw, founder, task_id=task_id, questions=[qid], resolutions=[resolution])
            self.assertEqual(self.task(task_id), before)
        self.apply_message(raw, founder, task_id=task_id, questions=[qid],
            resolutions=[{"issue_ids": [issues[0]["id"]], "resolution": claim(founder, "Bia confirmou a conclusão.")}])
        self.assertEqual([issue["id"] for issue in self.task(task_id)["consistency_issues"]], [issues[1]["id"]])
        self.assertEqual(len(self.task(task_id)["resolved_issues"]), 1)
        self.assertEqual(self.question(task_id, gap_kind="business_consistency")["status"], "pending")

    def test_E4_old_issue_evidence_never_reopens_but_new_contradictory_source_can(self):
        description = "Conclusão sem conferência."
        task_id, old_source = self.observe("Cronograma concluído; vistoria incompleta.", issues=[description])
        issue = copy.deepcopy(self.task(task_id)["consistency_issues"][0])
        qid = self.question(task_id, gap_kind="business_consistency")["id"]
        raw, founder = self.message("Bia conferiu todos os ambientes e concluiu.", [qid])
        self.apply_message(raw, founder, task_id=task_id, questions=[qid], resolutions=[{"issue_ids": [issue["id"]], "resolution": claim(founder, "Bia conferiu todos os ambientes.")}])
        self.assertEqual(self.question(task_id, gap_kind="business_consistency")["status"], "resolved")
        self.observe("Atualização de outro campo, sem nova divergência.", task_id=task_id, changes={}, when="2026-11-24T12:00:00+00:00")
        replay, source = self.message("Só relembro o registro antigo.")
        self.apply_message(replay, source, task_id=task_id,
            issues=[{"description": description, "evidence": refs(old_source)}])
        self.assertEqual(self.task(task_id)["consistency_issues"], [])
        self.assertEqual(self.question(task_id, gap_kind="business_consistency")["status"], "resolved")
        self.observe("Nova vistoria encontrou outro ambiente incompleto.", task_id=task_id, changes={},
                     when="2026-11-27T12:00:00+00:00", issues=[description])
        self.assertEqual(self.task(task_id)["consistency_issues"][0]["id"], issue["id"])
        self.assertEqual(self.question(task_id, gap_kind="business_consistency")["status"], "pending")

    def test_E9_question_quotes_real_sources_and_known_facts_do_not_cross_record_names(self):
        same, _ = self.observe("Registro A: vistoria técnica conferida.", record="REC-A", changes={"result": "Conferido", "purpose": "Validar o serviço"})
        other, _ = self.observe("Registro B: pagamento exclusivo do outro contrato.", record="REC-B", changes={"result": "Pago", "purpose": "Finalidade exclusiva B"})
        target, _ = self.observe("Registro A: falta confirmar o próximo responsável.", record="REC-A", changes={})
        question = self.question(target, "handoff")
        self.assertTrue(question["evidence"])
        for ref in question["evidence"]:
            self.assertIn(ref["quote"], self.source(ref["source_id"])["text"])
            self.assertEqual(ref["locator"], "sources/" + ref["source_id"] + ".json")
        known_tasks = {fact["task_id"] for fact in question["known_facts"]}
        self.assertIn(same, known_tasks)
        self.assertNotIn(other, known_tasks)
        self.assertFalse(any(fact["claim"]["value"] == "Finalidade exclusiva B" for fact in question["known_facts"]))
        self.assertEqual(question["observed_context"]["record_ref"], "REC-A")

    def test_E14_personal_and_excluded_leave_no_source_app_or_question(self):
        for disposition in ("personal", "excluded"):
            with self.subTest(disposition=disposition):
                before = self.brain.store.read()
                result = self.brain.ingest_context_gap({"schema_version": 1, "event_id": uid(), "device_id": uid(),
                    "occurred_at": "2026-11-23T12:00:00+00:00", "app": "PRIVATE Instagram profile title",
                    "surface_kind": "desktop_window", "synthetic": True, "privacy_disposition": disposition})
                self.assertEqual(result["state"], "excluded")
                self.assertEqual(self.brain.snapshot()["sources"], [])
                self.assertEqual(self.brain.snapshot()["questions"], [])
                self.assertEqual(self.brain.store.read().files, before.files)
                self.assertEqual(self.brain.store.read().head, before.head)

    def test_E14_withheld_is_sanitized_unknown_and_absent_privacy_does_not_mean_personal(self):
        for disposition in ("withheld", None):
            with self.subTest(disposition=disposition):
                payload = {"schema_version": 1, "event_id": uid(), "device_id": uid(), "occurred_at": "2026-11-23T12:00:00+00:00",
                    "app": "Withheld original app" if disposition else "Synthetic Unknown Tool", "surface_kind": "desktop_window", "synthetic": True}
                if disposition:
                    payload["privacy_disposition"] = disposition
                result = self.brain.ingest_context_gap(payload)
                raw = self.source(result["source_id"])
                if disposition:
                    self.assertEqual(result["state"], "coverage_only")
                    self.assertNotIn("question_id", result)
                    self.assertEqual(raw["safe_metadata"]["app"], "Aplicativo não compartilhado")
                    self.assertNotIn("Withheld original app", str(self.brain.store.read().files))
                else:
                    question = next(q for q in self.brain.snapshot()["questions"] if q["id"] == result["question_id"])
                    self.assertEqual(result["state"], "pending_context")
                    self.assertEqual(question["status"], "pending")
                    self.assertNotIn("privacy_disposition", raw["safe_metadata"])


if __name__ == "__main__":
    unittest.main()
