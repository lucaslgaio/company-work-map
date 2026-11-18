"""a31 public-seam outcome checks, synthetic only."""
import copy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, LocalStore
from company_pilot.inference import SemanticWorker
import test_engine as fixtures


def uid():
    return str(uuid4())


def empty():
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None}


def intent(source, kind, tasks=(), questions=(), disposition=None, delegate=None):
    return {"kind": kind, "task_ids": list(tasks), "question_ids": list(questions),
            "disposition": disposition, "delegate_to": delegate,
            "evidence": [{"source_id": source["id"], "quote": source["text"]}]}


class OutcomeRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética", synthetic=True)

    def source(self, sid):
        return next(s for s in self.brain.snapshot()["sources"] if s["id"] == sid)

    def task(self, complete=False):
        raw = self.brain.ingest_observation(fixtures.observation())
        self.brain.apply_analysis(raw["job_id"], fixtures.analysis(self.source(raw["observation_id"]), complete=complete))
        return self.brain.snapshot()["tasks"][0]

    def test_established_task_is_an_ordinary_message_target(self):
        task = self.task(True)
        self.assertFalse(any(q["status"] == "pending" for q in self.brain.snapshot()["questions"]))
        targets = self.brain.message_targets("corrigindo a transferência de ontem")
        self.assertIn(task["id"], [t["task_id"] for t in targets["correction_targets"]])
        raw = self.brain.record_message("Correção da transferência: a finalidade é quitar a revisão aprovada.", uid())
        source = self.source(raw["answer_id"])
        updated = fixtures.analysis(source, complete=True, task_id=task["id"])
        updated["tasks"][0]["fields"] = copy.deepcopy(task["fields"])
        updated["tasks"][0]["fields"]["purpose"] = {"value": "Quitar a revisão aprovada", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}
        updated["message_intent"] = intent(source, "correction", [task["id"]])
        snapshot = self.brain.snapshot()
        scope = {"tasks": [{"id": task["id"], "version": task["version"]}], "nodes": [], "edges": [], "source_ids": [s["id"] for s in snapshot["sources"]]}
        self.brain.apply_analysis(raw["job_id"], updated, map_scope=scope)
        self.assertEqual(self.brain.snapshot()["tasks"][0]["fields"]["purpose"]["value"], "Quitar a revisão aprovada")

    def test_same_source_topic_claims_and_grounded_names_accumulate(self):
        raw = self.brain.interview_answer("Usamos Planilha Ordens Oficina e Banco Empresas. A entrega depende da conferência da Ana.", uid())
        source = self.source(raw["source_id"])
        ref = [{"source_id": source["id"], "quote": source["text"]}]
        output = empty()
        output["context_updates"] = [{"topic": "tracking", "value": v, "basis": "reported", "evidence": ref, "mode": "add", "replaces": []} for v in ("Planilha Ordens Oficina", "Banco Empresas")]
        output["recognition_markers"] = [{"value": v, "kind": "record_name", "evidence": [{"source_id": source["id"], "quote": v}], "operation": "add"} for v in ("Planilha Ordens Oficina", "Banco Empresas")]
        self.brain.apply_analysis(raw["job_id"], output)
        snapshot = self.brain.snapshot()
        self.assertEqual(len([c for c in snapshot["context_claims"] if c["status"] == "active"]), 2)
        self.assertEqual(set(self.brain.device_policy()["work_signals"]), {"Planilha Ordens Oficina", "Banco Empresas"})

    def test_omission_does_not_replace_an_existing_completion_rule(self):
        raw = self.brain.interview_answer("As etapas são receber e entregar; Ana confere antes da entrega.", uid())
        source = self.source(raw["source_id"])
        claim = {"topic": "stages", "value": "Ana confere antes da entrega", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": "Ana confere antes da entrega"}], "mode": "add", "replaces": []}
        self.brain.apply_analysis(raw["job_id"], {**empty(), "context_updates": [claim]})
        later = self.brain.record_message("As etapas são receber, revisar e entregar.", uid())
        newer = self.source(later["answer_id"])
        update = {"topic": "stages", "value": "Receber, revisar e entregar", "basis": "reported", "evidence": [{"source_id": newer["id"], "quote": newer["text"]}], "mode": "add", "replaces": []}
        self.brain.apply_analysis(later["job_id"], {**empty(), "context_updates": [update], "message_intent": intent(newer, "context")})
        self.assertIn("Ana confere", self.brain.snapshot()["context"]["stages"]["value"])
        self.assertIn("revisar", self.brain.snapshot()["context"]["stages"]["value"])

    def test_chatter_keeps_source_without_creating_a_business_question(self):
        raw = self.brain.record_message("Obrigado, Nora!", uid())
        source = self.source(raw["answer_id"])
        self.brain.apply_analysis(raw["job_id"], {**empty(), "message_intent": intent(source, "chatter")})
        self.assertEqual(self.brain.snapshot()["questions"], [])
        self.assertTrue(self.source(raw["answer_id"]))

    def test_explicit_replacement_names_claim_ids_and_retains_old_claim(self):
        initial = self.brain.interview_answer("A entrega exige conferência da Ana.", uid())
        source = self.source(initial["source_id"])
        self.brain.apply_analysis(initial["job_id"], {**empty(), "context_updates": [{"topic": "stages", "value": "Conferência da Ana", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": "conferência da Ana"}], "mode": "add", "replaces": []}]})
        cid = self.brain.snapshot()["context_claims"][0]["id"]
        raw = self.brain.record_message("Correção: substitua a conferência da Ana pela conferência da Bia.", uid())
        current = self.source(raw["answer_id"])
        update = {"topic": "stages", "value": "Conferência da Bia", "basis": "reported", "evidence": [{"source_id": current["id"], "quote": current["text"]}], "mode": "replace", "replaces": []}
        with self.assertRaisesRegex(BrainError, "replacement_required"):
            self.brain.apply_analysis(raw["job_id"], {**empty(), "context_updates": [update], "message_intent": intent(current, "context")})
        update["replaces"] = [cid]
        self.brain.apply_analysis(raw["job_id"], {**empty(), "context_updates": [update], "message_intent": intent(current, "context")})
        final = self.brain.snapshot()
        self.assertEqual(final["context"]["stages"]["value"], "Conferência da Bia")
        self.assertEqual(next(c for c in final["context_claims"] if c["id"] == cid)["status"], "replaced")
        self.assertEqual(self.source(initial["source_id"])["text"], source["text"])

    def test_dismissal_survives_unrelated_task_update_without_hiding_gap(self):
        task = self.task()
        qid = next(q["id"] for q in self.brain.snapshot()["questions"] if q["missing_links"] == ["tracking_record"])
        raw = self.brain.record_message("Essa pergunta pode esperar; peça ao responsável depois.", uid(), question_ids=[qid])
        source = self.source(raw["answer_id"])
        self.brain.apply_analysis(raw["job_id"], {**empty(), "message_intent": intent(source, "delegate", questions=[qid], delegate="Mira")})
        disposition = next(q for q in self.brain.snapshot()["questions"] if q["id"] == qid)["response_disposition"]
        answer = self.brain.record_message("A finalidade da transferência é quitar o serviço aprovado.", uid())
        source = self.source(answer["answer_id"])
        output = fixtures.analysis(source, task_id=task["id"])
        output["tasks"][0]["fields"] = copy.deepcopy(task["fields"])
        output["tasks"][0]["fields"]["purpose"] = {"value": "Quitar o serviço aprovado", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}
        output["message_intent"] = intent(source, "answer", [task["id"]])
        self.brain.apply_analysis(answer["job_id"], output)
        question = next(q for q in self.brain.snapshot()["questions"] if q["id"] == qid)
        self.assertEqual(question["response_disposition"], disposition)
        self.assertEqual(question["status"], "awaiting_person")
        self.assertEqual(question["relevant_answer_ids"], [])
        self.assertEqual(self.brain.snapshot()["tasks"][0]["status"], "provisional")

    def test_known_payment_is_provisional_until_object_and_tracking_are_supported(self):
        raw = self.brain.ingest_observation(fixtures.observation())
        source = self.source(raw["observation_id"])
        output = fixtures.analysis(source, complete=True)
        output["tasks"][0]["payment"] = {key: {"value": None, "basis": "inferred", "evidence": []} for key in ("object", "tracking_record")}
        self.brain.apply_analysis(raw["job_id"], output)
        task = self.brain.snapshot()["tasks"][0]
        self.assertEqual(task["status"], "provisional")
        self.assertEqual(set(task["payment_missing"]), {"object", "tracking_record"})
        self.assertEqual({q["gap_kind"] for q in self.brain.snapshot()["questions"] if q["status"] == "pending"}, {"payment_object", "payment_tracking_record"})

    def test_actual_semantic_worker_corrects_established_task(self):
        task = self.task(True)
        raw = self.brain.record_message("Correção da transferência: a finalidade é quitar a revisão aprovada.", uid())
        class Interpreter:
            def interpret(inner, job, context):
                source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
                output = fixtures.analysis(source, task_id=task["id"])
                output["tasks"][0]["fields"] = copy.deepcopy(task["fields"])
                output["tasks"][0]["fields"]["purpose"] = {"value": "Quitar a revisão aprovada", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}
                output["message_intent"] = intent(source, "correction", [task["id"]])
                return output
        response = SemanticWorker(self.brain, Interpreter()).run_once()
        self.assertEqual(response["status"], "applied", response)
        self.assertEqual(response["receipt"]["selected_task_ids"], [task["id"]])
        self.assertEqual(self.brain.snapshot()["tasks"][0]["fields"]["purpose"]["value"], "Quitar a revisão aprovada")

    def test_not_company_dismissal_finishes_related_attribution_jobs_only(self):
        task = self.task()
        genuine = self.brain.snapshot()["questions"][0]["id"]
        original = self.brain.record_answer([], "Mensagem sem relação com a empresa.", uid(), candidate_question_ids=[genuine])
        self.brain.apply_analysis(original["job_id"], empty())
        qid = original["disambiguation_question_id"]
        followup = self.brain.record_answer([qid], "Não sei a que se refere.", uid())
        self.brain.apply_analysis(followup["job_id"], empty())
        self.assertEqual(len(self.brain.snapshot()["pending_jobs"]), 2)
        dismissal = self.brain.record_message("Isso não é assunto da empresa, descarte a pergunta.", uid(), question_ids=[qid])
        source = self.source(dismissal["answer_id"])
        self.brain.apply_analysis(dismissal["job_id"], {**empty(), "message_intent": intent(source, "dismiss", questions=[qid], disposition="not_company")})
        self.assertEqual(self.brain.snapshot()["pending_jobs"], [])
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == genuine)["status"], "pending")
        self.assertEqual(self.brain.snapshot()["tasks"][0], task)

    def test_coverage_summary_does_not_regress_on_older_event_or_equal_time_retry(self):
        import hashlib
        key = hashlib.sha256(b"opaque-fixture-context").hexdigest()
        device = uid()
        def gap(end, samples, windows, expired):
            return self.brain.ingest_context_gap({"schema_version": 1, "event_id": uid(), "device_id": device, "occurred_at": end,
                "app": "Synthetic Browser", "surface_kind": "desktop_window", "synthetic": True, "context_key": key,
                "coverage": {"period_start": "2026-11-17T12:00:00+00:00", "period_end": end, "observed_samples": samples, "distinct_windows": windows, "expired_unresolved": expired}})
        current = gap("2026-11-17T14:00:00+00:00", 4, 2, 1)
        gap("2026-11-17T13:00:00+00:00", 1, 1, 0)
        gap("2026-11-17T14:00:00+00:00", 3, 2, 0)
        question = next(q for q in self.brain.snapshot()["questions"] if q["id"] == current["question_id"])
        self.assertEqual(question["coverage"]["observed_samples"], 4)
        self.assertEqual(question["coverage"]["expired_unresolved"], 1)

    def test_payment_link_removal_is_provisional_and_explicit_reconnect_restores_it(self):
        raw = self.brain.ingest_observation(fixtures.observation("Joana pagou a revisão e registrou na Planilha da Oficina."))
        source = self.source(raw["observation_id"])
        output = fixtures.analysis(source, complete=True)
        supported = lambda value: {"value": value, "basis": "observed", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}
        output["tasks"][0]["payment"] = {"object": supported("Revisão aprovada"), "tracking_record": supported("Planilha da Oficina")}
        self.brain.apply_analysis(raw["job_id"], output)
        task = self.brain.snapshot()["tasks"][0]
        edge = next(e for e in self.brain.snapshot()["edges"] if e["kind"] == "pays_for")
        self.assertEqual(task["status"], "established")
        self.brain.change_map([{"op": "disconnect", "id": edge["id"]}], uid(), "Ainda não confirmei esse vínculo do pagamento.")
        self.assertEqual(self.brain.snapshot()["tasks"][0]["status"], "provisional")
        self.assertEqual(self.brain.snapshot()["tasks"][0]["payment_missing"], ["object"])
        self.brain.change_map([{"op": "connect", "from": edge["from"], "to": edge["to"], "kind": edge["kind"]}], uid(), "Confirmo de forma explícita o vínculo do pagamento.")
        self.assertEqual(self.brain.snapshot()["tasks"][0]["status"], "established")
