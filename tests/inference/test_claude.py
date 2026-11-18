import copy
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from company_pilot.inference import ClaudeInterpreter, InterpreterError
from company_pilot.inference.claude import ISOLATION_FLAGS, _bounded_run
from company_pilot.inference.schema import FIELD_NAMES, validate_analysis


def task(task_id=None):
    return {"task_id": task_id, "name": "Registrar pagamento",
            "fields": {name: {"value": None, "basis": "inferred", "evidence": []}
                       for name in FIELD_NAMES}, "links": []}


def analysis(tasks=None, question=None):
    return {"tasks": [task()] if tasks is None else tasks,
            "context_updates": [], "nodes": [], "next_question": question}


def stream(result, *, tools=None, mcp=None, calls=None):
    # Shape observed in the successful subscription CLI feasibility receipt.
    if calls is None:
        calls = [{"type": "tool_use", "id": "schema-1", "name": "StructuredOutput",
                  "input": result}]
    events = [
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5",
         "tools": ["StructuredOutput"] if tools is None else tools,
         "mcp_servers": [] if mcp is None else mcp},
        {"type": "assistant", "message": {"content": calls}},
        {"type": "result", "subtype": "success", "is_error": False,
         "structured_output": result},
    ]
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


class FakeRunner:
    def __init__(self, output=None, auth=None, error=None, returncode=0):
        self.output = stream(analysis()) if output is None else output
        self.auth = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
        if auth is not None:
            self.auth = auth
        self.error = error
        self.returncode = returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        # Check isolation at the moment the child would start, before cleanup.
        self.calls.append((list(command), kwargs, list(Path(kwargs["cwd"]).iterdir())))
        if "auth" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.auth), "secret stderr")
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, self.output, "secret stderr")


class ClaudeInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "job-1", "kind": "observation", "source_ids": ["source-1"],
                    "task_ids": []}
        self.context = {"company": {"name": "Exemplo"}, "context": {},
                        "sources": [{"id": "source-1", "text": "Registrou o pagamento."}],
                        "tasks": [], "questions": []}

    def interpret(self, runner, job=None, context=None, **kwargs):
        return ClaudeInterpreter("/fake/claude", runner=runner, **kwargs).interpret(
            self.job if job is None else job, self.context if context is None else context)

    def assert_code(self, code, runner, **kwargs):
        with self.assertRaises(InterpreterError) as raised:
            self.interpret(runner, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def test_structured_output_and_subscription_isolation(self):
        runner = FakeRunner()
        environment = {
            "HOME": "/fake/home", "PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "subscription-secret",
            "ANTHROPIC_API_KEY": "api-secret", "ANTHROPIC_AUTH_TOKEN": "api-secret",
            "ANTHROPIC_BASE_URL": "https://wrong.invalid", "OPENAI_API_KEY": "api-secret",
            "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDECODE": "1", "CLAUDE_CODE_SIMPLE": "1",
        }
        with patch.dict(os.environ, environment, clear=True):
            result = self.interpret(runner)
        self.assertEqual(result, analysis())
        self.assertEqual(len(runner.calls), 2)
        auth_command, auth_args, _ = runner.calls[0]
        command, args, contents = runner.calls[1]
        self.assertEqual(auth_command[-3:], ["auth", "status", "--json"])
        self.assertEqual(auth_args["cwd"], args["cwd"])
        self.assertEqual(contents, [])
        self.assertFalse(Path(args["cwd"]).exists())
        self.assertEqual(command[1:1 + len(ISOLATION_FLAGS)], list(ISOLATION_FLAGS))
        self.assertNotIn("--bare", command)
        self.assertIn("--verbose", command)
        self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
        self.assertEqual(args["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "subscription-secret")
        self.assertEqual(set(args["env"]), {"HOME", "PATH", "CLAUDE_CODE_OAUTH_TOKEN"})
        self.assertNotIn("secret", args["input"])

    def test_observation_can_refine_only_a_supplied_candidate(self):
        context = copy.deepcopy(self.context)
        context["tasks"] = [{"id": "existing-task", "name": "Tarefa em andamento"}]
        result = analysis([task("existing-task")])
        self.assertEqual(self.interpret(FakeRunner(output=stream(result)), context=context), result)
        self.assert_code("invalid_analysis", FakeRunner(output=stream(analysis([task("unseen-task")]))), context=context)

    def test_disambiguation_is_bounded_to_explicit_candidate_question_ids(self):
        job = {**self.job, "kind": "disambiguation", "unassigned_answer_id": "answer-1", "candidate_question_ids": ["question-1"]}
        result = {**analysis([]), "answer_assignments": [{"answer_id": "answer-1", "question_ids": ["question-1"], "evidence": [{"source_id": "source-1", "quote": "Registrou o pagamento."}]}]}
        self.assertEqual(self.interpret(FakeRunner(output=stream(result)), job=job), result)
        result["answer_assignments"][0]["question_ids"] = ["unseen-question"]
        self.assert_code("invalid_analysis", FakeRunner(output=stream(result)), job=job)

    def test_completed_interview_correction_uses_explicit_scoped_instruction(self):
        job = {**self.job, "kind": "interview", "interview_action": {"mode": "correct", "topic": "activities"}}
        result = {**analysis([]), "context_updates": [{"topic": "activities", "value": "Revisão", "basis": "reported", "evidence": [{"source_id": "source-1", "quote": "Registrou o pagamento."}]}]}
        runner = FakeRunner(output=stream(result))
        self.assertEqual(self.interpret(runner, job=job), result)
        payload = json.loads(runner.calls[1][1]["input"])
        self.assertEqual(payload["job"]["interview_action"], job["interview_action"])
        self.assertIn("correção explícita", payload["request"])
        result["context_updates"][0]["topic"] = "people"
        self.assert_code("invalid_analysis", FakeRunner(output=stream(result)), job=job)

    def test_no_login_is_unavailable_and_does_not_start_model(self):
        runner = FakeRunner(auth={"loggedIn": False, "authMethod": "none"})
        self.assert_code("subscription_unavailable", runner)
        self.assertEqual(len(runner.calls), 1)

    def test_api_only_auth_is_denied_without_fallback(self):
        runner = FakeRunner(auth={"loggedIn": True, "authMethod": "apiKey",
                                  "apiProvider": "firstParty"})
        self.assert_code("subscription_required", runner)
        self.assertEqual(len(runner.calls), 1)

    def test_other_provider_is_denied(self):
        self.assert_code("subscription_required", FakeRunner(auth={
            "loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"}))

    def test_missing_cli_is_stable(self):
        with patch("company_pilot.inference.claude.shutil.which", return_value=None):
            with self.assertRaisesRegex(InterpreterError, "^claude_unavailable$"):
                ClaudeInterpreter().interpret(self.job, self.context)

    def test_other_visible_tool_is_rejected(self):
        self.assert_code("tool_isolation_failed", FakeRunner(output=stream(
            analysis(), tools=["StructuredOutput", "Read"])))

    def test_mcp_server_is_rejected(self):
        self.assert_code("tool_isolation_failed", FakeRunner(output=stream(
            analysis(), mcp=[{"name": "private", "status": "connected"}])))

    def test_other_actual_tool_call_is_rejected(self):
        self.assert_code("tool_isolation_failed", FakeRunner(output=stream(
            analysis(), calls=[{"type": "tool_use", "name": "Read", "input": {}}])))

    def test_server_tool_call_is_rejected(self):
        self.assert_code("tool_isolation_failed", FakeRunner(output=stream(
            analysis(), calls=[{"type": "server_tool_use", "name": "StructuredOutput",
                                "input": analysis()}])))

    def test_unknown_tool_result_is_rejected(self):
        result = analysis()
        self.assert_code("tool_isolation_failed", FakeRunner(output=stream(result, calls=[
            {"type": "tool_use", "id": "schema-1", "name": "StructuredOutput", "input": result},
            {"type": "tool_result", "tool_use_id": "unknown-tool", "content": "private data"},
        ])))

    def test_structured_output_tool_result_is_allowed(self):
        result = analysis()
        self.assertEqual(self.interpret(FakeRunner(output=stream(result, calls=[
            {"type": "tool_use", "id": "schema-1", "name": "StructuredOutput", "input": result},
            {"type": "tool_result", "tool_use_id": "schema-1", "content": "Schema emitted"},
        ]))), result)

    def test_no_schema_emission_is_rejected(self):
        self.assert_code("tool_isolation_failed", FakeRunner(output=stream(analysis(), calls=[])))

    def test_missing_structured_output_is_distinct(self):
        events = [json.loads(line) for line in stream(analysis()).splitlines()]
        del events[-1]["structured_output"]
        self.assert_code("missing_structured_output", FakeRunner(
            output="\n".join(map(json.dumps, events))))

    def test_result_failure_cannot_apply_even_with_output(self):
        events = [json.loads(line) for line in stream(analysis()).splitlines()]
        events[-1]["subtype"] = "error_during_execution"
        self.assert_code("model_failed", FakeRunner(output="\n".join(map(json.dumps, events))))

    def test_raw_failure_details_never_escape(self):
        self.assert_code("model_failed", FakeRunner(returncode=1, output="API_KEY=secret"))

    def test_unexpected_runner_error_has_a_stable_code(self):
        self.assert_code("interpretation_failed", FakeRunner(error=RuntimeError("API_KEY=secret")))

    def test_timeout_is_stable(self):
        self.assert_code("model_timeout", FakeRunner(error=subprocess.TimeoutExpired(
            "secret command", 1, output="secret output", stderr="secret stderr")))

    def test_malformed_stream_is_rejected(self):
        self.assert_code("invalid_cli_output", FakeRunner(output="secret non-JSON banner"))

    def test_duplicate_json_key_is_rejected(self):
        self.assert_code("invalid_cli_output", FakeRunner(output='{"type":"result","type":"other"}'))

    def test_output_limit_is_enforced_with_mocked_runner(self):
        self.assert_code("output_limit_exceeded", FakeRunner(output="x" * 1000), output_limit=500)

    def test_required_fields_are_fixed(self):
        result = analysis()
        del result["tasks"][0]["fields"]["purpose"]
        self.assert_code("invalid_analysis", FakeRunner(output=stream(result)))
        result = analysis()
        result["tasks"][0]["fields"]["invented_gap"] = {"value": None}
        self.assert_code("invalid_analysis", FakeRunner(output=stream(result)))

    def test_invalid_basis_or_topic_is_rejected(self):
        result = analysis()
        result["tasks"][0]["fields"]["purpose"]["basis"] = "certain"
        self.assert_code("invalid_analysis", FakeRunner(output=stream(result)))
        with self.assertRaises(ValueError):
            validate_analysis(analysis(tasks=[], question={"topic": "finance", "text": "Como?"}))

    def test_observation_requires_one_to_three_granular_tasks(self):
        self.assert_code("invalid_analysis", FakeRunner(output=stream(analysis(tasks=[]))))
        self.assert_code("invalid_analysis", FakeRunner(output=stream(analysis(tasks=[task(), task(), task(), task()]))))

    def test_answer_revises_all_requested_task_ids(self):
        job = dict(self.job, kind="answer", task_ids=["task-a", "task-b"])
        result = analysis(tasks=[task("task-a"), task("task-b")])
        self.assertEqual(self.interpret(FakeRunner(output=stream(result)), job=job), result)
        self.assert_code("invalid_analysis", FakeRunner(output=stream(
            analysis(tasks=[task("task-a")]))), job=job)
        self.assert_code("invalid_analysis", FakeRunner(output=stream(
            analysis(tasks=[task("task-a"), task("other")]))), job=job)

    def test_interview_payload_preserves_adaptive_previous_context(self):
        context = copy.deepcopy(self.context)
        context["context"] = {"activities": [{"value": "Tradução de contratos", "basis": "reported"}],
                              "stages": [{"value": "Não se aplica", "basis": "reported"}]}
        context["questions"] = [{"topic": "activities", "text": "O que a empresa faz?",
                                 "answer": "Tradução de contratos"}]
        result = analysis(tasks=[], question={"topic": "people",
                                             "text": "Quem assume cada responsabilidade?"})
        runner = FakeRunner(output=stream(result))
        self.assertEqual(self.interpret(runner, job=dict(self.job, kind="interview"),
                                        context=context), result)
        payload = json.loads(runner.calls[-1][1]["input"])
        self.assertEqual(payload["base_context"], context)
        self.assertIn("Não se aplica", payload["base_context"]["context"]["stages"][0]["value"])
        self.assertIn("adaptativa", payload["request"])
        self.assertIn("não repita", payload["request"].lower())
        self.assertEqual(payload["job"]["source_ids"], ["source-1"])

    def test_input_limit_prevents_model_or_auth_call(self):
        runner = FakeRunner()
        self.assert_code("input_limit_exceeded", runner, input_limit=10)
        self.assertEqual(runner.calls, [])

    def test_safe_metadata_has_no_source_or_secret_content(self):
        interpreter = ClaudeInterpreter("/fake/claude", runner=FakeRunner())
        interpreter.interpret(self.job, self.context)
        self.assertEqual(interpreter.last_metadata["model"], "claude-opus-5-5")
        self.assertEqual(len(interpreter.last_metadata["input_sha256"]), 64)
        self.assertNotIn("Registrou", json.dumps(interpreter.last_metadata))


class BoundedProcessTests(unittest.TestCase):
    def test_subprocess_stdout_is_bounded_and_stderr_discarded(self):
        class Process:
            def __init__(self):
                self.stdout = io.BytesIO(b"x" * 100)
                self.stdin = io.BytesIO()
                self.returncode = 0
                self.killed = False

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                return self.returncode

        process = Process()
        with patch("company_pilot.inference.claude.subprocess.Popen", return_value=process) as popen:
            with self.assertRaisesRegex(InterpreterError, "^output_limit_exceeded$"):
                _bounded_run(["/fake/claude"], input="{}", cwd="/tmp", env={},
                             timeout=1, output_limit=10)
        self.assertTrue(process.killed)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
