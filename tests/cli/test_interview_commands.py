"""CLI contract for the founder's explicit correction and truthful unknown paths."""
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from company_pilot.brain import AccessDenied
from company_pilot.cli import main
from company_pilot.operator import write_private_json


class InterviewCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.json"
        write_private_json(self.config, {"schema_version": 1, "mode": "synthetic", "store": {"kind": "local", "root": str(self.root / "brain")}, "nora": {"enabled": False}})
        self.text = self.root / "answer.txt"
        self.text.write_text("Ainda não sei quem assume essa responsabilidade.\n", encoding="utf-8")
        self.calls = []
        outer = self
        class Brain:
            def interview_correct(self, topic, text, event_id):
                outer.calls.append(("correct", topic, text, event_id))
                return {"state": "pending_analysis"}
            def interview_defer(self, topic, text, event_id):
                outer.calls.append(("defer", topic, text, event_id))
                return {"state": "deferred_unknown", "question_id": "synthetic-question"}
        self.brain = Brain()

    def tearDown(self): self.tmp.cleanup()
    def invoke(self, *args):
        out, err = StringIO(), StringIO()
        def no_model(_config): raise AssertionError("interview intake does not interpret locally")
        code = main(["--config", str(self.config), "interview", *args], brain_factory=lambda _config: self.brain,
                    interpreter_factory=no_model, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_correct_and_defer_pass_literal_text_topic_and_stable_id(self):
        event = "cb87ba38-d574-41c5-aedd-62fc7489a4eb"
        for verb, state in (("correct", "pending_analysis"), ("defer", "deferred_unknown")):
            code, out, err = self.invoke(verb, "--topic", "people", "--text-file", str(self.text), "--event-id", event)
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["state"], state)
            self.assertEqual(self.calls[-1], (verb, "people", self.text.read_text(), event))

    def test_invalid_topic_or_missing_text_is_rejected_before_mutation(self):
        for args in (("correct", "--topic", "invented", "--text-file", str(self.text), "--event-id", "unused"),
                     ("defer", "--topic", "people")):
            code, out, err = self.invoke(*args)
            self.assertEqual(code, 2)
            self.assertIn("ARGUMENTOS_INVALIDOS", err)
        self.assertEqual(self.calls, [])

    def test_wrong_actor_error_is_safe_and_never_echoes_private_exception(self):
        def refuse(*_args): raise AccessDenied("PRIVATE ACTOR DETAIL")
        self.brain.interview_correct = refuse
        code, out, err = self.invoke("correct", "--topic", "people", "--text-file", str(self.text), "--event-id", "cb87ba38-d574-41c5-aedd-62fc7489a4eb")
        self.assertEqual(code, 2)
        self.assertIn("ACESSO_NAO_CONFIRMADO", err)
        self.assertNotIn("PRIVATE", out + err)


if __name__ == "__main__": unittest.main()
