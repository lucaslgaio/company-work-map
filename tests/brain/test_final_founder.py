"""Founder-visible outcomes: progress, carried answers, notes, lanes and the local cycle."""
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from company_pilot.brain import FIELDS
from company_pilot.brain.vault import search_notes
from company_pilot.local_cycle import run_local_cycle
import test_engine as fixtures
from test_round4_guards import empty


def uid():
    return str(uuid4())


class FinalFounderTests(unittest.TestCase):
    def setUp(self):
        from test_round4_guards import Round4PublicGuards
        self.base = Round4PublicGuards()
        self.base.setUp()
        self.addCleanup(self.base.temp.cleanup)

    def observe(self, *args, **kwargs):
        return self.base.observe(*args, **kwargs)

    def task(self, tid):
        return self.base.task(tid)

    def source(self, sid):
        return self.base.source(sid)

    @property
    def brain(self):
        return self.base.brain

    def test_later_citation_of_the_same_spreadsheet_is_progress(self):
        tid, _ = self.observe("Cronograma_Realizado conduítes 80%.", changes={"result": "Conduítes 80%"},
                              record="Cronograma_Realizado", when="2026-11-24T12:00:00+00:00")
        self.observe("No Cronograma_Realizado: Concluída, conferido Bia 26/11, 100%.", tid=tid,
                     changes={"result": "Concluída, conferido Bia 26/11, 100%"},
                     record="diario-obra um301", when="2026-11-26T15:00:00+00:00", update=self.base.progress())
        task = self.task(tid)
        self.assertEqual(task["fields"]["result"]["value"], "Concluída, conferido Bia 26/11, 100%")
        self.assertNotIn("result", task["conflicts"])
        page = self.brain.store.read().files[f"views/tasks/{tid}.md"]
        self.assertNotIn("Em conflito", page.split("## Resultado observável")[1].split("##")[0])

    def test_answer_before_the_task_fills_the_card_when_the_task_appears(self):
        raw = self.brain.record_message("o aditivo 01 tem que ir por escrito. o Ana monta e eu mando essa semana", uid())
        source = self.source(raw["answer_id"])
        observed = self.brain.ingest_observation(fixtures.observation("Aditivo 01 por escrito para assinatura."))
        seen = self.source(observed["observation_id"])
        fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
        fields["result"] = {"value": "Aditivo 01 por escrito", "basis": "observed",
                            "evidence": [{"source_id": seen["id"], "quote": seen["text"]}]}
        fields["action"] = {"value": "Fazer o aditivo 01", "basis": "observed",
                            "evidence": [{"source_id": seen["id"], "quote": seen["text"]}]}
        self.brain.apply_analysis(observed["job_id"], {**empty(), "tasks": [{"name": "Fazer o aditivo 01 por escrito", "fields": fields, "links": []}]})
        task = next(item for item in self.brain.snapshot()["tasks"] if "aditivo 01" in item["name"])
        self.assertEqual(task["fields"]["performer"]["value"], "Ana")
        self.assertIn(source["id"], {ref["source_id"] for ref in task["fields"]["performer"]["evidence"]})
        self.assertIn("manda", task["fields"]["handoff"]["value"])

    def test_correction_keeps_the_other_activity_claims(self):
        first = self.brain.interview_answer("A Empresa Alfa organiza obra residencial no Rio e cobra cerca de 10 a 15 por cento.", uid())
        self.brain.apply_analysis(first["job_id"], {**empty(), "context_updates": [{
            "topic": "activities", "value": "PMO de obra residencial no Rio, taxa de 10 a 15 por cento.",
            "basis": "reported", "evidence": [{"source_id": first["source_id"], "quote": first and self.source(first["source_id"])["text"]}]}]})
        second = self.brain.interview_answer("O cronograma e as três cotações também fazem parte do trabalho.", uid())
        self.brain.apply_analysis(second["job_id"], {**empty(), "context_updates": [{
            "topic": "activities", "value": "Cronograma e três cotações.",
            "basis": "reported", "evidence": [{"source_id": second["source_id"], "quote": self.source(second["source_id"])["text"]}],
            "mode": "add"}]})
        correction = self.brain.interview_correct("activities", "Ana rascunha o relatório e Lucas revisa.", uid())
        self.brain.apply_analysis(correction["job_id"], {**empty(), "context_updates": [{
            "topic": "activities", "value": "Ana rascunha o relatório e Lucas revisa.",
            "basis": "reported", "evidence": [{"source_id": correction["source_id"], "quote": self.source(correction["source_id"])["text"]}]}]})
        page = self.brain.store.read().files["views/empresa.md"]
        self.assertIn("obra residencial", page)
        self.assertIn("Cronograma", page)
        self.assertIn("Ana rascunha", page)
        people = self.brain.interview_answer("Lucas conduz, Ana cota e Bia confere medição.", uid())
        self.brain.apply_analysis(people["job_id"], {**empty(), "context_updates": [{
            "topic": "people", "value": "Lucas conduz, Ana cota e Bia confere medição.",
            "basis": "reported", "evidence": [{"source_id": people["source_id"], "quote": self.source(people["source_id"])["text"]}]}]})
        self.assertIn("Bia confere", self.brain.store.read().files["views/pessoas.md"])

    def test_notes_backlinks_search_lanes_and_readout_are_in_the_vault(self):
        tid, source = self.observe("Lista de material pronta.", changes={"result": "Lista de material pronta", "performer": "Ana"})
        files = self.brain.store.read().files
        notes = [path for path in files if path.startswith("notes/") and path.endswith(".md")]
        self.assertTrue(any(path.startswith("notes/tarefas/") for path in notes))
        self.assertIn("notes/indice.md", files)
        self.assertIn("notes/grafo.md", files)
        self.assertIn("notes/relacoes.md", files)
        task_note = next(text for path, text in files.items() if path.startswith("notes/tarefas/"))
        self.assertIn('type: "tarefa"', task_note)
        self.assertIn("[[", task_note)
        card = files[f"views/tasks/{tid}.md"]
        self.assertIn("Nota no cérebro", card)
        self.assertIn("## Ana", files["views/mapa.md"])
        self.assertIn("Quem mais repassa trabalho", files["views/insights.md"])
        self.assertIn("Leitura de segunda", files["views/leitura-segunda.md"])
        found = search_notes(files, "Lista de material")
        self.assertTrue(found)
        self.assertTrue(found[0]["path"].startswith("notes/"))
        gap = self.brain.ingest_context_gap({"schema_version": 1, "event_id": uid(), "device_id": uid(),
                                             "occurred_at": "2026-11-24T18:00:00+00:00", "app": "Notas",
                                             "surface_kind": "desktop_window", "synthetic": True,
                                             "privacy_disposition": "withheld"})
        files = self.brain.store.read().files
        self.assertNotIn(gap["source_id"], "\n".join(text for path, text in files.items() if path.startswith("notes/")))
        self.assertIn("Captura não mapeada", files["views/cobertura.md"])
        self.assertIn(source["id"], card)

    def test_local_cycle_with_no_captures_invents_nothing(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        result = run_local_cycle(Path(folder.name))
        self.assertEqual(result["captures"], 0)
        self.assertIn("Ainda não há captura para ler.", result["message"])
        self.assertIn("Nada foi inventado", result["message"])
        self.assertEqual(list(Path(folder.name).rglob("*")), [])

    def _lint_founder_pages(self, root):
        import re
        pages = list((root / "views").rglob("*.md")) + list((root / "notes").rglob("*.md"))
        self.assertTrue(pages)
        iso = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        for page in pages:
            text = page.read_text(encoding="utf-8")
            visible = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
            self.assertNotIn("local-fixture:", visible, page.name)
            self.assertNotIn("fixture-founder", visible, page.name)
            self.assertNotIn('created: ""', text, page.name)
            self.assertNotIn('updated: ""', text, page.name)
            lowered = text.casefold()
            for leak in ("offline-capture", "local_cycle", "local\\_cycle", "scripted", "task_id",
                         "\"basis\"", "basis:", "|handoff|", "provedor:", "modelo:", "confidence: \"pending\"",
                         "handoff", "notes/"):
                if leak == "notes/" and "leitura-segunda" not in page.name and "insights" not in page.name:
                    continue
                self.assertNotIn(leak, lowered if leak != "notes/" else text, f"{page.name} leaks {leak}")
            if "notes" in page.parts and page.name not in {"indice.md", "grafo.md", "relacoes.md"}:
                heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
                self.assertIsNone(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", heading), heading)
            self.assertIsNone(iso.search(visible), page.name)
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#")):
                    continue
                self.assertTrue((page.parent / target).resolve().is_file(), f"{page.name} -> {target}")
            if page.name == "pessoas.md":
                self.assertLess(text.count("\n- Ana.\n"), 1)
            if page.suffix == ".md" and "notes" in page.parts:
                heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
                self.assertNotIn("fixture-founder", heading)
                if text.startswith("---\n"):
                    end = text.find("\n---", 4)
                    block = text[4:end]
                    for row in block.splitlines():
                        key, _, value = row.partition(": ")
                        self.assertTrue(key.isidentifier() or key in {"type", "id", "fonte", "created", "updated", "confidence"})
                        self.assertTrue(value.startswith('"') and value.endswith('"'), row)
        stems = {note.stem for note in (root / "notes").rglob("*.md")}
        for note in (root / "notes").rglob("*.md"):
            for link in re.findall(r"\[\[([^\]]+)\]\]", note.read_text(encoding="utf-8")):
                self.assertIn(link, stems, f"{note.name} -> {link}")


if __name__ == "__main__":
    unittest.main()
