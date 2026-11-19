"""Report-derived F7 fixtures, not a replay of the original Grok run.

Exercise the actual interview -> analysis -> persisted policy seam. Every source
is synthetic; original quotes must remain byte-for-byte evidence.
"""
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, LocalStore
from company_pilot.desktop.policy import Policy


class Round4MarkerTests(unittest.TestCase):
    def setUp(self):
        self.fresh_brain()

    def fresh_brain(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brain = Brain(LocalStore(Path(self.temp.name)))
        self.brain.initialize("Empresa sintética", synthetic=True)

    def answer(self, text):
        receipt = self.brain.interview_answer(text, str(uuid4()))
        source = next(source for source in self.brain.snapshot()["sources"]
                      if source["id"] == receipt["source_id"])
        return receipt, source

    def analysis(self, source, value, kind="record_name", operation="add", quote=None):
        ref = {"source_id": source["id"], "quote": source["text"] if quote is None else quote}
        return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
                "recognition_markers": [{"value": value, "kind": kind,
                                         "evidence": [ref], "operation": operation}]}

    def test_case_only_interview_marker_applies_with_raw_source_intact(self):
        raw, source = self.answer("O acompanhamento usa o diário de obra.")
        result = self.analysis(source, "Diário de obra")
        result["context_updates"] = [{"topic": "tracking", "value": "Diário de obra", "basis": "reported",
                                      "evidence": copy.deepcopy(result["recognition_markers"][0]["evidence"]),
                                      "mode": "add", "replaces": []}]
        self.brain.apply_analysis(raw["job_id"], result)
        snapshot = self.brain.snapshot()
        self.assertEqual(snapshot["pending_jobs"], [])
        self.assertEqual(snapshot["context"]["tracking"]["value"], "Diário de obra")
        marker = snapshot["recognition_markers"][0]
        self.assertEqual(marker["value"], "Diário de obra")
        self.assertEqual(marker["evidence"], result["recognition_markers"][0]["evidence"])
        self.assertEqual(next(item for item in snapshot["sources"]
                              if item["id"] == source["id"]), source)
        self.assertEqual(self.brain.device_policy()["work_signals"], ["Diário de obra"])

    def test_accent_and_whitespace_variants_apply_without_rewriting_evidence(self):
        for literal, term in (("diário de obra", "DIARIO DE OBRA"),
                              ("diario de obra", "Diário de obra"),
                              ("dia\u0301rio de obra", "Diário de obra"),
                              ("diário\n  de\tobra", "Diário de obra"),
                              ("empresa alfa", "Empresa Alfa"),
                              ("EMPRESA ÁLFA", "Empresa Alfa")):
            with self.subTest(literal=literal, term=term):
                self.fresh_brain()
                raw, source = self.answer("Usamos «" + literal + "».")
                original = copy.deepcopy(source)
                output = self.analysis(source, term)
                self.brain.apply_analysis(raw["job_id"], output)
                marker = next(item for item in self.brain.snapshot()["recognition_markers"]
                              if item["source_id"] == source["id"])
                self.assertEqual(marker["evidence"][0]["quote"], source["text"])
                self.assertEqual(source, original)
                self.assertEqual(next(item for item in self.brain.snapshot()["sources"]
                                      if item["id"] == source["id"]), original)

    def test_normalized_update_and_retirement_preserve_identity_and_history(self):
        first, source = self.answer("O registro é Diário de obra.")
        self.brain.apply_analysis(first["job_id"], self.analysis(source, "Diário de obra"))
        before = copy.deepcopy(self.brain.snapshot()["recognition_markers"][0])
        later, current = self.answer("O nome continua DIARIO DE OBRA.")
        self.brain.apply_analysis(later["job_id"], self.analysis(current, "DIARIO DE OBRA"))
        markers = self.brain.snapshot()["recognition_markers"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["id"], before["id"])
        historical = {key: before[key] for key in ("value", "evidence", "source_id", "status")}
        self.assertEqual(markers[0]["history"], [historical])
        retire, retirement = self.answer("Retire o marcador diario de obra.")
        self.brain.apply_analysis(retire["job_id"], self.analysis(retirement, "Diario de obra", operation="retire"))
        final = self.brain.snapshot()
        self.assertEqual(len(final["recognition_markers"]), 1)
        self.assertEqual(final["recognition_markers"][0]["status"], "retired")
        self.assertEqual(final["recognition_markers"][0]["history"][0], historical)
        self.assertEqual(self.brain.device_policy()["work_signals"], [])
        self.assertEqual(next(item for item in final["sources"] if item["id"] == source["id"]), source)

    def test_word_boundary_superset_and_changed_punctuation_do_not_authorize_names(self):
        for text, term in (("O registro cita obra.", "Diário de obra"),
                           ("O registro é Empresa Alfal.", "Empresa Alfa"),
                           ("O registro é aEmpresa Alfa.", "Empresa Alfa"),
                           ("O registro é Empresa Alfa.", "EmpresaAlfa"),
                           ("O registro é diário-de-obra.", "Diário de obra"),
                           ("O registro é diário de obra.", "Diário da obra")):
            with self.subTest(text=text, term=term):
                self.fresh_brain()
                raw, source = self.answer(text)
                with self.assertRaisesRegex(BrainError, "marker_name_must_be_literal_in_source"):
                    self.brain.apply_analysis(raw["job_id"], self.analysis(source, term))
                self.assertEqual(self.brain.snapshot()["recognition_markers"], [])

    def test_forged_normalized_quote_and_unrelated_source_remain_rejected(self):
        raw, source = self.answer("O registro é diário de obra.")
        with self.assertRaisesRegex(BrainError, "source_quote_not_found"):
            self.brain.apply_analysis(raw["job_id"], self.analysis(source, "Diário de obra", quote="Caderno de campo"))
        self.brain.apply_analysis(raw["job_id"], {"tasks": [], "nodes": [], "context_updates": [], "next_question": None})
        unrelated, other = self.answer("Outro registro é Empresa Alfa.")
        with self.assertRaisesRegex(BrainError, "unavailable_or_out_of_scope_source"):
            self.brain.apply_analysis(unrelated["job_id"], self.analysis(source, "Diário de obra"))
        self.assertEqual(self.brain.snapshot()["recognition_markers"], [])

    def test_generic_markers_remain_rejected_after_normalization(self):
        for term in ("Chrome", "GMAIL", "Plánilha", "WhatsApp!", "«Banco»", "E-mail"):
            with self.subTest(term=term):
                self.fresh_brain()
                raw, source = self.answer("Usamos " + term + ".")
                with self.assertRaisesRegex(BrainError, "app_only_or_generic_marker_not_authorized"):
                    self.brain.apply_analysis(raw["job_id"], self.analysis(source, term))
                self.assertEqual(self.brain.snapshot()["recognition_markers"], [])

    def test_invalid_marker_schema_stays_closed(self):
        raw, source = self.answer("O registro é Diário de obra.")
        for change in ({"kind": "app"}, {"operation": "replace"}, {"unsupported": True}, {"value": "ab"}):
            with self.subTest(change=change):
                output = self.analysis(source, "Diário de obra")
                output["recognition_markers"][0].update(change)
                with self.assertRaisesRegex(BrainError, "invalid_grounded_marker"):
                    self.brain.apply_analysis(raw["job_id"], output)
                self.assertEqual(self.brain.snapshot()["recognition_markers"], [])

    def test_personal_source_never_enters_marker_analysis(self):
        text = "Diário de obra pessoal."
        payload = {"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()),
                   "occurred_at": "2026-11-17T12:00:00+00:00", "policy_version": 1,
                   "source": {"kind": "active_window", "app": "Synthetic Editor", "origin": "fixture"},
                   "classification": {"label": "personal", "reason": "explicitly personal", "context_version": 1},
                   "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}
        with self.assertRaisesRegex(BrainError, "classified_company_only"):
            self.brain.ingest_observation(payload)
        self.assertEqual(self.brain.snapshot()["sources"], [])
        self.assertEqual(self.brain.snapshot()["recognition_markers"], [])

    def test_normalized_alias_does_not_classify_generic_apps_and_weak_names_as_company(self):
        raw, source = self.answer("A empresa se chama empresa alfa e o registro é Obra.")
        output = self.analysis(source, "Empresa Alfa", kind="company_alias")
        output["recognition_markers"] += self.analysis(source, "Obra")["recognition_markers"]
        self.brain.apply_analysis(raw["job_id"], output)
        # Pure local policy fixture; this neither grants consent nor captures a surface.
        policy = replace(Policy.from_brain(self.brain.device_policy()),
                         capture_allowed=True, local_exclusions_bound=True)
        at = datetime.now(timezone.utc)
        self.assertEqual(policy.classify({"title": "EMPRESA ALFA — controle", "app_id": "Gmail"}, at).state, "company")
        for surface in ({"title": "Inbox", "app_id": "Gmail", "text": "Empresa Alfa"},
                        {"title": "Página inicial", "app_id": "Chrome"},
                        {"title": "Empresa Alfal", "app_id": "Chrome"},
                        {"title": "Obra", "app_id": "Gmail"}):
            with self.subTest(surface=surface):
                self.assertEqual(policy.classify(surface, at).state, "uncertain")


if __name__ == "__main__":
    unittest.main()
