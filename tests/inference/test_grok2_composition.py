"""Report-derived Grok-2 composition; only the external Claude CLI is faked.

LocalStore, Brain, SemanticWorker, ClaudeInterpreter and rendered cards are real.
These synthetic decisions do not replay the unavailable original raw run.
"""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID, uuid4

from company_pilot.brain import Brain, BrainError, FIELDS, LocalStore
from company_pilot.inference import ClaudeInterpreter, SemanticWorker
from tests.inference.test_claude import FakeRunner, stream


def uid():
    return str(uuid4())


def empty():
    return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None}


def claim(source, value, basis="observed", quote=None):
    return {"value": value, "basis": basis,
            "evidence": [{"source_id": source["id"], "quote": source["text"] if quote is None else quote}]}


def candidate(source, name, fields, task_id=None):
    values = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
    values.update({key: claim(source, value) for key, value in fields.items()})
    return {"task_id": task_id, "name": name, "fields": values, "links": []}


class Grok2CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética — composição Grok2", synthetic=True)
        self.artifact_run = uid()

    def source(self, source_id):
        return next(source for source in self.brain.snapshot()["sources"] if source["id"] == source_id)

    def observe(self, text, when="2026-11-23T12:00:00+00:00", record_ref="RECORD-SYN-1"):
        payload = {"schema_version": 1, "event_id": uid(), "device_id": uid(), "occurred_at": when,
                   "policy_version": self.brain.snapshot()["company"]["policy_version"],
                   "source": {"kind": "active_window", "app": "Synthetic Work Viewer", "record_ref": record_ref},
                   "classification": {"label": "company", "reason": "conteúdo sintético permitido"},
                   "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}
        receipt = self.brain.ingest_observation(payload)
        return receipt, self.source(receipt["observation_id"])

    def run_model(self, decision, require_applied=True):
        runner = FakeRunner(output=stream(decision))
        response = SemanticWorker(self.brain, ClaudeInterpreter("/fake/claude", runner=runner)).run_once()
        if require_applied:
            self.assertEqual(response["status"], "applied", response)
        payload = json.loads(runner.calls[-1][1]["input"])
        return response, payload

    def emit(self, name):
        parent = Path(tempfile.gettempdir()) / "a39-composed" / name
        root = parent / self.artifact_run
        root.mkdir(parents=True, exist_ok=True)
        (root / "snapshot.json").write_text(json.dumps(self.brain.snapshot(), ensure_ascii=False, indent=2) + "\n")
        rendered = {}
        for path, content in self.brain.store.read().files.items():
            if path == "README.md" or path.startswith(("views/tasks/", "views/questions/")):
                rendered[path] = content
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        (root / "rendered-cards.json").write_text(json.dumps(rendered, ensure_ascii=False, indent=2) + "\n")
        (root / "evidence-level.json").write_text(json.dumps({
            "evidence_level": "E1", "synthetic": True, "report_derived": True,
            "original_raw_replay": False, "fake_boundary": "Claude CLI only",
            "actual_components": ["LocalStore", "Brain", "SemanticWorker", "ClaudeInterpreter", "rendered cards"],
        }, ensure_ascii=False, indent=2) + "\n")
        (parent / "latest.json").write_text(json.dumps({"run": self.artifact_run, "directory": str(root),
                                                       "head": self.brain.snapshot()["head"]}, indent=2) + "\n")

    def test_e13_observation_receives_named_pending_task_question_and_accepted_answer_source(self):
        _, earlier = self.observe("A revisão do pedido existe para evitar uma compra duplicada.")
        self.run_model({**empty(), "tasks": [candidate(earlier, "Revisar pedido", {"action": "Revisar o pedido"})]})
        task = self.brain.snapshot()["tasks"][0]
        question = next(q for q in self.brain.snapshot()["questions"] if q["task_id"] == task["id"] and q["missing_links"] == ["purpose"])
        _, current = self.observe("Ana retomou a revisão do pedido.", "2026-11-24T12:00:00+00:00")
        refined = {"task_id": task["id"], "name": task["name"], "fields": copy.deepcopy(task["fields"]), "links": []}
        decision = {**empty(), "tasks": [refined], "question_answers": [
            {"question_id": question["id"], "claim": claim(earlier, "Evitar uma compra duplicada", "reported")}]}
        response, payload = self.run_model(decision, require_applied=False)
        self.assertIn(question["id"], {q["id"] for q in payload["base_context"]["questions"]})
        self.assertLessEqual(len(payload["base_context"]["questions"]), 100)
        self.assertEqual(response["status"], "applied", response)
        final = self.brain.snapshot()
        updated = next(t for t in final["tasks"] if t["id"] == task["id"])
        self.assertEqual(updated["fields"]["purpose"]["value"], "Evitar uma compra duplicada")
        self.assertEqual(next(q for q in final["questions"] if q["id"] == question["id"])["status"], "resolved")
        self.assertTrue({earlier["id"], current["id"]} <= {source["id"] for source in payload["base_context"]["sources"]})
        self.emit("e13-accepted-named-answer")

    def test_e10_two_facts_create_distinct_canonical_tasks_with_both_consistency_quotes(self):
        _, prior = self.observe("23/11: cronograma de conduítes marcado Concluído, 100%, pelo áudio.")
        self.run_model({**empty(), "tasks": [candidate(prior, "Atualizar cronograma", {
            "action": "Registrar conclusão dos conduítes", "result": "Concluído, 100%"})]})
        self.emit("e10-before-current-capture")
        _, current = self.observe("24/11: vistoria de Bia mostra quarto 2 sem conduíte. Cozinha: verificar compatibilidade da lava-louça.",
                                  "2026-11-24T12:00:00+00:00")
        inspection = candidate(current, "Vistoriar conduítes", {
            "action": "Conferir conduítes no quarto 2", "result": "Quarto 2 sem conduíte"})
        refs = claim(prior, "Anterior")["evidence"] + claim(current, "Vistoria")["evidence"]
        inspection["consistency_issues"] = [{"description": "Conclusão integral no cronograma contradiz o quarto 2 sem conduíte na vistoria.", "evidence": refs}]
        dishwasher = candidate(current, "Verificar lava-louça", {"action": "Verificar compatibilidade da lava-louça"})
        response, payload = self.run_model({**empty(), "tasks": [inspection, dishwasher]})
        created_ids = [entry["task_id"] for entry in response["receipt"]["tasks"]]
        self.assertEqual(len(set(created_ids)), 2)
        for task_id in created_ids:
            UUID(task_id)
        final = self.brain.snapshot()
        created = [task for task in final["tasks"] if task["id"] in created_ids]
        self.assertEqual({task["name"] for task in created}, {"Vistoriar conduítes", "Verificar lava-louça"})
        self.assertEqual(len(final["tasks"]), 3)
        self.assertTrue(all(current["id"] in task["evidence_ids"] for task in created))
        issue = next(task for task in created if task["name"] == "Vistoriar conduítes")["consistency_issues"][0]
        self.assertEqual(issue["evidence"], refs)
        self.assertTrue({prior["id"], current["id"]} <= {source["id"] for source in payload["base_context"]["sources"]})
        self.emit("e10-two-canonical-tasks")

    def test_pending_raw_source_is_not_supplied_or_authorized_by_a_forged_scope(self):
        _, prior = self.observe("Cronograma inicial do registro autorizado.")
        self.run_model({**empty(), "tasks": [candidate(prior, "Conferir cronograma", {"action": "Conferir cronograma"})]})
        task = self.brain.snapshot()["tasks"][0]
        current_receipt, current = self.observe("Vistoria atual do registro autorizado.", "2026-11-24T12:00:00+00:00")
        pending_receipt, pending = self.observe("Outro registro ainda aguardando interpretação.", "2026-11-25T12:00:00+00:00", "OTHER-RECORD")
        # Keep this unrelated raw job pending but outside the current eligible
        # retry window; queue order is not an authority-selection mechanism.
        self.brain.mark_job_error(pending_receipt["job_id"], "fixture_awaiting_interpretation")
        refined = {"task_id": task["id"], "name": task["name"], "fields": copy.deepcopy(task["fields"]), "links": [],
                   "consistency_issues": [{"description": "Comparação indevida com fonte ainda não aceita.",
                                            "evidence": claim(pending, "Indevida")["evidence"]}]}
        decision = {**empty(), "tasks": [refined]}
        before = copy.deepcopy(self.brain.snapshot())
        response, payload = self.run_model(decision, require_applied=False)
        self.assertNotIn(pending["id"], {source["id"] for source in payload["base_context"]["sources"]})
        self.assertEqual(response["status"], "pending", response)
        self.assertEqual(response["error_code"], "analysis_rejected")
        after = self.brain.snapshot()
        for group in ("tasks", "nodes", "edges", "questions", "sources"):
            self.assertEqual(after[group], before[group], group)
        frozen = self.brain.store.read()
        forged = {"tasks": [{"id": task["id"], "version": task["version"]}], "nodes": [], "edges": [],
                  "source_ids": [prior["id"], current["id"], pending["id"]]}
        with self.assertRaisesRegex(BrainError, "source_not_previously_accepted_or_outside_bound"):
            self.brain.apply_analysis(current_receipt["job_id"], decision, map_scope=forged)
        self.assertEqual(self.brain.store.read(), frozen)
        self.emit("pending-raw-scope-denied")

    def test_e2_e4_scoped_founder_answer_resolves_context_issue_and_current_field(self):
        _, observed = self.observe("Conduítes constam Concluídos, 100%, mas a vistoria mostra quarto 2 incompleto.")
        initial = candidate(observed, "Conferir conclusão dos conduítes", {
            "action": "Conferir execução dos conduítes", "result": "Concluído, 100%"})
        initial["consistency_issues"] = [{"description": "Conclusão integral sem conferência em todos os ambientes.",
                                          "evidence": claim(observed, "Dúvida")["evidence"]}]
        self.run_model({**empty(), "tasks": [initial]})
        task = self.brain.snapshot()["tasks"][0]
        issue_question = next(q for q in self.brain.snapshot()["questions"] if q.get("gap_kind") == "business_consistency")
        context_raw = self.brain.ingest_context_gap({"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": "2026-11-23T12:10:00+00:00", "app": "Transcritor de reuniões",
            "surface_kind": "desktop_window", "synthetic": True})
        self.emit("e2-e4-before-founder-answer")
        text = "É a transcrição das reuniões da obra. Era só sala e cozinha; Bia confirma na vistoria. O estado correto é Em andamento, 80%."
        question_ids = [issue_question["id"], context_raw["question_id"]]
        raw = self.brain.record_message(text, uid(), question_ids=question_ids, candidate_question_ids=question_ids,
                                        candidate_task_versions=[{"id": task["id"], "version": task["version"]}])
        founder = self.source(raw["answer_id"])
        fields = copy.deepcopy(task["fields"])
        fields["result"] = claim(founder, "Em andamento, 80%", "reported")
        decision = {**empty(), "tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": [],
            "issue_resolutions": [{"issue_ids": [issue["id"] for issue in task["consistency_issues"]],
                                   "resolution": claim(founder, "Era só sala e cozinha; Bia confirma na vistoria.", "reported")}]}],
            "question_answers": [{"question_id": context_raw["question_id"], "claim": claim(founder, "Transcrição das reuniões da obra", "reported")}],
            "message_intent": {"kind": "answer", "task_ids": [task["id"]], "question_ids": question_ids,
                               "delegate_to": None, "disposition": None, "evidence": claim(founder, "Resposta", "reported")["evidence"]}}
        self.run_model(decision)
        final = self.brain.snapshot()
        updated = next(t for t in final["tasks"] if t["id"] == task["id"])
        self.assertEqual(updated["fields"]["result"]["value"], "Em andamento, 80%")
        self.assertNotIn("result", updated["conflicts"])
        self.assertEqual(updated.get("consistency_issues"), [])
        self.assertTrue(updated["resolved_issues"])
        self.assertTrue(all(next(q for q in final["questions"] if q["id"] == qid)["status"] == "resolved" for qid in question_ids))
        self.assertTrue(any(value["value"] == "Transcrição das reuniões da obra" for value in final["context"].values()))
        files = self.brain.store.read().files
        self.assertIn("Concluído, 100%", files[f"records/tasks/{task['id']}/v000001.json"])
        self.assertIn("Em andamento, 80%", files[f"views/tasks/{task['id']}.md"])
        self.assertIn("Inconsistência esclarecida pelo fundador", files[f"views/tasks/{task['id']}.md"])
        self.emit("e2-e4-founder-resolution-applied")

    def test_e11_explicit_result_to_input_handoff_forms_chain_names_alone_do_not(self):
        _, consumer_source = self.observe("Ana cota componentes no projeto Alfa usando a lista de materiais revisada como entrada.", record_ref="ALFA-LISTA")
        self.run_model({**empty(), "tasks": [candidate(consumer_source, "Cotar componentes", {
            "action": "Cotar componentes", "input": "Lista de materiais revisada", "performer": "Ana"})]})
        consumer = self.brain.snapshot()["tasks"][0]
        _, producer_source = self.observe("Ana concluiu a lista de materiais revisada e a encaminhou como entrada para a tarefa Cotar componentes do projeto Alfa.",
                                          "2026-11-24T12:00:00+00:00", "ALFA-LISTA")
        producer = candidate(producer_source, "Revisar lista", {
            "action": "Revisar lista de materiais", "result": "Lista de materiais revisada", "performer": "Ana"})
        producer["links"] = [{"kind": "handoff", "target_id": consumer["id"], "basis": "observed",
                              "evidence": claim(producer_source, "Repasse")["evidence"]}]
        response, _ = self.run_model({**empty(), "tasks": [producer]})
        producer_id = response["receipt"]["tasks"][0]["task_id"]
        _, unrelated = self.observe("Ana atualizou um quadro no projeto Alfa.", "2026-11-25T12:00:00+00:00", "ALFA-QUADRO")
        self.run_model({**empty(), "tasks": [candidate(unrelated, "Atualizar quadro", {
            "action": "Atualizar quadro", "result": "Quadro atualizado", "performer": "Ana"})]})
        final = self.brain.snapshot()
        task_ids = {task["id"] for task in final["tasks"]}
        flows = [edge for edge in final["edges"] if edge["active"] and edge["from"] in task_ids and edge["to"] in task_ids]
        self.assertEqual([(edge["from"], edge["to"], edge["kind"]) for edge in flows], [(producer_id, consumer["id"], "handoff")])
        self.assertEqual(flows[0]["evidence"], claim(producer_source, "Repasse")["evidence"])
        self.assertIn("Cotar componentes", self.brain.store.read().files[f"views/tasks/{producer_id}.md"])
        self.emit("e11-explicit-chain-only")

    def test_e13_observation_question_context_is_bounded_without_guessing_missing_meaning(self):
        for batch, count in enumerate((3, 3, 3, 3, 2)):
            _, source = self.observe(f"Registros sintéticos do lote {batch} abertos para revisão.")
            self.run_model({**empty(), "tasks": [candidate(source, f"Revisar registro {batch}-{index}", {
                "action": f"Revisar registro {batch}-{index}"}) for index in range(count)]})
        prior = self.brain.snapshot()
        self.assertGreater(len([q for q in prior["questions"] if q["status"] == "pending"]), 100)
        target = prior["tasks"][-1]
        self.observe("Os registros anteriores continuam em revisão.", "2026-11-25T12:00:00+00:00")
        refined = {"task_id": target["id"], "name": target["name"], "fields": copy.deepcopy(target["fields"]), "links": []}
        _, payload = self.run_model({**empty(), "tasks": [refined]})
        context = payload["base_context"]
        self.assertEqual(len(context["questions"]), 100)
        supplied_ids = {task["id"] for task in context["tasks"]}
        self.assertTrue(all(q["task_id"] in supplied_ids and q["status"] == "pending" for q in context["questions"]))
        self.assertTrue(all(task["fields"]["purpose"]["value"] is None for task in self.brain.snapshot()["tasks"]))

    def test_disambiguation_cannot_write_substantive_question_answers(self):
        _, observed = self.observe("Registro de revisão aberto.")
        self.run_model({**empty(), "tasks": [candidate(observed, "Revisar registro", {"action": "Revisar registro"})]})
        question = next(q for q in self.brain.snapshot()["questions"] if q["missing_links"] == ["purpose"])
        raw = self.brain.record_answer([], "É sobre um dos registros, sem identificação suficiente.", uid(),
                                       candidate_question_ids=[question["id"]])
        source = self.source(raw["answer_id"])
        decision = {**empty(), "answer_assignments": [], "question_answers": [
            {"question_id": question["id"], "claim": claim(source, "Finalidade que não foi atribuída", "reported")}]}
        response, _ = self.run_model(decision, require_applied=False)
        self.assertEqual(response["status"], "pending", response)
        self.assertEqual(response["error_code"], "invalid_analysis")
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == question["id"])["status"], "pending")

    def test_e7_company_record_analysis_learns_literal_aliases_for_device_policy(self):
        self.brain.update_policy({"watchdog_actors": ["fixture-founder"]}, uid())
        excerpts = [
            ("DOC22-report-derived", "O registro aparece como MEDICAO 02 GAVEA. A conversa empresarial da cliente aparece como Caio Prado.",
             [("MEDICAO 02 GAVEA", "record_name"), ("Caio Prado", "record_name")]),
            ("DOC24-report-derived", "A conta empresarial de e-mail do escritório aparece como Camila Rocha Arquitetura.",
             [("Camila Rocha Arquitetura", "business_account")]),
        ]
        expected_sources = {}
        for record_id, text, names in excerpts:
            digest = hashlib.sha256(text.encode()).hexdigest()
            raw = self.brain.ingest_company_record({"schema_version": 1, "event_id": uid(),
                "occurred_at": "2026-11-23T12:00:00+00:00", "synthetic": True,
                "source": {"system": "grok2-report-derived-fixture", "record_id": record_id,
                           "url": "https://example.invalid/grok2/" + record_id, "revision": "report-derived-r1"},
                "operation": "upsert", "content": {"text": text, "sha256": digest, "complete": False}, "object": None})
            source = self.source(raw["source_id"])
            decision = {**empty(), "recognition_markers": [
                {"value": value, "kind": kind, "operation": "add", "evidence": [
                    {"source_id": source["id"], "quote": value}]} for value, kind in names],
                "record_extraction": {"complete": False, "limitations": ["Trecho sintético derivado do relatório; exportação original pendente."]}}
            _, payload = self.run_model(decision)
            self.assertEqual(payload["job"]["kind"], "company_record")
            self.assertTrue(any(item["id"] == source["id"] and item["source_record"]["record_id"] == record_id
                                for item in payload["base_context"]["sources"]))
            expected_sources.update({value: source["id"] for value, _kind in names})
        policy = self.brain.device_policy()
        active = {marker["value"]: marker for marker in policy["recognition_markers"]}
        self.assertTrue(set(expected_sources) <= set(policy["work_signals"]))
        for value, source_id in expected_sources.items():
            self.assertEqual(active[value]["evidence"], [{"source_id": source_id, "quote": value}])
        self.assertNotIn("Gmail", policy["work_signals"])
        self.emit("e7-record-aliases-to-policy")
        root = Path(tempfile.gettempdir()) / "a39-composed" / "e7-record-aliases-to-policy" / self.artifact_run
        (root / "device-policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")

    def test_e12_explicit_interview_names_become_individual_typed_people(self):
        text = "As pessoas da equipe são Ana, Bia e Davi. Caio e Marcos Prado são clientes."
        raw = self.brain.interview_answer(text, uid())
        source = self.source(raw["source_id"])
        names = ["Ana", "Bia", "Davi"]
        decision = {**empty(), "nodes": [{"kind": "person", "name": name, "evidence": [
            {"source_id": source["id"], "quote": name}]} for name in names]}
        self.run_model(decision)
        people = [node for node in self.brain.snapshot()["nodes"] if node["kind"] == "person"]
        self.assertEqual({node["name"] for node in people}, set(names))
        self.assertEqual(len(people), 3)
        self.assertNotIn("Caio Prado", {node["name"] for node in people})
        self.assertTrue(all(node["evidence"][0]["source_id"] == source["id"] for node in people))
        self.emit("e12-explicit-individual-people")



    def test_e3_explicit_progress_preserves_established_status_for_supported_complete_task(self):
        text='Pedido CT-42 recebido. Bia usa a lista de resíduos para agendar a retirada de caçamba. Agendada quarta. Finalidade liberar a área. Logística da obra, repasse à transportadora, controle na Planilha CT-42.'
        raw,source=self.observe(text,record_ref='CT-42')
        facts=dict(trigger='Pedido CT-42 recebido',performer='Bia',input='Lista de resíduos',action='Agendar retirada de caçamba',result='Agendada quarta',purpose='Liberar a área',placement='Logística da obra',handoff='Transportadora',tracking_record='Planilha CT-42')
        self.run_model(dict(empty(),tasks=[candidate(source,'Agendar retirada da caçamba',facts)]))
        task=self.brain.snapshot()['tasks'][0];self.assertEqual(task['status'],'established')
        retained=copy.deepcopy(self.brain.store.read().files)
        raw,source=self.observe('CT-42: o caminhão quebrou; retirada remarcada para quinta às 7h30.',when='2026-11-25T12:00:00+00:00',record_ref='CT-42')
        fields=copy.deepcopy(task['fields']);fields['result']=claim(source,'Remarcada para quinta às 7h30')
        decision=dict(empty(),tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[],field_updates=dict(result=dict(mode='progress',effective_at=None,evidence=fields['result']['evidence'])))])
        self.run_model(decision)
        current=self.brain.snapshot()['tasks'][0]
        self.assertEqual(current['status'],'established');self.assertFalse(current['conflicts'])
        self.assertEqual(current['fields']['result']['value'],'Remarcada para quinta às 7h30')
        for path,value in retained.items():
            if path.startswith(('sources/','records/','ledger/')):self.assertEqual(self.brain.store.read().files[path],value)
        self.emit('e3-established-progress')

if __name__ == "__main__":
    unittest.main()
