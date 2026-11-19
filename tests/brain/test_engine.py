"""Representative synthetic flows; no network, company facts or OS capture."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from company_pilot.brain import AccessDenied, Brain, BrainError, FIELDS, IntegrityError, LocalStore


def uid():
    return str(uuid.uuid4())


def observation(text="Joana abriu uma fatura e salvou um comprovante."):
    return {"schema_version": 1, "event_id": uid(), "device_id": uid(), "occurred_at": "2026-11-16T12:00:00+00:00", "policy_version": 1,
            "source": {"kind": "active_window", "app": "Synthetic Editor", "record_ref": "SYN-01", "origin": "fixture"},
            "classification": {"label": "company", "reason": "documento sintético da empresa", "context_version": 1},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}


def analysis(source, complete=False, task_id=None, performer="Joana"):
    fields = {}
    for field in FIELDS:
        value = (performer if field == "performer" else field + " sintético") if complete or field not in ("purpose", "placement", "handoff", "tracking_record") else None
        fields[field] = {"value": value, "basis": ("observed" if source["kind"] == "observation" else "reported") if value else "inferred",
                         "evidence": [{"source_id": source["id"], "quote": source["text"]}] if value else []}
    return {"tasks": [{"task_id": task_id, "name": "Registrar transferência sintética", "fields": fields, "links": []}], "nodes": [], "context_updates": [], "next_question": None}


class BrainFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brain = Brain(LocalStore(self.root))
        self.brain.initialize("Empresa sintética", synthetic=True)

    def tearDown(self):
        self.temp.cleanup()

    def task(self):
        receipt = self.brain.ingest_observation(observation())
        source = next(x for x in self.brain.snapshot()["sources"] if x["id"] == receipt["observation_id"])
        self.brain.apply_analysis(receipt["job_id"], analysis(source))
        return self.brain.snapshot()["tasks"][0]

    def test_complete_task_clarification_cycle_and_replays(self):
        payload = observation()
        receipt = self.brain.send_observation(payload)
        self.assertEqual(receipt, self.brain.send_observation(payload))
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["pending_jobs"][0]["age_seconds"], 0)
        self.assertEqual(snapshot["sources"][0]["actor"], "fixture-founder")
        source = snapshot["sources"][0]
        first = self.brain.apply_analysis(receipt["job_id"], analysis(source))
        self.assertEqual(first, self.brain.apply_analysis(receipt["job_id"], analysis(source, complete=True)))
        task = self.brain.snapshot()["tasks"][0]
        self.assertEqual(task["status"], "provisional")
        self.assertEqual(len(self.brain.snapshot()["questions"]), 4)
        questions = [q["id"] for q in self.brain.snapshot()["questions"]]
        answer_event = uid()
        raw = self.brain.record_answer(questions, "A finalidade, encaixe, repasse e controle foram explicados pelo fundador.", answer_event)
        self.assertEqual(raw, self.brain.record_answer(questions, "A finalidade, encaixe, repasse e controle foram explicados pelo fundador.", answer_event))
        answer = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["answer_id"])
        candidate = analysis(answer, complete=True, task_id=task["id"])
        for field in ("trigger", "performer", "input", "action", "result"):
            candidate["tasks"][0]["fields"][field] = copy.deepcopy(task["fields"][field])
        self.brain.apply_analysis(raw["job_id"], candidate)
        final = self.brain.snapshot()
        self.assertEqual(final["tasks"][0]["status"], "established")
        self.assertTrue(all(q["status"] == "resolved" for q in final["questions"]))
        self.assertFalse(final["pending_jobs"])
        self.assertEqual(len([edge for edge in final["edges"] if edge["active"]]), 4)
        files = self.brain.store.read().files
        self.assertIn(f"records/tasks/{task['id']}/v000001.json", files)
        self.assertIn("O que a empresa faz", files["README.md"])
        self.assertIn("Exemplo sintético", files["README.md"])
        self.assertIn(raw["answer_id"], final["tasks"][0]["evidence_ids"])

    def test_actor_override_personal_and_changed_replay_denied(self):
        payload = observation()
        with self.assertRaises(BrainError):
            self.brain.ingest_observation({**payload, "actor": "other-person"})
        with self.assertRaises(BrainError):
            self.brain.ingest_observation({**payload, "classification": {**payload["classification"], "label": "personal"}})
        self.brain.ingest_observation(payload)
        changed = copy.deepcopy(payload)
        changed["source"]["app"] = "Other"
        with self.assertRaisesRegex(BrainError, "idempotency"):
            self.brain.ingest_observation(changed)

    def test_no_evidence_no_established_and_unsupported_source_rejected(self):
        receipt = self.brain.ingest_observation(observation())
        source = self.brain.snapshot()["sources"][0]
        result = analysis(source, complete=True)
        result["tasks"][0]["fields"]["purpose"]["evidence"][0]["quote"] = "Fabricated text"
        with self.assertRaisesRegex(BrainError, "quote_not_found"):
            self.brain.apply_analysis(receipt["job_id"], result)
        self.assertEqual(self.brain.snapshot()["tasks"], [])
        result = analysis(source, complete=True)
        result["tasks"][0]["fields"]["purpose"]["basis"] = "inferred"
        self.brain.apply_analysis(receipt["job_id"], result)
        self.assertEqual(self.brain.snapshot()["tasks"][0]["status"], "provisional")

    def test_stale_conflicting_answer_preserved_then_explicit_correction(self):
        task = self.task()
        questions = [q["id"] for q in self.brain.snapshot()["questions"]]
        self.brain.change_map([{"op": "reassign", "id": task["id"], "performer": "Mira"}], uid(), "A execução foi atribuída a Mira.")
        raw = self.brain.record_answer(questions, "Joana executa o trabalho segundo o relato antigo.", uid(), {task["id"]: 1})
        source = next(x for x in self.brain.snapshot()["sources"] if x["id"] == raw["answer_id"])
        self.brain.apply_analysis(raw["job_id"], analysis(source, complete=True, task_id=task["id"], performer="Joana"))
        task = self.brain.snapshot()["tasks"][0]
        self.assertEqual(task["fields"]["performer"]["value"], "Mira")
        self.assertIn("performer", task["conflicts"])
        self.assertEqual(task["status"], "provisional")
        self.brain.change_map([{"op": "correct", "id": task["id"], "fields": {"performer": "Mira"}}], uid(), "Correção explícita: Mira é a executora.")
        current = self.brain.snapshot()["tasks"][0]
        self.assertNotIn("performer", current["conflicts"])
        self.assertIn(raw["answer_id"], current["evidence_ids"])

    def test_split_and_merge_preserve_every_source(self):
        task = self.task()
        before = self.brain.store.read().head
        invalid = {"op": "split", "id": task["id"], "successors": [{"name": "A", "evidence_ids": []}, {"name": "B", "evidence_ids": []}]}
        with self.assertRaisesRegex(BrainError, "allocation"):
            self.brain.change_map([invalid], uid(), "Dividir o trabalho.")
        self.assertEqual(self.brain.store.read().head, before)
        successors = [{"name": name, "evidence_ids": task["evidence_ids"]} for name in ("Preparar transferência", "Registrar comprovante")]
        split = self.brain.change_map([{"op": "split", "id": task["id"], "successors": successors}], uid(), "Dividir preparação e registro.")
        current = self.brain.snapshot()
        active = [item for item in current["tasks"] if not item["superseded_by"]]
        self.assertEqual(len(active), 2)
        all_sources = sorted({sid for item in active for sid in item["evidence_ids"]})
        self.brain.change_map([{"op": "merge", "ids": split["changed_ids"], "successors": [{"name": "Fluxo financeiro", "evidence_ids": all_sources}]}], uid(), "Unificar novamente sem perder fontes.")
        active = [item for item in self.brain.snapshot()["tasks"] if not item["superseded_by"]]
        self.assertEqual(len(active), 1)
        self.assertTrue(set(task["evidence_ids"]) <= set(active[0]["evidence_ids"]))

    def test_concurrent_observations_do_not_lose_events(self):
        payloads = [observation(f"Documento sintético {index}") for index in range(4)]
        def ingest(payload):
            return Brain(LocalStore(self.root), retries=12).ingest_observation(payload)
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(ingest, payloads))
        self.assertEqual(len({r["observation_id"] for r in receipts}), 4)
        self.assertEqual(len(self.brain.snapshot()["pending_jobs"]), 4)

    def test_batch_is_one_atomic_commit_and_source_replay_has_no_churn(self):
        payloads = [observation(f"Registro sintético {index}") for index in range(12)]
        before = self.brain.store.read().files
        results = self.brain.ingest_batch(payloads)
        files = self.brain.store.read().files
        self.assertEqual(len([p for p in files if p.startswith("ledger/")]) - len([p for p in before if p.startswith("ledger/")]), 1)
        head = self.brain.store.read().head
        self.assertEqual(self.brain.ingest_batch(payloads, uid()), results)
        self.assertEqual(self.brain.store.read().head, head)
        broken = [observation(), {**observation(), "actor": "forged"}]
        with self.assertRaises(BrainError):
            self.brain.ingest_batch(broken)
        self.assertEqual(self.brain.store.read().head, head)

    def test_screenshot_policy_separates_founder_and_team_and_revokes(self):
        data = b"\x89PNG\r\n\x1a\nSYNTHETIC-TEST-ONLY"
        sha = hashlib.sha256(data).hexdigest()
        self.brain.update_policy({"screenshots": {"founder": False, "team": False}}, uid())
        with self.assertRaises(AccessDenied):
            self.brain.upload_attachment(uid(), data, sha)
        self.brain.update_policy({"screenshots": {"founder": True, "team": False}}, uid())
        team = Brain(LocalStore(self.root, actor="fixture-team", permissions={"read", "write"}))
        self.assertFalse(team.device_policy()["screenshots_allowed"])
        with self.assertRaises(AccessDenied):
            team.upload_attachment(uid(), data, sha)
        payload = observation()
        payload["policy_version"] = 3
        uploaded = self.brain.upload_attachment(payload["event_id"], data, sha)
        payload["screenshot"] = {"attachment_id": uploaded["attachment_id"], "sha256": sha, "media_type": "image/png"}
        self.brain.update_policy({"screenshots": {"founder": False, "team": False}}, uid())
        payload["policy_version"] = 4
        with self.assertRaises(AccessDenied):
            self.brain.send_observation(payload)

    def test_nora_attestation_and_unassigned_reply(self):
        self.task()
        self.brain.update_policy({"founders": ["fixture-founder", "real-founder"], "nora_actors": ["fixture-founder", "nora-service"]}, uid())
        route = {"id": "route-test", "account": "company", "conversation": "group-test", "founder_sender": "verified-sender", "founder_actor_id": "real-founder", "enabled": True}
        self.brain.transact_nora(lambda state: state.update(route=route))
        nora = Brain(LocalStore(self.root, actor="nora-service", permissions={"read", "write"}))
        metadata = {"kind": "nora", "route_id": "route-test", "message_id": "message-test", "account": "company", "conversation": "group-test", "sender": "verified-sender"}
        answer = nora.record_answer([], "Resposta ainda não correlacionada.", uid(), source=metadata)
        self.assertEqual(answer["speaker"], "real-founder")
        self.assertEqual(answer["attested_by"], "nora-service")
        self.assertEqual(answer["state"], "awaiting_disambiguation")
        with self.assertRaises(AccessDenied):
            nora.record_answer([], "Remetente errado.", uid(), source={**metadata, "sender": "wrong"})
        raw = next(s for s in self.brain.snapshot()["sources"] if s["id"] == answer["answer_id"])
        self.assertEqual(raw["channel"]["message_id"], "message-test")

    def test_empty_nora_poll_has_no_new_commit(self):
        before = self.brain.store.read().head
        self.assertEqual(self.brain.transact_nora(lambda state: {"empty": True}), {"empty": True})
        self.assertEqual(self.brain.store.read().head, before)

    def test_model_unavailable_queue_visible_and_retries_back_off(self):
        receipt = self.brain.ingest_observation(observation())
        self.brain.mark_job_error(receipt["job_id"], "subscription_unavailable")
        self.assertEqual(self.brain.pending_jobs(), [])
        job = self.brain.snapshot()["pending_jobs"][0]
        self.assertEqual(job["attempts"], 1)
        self.assertIn("next_attempt_at", job)
        self.assertIn("Falha no processamento", self.brain.store.read().files["views/fila.md"])

    def test_safe_unknown_context_routes_without_inventing_a_company_task(self):
        payload = {"schema_version": 1, "event_id": uid(), "device_id": uid(), "occurred_at": "2026-11-16T12:00:00Z", "app": "synthetic.editor", "surface_kind": "desktop_window", "synthetic": True}
        gap = self.brain.ingest_context_gap(payload)
        self.assertEqual(gap, self.brain.ingest_context_gap(payload))
        self.assertEqual(self.brain.snapshot()["tasks"], [])
        self.assertEqual(len(self.brain.snapshot()["questions"]), 1)
        with self.assertRaises(BrainError):
            self.brain.ingest_context_gap({**payload, "title": "Unclassified private text"})
        raw = self.brain.record_answer([gap["question_id"]], "Os registros do projeto fictício Aurora identificam nosso trabalho nesse aplicativo.", uid())
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["answer_id"])
        self.brain.apply_analysis(raw["job_id"], {"tasks": [], "nodes": [], "context_updates": [{"topic": "activities", "value": "Registros do projeto fictício Aurora", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}], "next_question": None})
        self.assertEqual(self.brain.snapshot()["questions"][0]["status"], "resolved")
        self.assertIn("Registros do projeto fictício Aurora", self.brain.device_policy()["work_signals"])

    def test_interview_adapts_and_reply_replay_keeps_sources(self):
        question = self.brain.interview_next()
        self.assertEqual(question["topic"], "activities")
        event_id = uid()
        raw = self.brain.interview_answer("Somos uma agência de tradução; revisamos textos.", event_id)
        self.assertEqual(raw, self.brain.interview_answer("Somos uma agência de tradução; revisamos textos.", event_id))
        source = next(x for x in self.brain.snapshot()["sources"] if x["id"] == raw["source_id"])
        result = {"tasks": [], "nodes": [{"kind": "process", "name": "Revisão de textos", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}],
                  "context_updates": [{"topic": "activities", "value": "Tradução e revisão", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}],
                  "next_question": {"topic": "people", "text": "Quem traduz e quem revisa os textos?"}}
        self.brain.apply_analysis(raw["job_id"], result)
        self.assertEqual(self.brain.interview_next()["question"], "Quem traduz e quem revisa os textos?")
        self.assertEqual(self.brain.snapshot()["nodes"][0]["name"], "Revisão de textos")
        with tempfile.TemporaryDirectory() as other:
            alternative = Brain(LocalStore(other))
            alternative.initialize("Oficina sintética", synthetic=True)
            raw2 = alternative.interview_answer("Consertamos bicicletas e controlamos peças.", uid())
            source2 = alternative.snapshot()["sources"][0]
            result2 = copy.deepcopy(result)
            result2["nodes"][0].update(name="Reparo de bicicletas", evidence=[{"source_id": source2["id"], "quote": source2["text"]}])
            result2["context_updates"][0].update(value="Conserto de bicicletas", evidence=[{"source_id": source2["id"], "quote": source2["text"]}])
            result2["next_question"]["text"] = "Quem recebe a bicicleta e quem executa o reparo?"
            alternative.apply_analysis(raw2["job_id"], result2)
            self.assertNotEqual(alternative.interview_next()["question"], self.brain.interview_next()["question"])

    def test_immutable_source_and_retained_record_cannot_change(self):
        self.task()
        before = self.brain.store.read()
        files = dict(before.files)
        path = next(p for p in files if p.startswith("sources/"))
        files[path] = "{}"
        with self.assertRaises(IntegrityError):
            self.brain.store.commit(before.head, files, "rewrite")

    def test_non_worker_cannot_process_other_members(self):
        self.brain.ingest_observation(observation())
        team = Brain(LocalStore(self.root, actor="team", permissions={"read", "write"}))
        with self.assertRaises(AccessDenied):
            team.pending_jobs()
        viewer = Brain(LocalStore(self.root, actor="reader", permissions={"read"}))
        self.assertEqual(len(viewer.snapshot()["pending_jobs"]), 1)
        with self.assertRaises(AccessDenied):
            viewer.ingest_observation(observation("Leitura somente"))

    def test_question_revision_tracks_every_projected_change(self):
        payload = {"schema_version": 1, "event_id": uid(), "device_id": uid(), "occurred_at": "2026-11-16T12:00:00Z", "app": "synthetic.editor", "surface_kind": "desktop_window", "synthetic": True}
        self.brain.ingest_context_gap(payload)
        first = self.brain.snapshot()["questions"][0]
        self.brain.ingest_context_gap({**payload, "event_id": uid()})
        second = self.brain.snapshot()["questions"][0]
        self.assertEqual(second["revision"], first["revision"] + 1)
        self.assertEqual(second["version"], first["version"] + 1)
        self.brain.ingest_context_gap(payload)
        self.assertEqual(self.brain.snapshot()["questions"][0]["revision"], second["revision"])
        task = self.task()
        task_question = next(q for q in self.brain.snapshot()["questions"] if q["task_id"] == task["id"] and q["missing_fields"] == ["purpose"])
        self.brain.change_map([{"op": "correct", "id": task["id"], "fields": {"purpose": "Finalidade esclarecida"}}], uid(), "O fundador esclareceu a finalidade.")
        updated = next(q for q in self.brain.snapshot()["questions"] if q["id"] == task_question["id"])
        self.assertEqual(updated["status"], "resolved")
        self.assertGreater(updated["revision"], task_question["revision"])

    def test_existing_task_refinement_keeps_gap_ids_and_rejects_unknown_task_id(self):
        task = self.task()
        old_questions = {q["id"] for q in self.brain.snapshot()["questions"]}
        receipt = self.brain.ingest_observation(observation("Joana continuou a mesma tarefa sintética."))
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == receipt["observation_id"])
        self.brain.apply_analysis(receipt["job_id"], analysis(source, task_id=task["id"]))
        self.assertEqual(len(self.brain.snapshot()["tasks"]), 1)
        self.assertEqual({q["id"] for q in self.brain.snapshot()["questions"]}, old_questions)
        self.assertIn(source["id"], self.brain.snapshot()["tasks"][0]["evidence_ids"])
        other = self.brain.ingest_observation(observation("Outra observação sintética."))
        evidence = next(s for s in self.brain.snapshot()["sources"] if s["id"] == other["observation_id"])
        with self.assertRaisesRegex(BrainError, "unknown_task_refinement"):
            self.brain.apply_analysis(other["job_id"], analysis(evidence, task_id=uid()))

    def test_not_applicable_retires_old_edges_but_retains_history(self):
        receipt = self.brain.ingest_observation(observation())
        source = self.brain.snapshot()["sources"][0]
        self.brain.apply_analysis(receipt["job_id"], analysis(source, complete=True))
        task = self.brain.snapshot()["tasks"][0]
        before = [edge for edge in self.brain.snapshot()["edges"] if edge["active"] and edge.get("derived_field") in ("handoff", "tracking_record")]
        self.assertEqual(len(before), 2)
        self.brain.change_map([{"op": "correct", "id": task["id"], "fields": {"handoff": "Não se aplica", "tracking_record": "Não se aplica"}}], uid(), "Não há repasse nem registro de acompanhamento neste caso.")
        edges = self.brain.snapshot()["edges"]
        self.assertFalse(any(edge["active"] and edge.get("derived_field") in ("handoff", "tracking_record") for edge in edges))
        files = self.brain.store.read().files
        for edge in before:
            self.assertIn(f"records/edges/{edge['id']}/v000001.json", files)

    def test_missing_context_mode_accumulates_complementary_facts(self):
        for text, value in (("Traduzimos documentos.", "Tradução"), ("Também revisamos os textos antes da entrega.", "Revisão dos textos")):
            raw = self.brain.interview_answer(text, uid())
            source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["source_id"])
            result = {"tasks": [], "nodes": [], "context_updates": [{"topic": "activities", "value": value, "basis": "reported", "evidence": [{"source_id": source["id"], "quote": text}]}], "next_question": None}
            self.brain.apply_analysis(raw["job_id"], result)
        snapshot = self.brain.snapshot()
        claims = [record for record in snapshot["context_claims"] if record["topic"] == "activities" and record["status"] == "active"]
        self.assertEqual({record["claim"]["value"] for record in claims}, {"Tradução", "Revisão dos textos"})
        self.assertEqual({ref["source_id"] for record in claims for ref in record["claim"]["evidence"]}, {record["source_id"] for record in claims})
        self.assertEqual(snapshot["context"]["activities"]["value"], "Tradução\nRevisão dos textos")
        self.assertFalse(any(conflict.get("status") == "pending" for conflict in snapshot["interview"].get("context_conflicts", [])))

    def test_explicit_replace_still_replaces_the_named_claim(self):
        first = self.brain.interview_answer("Traduzimos documentos.", uid())
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == first["source_id"])
        self.brain.apply_analysis(first["job_id"], {"tasks": [], "nodes": [], "context_updates": [{"topic": "activities", "value": "Tradução", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}], "next_question": None})
        claim_id = next(record["id"] for record in self.brain.snapshot()["context_claims"] if record["topic"] == "activities" and record["status"] == "active")
        second = self.brain.interview_answer("A atividade passou a ser só revisão.", uid())
        replacement = next(s for s in self.brain.snapshot()["sources"] if s["id"] == second["source_id"])
        self.brain.apply_analysis(second["job_id"], {"tasks": [], "nodes": [], "context_updates": [{"topic": "activities", "value": "Revisão", "basis": "reported", "evidence": [{"source_id": replacement["id"], "quote": replacement["text"]}], "mode": "replace", "replaces": [claim_id]}], "next_question": None})
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["context"]["activities"]["value"], "Revisão")
        self.assertEqual(next(record["status"] for record in snapshot["context_claims"] if record["id"] == claim_id), "replaced")

    def test_business_context_conflict_routes_and_founder_answer_resolves(self):
        for text, value, mode in (("Traduzimos documentos.", "Tradução", None), ("Correção: consertamos bicicletas.", "Reparo de bicicletas", "conflict")):
            raw = self.brain.interview_answer(text, uid())
            source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["source_id"])
            update = {"topic": "activities", "value": value, "basis": "reported", "evidence": [{"source_id": source["id"], "quote": text}]}
            if mode:
                update["mode"] = mode
            result = {"tasks": [], "nodes": [], "context_updates": [update], "next_question": None}
            self.brain.apply_analysis(raw["job_id"], result)
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["interview"]["context_conflicts"][0]["origin"], "explicit_mode")
        question = next(q for q in snapshot["questions"] if q.get("target_kind") == "context_conflict")
        self.assertEqual(question["status"], "pending")
        self.assertEqual(snapshot["context"]["activities"]["value"], "Tradução")
        self.assertEqual(len(question["evidence_ids"]), 2)
        raw = self.brain.record_answer([question["id"]], "A atividade atual é reparar bicicletas; a descrição anterior estava errada.", uid())
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["answer_id"])
        self.brain.apply_analysis(raw["job_id"], {"tasks": [], "nodes": [], "context_updates": [{"topic": "activities", "value": "Reparo de bicicletas", "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}], "next_question": None})
        final = self.brain.snapshot()
        self.assertEqual(final["context"]["activities"]["value"], "Reparo de bicicletas")
        self.assertEqual(next(q for q in final["questions"] if q["id"] == question["id"])["status"], "resolved")
        conflict = final["interview"]["context_conflicts"][0]
        self.assertEqual(conflict["status"], "resolved")
        self.assertEqual(conflict["previous"]["value"], "Tradução")
        self.assertEqual(conflict["reported_candidate"]["value"], "Reparo de bicicletas")

    def test_ambiguous_answer_has_scheduled_disambiguation_then_automatic_resume(self):
        from company_pilot.inference import SemanticWorker
        first = self.task()
        receipt = self.brain.ingest_observation(observation("Mira abriu outro registro sintético."))
        source = next(s for s in self.brain.snapshot()["sources"] if s["id"] == receipt["observation_id"])
        self.brain.apply_analysis(receipt["job_id"], analysis(source))
        second = next(task for task in self.brain.snapshot()["tasks"] if task["id"] != first["id"])
        candidates = [next(q["id"] for q in self.brain.snapshot()["questions"] if q["task_id"] == task["id"] and q["missing_fields"] == ["purpose"]) for task in (first, second)]
        raw = self.brain.record_answer([], "O pagamento foi pelo projeto Aurora.", uid(), candidate_question_ids=candidates)
        original = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["answer_id"])
        empty = {"tasks": [], "nodes": [], "context_updates": [], "next_question": None, "answer_assignments": []}
        self.brain.apply_analysis(raw["job_id"], empty)
        class NoModel:
            def interpret(self, *_args):
                raise AssertionError("no interpretation without new evidence")
        state = SemanticWorker(self.brain, NoModel()).run_once()
        self.assertEqual(state["status"], "awaiting_clarification")
        qid = raw["disambiguation_question_id"]
        self.assertEqual(state["question_ids"], [qid])
        saved = next(s for s in self.brain.snapshot()["sources"] if s["id"] == raw["answer_id"])
        self.assertEqual(saved["candidate_question_ids"], candidates)
        followup = self.brain.record_answer([qid], "Essa resposta descrevia a primeira transferência de Joana.", uid())
        confirmation = next(s for s in self.brain.snapshot()["sources"] if s["id"] == followup["answer_id"])
        assignment = {**empty, "answer_assignments": [{"answer_id": raw["answer_id"], "question_ids": [candidates[0]], "evidence": [{"source_id": confirmation["id"], "quote": confirmation["text"]}]}]}
        self.brain.apply_analysis(followup["job_id"], assignment)
        pending = self.brain.pending_jobs()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["task_ids"], [first["id"]])
        self.assertIn(original["id"], pending[0]["source_ids"])
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == qid)["status"], "resolved")
        result = analysis(original, task_id=first["id"])
        for field in ("trigger", "performer", "input", "action", "result"):
            result["tasks"][0]["fields"][field] = first["fields"][field]
        result["tasks"][0]["fields"]["purpose"] = {"value": "Pagar o projeto Aurora", "basis": "reported", "evidence": [{"source_id": original["id"], "quote": original["text"]}]}
        self.brain.apply_analysis(pending[0]["id"], result)
        final = self.brain.snapshot()
        self.assertEqual(next(t for t in final["tasks"] if t["id"] == first["id"])["fields"]["purpose"]["value"], "Pagar o projeto Aurora")
        self.assertIsNone(next(t for t in final["tasks"] if t["id"] == second["id"])["fields"]["purpose"]["value"])
        self.assertEqual(next(s for s in final["sources"] if s["id"] == original["id"])["question_ids"], [])

    def test_ambiguous_no_candidates_is_visible_and_cannot_assign_unseen_ids(self):
        from company_pilot.inference import SemanticWorker
        raw = self.brain.record_answer([], "Uma resposta ainda sem atividade conhecida.", uid())
        self.assertEqual(raw["state"], "awaiting_disambiguation")
        self.assertEqual(SemanticWorker(self.brain, object()).run_once()["status"], "awaiting_clarification")
        self.assertTrue(any(q["id"] == raw["disambiguation_question_id"] and q["status"] == "pending" for q in self.brain.snapshot()["questions"]))
        before = self.brain.store.read().head
        bad = {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
               "answer_assignments": [{"answer_id": raw["answer_id"], "question_ids": [uid()], "evidence": []}]}
        with self.assertRaisesRegex(BrainError, "outside_candidates"):
            self.brain.apply_analysis(raw["job_id"], bad)
        self.assertEqual(self.brain.store.read().head, before)


if __name__ == "__main__":
    unittest.main()
