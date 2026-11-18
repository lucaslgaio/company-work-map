"""Public CLI failure paths against a real local Brain; no remote access."""
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.brain import AccessDenied, Brain, LocalStore
from company_pilot.cli import main
from company_pilot.operator import write_private_json


class FailureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.store = LocalStore(self.root / "data")
        write_private_json(self.config, {
            "schema_version": 1, "mode": "synthetic",
            "store": {"kind": "local", "root": str(self.store.root)},
            "nora": {"enabled": False},
        })

    def invoke(self, *args, **kwargs):
        out, err = StringIO(), StringIO()
        code = main(["--config", str(self.config), *args], out=out, err=err, **kwargs)
        self.assertEqual((code, out.getvalue()), (2, ""))
        return json.loads(err.getvalue())

    def test_empty_destination_identified_without_creating_another_company(self):
        result = self.invoke("status")
        self.assertEqual(result["codigo"], "DADOS_NAO_ACEITOS")
        self.assertEqual(result["subcodigo"], "brain_not_initialized")
        self.assertIn("branch", result["acao"])
        self.assertIn("operador", result["acao"])
        self.assertIsNone(self.store.read().head)

    def test_native_write_does_not_imply_founder_authority(self):
        Brain(self.store).initialize("Synthetic workshop")
        before = self.store.read().head
        choices = self.root / "images.json"
        choices.write_text('{"founder": false, "team": false}')
        participant = Brain(LocalStore(self.store.root, actor="fixture-member", permissions={"read", "write"}))
        result = self.invoke("policy", "set", "--json-file", str(choices),
                             brain_factory=lambda _config: participant)
        self.assertEqual(result["codigo"], "ACESSO_NAO_CONFIRMADO")
        self.assertEqual(result["subcodigo"], "founder_authority_required")
        self.assertIn("fundador", result["mensagem"])
        self.assertIn("enroll-founder", result["acao"])
        self.assertEqual(self.store.read().head, before)

    def test_real_data_refused_by_development_adapter_has_specific_remedy(self):
        # A deliberately wrong factory reproduces the adapter/mode boundary,
        # without constructing a remote store or making any network request.
        self.config = self.root / "new-config.json"
        result = self.invoke("init", "--name", "Synthetic workshop", "--mode", "company",
                             "--store", "github", "--repo", "fixture/private",
                             brain_factory=lambda _config: Brain(self.store))
        self.assertEqual(result["subcodigo"], "development_adapter_accepts_synthetic_only")
        self.assertIn("company-state", result["acao"])
        self.assertFalse(self.config.exists())
        self.assertIsNone(self.store.read().head)

    def test_internal_code_with_private_suffix_is_not_a_public_diagnostic(self):
        def failed_factory(_config):
            raise AccessDenied("founder_authority_required: TOKEN=PRIVATE-VALUE")
        result = self.invoke("status", brain_factory=failed_factory)
        self.assertEqual(result["codigo"], "ACESSO_NAO_CONFIRMADO")
        self.assertNotIn("subcodigo", result)
        self.assertNotIn("PRIVATE-VALUE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
