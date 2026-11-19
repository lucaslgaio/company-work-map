"""0.1.3: work on the work computer is shared unless it is clearly personal."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.desktop.collector import Collector
from company_pilot.desktop.personal import personal_reason, redact_secrets
from company_pilot.desktop.policy import Policy
from company_pilot.local_cycle import run_local_cycle
from test_collector import DEVICE, NOW, NativeStub, policy


class WorkByDefaultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "queue.sqlite"
        from company_pilot.desktop.outbox import Outbox
        self.box = Outbox(self.path)
        self.current = policy(True)
        self.native = NativeStub("Cronograma sem nome confirmado")
        self.collector = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: NOW, episode_seconds=0)

    def tearDown(self):
        self.box.close()
        self.directory.cleanup()

    def payload(self):
        return json.loads(self.box.db.execute("SELECT payload FROM events ORDER BY created_at LIMIT 1").fetchone()[0])

    def test_window_without_a_confirmed_name_is_shared(self):
        self.current = replace(self.current, work_markers=(), record_refs=())
        self.native.surface.update(title="Medição do bloco B", text="Concretagem da laje", record_ref="")
        result = self.collector.tick()
        self.assertEqual(result["classification"], "company")
        self.assertEqual(self.payload()["classification"]["reason"], "work_computer_default")
        self.assertIn("Concretagem da laje", self.payload()["evidence"]["text"])
        self.assertEqual(self.box.quarantine_pending(NOW.timestamp()), [])

    def test_personal_denylist_never_leaves_the_machine(self):
        samples = (
            {"title": "Nubank", "app_id": "br.com.nubank", "record_ref": "https://app.nubank.com.br/home"},
            {"title": "Netflix", "app_id": "com.netflix.Netflix", "record_ref": ""},
            {"title": "Instagram", "app_id": "com.burbn.instagram", "record_ref": "https://www.instagram.com/"},
            {"title": "1Password", "app_id": "com.1password.1password", "record_ref": ""},
            {"title": "Consulta médica", "app_id": "com.apple.Health", "record_ref": "https://www.doctoralia.com.br/"},
            {"title": "Tinder", "app_id": "com.cardify.tinder", "record_ref": ""},
            {"title": "Mail - Incognito", "app_id": "com.google.Chrome", "record_ref": ""},
            {"title": "Tela Bloqueada", "app_id": "loginwindow", "record_ref": ""},
        )
        for surface in samples:
            with self.subTest(surface=surface["title"]):
                self.assertTrue(str(personal_reason(surface)).startswith("clearly_personal"))
                self.native = NativeStub()
                self.native.surface.update(**surface, text="SEGREDO DA JANELA")
                collector = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: NOW, episode_seconds=0)
                self.assertEqual(collector.tick()["status"], "excluded")
                self.assertEqual((self.native.text_calls, self.native.image_calls), (0, 0))
        self.assertEqual(self.box.status(), {})
        self.assertNotIn(b"SEGREDO", self.path.read_bytes())

    def test_work_surfaces_are_not_caught_by_the_denylist(self):
        for surface in (
            {"title": "Fluxo de caixa da obra", "app_id": "com.microsoft.Excel", "record_ref": ""},
            {"title": "WhatsApp", "app_id": "net.whatsapp.WhatsApp", "record_ref": ""},
            {"title": "YouTube - treinamento de forma", "app_id": "com.google.Chrome", "record_ref": "https://www.youtube.com/watch?v=abc"},
            {"title": "NR-18 saúde e segurança", "app_id": "com.microsoft.Word", "record_ref": ""},
        ):
            with self.subTest(surface=surface["title"]):
                self.assertIsNone(personal_reason(surface))
                self.assertEqual(replace(self.current, work_markers=()).classify(surface, NOW).state, "company")

    def test_local_exclusion_is_honored_exactly(self):
        self.current = replace(self.current, personal_markers=("meu arquivo privado",))
        self.native.surface.update(title="Planilha meu arquivo privado", text="NAO PODE SAIR")
        self.assertEqual(self.collector.tick()["status"], "excluded")
        self.assertEqual(self.native.text_calls, 0)
        self.assertNotIn(b"NAO PODE SAIR", self.path.read_bytes())
        self.native = NativeStub()
        self.native.surface.update(title="Planilha da obra", text="pode sair")
        collector = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: NOW, episode_seconds=0)
        self.assertEqual(collector.tick()["classification"], "company")

    def test_labeled_secrets_are_redacted_and_secure_windows_stay_out(self):
        self.assertEqual(redact_secrets("senha: caça-123\nMedição 80%"), "senha: [redigido]\nMedição 80%")
        self.current = replace(self.current, work_markers=())
        self.native.surface.update(title="Diário da obra", text="password: super-secret\nConcretagem feita")
        self.assertEqual(self.collector.tick()["classification"], "company")
        body = json.dumps(self.payload())
        self.assertNotIn("super-secret", body)
        self.assertIn("[redigido]", body)
        self.assertIn("Concretagem feita", body)
        self.native.surface["secure_context"] = True
        self.native.surface["text"] = "senha: outro-segredo"
        blocked = Collector(DEVICE, self.native, self.box, lambda: self.current, lambda: NOW, episode_seconds=0)
        before = self.native.text_calls
        self.assertEqual(blocked.tick()["status"], "excluded")
        self.assertEqual(self.native.text_calls, before)

    def test_old_quarantine_is_reread_once_and_an_empty_queue_is_safe(self):
        surface = {"app_id": "test.browser", "window_id": "9", "process_id": 3, "title": "Cronograma geral",
                   "record_ref": "", "evidence_level": "synthetic"}
        self.assertTrue(self.box.quarantine_metadata(DEVICE, surface, NOW.isoformat(), NOW.timestamp()))
        personal = dict(surface, title="Spotify", window_id="10")
        self.assertTrue(self.box.quarantine_metadata(DEVICE, personal, NOW.isoformat(), NOW.timestamp()))
        self.native.surface.update(title="outra", window_id="ausente")
        self.current = replace(self.current, work_markers=(), screenshot_allowed=False)
        promoted = self.collector.reclassify_quarantine()
        self.assertEqual(promoted, 1)
        rows = [json.loads(row[0]) for row in self.box.db.execute("SELECT payload FROM events")]
        self.assertTrue(any("Cronograma geral" in row["evidence"]["text"] and row["classification"]["state"] == "company" for row in rows))
        self.assertNotIn("Spotify", json.dumps(rows))
        self.assertEqual(self.box.quarantine_pending(NOW.timestamp()), [])
        self.assertEqual(self.collector.reclassify_quarantine(), 0)

    def test_missing_screen_permission_keeps_text_and_a_granted_one_stores_the_image(self):
        self.current = replace(self.current, work_markers=())
        self.native.surface.update(title="Planilha de medição", text="Linha 12 conferida")
        self.native.image_error = "screen_permission_denied"
        denied = self.collector.tick()
        self.assertEqual(denied["classification"], "company")
        self.assertIn("screen_permission_denied", self.payload()["capture_gaps"])
        self.assertNotIn("image_sha256", self.payload()["evidence"])
        self.assertIn("Linha 12 conferida", self.payload()["evidence"]["text"])
        self.box.db.execute("DELETE FROM events")
        self.native.image_error = None
        self.native.image_calls = 0
        self.native.surface["text"] = "Linha 13 conferida"
        granted = self.collector.tick()
        self.assertEqual(granted["classification"], "company")
        stored = json.loads(self.box.db.execute("SELECT payload FROM events ORDER BY created_at DESC LIMIT 1").fetchone()[0])
        self.assertIn("image_sha256", stored["evidence"])
        self.assertEqual(self.native.image_calls, 1)
        self.collector.tick()
        self.assertEqual(self.native.image_calls, 1)

    def test_shared_capture_becomes_an_activity_and_a_chain(self):
        bare = replace(policy(False), work_markers=(), record_refs=())
        self.assertEqual(bare.classify({"title": "Cotar a lista de material", "app_id": "com.microsoft.Excel"}, NOW).state, "company")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        first = "Ana vai cotar a lista de material.\nEntrada: cronograma atualizado\nResultado pronto: lista de material pronta"
        second = "Bia vai atualizar o cronograma.\nResultado pronto: cronograma atualizado\nMandei o cronograma atualizado para Cotar a lista de material."
        captures = [
            {"when": "2026-11-18T12:00:00-03:00", "app": "Excel", "record_ref": "lista", "text": first},
            {"when": "2026-11-18T15:00:00-03:00", "app": "Excel", "record_ref": "cronograma", "text": second},
        ]
        result = run_local_cycle(root, captures, "Empresa fictícia", "Ana")
        page = (root / "views" / "mapa.md").read_text(encoding="utf-8")
        self.assertGreater(result["captures"], 0)
        self.assertIn("Cotar a lista de material", page)
        self.assertIn("Ana", page)
        self.assertNotIn("Ainda não há atividades", page)
        self.assertIn("→", page)


class ScreenshotReportTests(unittest.TestCase):
    def test_missing_permission_is_reported_in_portuguese_without_blocking_text(self):
        from datetime import timedelta
        from company_pilot.desktop.__main__ import screenshot_report
        from company_pilot.desktop.outbox import Outbox
        from company_pilot.desktop.policy import utcnow
        current = replace(policy(True), expires_at=utcnow() + timedelta(hours=1))
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / "queue.sqlite")
            try:
                report = screenshot_report(
                    {"capture_enabled": True}, current, box,
                    {"platform": "macOS", "accessibility_granted": True, "screen_recording_granted": False})
            finally:
                box.close()
        self.assertFalse(report["actually_taking"])
        self.assertTrue(report["text_capture_continues"])
        self.assertIn("Privacidade e Segurança", report["next_step"])
        self.assertIn("Gravação de Tela", report["next_step"])
        self.assertIn("launchctl kickstart", report["next_step"])


if __name__ == "__main__":
    unittest.main()
