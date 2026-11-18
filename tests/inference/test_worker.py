import unittest

from company_pilot.inference import InterpreterError, SemanticWorker


class Brain:
    def __init__(self):
        self.jobs = [{"id": "job-1", "kind": "observation", "source_ids": ["source-1"],
                      "task_ids": []}]
        self.errors = []
        self.applied = []
        self.context = {"company": {}, "context": {}, "sources": [], "tasks": [], "questions": []}

    def pending_jobs(self):
        return self.jobs[:]

    def snapshot(self):
        return self.context

    def apply_analysis(self, job_id, result):
        self.applied.append((job_id, result))
        self.jobs = [job for job in self.jobs if job["id"] != job_id]
        return {"job_id": job_id, "revision": "revision-1"}

    def mark_job_error(self, job_id, code):
        self.errors.append((job_id, code))
        return {"job_id": job_id, "error_code": code}


class Interpreter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def interpret(self, job, context):
        self.calls.append((job, context))
        if self.error:
            raise self.error
        return {"synthetic": "validated result"}


class SemanticWorkerTests(unittest.TestCase):
    def test_applies_one_job_with_current_snapshot(self):
        brain = Brain()
        brain.jobs.append(dict(brain.jobs[0], id="job-2"))
        interpreter = Interpreter()
        result = SemanticWorker(brain, interpreter).run_once()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(interpreter.calls[0][1], brain.context)
        self.assertEqual([job["id"] for job in brain.jobs], ["job-2"])

    def test_model_error_records_code_and_preserves_job_for_retry(self):
        brain = Brain()
        interpreter = Interpreter(InterpreterError("subscription_unavailable"))
        worker = SemanticWorker(brain, interpreter)
        result = worker.run_once()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(brain.errors, [("job-1", "subscription_unavailable")])
        self.assertEqual(len(brain.jobs), 1)
        self.assertEqual(brain.applied, [])
        interpreter.error = None
        self.assertEqual(worker.run_once()["status"], "applied")

    def test_unexpected_interpreter_error_never_exposes_details(self):
        brain = Brain()
        result = SemanticWorker(brain, Interpreter(RuntimeError("API_KEY=secret"))).run_once()
        self.assertEqual(result["error_code"], "interpretation_failed")
        self.assertNotIn("secret", str(result))
        self.assertEqual(len(brain.jobs), 1)

    def test_brain_rejection_preserves_job(self):
        brain = Brain()
        def reject(*_args):
            raise ValueError("invalid quote containing private text")
        brain.apply_analysis = reject
        result = SemanticWorker(brain, Interpreter()).run_once()
        self.assertEqual(result["error_code"], "analysis_rejected")
        self.assertEqual(brain.errors, [("job-1", "analysis_rejected")])
        self.assertEqual(len(brain.jobs), 1)

    def test_error_receipt_failure_is_visible_without_erasing_job(self):
        brain = Brain()
        def fail(*_args):
            raise OSError("disk details")
        brain.mark_job_error = fail
        result = SemanticWorker(brain, Interpreter(InterpreterError("model_timeout"))).run_once()
        self.assertFalse(result["error_recorded"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(brain.jobs), 1)

    def test_idle_never_invokes_model(self):
        brain = Brain()
        brain.jobs = []
        interpreter = Interpreter()
        self.assertEqual(SemanticWorker(brain, interpreter).run_once(), {"status": "idle"})
        self.assertEqual(interpreter.calls, [])


if __name__ == "__main__":
    unittest.main()
