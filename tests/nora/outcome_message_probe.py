"""E1 deterministic B5/B8/B9 before/after messages; no model or real sender.

python3 THIS_FILE PRODUCT_ROOT OUTPUT_DIRECTORY
The same explicit synthetic activity/partial-answer inputs run on each source.
Original simulation records are never modified or replayed by this probe.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
product, output = [Path(value).resolve() for value in sys.argv[1:3]]
sys.path.insert(0, str(product))
from company_pilot.nora import Nora, NoraState, Question, Route, SendReceipt


class MemoryFixture:
    def __init__(self): self.state = NoraState()
    def transact(self, company, operation):
        next_state = deepcopy(self.state)
        result = operation(next_state)
        self.state = next_state
        return result


clock = [datetime.fromisoformat("2026-11-17T06:00:00-03:00")]
repository = MemoryFixture()
route = Route("fixture-company", "fixture-route", 1, "fixture-account", "100000000000@g.us", "fixture-founder", "fixture-actor", True)
repository.state.route = route
messages = []


class Sender:
    def send(self, target, text):
        messages.append({"slot": clock[0].isoformat(), "text": text})
        return SendReceipt(target.account, target.conversation, "fixture-message-" + str(len(messages)))


nora = Nora(route.company_id, repository, lambda: clock[0], Sender())
name = "Revisar Relatório de Qualidade no com.google.Chrome"
questions = []
for field, label in (("result", "resultado observável"), ("purpose", "finalidade"), ("handoff", "repasse")):
    question = Question("fixture-" + field, route.company_id, "fixture-task", 1, 1, clock[0], "fixture-task",
        name, name, f"Na atividade «{name}», falta esclarecer: {label}.",
        "https://brain.invalid/views/tasks/fixture-task.md", ("fixture-observation",), (field,))
    nora.put_question(question)
    questions.append(question)
ticks = []
for stamp, partial in [("2026-11-17T06:00:00-03:00", False), ("2026-11-17T10:00:00-03:00", True),
                       ("2026-11-18T06:00:00-03:00", False), ("2026-11-18T10:00:00-03:00", False),
                       ("2026-11-19T06:00:00-03:00", False)]:
    clock[0] = datetime.fromisoformat(stamp)
    if partial:
        # Canonical partial update explains performer elsewhere in the same task;
        # these three unchanged gaps retain that source as supporting history.
        for question in questions:
            nora.put_question(replace(question, revision=2, task_version=2, answer_ids=("fixture-performer-answer",)))
    tick = nora.tick()
    ticks.append({"slot": stamp, "outcome": tick.outcome, "pending": tick.pending_count})

output.mkdir(parents=True, exist_ok=True)
for index, message in enumerate(messages, 1):
    (output / f"message-{index:02}.txt").write_text(message["text"] + "\n")
receipt = {"evidence_level": "E1_synthetic_formatter_scheduler_fixture", "inputs": {"activity": name,
    "unchanged_gaps": [q.id for q in questions], "partial_answer_scope": "performer; supporting source retained, no new gap context"},
    "ticks": ticks, "messages": messages,
    "rounds": [{"id": r.id, "slot": r.slot.isoformat(), "status": r.status,
                "items": [{"id": item.question.id, "reminder": item.reminder, "prior_ask_at": item.prior_ask_at.isoformat() if item.prior_ask_at else None} for item in r.items]}
                for r in repository.state.rounds.values()],
    "questions_still_pending": sum(q.status == "pending" for q in repository.state.questions.values()),
    "source_sha256": {name: sha256((product / name).read_bytes()).hexdigest() for name in
        ["company_pilot/nora/engine.py", "company_pilot/nora/models.py"]},
    "real_messages": 0, "model_calls": 0, "native_network_account_service_actions": 0}
(output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"output": str(output), "ticks": ticks, "message_count": len(messages)}, ensure_ascii=False))
