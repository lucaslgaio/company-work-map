"""FR8: explicit corrections and unknowns through delivered APIs, E1 only."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
import uuid

from company_pilot.brain import AccessDenied, Brain, BrainError, LocalStore, TOPICS


def uid():
    return str(uuid.uuid4())


def interpreted(source, values):
    return {"tasks": [], "nodes": [], "next_question": None, "context_updates": [
        {"topic": topic, "value": value, "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}]}
        for topic, value in values.items()
    ]}


BUSINESSES = (
    {"activities": "Tradução de textos", "people": "Ana traduz e Bia revisa", "stages": "Receber, traduzir e revisar",
     "tracking": "Planilha de projetos Aurora", "documents": "Briefings e textos do cliente"},
    {"activities": "Reparo de bicicletas", "people": "Caio recebe e Dora conserta", "stages": "Diagnosticar, reparar e testar",
     "tracking": "Ordens de serviço Roda", "documents": "Laudos e listas de peças"},
)


class InterviewUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brain = Brain(LocalStore(self.root))
        self.brain.initialize("Empresa fictícia de desenvolvimento", synthetic=True)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, source_id):
        return next(source for source in self.brain.snapshot()["sources"] if source["id"] == source_id)

    def complete(self, values=BUSINESSES[0]):
        text = "\n".join(f"{topic}: {value}" for topic, value in values.items())
        raw = self.brain.interview_answer(text, uid())
        self.brain.apply_analysis(raw["job_id"], interpreted(self.source(raw["source_id"]), values))
        self.assertEqual(self.brain.interview_next()["state"], "complete")

    def test_two_businesses_accept_completed_readback_correction_and_keep_unrelated_context(self):
        for values in BUSINESSES:
            with self.subTest(business=values["activities"]), tempfile.TemporaryDirectory() as root:
                brain = Brain(LocalStore(root))
                brain.initialize("Exemplo sintético", synthetic=True)
                raw = brain.interview_answer("\n".join(values.values()), uid())
                source = next(s for s in brain.snapshot()["sources"] if s["id"] == raw["source_id"])
                brain.apply_analysis(raw["job_id"], interpreted(source, values))
                before = brain.snapshot()
                retained = {p: data for p, data in brain.store.read().files.items() if p.startswith(("sources/", "records/"))}
                literal = "Correção: o acompanhamento é feito no registro fictício V2, mantendo as demais respostas."
                correction = brain.interview_correct("tracking", literal, uid())
                self.assertEqual(correction["state"], "pending_analysis")
                self.assertEqual(brain.interview_next()["state"], "pending_analysis")
                source = next(s for s in brain.snapshot()["sources"] if s["id"] == correction["source_id"])
                self.assertEqual(source["text"], literal)
                self.assertEqual(source["actor"], "fixture-founder")
                self.assertEqual(source["previous_context"], before["context"]["tracking"])
                brain.apply_analysis(correction["job_id"], interpreted(source, {"tracking": "Registro fictício V2"}))
                after = brain.snapshot()
                self.assertEqual(after["context"]["tracking"]["value"], "Registro fictício V2")
                for topic in set(TOPICS) - {"tracking"}:
                    self.assertEqual(after["context"][topic], before["context"][topic])
                self.assertEqual(brain.interview_next()["state"], "complete")
                history = after["interview"]["context_history"][-1]
                self.assertEqual(history["previous"], before["context"]["tracking"])
                self.assertEqual(history["source_id"], correction["source_id"])
                self.assertTrue(all(brain.store.read().files[path] == data for path, data in retained.items()))

    def test_correction_replay_and_changed_payload(self):
        self.complete()
        event = uid()
        first = self.brain.interview_correct("documents", "O documento correto é o briefing V2.", event)
        self.assertEqual(first, self.brain.interview_correct("documents", "O documento correto é o briefing V2.", event))
        source = self.source(first["source_id"])
        self.brain.apply_analysis(first["job_id"], interpreted(source, {"documents": "Briefing V2"}))
        head = self.brain.store.read().head
        self.assertEqual(first, self.brain.interview_correct("documents", "O documento correto é o briefing V2.", event))
        self.assertEqual(head, self.brain.store.read().head)
        for topic, text in (("documents", "Texto alterado"), ("people", "O documento correto é o briefing V2.")):
            with self.assertRaisesRegex(BrainError, "idempotency_payload_conflict"):
                self.brain.interview_correct(topic, text, event)

    def test_wrong_actor_cannot_correct_or_defer_and_invalid_topics_fail(self):
        head = self.brain.store.read().head
        teammate = Brain(LocalStore(self.root, actor="fixture-team", permissions={"read", "write"}))
        for method in (teammate.interview_correct, teammate.interview_defer):
            with self.assertRaises(AccessDenied):
                method("activities", "Texto de um terceiro", uid())
        for method in (self.brain.interview_correct, self.brain.interview_defer):
            with self.assertRaisesRegex(BrainError, "unknown_context_topic"):
                method("invented_department", "Texto", uid())
        self.assertEqual(self.brain.store.read().head, head)

    def test_unknown_topics_can_finish_without_creating_facts_or_model_jobs(self):
        source_ids, question_ids = [], []
        for topic in TOPICS:
            next_question = self.brain.interview_next()
            self.assertEqual(next_question["topic"], topic)
            raw = self.brain.interview_defer(topic, f"Ainda não sei informar sobre {topic}.", uid())
            source_ids.append(raw["source_id"])
            question_ids.append(raw["question_id"])
        progress = self.brain.interview_next()
        self.assertEqual(progress["state"], "complete")
        self.assertEqual(set(progress["deferred_topics"]), set(TOPICS))
        self.assertEqual(progress["business_context_status"], "pending_clarification")
        self.assertEqual(set(progress["pending_context_question_ids"]), set(question_ids))
        self.assertEqual(self.brain.snapshot()["context"], {})
        self.assertEqual(self.brain.pending_jobs(), [])
        self.assertEqual(self.brain.device_policy()["work_signals"], [])
        self.assertEqual({s["id"] for s in self.brain.snapshot()["sources"]}, set(source_ids))
        self.assertTrue(all(q["status"] == "pending" for q in self.brain.snapshot()["questions"]))
        view = self.brain.store.read().files["views/empresa.md"]
        self.assertIn("continua sem confirmação", view)
        self.assertIn("Ainda não esclarecido", view)

    def test_defer_is_idempotent_and_reopens_one_stable_question(self):
        event = uid()
        first = self.brain.interview_defer("people", "Ainda não sei.", event)
        head = self.brain.store.read().head
        self.assertEqual(first, self.brain.interview_defer("people", "Ainda não sei.", event))
        self.assertEqual(head, self.brain.store.read().head)
        with self.assertRaisesRegex(BrainError, "idempotency_payload_conflict"):
            self.brain.interview_defer("people", "Resposta diferente", event)
        with self.assertRaisesRegex(BrainError, "idempotency_payload_conflict"):
            self.brain.interview_correct("people", "Ainda não sei.", event)
        second = self.brain.interview_defer("people", "Preciso confirmar os nomes.", uid())
        self.assertEqual(first["question_id"], second["question_id"])
        self.assertEqual(len(self.brain.snapshot()["questions"]), 1)
        self.assertGreater(self.brain.snapshot()["questions"][0]["revision"], 1)

    def test_defer_preserves_unrelated_facts_in_an_unprocessed_answer_without_looping(self):
        old = self.brain.interview_answer("Ainda não sei descrever as atividades. Ana participa da empresa.", uid())
        self.assertEqual(self.brain.interview_next()["state"], "pending_analysis")
        raw = self.brain.interview_defer("activities", "Vou confirmar a descrição da atividade.", uid())
        self.assertEqual(self.brain.interview_next()["topic"], "people")
        self.assertEqual(self.brain.pending_jobs()[0]["excluded_context_topics"], ["activities"])
        old_source = self.source(old["source_id"])
        receipt = self.brain.apply_analysis(old["job_id"], interpreted(old_source, {"activities": "Não deveria ser aplicada", "people": "Ana participa"}))
        self.assertEqual(receipt["deferred_topics"], ["activities"])
        self.assertNotIn("activities", self.brain.snapshot()["context"])
        self.assertEqual(self.brain.snapshot()["context"]["people"]["value"], "Ana participa")
        self.assertIn(old["source_id"], self.brain.snapshot()["questions"][0]["evidence_ids"])
        self.assertEqual(self.source(raw["source_id"])["text"], "Vou confirmar a descrição da atividade.")

    def test_later_defer_excludes_topic_from_every_older_pending_interview_answer(self):
        # The old response was asked about activities but also named a person.
        # A later explicit unknown about people must supersede that older fact.
        old = self.brain.interview_answer("Fazemos tradução. Ana integra a equipe.", uid())
        deferred = self.brain.interview_defer("people", "Ainda não confirmei quem integra a equipe.", uid())
        source = self.source(old["source_id"])
        self.brain.apply_analysis(old["job_id"], interpreted(source, {"activities": "Tradução", "people": "Ana"}))
        final = self.brain.snapshot()
        self.assertEqual(final["context"]["activities"]["value"], "Tradução")
        self.assertNotIn("people", final["context"])
        self.assertEqual(final["interview"]["deferred_topics"]["people"]["status"], "pending")
        self.assertEqual(next(q for q in final["questions"] if q["id"] == deferred["question_id"])["status"], "pending")
        self.assertIn(old["source_id"], next(q for q in final["questions"] if q["id"] == deferred["question_id"])["evidence_ids"])
        self.assertEqual(source["text"], "Fazemos tradução. Ana integra a equipe.")
        self.assertEqual(self.brain.interview_next()["topic"], "stages")

    def test_temporal_defer_keeps_unrelated_scoped_corrections_and_supersedes_matching_one(self):
        self.complete()
        correction = self.brain.interview_correct("documents", "O documento agora é Briefing V5.", uid())
        self.brain.interview_defer("people", "Ainda não confirmei os nomes.", uid())
        job = next(job for job in self.brain.pending_jobs() if job["id"] == correction["job_id"])
        self.assertNotIn("excluded_context_topics", job)
        self.brain.apply_analysis(job["id"], interpreted(self.source(correction["source_id"]), {"documents": "Briefing V5"}))
        self.assertEqual(self.brain.snapshot()["context"]["documents"]["value"], "Briefing V5")
        self.assertNotIn("people", self.brain.snapshot()["context"])
        newer = self.brain.interview_correct("documents", "Talvez o documento seja Briefing V6.", uid())
        self.brain.interview_defer("documents", "Preciso confirmar o documento antes de afirmar.", uid())
        receipt = self.brain.apply_analysis(newer["job_id"], interpreted(self.source(newer["source_id"]), {"documents": "Briefing V6"}))
        self.assertEqual(receipt["state"], "deferred")
        self.assertNotIn("documents", self.brain.snapshot()["context"])

    def test_nora_answer_resolves_deferred_topic_and_keeps_original_unknown(self):
        self.complete()
        before = copy.deepcopy(self.brain.snapshot()["context"])
        defer_event = uid()
        unknown = self.brain.interview_defer("documents", "Preciso verificar quais documentos usamos.", defer_event)
        self.brain.update_policy({"founders": ["fixture-founder", "verified-founder"]}, uid())
        self.brain.transact_nora(lambda state: state.update(route={"id": "synthetic-route", "enabled": True,
            "account": "synthetic-company", "conversation": "synthetic-group", "founder_sender": "verified-sender", "founder_actor_id": "verified-founder"}))
        raw = self.brain.record_answer([unknown["question_id"]], "Usamos briefings revisados e registros de entrega.", uid(),
            source={"kind": "nora", "route_id": "synthetic-route", "message_id": "synthetic-message", "account": "synthetic-company", "conversation": "synthetic-group", "sender": "verified-sender"})
        source = self.source(raw["answer_id"])
        self.assertEqual(source["speaker"], "verified-founder")
        self.brain.apply_analysis(raw["job_id"], interpreted(source, {"documents": "Briefings revisados e registros de entrega"}))
        final = self.brain.snapshot()
        self.assertEqual(final["context"]["documents"]["value"], "Briefings revisados e registros de entrega")
        self.assertEqual(final["interview"]["deferred_topics"]["documents"]["status"], "resolved")
        self.assertEqual(next(q for q in final["questions"] if q["id"] == unknown["question_id"])["status"], "resolved")
        self.assertEqual(self.brain.interview_next()["deferred_topics"], [])
        self.assertEqual(self.source(unknown["source_id"])["text"], "Preciso verificar quais documentos usamos.")
        self.assertEqual(self.source(unknown["source_id"])["previous_context"], before["documents"])
        resolved_head = self.brain.store.read().head
        self.assertEqual(self.brain.interview_defer("documents", "Preciso verificar quais documentos usamos.", defer_event), unknown)
        self.assertEqual(self.brain.store.read().head, resolved_head)
        self.assertEqual(self.brain.interview_next()["deferred_topics"], [])
        for topic in set(TOPICS) - {"documents"}:
            self.assertEqual(final["context"][topic], before[topic])

    def test_older_deferred_reply_keeps_newer_correction_and_tracks_conflict(self):
        gap = self.brain.interview_defer("people", "Ainda não confirmei a equipe.", uid())
        old = self.brain.record_answer([gap["question_id"]], "Ana traduz.", uid())
        old_source = self.source(old["answer_id"])
        self.assertEqual(old_source["context_bases"], {"people": None})
        retained = {p: data for p, data in self.brain.store.read().files.items() if p.startswith("sources/")}
        correction = self.brain.interview_correct("people", "Correção posterior: Bia traduz, Ana saiu.", uid())
        self.brain.apply_analysis(correction["job_id"], interpreted(self.source(correction["source_id"]), {"people": "Bia traduz"}))
        result = interpreted(old_source, {"people": "Ana traduz"})
        receipt = self.brain.apply_analysis(old["job_id"], result)
        final = self.brain.snapshot()
        self.assertEqual(final["context"]["people"]["value"], "Bia traduz")
        conflict = next(q for q in final["questions"] if q.get("target_kind") == "context_conflict" and q["status"] == "pending")
        self.assertEqual(set(conflict["evidence_ids"]), {old["answer_id"], correction["source_id"]})
        self.assertTrue(all(self.brain.store.read().files[p] == data for p, data in retained.items()))
        head = self.brain.store.read().head
        self.assertEqual(self.brain.apply_analysis(old["job_id"], result), receipt)
        self.assertEqual(self.brain.store.read().head, head)
        # A fresh reply to the conflict remains a usable resolution path.
        newer = self.brain.record_answer([conflict["id"]], "Confirmo que Bia traduz.", uid())
        self.brain.apply_analysis(newer["job_id"], interpreted(self.source(newer["answer_id"]), {"people": "Bia traduz"}))
        self.assertFalse(any(q.get("target_kind") == "context_conflict" and q["status"] == "pending" for q in self.brain.snapshot()["questions"]))

    def test_old_same_value_assignment_cannot_close_newer_context_conflict(self):
        gap = self.brain.interview_defer("people", "Ainda não confirmei a equipe.", uid())
        old = self.brain.record_answer([], "Bia traduz.", uid(), candidate_question_ids=[gap["question_id"]])
        old_source = self.source(old["answer_id"])
        self.assertEqual(old_source["context_bases"], {"people": None})
        self.assertEqual(old_source["context_question_bases"]["people"], {gap["question_id"]: 1})
        correction = self.brain.interview_correct("people", "Correção posterior: Bia traduz.", uid())
        self.brain.apply_analysis(correction["job_id"], interpreted(self.source(correction["source_id"]), {"people": "Bia"}))
        later = self.brain.interview_answer("Relato posterior: Cia traduz.", uid())
        self.brain.apply_analysis(later["job_id"], interpreted(self.source(later["source_id"]), {"people": "Cia"}))
        conflict = next(q for q in self.brain.snapshot()["questions"] if q.get("target_kind") == "context_conflict")
        assignment = self.brain.apply_analysis(old["job_id"], {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
            "answer_assignments": [{"answer_id": old["answer_id"], "question_ids": [gap["question_id"]], "evidence": [{"source_id": old["answer_id"], "quote": "Bia traduz."}]}]})
        resumed = assignment["answer_assignments"][0]["next_job_id"]
        self.brain.apply_analysis(resumed, interpreted(old_source, {"people": "Bia"}))
        final = self.brain.snapshot()
        self.assertEqual(final["context"]["people"]["value"], "Bia")
        self.assertEqual(next(q for q in final["questions"] if q["id"] == conflict["id"]), conflict)
        self.assertEqual(final["interview"]["context_conflicts"][-1]["status"], "pending")
        # A new response sees this conflict generation and can resolve it.
        fresh = self.brain.record_answer([conflict["id"]], "Confirmo: Bia traduz, Cia não.", uid())
        self.brain.apply_analysis(fresh["job_id"], interpreted(self.source(fresh["answer_id"]), {"people": "Bia"}))
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == conflict["id"])["status"], "resolved")

    def test_unknown_question_projects_through_the_actual_nora_repository(self):
        from company_pilot.nora import BrainRepository, Nora, QuestionStatus
        from company_pilot.operator import project_questions
        raw = self.brain.interview_defer("people", "Ainda preciso confirmar a equipe.", uid())
        company_id = self.brain.snapshot()["company"]["id"]
        repository = BrainRepository(company_id, self.brain.transact_nora)
        nora = Nora(company_id, repository, lambda: datetime(2026, 11, 17, 12, tzinfo=timezone.utc), object())
        self.assertEqual(project_questions(self.brain, nora), 1)
        question = repository.transact(company_id, lambda state: state.questions[raw["question_id"]])
        self.assertEqual(question.status, QuestionStatus.PENDING)
        self.assertEqual(question.missing_links, ("performer",))
        self.assertIn(raw["source_id"], question.observation_ids)
        self.assertIn("confirmar a equipe", question.prompt)

    def test_unchanged_selected_value_does_not_hide_new_question_generation(self):
        gap = self.brain.interview_defer("people", "Preciso confirmar.", uid())
        correction = self.brain.interview_correct("people", "Bia traduz.", uid())
        self.brain.apply_analysis(correction["job_id"], interpreted(self.source(correction["source_id"]), {"people": "Bia"}))
        old = self.brain.record_answer([], "Confirmo Bia.", uid(), candidate_question_ids=[gap["question_id"]])
        selected = copy.deepcopy(self.brain.snapshot()["context"]["people"])
        later = self.brain.interview_answer("Cia traduz.", uid())
        self.brain.apply_analysis(later["job_id"], interpreted(self.source(later["source_id"]), {"people": "Cia"}))
        self.assertEqual(self.brain.snapshot()["context"]["people"], selected)
        conflict = next(q for q in self.brain.snapshot()["questions"] if q.get("target_kind") == "context_conflict")
        assignment = self.brain.apply_analysis(old["job_id"], {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
            "answer_assignments": [{"answer_id": old["answer_id"], "question_ids": [gap["question_id"]], "evidence": [{"source_id": old["answer_id"], "quote": "Confirmo Bia."}]}]})
        self.brain.apply_analysis(assignment["answer_assignments"][0]["next_job_id"], interpreted(self.source(old["answer_id"]), {"people": "Bia"}))
        self.assertEqual(next(q for q in self.brain.snapshot()["questions"] if q["id"] == conflict["id"]), conflict)

    def test_ambiguous_correction_keeps_prior_context_and_gets_answer_path(self):
        self.complete()
        before = copy.deepcopy(self.brain.snapshot()["context"])
        raw = self.brain.interview_correct("activities", "Talvez a descrição anterior esteja errada; vou confirmar.", uid())
        self.brain.apply_analysis(raw["job_id"], {"tasks": [], "nodes": [], "context_updates": [], "next_question": None})
        self.assertEqual(self.brain.snapshot()["context"], before)
        progress = self.brain.interview_next()
        self.assertEqual(progress["state"], "complete")
        self.assertEqual(progress["deferred_topics"], ["activities"])
        question = next(q for q in self.brain.snapshot()["questions"] if q["id"] in progress["pending_context_question_ids"])
        self.assertEqual(question["target_kind"], "context_deferred")
        self.assertNotIn(before["activities"]["value"], self.brain.device_policy()["work_signals"])
        reply = self.brain.record_answer([question["id"]], "Confirmo: a atividade é revisão de textos.", uid())
        self.brain.apply_analysis(reply["job_id"], interpreted(self.source(reply["answer_id"]), {"activities": "Revisão de textos"}))
        self.assertEqual(self.brain.snapshot()["context"]["activities"]["value"], "Revisão de textos")
        self.assertEqual(self.brain.interview_next()["deferred_topics"], [])

    def test_stale_corrections_and_multiple_alternatives_remain_tracked(self):
        self.complete()
        first = self.brain.interview_correct("tracking", "O acompanhamento está no sistema A.", uid())
        second = self.brain.interview_correct("tracking", "O acompanhamento está no sistema B.", uid())
        self.brain.apply_analysis(first["job_id"], interpreted(self.source(first["source_id"]), {"tracking": "Sistema A"}))
        self.brain.apply_analysis(second["job_id"], interpreted(self.source(second["source_id"]), {"tracking": "Sistema B"}))
        self.assertEqual(self.brain.snapshot()["context"]["tracking"]["value"], "Sistema A")
        self.assertTrue(any(q.get("target_kind") == "context_conflict" and q["status"] == "pending" for q in self.brain.snapshot()["questions"]))
        raw = self.brain.interview_correct("activities", "Podemos estar traduzindo ou revisando; preciso confirmar.", uid())
        output = interpreted(self.source(raw["source_id"]), {"activities": "Tradução"})
        output["context_updates"].append({**output["context_updates"][0], "value": "Revisão"})
        self.brain.apply_analysis(raw["job_id"], output)
        self.assertEqual(self.brain.snapshot()["context"]["activities"]["value"], BUSINESSES[0]["activities"])
        entry = self.brain.snapshot()["interview"]["deferred_topics"]["activities"]
        self.assertEqual(len(entry["candidate_claims"]), 2)

    def test_unrelated_model_updates_rejected_and_not_applicable_is_a_fact(self):
        values = {**BUSINESSES[0], "tracking": "Não se aplica: não há pagamentos neste exemplo"}
        self.complete(values)
        self.assertEqual(self.brain.interview_next()["deferred_topics"], [])
        self.assertEqual(self.brain.snapshot()["context"]["tracking"]["basis"], "reported")
        before = copy.deepcopy(self.brain.snapshot()["context"])
        raw = self.brain.interview_correct("documents", "O briefing corrigido é V3.", uid())
        with self.assertRaisesRegex(BrainError, "correction_topic_mismatch"):
            self.brain.apply_analysis(raw["job_id"], interpreted(self.source(raw["source_id"]), {"documents": "Briefing V3", "people": "Pessoa não solicitada"}))
        self.assertEqual(self.brain.snapshot()["context"], before)
        self.assertEqual(self.source(raw["source_id"])["text"], "O briefing corrigido é V3.")


if __name__ == "__main__":
    unittest.main()
