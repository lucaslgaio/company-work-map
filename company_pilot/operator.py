"""Operação local com filas duráveis, sinais de parada e saúde sem conteúdo."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import tempfile
from threading import Event, current_thread, main_thread
from uuid import NAMESPACE_URL, uuid5


def utcnow():
    return datetime.now(timezone.utc)


def write_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("unsafe_output_path")
    descriptor, temporary = tempfile.mkstemp(prefix=".company-pilot-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def diagnostic_code(value, fallback="operation_pending"):
    # Only closed, metadata-only worker codes cross the reporting boundary.
    allowed = {"queue_unavailable", "context_unavailable", "interpretation_failed",
               "analysis_rejected", "model_timeout", "model_unavailable", "model_failed",
               "claude_unavailable", "auth_check_failed", "subscription_unavailable",
               "subscription_required", "tool_isolation_failed", "invalid_analysis",
               "invalid_cli_output", "input_limit_exceeded", "output_limit_exceeded",
               "missing_structured_output", "operation_pending", "nora_pending", "retry_deferred", "answer_unassigned", "awaiting_clarification"}
    return value if isinstance(value, str) and value in allowed else fallback


def queue_status(snapshot, at=None):
    """Content-free queue freshness, also usable by the public readback CLI."""
    at = at or utcnow()
    jobs = snapshot.get("pending_jobs", [])
    sources = {source["id"]: source for source in snapshot.get("sources", [])}
    answer_jobs = [job for job in jobs if any(sources.get(sid, {}).get("kind") in ("answer", "message")
                   for sid in job.get("source_ids", []))]
    oldest = min((job["created_at"] for job in jobs if job.get("created_at")), default=None)
    answer_oldest = min((job["created_at"] for job in answer_jobs if job.get("created_at")), default=None)
    analyses = [source["received_at"] for source in sources.values() if source.get("kind") == "analysis" and source.get("received_at")]
    def age(value):
        return max(0, int((at - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())) if value else None
    coverage = [{key: item[key] for key in ("source_id", "status", "reason") if key in item}
                for item in snapshot.get("coverage", []) if item.get("status") == "coverage_only"]
    coverage.sort(key=lambda item: item.get("source_id", ""))
    from .brain.knowledge import RECOGNITION_HEALTH_MESSAGE, recognition_is_confirmed
    from .brain.quotes import MANUAL_INTERVIEW_MESSAGE, awaiting_manual_interview
    summary = (f"{len(jobs)} registro(s) aguardam processamento ou esclarecimento; {len(answer_jobs)} são respostas já recebidas. "
               "A leitura que vira atividade roda no operador externo, com operator export e operator submit. Este computador não chama modelo.")
    manual = any(awaiting_manual_interview(job) for job in jobs)
    if manual:
        summary = summary + " " + MANUAL_INTERVIEW_MESSAGE
    names_confirmed = recognition_is_confirmed(snapshot)
    if not names_confirmed:
        summary = summary + " " + RECOGNITION_HEALTH_MESSAGE
    return {"pending_job_count": len(jobs), "queued_answer_count": len(answer_jobs),
            "recognition_names_confirmed": names_confirmed,
            "coverage_constraint_count": len(coverage), "coverage_constraints": coverage[:20],
            "awaiting_clarification_count": sum(job.get("status") == "awaiting_disambiguation" for job in jobs),
            "oldest_pending_at": oldest, "oldest_pending_age_seconds": age(oldest),
            "oldest_queued_answer_at": answer_oldest, "oldest_queued_answer_age_seconds": age(answer_oldest),
            "last_analysis_at": max(analyses, default=None),
            "interpretation_availability": "requires_awake_online_operator",
            **({"recommended_route": "manual"} if manual else {}),
            "summary": summary}


def project_questions(brain, nora, *, report=None):
    """Project canonical gaps, without making ordinary recurrences resend triggers."""
    from .nora.models import Question, QuestionStatus
    from .nora.engine import conflict_ids, disposition_basis_digest
    from .nora.projection import question_support, known_fact_support, priority_support
    snapshot = brain.snapshot()
    sources = {source["id"]: source for source in snapshot.get("sources", [])}
    tasks = {task["id"]: task for task in snapshot.get("tasks", [])}
    projected = {}
    for raw in snapshot.get("questions", []):
        support = tuple(raw.get("observation_ids") or ())
        if not support:
            continue
        evidence = raw.get("evidence_ids", [])
        conflict = raw.get("conflict")
        conflicts = conflict_ids(conflict)
        question_evidence, observed_context, issues = question_support(raw, tasks.get(raw["task_id"], {}), sources)
        prompt = raw["prompt"]
        if raw.get("target_kind") == "context" and raw.get("app"):
            from .nora.engine import display_text
            app = display_text(raw["app"])
            prompt = (f"Ainda não identificamos a atividade destas janelas no {app}. Elas eram da empresa? "
                      "Se eram, qual trabalho e quais nomes de registros ajudam a reconhecê-lo? "
                      "Se eram pessoais ou fora do escopo, informe isso.")
        projected[raw["id"]] = Question(
            id=raw["id"], company_id=snapshot["company"]["id"], task_id=raw["task_id"],
            task_version=raw.get("task_version", raw.get("base_version", 1)),
            revision=raw.get("revision", 1), created_at=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
            context_key=raw["context_key"],
            context_name=tasks.get(raw["task_id"], {}).get("name") or raw.get("activity_summary", "Contexto do trabalho"),
            activity_summary=raw["activity_summary"], prompt=prompt,
            source_url=brain.store.inspect_url("views/questions/" + raw["id"] + ".md"),
            observation_ids=support, missing_links=tuple(raw["missing_links"]),
            answer_ids=tuple(sorted(raw.get("relevant_answer_ids", [sid for sid in evidence if sources.get(sid, {}).get("kind") == "answer"]))),
            conflict_ids=conflicts,
            revisit_evidence_ids=tuple(sid for sid in raw.get("revisit_evidence_ids", []) if sid in support),
            resend_request_id=raw.get("resend_request_id"),
            status=QuestionStatus(raw["status"]),
            target_kind=raw.get("target_kind", "task"),
            activity_url=brain.store.inspect_url("views/tasks/" + raw["task_id"] + ".md") if raw["task_id"] in tasks else None,
            response_disposition=raw.get("response_disposition"),
            coverage=raw.get("coverage"),
            gap_kind=raw.get("gap_kind"), evidence=question_evidence, observed_context=observed_context,
            known_facts=known_fact_support(raw, sources), conflict=raw.get("conflict"), issue_ids=tuple(raw.get("issue_ids", [])), issues=issues,
            application=raw.get("application"), privacy_disposition=raw.get("privacy_disposition"),
            priority_hint=priority_support(raw, sources),
        )
    waiting = set()
    for job in snapshot.get("pending_jobs", []):
        if job.get("status") != "pending":
            continue  # Attribution questions awaiting the founder remain askable.
        job_sources = [sources.get(sid, {}) for sid in job.get("source_ids", [])]
        if not any(source.get("kind") in ("answer", "message") for source in job_sources):
            continue
        waiting.update(job.get("question_ids", []))
        waiting.update(job.get("candidate_question_ids", []))
        target_tasks = set(job.get("task_ids", []))
        for source in job_sources:
            waiting.update(source.get("question_ids", []))
            waiting.update(source.get("candidate_question_ids", []))
        waiting.update(qid for qid, question in projected.items() if question.task_id in target_tasks)
    metadata = queue_status(snapshot, nora.now())
    if report is not None:
        report.clear()
        report.update(metadata)
    # Ages are derived on read; an idle projection must not create a new commit.
    for key in ("oldest_pending_age_seconds", "oldest_queued_answer_age_seconds"):
        metadata.pop(key)
    map_hash = hashlib.sha256(json.dumps({key: snapshot.get(key) for key in ("tasks", "nodes", "edges", "context")}, sort_keys=True).encode()).hexdigest()
    def update(state):
        for identifier, question in tuple(projected.items()):
            prior = state.questions.get(identifier)
            disposition = question.response_disposition
            if disposition:
                same = prior and prior.response_disposition == disposition
                basis = disposition.get("uncertainty_basis")
                # Decision-time basis survives a conflict that arrives before
                # the first projection. Never invent an anchor from later state.
                if basis is not None:
                    anchor = disposition_basis_digest(basis)
                elif same:
                    anchor = prior.disposition_digest
                else:
                    anchor = None
                projected[identifier] = replace(question, disposition_digest=anchor)
        for identifier, old in tuple(state.questions.items()):
            if identifier not in projected and old.status not in (QuestionStatus.RESOLVED, QuestionStatus.SUPERSEDED):
                state.questions[identifier] = replace(old, status=QuestionStatus.SUPERSEDED)
        state.questions.update(projected)
        state.waiting_question_ids = tuple(sorted(waiting & projected.keys()))
        changed_at = state.freshness.get("map_last_change_observed_at") if state.freshness.get("map_content_sha256") == map_hash else nora.now().isoformat()
        state.freshness = {**metadata, "map_content_sha256": map_hash, "map_last_change_observed_at": changed_at}
    nora.repository.transact(nora.company_id, update)
    return len(projected)


def drain_nora_inbox(brain):
    """Commit answer first, then advance its durable inbox marker; replay is idempotent."""
    snapshot = brain.snapshot()
    raw_state = snapshot.get("nora") or {}
    route = raw_state.get("route") or {}
    questions = {q["id"]: q for q in snapshot.get("questions", [])}
    task_ids = {task["id"] for task in snapshot.get("tasks", [])}
    processed, media, pending = 0, 0, 0
    for key, answer in raw_state.get("inbox", {}).items():
        if answer.get("status") == "awaiting_text":
            media += 1
            continue
        if answer.get("status") != "awaiting_interpretation":
            continue
        event = answer["event"]
        if not event.get("text", "").strip():
            pending += 1
            continue
        event_id = str(uuid5(NAMESPACE_URL, "company-pilot:nora:" + answer["company_id"] + ":" + key))
        bases = {questions[qid]["task_id"]: version for qid, version, _revision in answer.get("question_versions", [])
                 if qid in questions and questions[qid]["task_id"] in task_ids}
        source = {"kind": "nora", "route_id": route.get("id"),
                  "message_id": event["message_id"], "account": event["account"],
                  "conversation": event["conversation"], "sender": event["sender"],
                  "received_at": event["received_at"]}
        if answer.get("conversation_contexts"):
            source["sent_question_context"] = answer["conversation_contexts"]
        try:
            linked = list(answer.get("question_ids", []))
            candidates = [qid for qid in answer.get("candidate_question_ids", [])
                          if questions.get(qid, {}).get("target_kind") != "answer_assignment"
                          and questions.get(qid, {}).get("status") in ("pending", "partially_answered")] if not linked else []
            # The Brain selects intent/correction targets, including established
            # tasks without open questions. Nora never guesses business semantics.
            scope = answer.get("handoff_scope")
            if scope is None:
                targets = brain.message_targets(event["text"])
                available = set(targets["open_question_ids"])
                # A reply may explicitly redirect attention to another relevant
                # question. Keep its actual sent selection separate, and freeze
                # Brain-ranked body-relevant candidates before downstream commit.
                candidates = list(dict.fromkeys([qid for qid in candidates if qid in available]
                                                + targets["open_question_ids"]))[:50]
                versions = dict(bases)  # Quoted bases take priority over current candidates.
                for item in targets["correction_targets"]:
                    if len(versions) < 50 and item["task_id"] not in versions:
                        versions[item["task_id"]] = item["version"]
                proposed = {"question_ids": linked, "source": source, "candidate_question_ids": candidates,
                            "candidate_task_versions": [{"id": tid, "version": version} for tid, version in versions.items()]}
                def freeze(raw, source_key=key, source_hash=answer["source_hash"], proposed=proposed):
                    current = raw.get("inbox", {}).get(source_key)
                    if not current or current.get("source_hash") != source_hash:
                        raise ValueError("inbox_changed")
                    if current.get("handoff_scope") is None:
                        current["handoff_scope"] = proposed
                    return current["handoff_scope"]
                # Freeze before the downstream commit. Unknown-result replay
                # uses identical scope even if map/questions change meanwhile.
                scope = brain.transact_nora(freeze, event_id=str(uuid5(NAMESPACE_URL, event_id + ":scope")))
            brain.record_message(event["text"], event_id, **scope)
            def advance(raw, source_key=key, source_hash=answer["source_hash"]):
                current = raw.get("inbox", {}).get(source_key)
                if not current or current.get("source_hash") != source_hash:
                    raise ValueError("inbox_changed")
                current["status"] = "queued_for_analysis"
                return {"state": "queued_for_analysis"}
            brain.transact_nora(advance)
            processed += 1
        except Exception:
            # No cursor advance on unknown/failed commit. Same immutable answer is retried.
            pending += 1
    return {"queued_answers": processed, "media_awaiting_text": media, "pending_intake": pending}


class Operator:
    def __init__(self, worker, *, brain=None, nora=None, intake=None, health_file=None,
                 poll_seconds=60, retry_initial_seconds=5, retry_max_seconds=60,
                 max_failures=5, stop=None, clock=utcnow):
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (poll_seconds, retry_initial_seconds, retry_max_seconds)) or not 0 < poll_seconds <= 60 or not 0 < retry_initial_seconds <= retry_max_seconds <= 300:
            raise ValueError("invalid_operator_intervals")
        if type(max_failures) is not int or not 1 <= max_failures <= 20:
            raise ValueError("invalid_failure_limit")
        if worker is None and (brain is None or nora is None):
            raise ValueError("nora_only_requires_configured_route")
        self.worker, self.brain, self.nora, self.intake = worker, brain, nora, intake
        self.role = "nora" if worker is None else "analysis"
        self.health_file = Path(health_file) if health_file else None
        self.poll_seconds, self.retry_initial_seconds = poll_seconds, retry_initial_seconds
        self.retry_max_seconds, self.max_failures = retry_max_seconds, max_failures
        self.stop, self.clock = stop if stop is not None else Event(), clock
        self.failures = 0
        self.health = {"schema_version": 1, "pid": os.getpid(), "state": "not_started", "role": self.role,
                       "last_tick_at": None, "last_success_at": None, "consecutive_failures": 0,
                       "error_code": None, "availability": "requires_online_nora_host" if self.role == "nora" else "requires_awake_online_operator",
                       "channel_connectivity": "not_probed",
                       "schedule_catchup": False}

    def _save(self, state):
        self.health.update(state=state, last_tick_at=self.clock().isoformat(), consecutive_failures=self.failures)
        if self.health_file:
            write_private_json(self.health_file, self.health)

    def once(self):
        result = {"role": self.role, "nora": "inactive"}
        nora_failed = False
        projected_metadata = {}
        freshness_snapshot = None
        if self.nora is not None:
            try:
                # Correlate inbound replies only after canonical candidates are current.
                project_questions(self.brain, self.nora, report=projected_metadata)
                if self.intake is not None:
                    result.update(self.intake())
                result.update(drain_nora_inbox(self.brain))
                if result.get("received_records", 0) or result.get("queued_answers", 0):
                    # Handoff can create clarification questions needed by this slot.
                    project_questions(self.brain, self.nora, report=projected_metadata)
                if result.get("pending_intake", 0):
                    nora_failed = True
                tick = self.nora.tick()
                outcome = getattr(tick, "outcome", None)
                result["nora"] = outcome if isinstance(outcome, str) and re.fullmatch(r"[a-z_]{1,50}", outcome) else "checked"
            except Exception:
                result["nora"] = "pending"
                nora_failed = True
        if self.worker is None:
            # This host only moves clarification records; interpretation stays on the analysis host.
            result["analysis"] = "not_run"
            raw = {"status": "pending", "error_code": "nora_pending"} if nora_failed else {"status": "idle"}
        else:
            try:
                raw = self.worker.run_once()
            except Exception:
                raw = {"status": "pending", "error_code": "operation_pending"}
        if type(raw) is not dict:
            raw = {"status": "pending", "error_code": "operation_pending"}
        status = raw.get("status") if raw.get("status") in ("applied", "idle", "pending", "awaiting_clarification") else "pending"
        waiting = status == "awaiting_clarification"
        if waiting:
            status = "pending"
            raw["error_code"] = "awaiting_clarification"
        if status == "idle" and self.worker is not None and self.brain is not None:
            try:
                freshness_snapshot = self.brain.snapshot()
                queued = freshness_snapshot.get("pending_jobs", [])
                if queued:
                    status, waiting = "pending", True
                    raw["error_code"] = "answer_unassigned" if any(job.get("status") == "unassigned" for job in queued) else "retry_deferred"
                    result["pending_job_count"] = len(queued)
            except Exception:
                status = "pending"
                raw["error_code"] = "queue_unavailable"
        if self.brain is not None:
            try:
                # Reuse the projection/idle inspection rather than adding idle
                # remote reads. Applied jobs refresh current map/queue state.
                semantic_completion = (isinstance(raw, dict) and (raw.get("status") == "applied" or
                    (raw.get("status") == "awaiting_clarification" and raw.get("application_status") in ("partially_applied", "awaiting_clarification"))))
                if self.nora is not None and semantic_completion:
                    project_questions(self.brain, self.nora, report=projected_metadata)
                freshness = projected_metadata or queue_status(freshness_snapshot or self.brain.snapshot(), self.clock())
                self.health["processing"] = freshness
                result["processing"] = freshness
            except Exception:
                self.health["processing"] = {"state": "unavailable", "summary": "Não foi possível verificar a fila; preserve as respostas recebidas."}
        result["status"] = status
        if waiting and not nora_failed:
            # A backoff/assignment wait is visible and does not erase prior failure history.
            result["error_code"] = raw["error_code"]
            self.health["error_code"] = result["error_code"]
        elif status == "pending" or nora_failed:
            self.failures += 1
            result["error_code"] = diagnostic_code(raw.get("error_code"), "nora_pending" if nora_failed else "operation_pending")
            self.health["error_code"] = result["error_code"]
        else:
            self.failures = 0
            self.health.update(error_code=None, last_success_at=self.clock().isoformat())
        self._save("attention_required" if self.failures >= self.max_failures else "running")
        return result

    def watch(self, report, *, max_cycles=None):
        if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
            raise ValueError("invalid_cycle_limit")
        previous = {}
        if current_thread() is main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda _signum, _frame: self.stop.set())
        cycles = 0
        try:
            while not self.stop.is_set():
                result = self.once()
                report(result)
                cycles += 1
                if self.failures >= self.max_failures:
                    return 3
                if max_cycles is not None and cycles >= max_cycles:
                    break
                delay = min(self.retry_max_seconds, self.retry_initial_seconds * 2 ** min(self.failures - 1, 8)) if self.failures else self.poll_seconds
                self.stop.wait(delay)
            self._save("stopped")
            return 0
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
