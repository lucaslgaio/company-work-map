"""Offline request budgets through actual Brain/GitHubStore/operator calls."""
from datetime import datetime
import importlib.util
from pathlib import Path
import tempfile
import unittest

from company_pilot.brain import Brain
from company_pilot.nora import BrainRepository, Nora, Route
from company_pilot.operator import Operator


def fixture_store(root):
    path = Path(__file__).resolve().parents[1] / "brain" / "test_github_store.py"
    spec = importlib.util.spec_from_file_location("pilot_request_budget_fake", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeGitHub(str(root))


class NoInterpreter:
    def interpret(self, *_args): raise AssertionError("idle fixture must not invoke interpretation")


class NoSend:
    def send(self, *_args): raise AssertionError("off-slot fixture must not send")


class RequestBudgetTests(unittest.TestCase):
    def _measure(self, operator, store):
        operator.once()  # Establish Nora encoding/projection before counting steady loops.
        counts = []
        for _ in range(3):
            start = len(store.calls)
            self.assertEqual(operator.once()["status"], "idle")
            calls = store.calls[start:]
            self.assertTrue(all(method == "GET" for _path, method, _data in calls))
            counts.append(len(calls))
        self.assertEqual(len(set(counts)), 1)
        self.assertLessEqual(counts[0], 28, "idle request budget must not grow; lower is welcome")
        self.assertEqual(operator.poll_seconds, 60)

    def test_analysis_idle_once_budget(self):
        from company_pilot.inference import SemanticWorker
        with tempfile.TemporaryDirectory() as directory:
            store = fixture_store(Path(directory))
            brain = Brain(store); brain.initialize("Empresa sintética de orçamento de requisições", synthetic=True)
            self._measure(Operator(SemanticWorker(brain, NoInterpreter()), brain=brain), store)

    def test_nora_only_idle_budget_with_pending_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            store = fixture_store(Path(directory))
            brain = Brain(store); brain.initialize("Empresa sintética de orçamento Nora", synthetic=True)
            brain.interview_answer("Prestamos manutenção.", "ac42c69a-398e-45c3-9014-de459cfa0d10")
            company = brain.snapshot()["company"]["id"]
            repository = BrainRepository(company, brain.transact_nora)
            route = Route(company, "synthetic-route", 1, "synthetic-account", "synthetic-conversation", "synthetic-sender", store.login, True)
            repository.transact(company, lambda state: setattr(state, "route", route))
            nora = Nora(company, repository, lambda: datetime.fromisoformat("2026-11-18T07:00:00-03:00"), NoSend())
            operator = Operator(None, brain=brain, nora=nora)
            self._measure(operator, store)
            self.assertEqual(operator.failures, 0)
            self.assertEqual(len(brain.snapshot()["pending_jobs"]), 1)


if __name__ == "__main__": unittest.main()
