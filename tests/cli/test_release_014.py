"""The founder device stores work and does not call the model."""
import json
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from company_pilot.cli import main


class FounderDeviceTests(unittest.TestCase):
    def test_operator_once_never_builds_the_model_after_the_update(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "company.json"
            calls = []

            class Dummy:
                def interpret(self, *_args, **_kwargs):
                    raise AssertionError("o computador do fundador chamou o modelo")

            def factory(_config):
                calls.append("model")
                return Dummy()

            out, err = StringIO(), StringIO()
            code = main(["--config", str(config), "init", "--name", "Oficina Leste", "--mode", "synthetic",
                         "--store", "local", "--root", str(root / "brain")], out=out, err=err,
                        interpreter_factory=factory)
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(calls, [])
            out, err = StringIO(), StringIO()
            code = main(["--config", str(config), "operator", "once"], out=out, err=err, interpreter_factory=factory)
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(calls, ["model"])
            calls.clear()
            out, err = StringIO(), StringIO()
            code = main(["--config", str(config), "setup", "external-analyst", "--login", "analista-sintetico"],
                        out=out, err=err, interpreter_factory=factory)
            self.assertEqual(code, 0, err.getvalue())
            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(saved["operator"]["analysis"], "external")
            self.assertEqual(calls, [])
            out, err = StringIO(), StringIO()
            code = main(["--config", str(config), "operator", "once"], out=out, err=err, interpreter_factory=factory)
            self.assertEqual(code, 0, err.getvalue())
            report = json.loads(out.getvalue())
            self.assertEqual(report["status"], "external")
            self.assertFalse(report["local_model"])
            self.assertEqual(calls, [])
            out, err = StringIO(), StringIO()
            code = main(["--config", str(config), "operator", "watch", "--max-cycles", "1"],
                        out=out, err=err, interpreter_factory=factory)
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(calls, [])
            self.assertEqual(json.loads(out.getvalue())["analysis"], "not_run")


if __name__ == "__main__":
    unittest.main()
