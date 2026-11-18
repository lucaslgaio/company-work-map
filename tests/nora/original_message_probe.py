"""New typed fixture decisions on original local states/messages, never exact replay.

python3 THIS_FILE COMPOSED_PRODUCT ORIGINAL_BUNDLE OUTPUT_DIRECTORY
No old model response is reused. No model, gateway, network or real sender runs.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

sys.dont_write_bytecode = True
product, original, output = [Path(value).resolve() for value in sys.argv[1:4]]
sys.path.insert(0, str(product))
from company_pilot.brain import Brain, LocalStore
from company_pilot.inference import SemanticWorker
from company_pilot.nora import BrainRepository, InboundEvent, Nora, SendReceipt
from company_pilot.nora.engine import question_label
from company_pilot.nora.worker import health
from company_pilot.operator import drain_nora_inbox, project_questions

run = original / "cwm-sim/runs/sim"
interactions = {item["id"]: item for item in map(json.loads, (run / "interactions.jsonl").read_text().splitlines())}
payment_id = "f2e435dc-aef6-5759-8054-fc44a8fdce8b"


def version(number):
    return json.loads((run / f"versions/v{number:03}.json").read_text())


def restore(number, root):
    recorded = version(number)
    archive = subprocess.check_output(["git", "--git-dir=" + str(original / "cwm-sim/gh-sim/remote.git"), "archive", "--format=tar", recorded["head"]])
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        files = {entry.name: stream.extractfile(entry).read().decode() for entry in stream if entry.isfile()}
    store = LocalStore(root, actor="lucas-sim")
    store.commit(None, files, "new fixture reconstruction of retained original head")
    brain = Brain(store)
    assert brain.snapshot()["tasks"] == recorded["snapshot"]["tasks"]
    return brain, recorded["head"]


def save(name, value):
    (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def case(name, before_version, after_event_version, interaction_id, intent_kind):
    event_id = interactions[interaction_id]["nora_message_id"]
    raw_event = next(answer["event"] for answer in version(after_event_version)["snapshot"]["nora"]["inbox"].values() if answer["event"]["message_id"] == event_id)
    event = InboundEvent(**{**raw_event, "received_at": datetime.fromisoformat(raw_event["received_at"])})
    clock = [event.received_at]
    sent = []
    with TemporaryDirectory(prefix="nora-original-after-") as root, patch("company_pilot.brain.engine.now", side_effect=lambda: clock[0].isoformat()):
        brain, head = restore(before_version, Path(root) / "brain")
        before = brain.snapshot()
        nora = Nora(before["company"]["id"], BrainRepository(before["company"]["id"], brain.transact_nora), lambda: clock[0],
            type("FixtureSender", (), {"send": lambda _self, route, text: (sent.append(text), SendReceipt(route.account, route.conversation, "fixture-after-message"))[1]})())
        project_questions(brain, nora)
        received = nora.receive(event)
        drained = drain_nora_inbox(brain)
        assert drained["pending_intake"] == 0 and drained["queued_answers"] == 1, drained
        after_intake = brain.snapshot()
        new_jobs = [job for job in after_intake["pending_jobs"] if job["id"] not in {item["id"] for item in before["pending_jobs"]}]
        assert len(new_jobs) == 1 and new_jobs[0]["kind"] == "message", new_jobs
        expected_job = new_jobs[0]["id"]
        calls = []
        class Fixture:
            def interpret(self, job, context):
                assert job["id"] == expected_job, "different queued request; stop instead of forcing a response"
                calls.append({"job": deepcopy(job), "context": deepcopy(context)})
                source = next(source for source in context["sources"] if source.get("text") == raw_event["text"] and source.get("kind") in ("answer", "message"))
                evidence = [{"source_id": source["id"], "quote": source["text"]}]
                selected = list(received.question_ids) if intent_kind == "dismiss" else []
                result = {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
                    "message_intent": {"kind": intent_kind, "task_ids": [payment_id] if intent_kind == "correction" else [],
                        "question_ids": selected, "delegate_to": None,
                        "disposition": "not_company" if intent_kind == "dismiss" else None, "evidence": evidence}}
                if intent_kind == "correction":
                    task = next(task for task in context["tasks"] if task["id"] == payment_id)
                    business_source = source if interaction_id == "I065" else next(s for s in context["sources"]
                        if s.get("kind") == "answer" and "AVM 82 mesmo" in s.get("text", ""))
                    business_evidence = [{"source_id": business_source["id"], "quote": business_source["text"]}]
                    if business_source["id"] != source["id"]:
                        # I074 supplies current target confirmation; the original
                        # I065 supplies AVM82/approval facts. Cite both explicitly.
                        business_evidence += evidence
                    fields = deepcopy(task["fields"])
                    fields["purpose"] = {"value": "Pagamento da caçamba para a obra AVM 82", "basis": "reported", "evidence": business_evidence}
                    result["tasks"] = [{"task_id": payment_id, "name": task["name"], "fields": fields, "links": [],
                        "payment": {"object": {"value": "Caçamba da Rio Verde para a obra AVM 82", "basis": "reported", "evidence": business_evidence},
                                    "tracking_record": fields["tracking_record"]}}]
                    result["context_updates"] = [{"topic": "people", "mode": "add", "replaces": [],
                        "value": "O fundador aprova o pagamento; Paula executa o Pix.", "basis": "reported", "evidence": business_evidence}]
                return result
        result = SemanticWorker(brain, Fixture()).run_once()
        assert result["status"] == "applied", result
        after = brain.snapshot()
        assert len(calls) == 1
        if intent_kind in ("chatter", "dismiss"):
            assert before["tasks"] == after["tasks"], "social/dismissal intent changed genuine business tasks"
        if intent_kind == "chatter":
            assert not any(q.get("unassigned_answer_id") == result.get("receipt", {}).get("source_ids", [None])[0] and q["status"] == "pending" for q in after["questions"])
        if intent_kind == "correction":
            task = next(task for task in after["tasks"] if task["id"] == payment_id)
            assert "AVM 82" in task["fields"]["purpose"]["value"] and "CAP 14" not in task["fields"]["purpose"]["value"]
            old = next(task for task in before["tasks"] if task["id"] == payment_id)
            assert task["fields"]["performer"] == old["fields"]["performer"]
            assert [t for t in before["tasks"] if t["id"] != payment_id] == [t for t in after["tasks"] if t["id"] != payment_id]
            assert all(q["status"] not in ("resolved", "superseded") or q["id"] in received.question_ids for q in calls[0]["context"]["questions"])
            (output / (interaction_id + "-payment-after.md")).write_text(brain.store.read().files["views/tasks/" + payment_id + ".md"])
        clock[0] = clock[0].replace(hour=18, minute=0, second=0, microsecond=0)
        project_questions(brain, nora)
        nora.tick()
        if intent_kind == "dismiss":
            labels = [question_label(qid) for qid in received.question_ids]
            assert labels and not any(label in text for label in labels for text in sent)
            assert not any(job.get("disambiguation_question_id") in received.question_ids for job in after["pending_jobs"]), "dismissed attribution still owns waiting legacy jobs"
        for index, text in enumerate(sent, 1):
            (output / f"{interaction_id}-after-message-{index}.txt").write_text(text + "\n")
        receipt = {"method": "new typed fixture decision on reconstructed original state; no recorded model response replay",
            "original_head": head, "before_version": before_version, "original_interaction": interaction_id,
            "literal_message": raw_event, "received": asdict(received), "drain": drained,
            "worker_input_job": calls[0]["job"], "worker_input_task_ids": [task["id"] for task in calls[0]["context"]["tasks"]],
            "worker_input_question_statuses": [{key: q.get(key) for key in ("id", "status", "target_kind")} for q in calls[0]["context"]["questions"]],
            "worker_result": result, "health": health(nora), "after_messages": sent,
            "business_tasks_unchanged": before["tasks"] == after["tasks"],
            "questions_after": [q for q in after["questions"] if q["id"] in received.question_ids or (intent_kind == "correction" and q["task_id"] == payment_id)],
            "pending_question_count": sum(q["status"] in ("pending", "partially_answered") for q in after["questions"]),
            "pending_jobs_after": after["pending_jobs"]}
        save(interaction_id + "-receipt.json", receipt)
        return {"interaction": interaction_id, "kind": intent_kind, "worker": result["status"], "real_messages": 0}


def multi_fact_and_delegate():
    spec = importlib.util.spec_from_file_location("aligned_nora_proof_fixture", product / "tests/nora/test_operator_outcomes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.OperatorOutcomeTests("test_queue_readback_does_not_expose_message_bodies")
    fixture.setUp()
    try:
        brain, nora = fixture.brain, fixture.nora
        project_questions(brain, nora)
        nora.tick()
        first_message = fixture.sent[0]
        task = brain.snapshot()["tasks"][0]
        initial_questions = brain.snapshot()["questions"]
        selected = [q["id"] for q in initial_questions if set(q["missing_links"]) & {"performer", "purpose", "handoff"}]
        text = "Ana revisa o relatório para liberar o resultado ao cliente; Renan recebe a revisão."
        nora.receive(InboundEvent(fixture.route.account, fixture.route.conversation, fixture.route.founder_sender,
            "fixture-natural-multifact", fixture.now, text))
        assert drain_nora_inbox(brain)["queued_answers"] == 1
        fixture.now += timedelta(hours=4)
        project_questions(brain, nora)
        assert nora.tick().outcome == "empty"
        paused = health(nora)
        assert paused["processing"]["oldest_queued_answer_age_seconds"] == 14400
        class AnswerFixture:
            def interpret(self, job, context):
                source = next(s for s in context["sources"] if s.get("text") == text)
                evidence = [{"source_id": source["id"], "quote": text}]
                fields = deepcopy(task["fields"])
                for field, value in {"performer": "Ana", "purpose": "Liberar o resultado ao cliente", "handoff": "Renan"}.items():
                    fields[field] = {"value": value, "basis": "reported", "evidence": evidence}
                return {"tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}],
                    "nodes": [], "context_updates": [], "next_question": None,
                    "message_intent": {"kind": "answer", "task_ids": [task["id"]], "question_ids": selected,
                        "delegate_to": None, "disposition": None, "evidence": evidence}}
        applied = SemanticWorker(brain, AnswerFixture()).run_once()
        assert applied["status"] == "applied", applied
        project_questions(brain, nora)
        assert all(q["status"] == "resolved" for q in brain.snapshot()["questions"] if q["id"] in selected)
        tracking = next(q for q in brain.snapshot()["questions"] if q["missing_links"] == ["tracking_record"])
        delegate_text = "Quem consegue explicar o controle e o saldo é o Renan; aguarde ele confirmar."
        nora.receive(InboundEvent(fixture.route.account, fixture.route.conversation, fixture.route.founder_sender,
            "fixture-delegation", fixture.now, delegate_text))
        assert drain_nora_inbox(brain)["queued_answers"] == 1
        class DelegateFixture:
            def interpret(self, job, context):
                source = next(s for s in context["sources"] if s.get("text") == delegate_text)
                return {"tasks": [], "nodes": [], "context_updates": [], "next_question": None,
                    "message_intent": {"kind": "delegate", "task_ids": [], "question_ids": [tracking["id"]],
                        "delegate_to": "Renan", "disposition": None,
                        "evidence": [{"source_id": source["id"], "quote": delegate_text}]}}
        delegated = SemanticWorker(brain, DelegateFixture()).run_once()
        assert delegated["status"] == "applied", delegated
        fixture.now += timedelta(days=1)
        project_questions(brain, nora)
        nora.tick()
        latest = max(brain.snapshot()["nora"]["rounds"].values(), key=lambda value: value["slot"])
        assert tracking["id"] not in [item["question"]["id"] for item in latest["items"]]
        assert health(nora)["delegated_unchanged_questions"] == 1
        current = next(q for q in brain.snapshot()["questions"] if q["id"] == tracking["id"])
        assert current["status"] == "pending"
        page = brain.store.read().files["views/questions/" + tracking["id"] + ".md"]
        assert "Renan" in page
        (output / "synthetic-delegation-question.md").write_text(page)
        (output / "synthetic-multifact-map.md").write_text(brain.store.read().files["views/tasks/" + task["id"] + ".md"])
        (output / "synthetic-activity-message.txt").write_text(first_message + "\n")
        # New unknown context in an already explained app is a new gap, not a
        # reminder/reopening of the earlier app context. No title/body is sent.
        fixture.now = fixture.now.replace(hour=14)
        device = str(uuid4())
        def gap(key):
            return brain.ingest_context_gap({"schema_version": 1, "event_id": str(uuid4()),
                "device_id": device, "occurred_at": fixture.now.isoformat(), "app": "com.google.Chrome",
                "surface_kind": "desktop_window", "reason": "unrecognised_safe_context", "synthetic": True,
                "context_key": key, "coverage": {"period_start": (fixture.now - timedelta(hours=1)).isoformat(),
                    "period_end": fixture.now.isoformat(), "observed_samples": 2, "distinct_windows": 2, "expired_unresolved": 0}})
        old_context = gap("a" * 64)["question_id"]
        project_questions(brain, nora)
        nora.tick()
        context_text = question_label(old_context) + ": Estas janelas eram da revisão do Relatório de Qualidade para clientes."
        nora.receive(InboundEvent(fixture.route.account, fixture.route.conversation, fixture.route.founder_sender,
            "fixture-explained-context", fixture.now, context_text))
        assert drain_nora_inbox(brain)["queued_answers"] == 1
        class ContextFixture:
            def interpret(self, job, context):
                source = next(s for s in context["sources"] if s.get("text") == context_text)
                evidence = [{"source_id": source["id"], "quote": context_text}]
                return {"tasks": [], "nodes": [], "next_question": None,
                    "context_updates": [{"topic": "activities", "value": "Revisão do Relatório de Qualidade para clientes", "basis": "reported", "evidence": evidence}],
                    "message_intent": {"kind": "context", "task_ids": [], "question_ids": [old_context],
                        "delegate_to": None, "disposition": None, "evidence": evidence}}
        context_result = SemanticWorker(brain, ContextFixture()).run_once()
        assert context_result["status"] == "applied", context_result
        gap("a" * 64)
        new_context = gap("b" * 64)["question_id"]
        current_questions = {q["id"]: q for q in brain.snapshot()["questions"]}
        assert new_context != old_context and current_questions[old_context]["status"] == "resolved"
        assert current_questions[new_context]["status"] == "pending"
        fixture.now = fixture.now.replace(hour=18)
        project_questions(brain, nora)
        nora.tick()
        assert question_label(new_context) in fixture.sent[-1] and question_label(old_context) not in fixture.sent[-1]
        assert "com.google.Chrome" not in fixture.sent[-1] and "2 janelas" in fixture.sent[-1]
        (output / "synthetic-new-context-message.txt").write_text(fixture.sent[-1] + "\n")
        save("synthetic-multifact-delegation.json", {"evidence_level": "E1_aligned_clock_actual_message_worker_with_explicit_fixtures",
            "natural_answer": text, "answer_receipt": applied, "queued_worker_paused": paused,
            "delegation": delegate_text, "delegation_receipt": delegated, "waiting_question": current,
            "health": health(nora), "messages": fixture.sent, "real_messages": 0, "team_routes_created": 0})
        save("synthetic-context-generations.json", {"old_question": current_questions[old_context],
            "new_question": current_questions[new_context], "context_answer_receipt": context_result,
            "no_titles_or_bodies_in_coverage": True, "real_messages": 0})
        return {"interaction": "new_aligned_synthetic_company", "kind": "multi_fact_and_delegation", "worker": "applied", "real_messages": 0}
    finally:
        fixture.doCleanups()


output.mkdir(parents=True, exist_ok=True)
watched = [run / f"versions/v{number:03}.json" for number in (33, 34, 52, 53, 57, 58, 66, 67)] + [run / "interactions.jsonl"]
hashes = {str(path.relative_to(original)): sha256(path.read_bytes()).hexdigest() for path in watched}
cases = [case("chatter", 33, 34, "I037", "chatter"), case("dismissal", 52, 53, "I059", "dismiss"), case("correction", 57, 58, "I065", "correction"), case("current_candidates", 66, 67, "I074", "correction"), multi_fact_and_delegate()]
assert hashes == {str(path.relative_to(original)): sha256(path.read_bytes()).hexdigest() for path in watched}
save("receipt.json", {"evidence_level": "E1_original_inputs_new_typed_fixture_decisions", "cases": cases,
    "original_sha256_unchanged": hashes, "model_native_network_channel_account_service_actions": 0,
    "source_sha256": {path: sha256((product / path).read_bytes()).hexdigest() for path in
        ("company_pilot/brain/engine.py", "company_pilot/inference/worker.py", "company_pilot/operator.py", "company_pilot/nora/engine.py")}})
print(json.dumps(cases))
