"""Verified ordinary-message intake, separate from open-question attribution."""
import copy
import re
import unicodedata

from .stores import AccessDenied, BrainError


def uncertainty_basis(question):
    """Immutable semantic ask scope, without task-wide/time/projection churn."""
    support = set(question.get("observation_ids", []))
    basis = {"missing_links": sorted(question["missing_links"]),
            "revisit_evidence_ids": sorted(sid for sid in question.get("revisit_evidence_ids", []) if sid in support),
            "conflict": copy.deepcopy(question.get("conflict")) or None,
            "resend_request_id": question.get("resend_request_id")}
    if question.get("issue_ids"):
        basis["issue_ids"] = sorted(question["issue_ids"])
    return basis


def targets(state, text="", limit=50):
    if type(limit) is not int or not 1 <= limit <= 50 or not isinstance(text, str):
        raise BrainError("bounded_message_targets_required")
    def terms(value):
        value = "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
        stop = {"para", "pela", "pelo", "que", "quem", "isso", "essa", "esse", "com", "uma", "qual", "onde", "como", "correcao", "corrigir", "antes", "depois", "cliente", "trabalho", "atividade", "empresa", "sobre", "ainda", "ser", "foi"}
        return set(re.findall(r"[\w-]{3,}", value)) - stop
    words = terms(text)
    discovery = text == ""  # Only explicit empty discovery may list unrelated candidates.
    active = [task for task in state.tasks.values() if not task.get("superseded_by")]
    def score(task):
        values = [task["name"]] + [claim.get("value") or "" for claim in task["fields"].values()]
        return len(words & terms(" ".join(values)))
    active.sort(key=lambda task: (-score(task), task["id"]))
    questions = [q for q in state.questions.values() if q["status"] not in ("resolved", "superseded", "awaiting_person") and q.get("target_kind") != "answer_assignment"]
    questions.sort(key=lambda q: (-len(words & terms(q.get("prompt", "") + " " + q.get("activity_summary", ""))), q["id"]))
    questions = [q["id"] for q in questions if discovery or words & terms(q.get("prompt", "") + " " + q.get("activity_summary", ""))]
    active = [task for task in active if discovery or score(task)]
    return {"open_question_ids": questions[:50], "correction_targets": [
        {"task_id": task["id"], "version": task["version"], "name": task["name"], "status": task["status"], "evidence_ids": task["evidence_ids"]}
        for task in active[:limit]], "context_targets": [copy.deepcopy(c) for c in state.context_claims if c["status"] == "active"]}


class MessageMixin:
    def message_targets(self, text="", limit=50):
        from .engine import _State
        return targets(_State(self.store.read()), text, limit)

    def record_message(self, text, event_id, *, source=None, question_ids=None, candidate_question_ids=None, candidate_task_versions=None):
        from .engine import TOPICS, _text, identifier
        _text(text)
        source = source or {"kind": "founder"}
        explicit = question_ids or []
        for ids in (explicit, candidate_question_ids if candidate_question_ids is not None else []):
            if not isinstance(ids, list) or len(ids) > 50 or any(not isinstance(item, str) for item in ids):
                raise BrainError("bounded_question_ids_required")
        payload = {"text": text, "source": source, "question_ids": explicit, "candidate_question_ids": candidate_question_ids, "candidate_task_versions": candidate_task_versions}
        def apply(state, actor):
            speaker = actor["id"]
            if source.get("kind") == "nora":
                route = state.nora.get("route") or {}
                if actor["id"] not in state.company["nora_actors"] or not route.get("enabled") or source.get("route_id") not in (route.get("id"), route.get("route_id")) or not source.get("message_id") or any(source.get(key) != route.get(target) for key, target in (("account", "account"), ("conversation", "conversation"), ("sender", "founder_sender"))):
                    raise AccessDenied("verified_founder_route_required")
                speaker = route.get("founder_actor_id")
                if speaker not in state.company["founders"]:
                    raise AccessDenied("route_founder_not_authorized")
            else:
                self._founder(state, actor)
            current = targets(state, text)
            candidates = candidate_question_ids if candidate_question_ids is not None else current["open_question_ids"]
            candidates = list(dict.fromkeys(candidates + explicit))
            if any(qid not in state.questions for qid in candidates):
                raise BrainError("unknown_question")
            refs = candidate_task_versions
            if refs is None:
                linked = {state.questions[qid].get("task_id") for qid in explicit + (candidate_question_ids or [])}
                relevant = {item["task_id"]: item["version"] for item in current["correction_targets"]}
                relevant.update({tid: state.tasks[tid]["version"] for tid in linked if tid in state.tasks})
                refs = [{"id": tid, "version": version} for tid, version in relevant.items()]
            if not isinstance(refs, list) or len(refs) > 50 or any(not isinstance(ref, dict) or set(ref) != {"id", "version"} or ref["id"] not in state.tasks or type(ref["version"]) is not int or ref["version"] < 1 for ref in refs) or len({ref["id"] for ref in refs}) != len(refs):
                raise BrainError("bounded_task_versions_required")
            bases = {ref["id"]: ref["version"] for ref in refs}
            context_questions = [qid for qid in explicit if state.questions[qid].get("target_kind", "").startswith("context")]
            raw = self._source(state, actor, identifier(state.company["id"], "message:" + event_id), "answer", text,
                speaker=speaker, attested_by=actor["id"], channel=copy.deepcopy(source),
                question_ids=explicit, context_questions=context_questions, candidate_question_ids=candidates,
                candidate_task_versions=copy.deepcopy(refs), base_versions=bases,
                context_claim_ids=[record["id"] for record in state.context_claims if record["status"] == "active"],
                question_bases={qid: uncertainty_basis(state.questions[qid]) for qid in candidates},
                context_bases={topic: copy.deepcopy(state.context.get(topic)) for topic in TOPICS},
                context_question_bases={topic: {qid: q["revision"] for qid, q in state.questions.items() if q.get("context_topic") == topic and q.get("target_kind") in ("context_conflict", "context_deferred")} for topic in TOPICS})
            job_id = self._queue(state, raw, "message")
            original_ids = [state.questions[qid]["unassigned_answer_id"] for qid in explicit if state.questions[qid].get("target_kind") == "answer_assignment"]
            state.jobs[job_id].update(candidate_task_versions=copy.deepcopy(refs), candidate_question_ids=candidates,
                question_ids=explicit, open_question_ids=current["open_question_ids"], context_questions=context_questions,
                source_ids=list(dict.fromkeys([raw["id"]] + original_ids)))
            return {"answer_id": raw["id"], "job_id": job_id, "state": "pending", "speaker": speaker,
                    "attested_by": actor["id"], "candidate_task_versions": refs, "candidate_question_ids": candidates}
        return self._transaction("message", event_id, payload, apply)

    def _apply_message_intent(self, state, actor, job, result, source, scope):
        from .engine import identifier
        value = result.get("message_intent")
        keys = {"kind", "task_ids", "question_ids", "delegate_to", "disposition", "evidence"}
        if not isinstance(value, dict) or set(value) != keys or value["kind"] not in ("answer", "correction", "context", "dismiss", "delegate", "chatter", "unclear"):
            raise BrainError("typed_message_intent_required")
        if source.get("speaker") not in state.company["founders"]:
            raise AccessDenied("verified_founder_answer_required")
        task_ids, question_ids = value["task_ids"], value["question_ids"]
        candidates = {ref["id"] for ref in job["candidate_task_versions"]}
        if not isinstance(task_ids, list) or not isinstance(question_ids, list) or not set(task_ids) <= candidates or not set(question_ids) <= set(job["candidate_question_ids"]):
            raise BrainError("message_selection_outside_receipt_scope")
        self._claim(state, {"value": value["kind"], "basis": "reported", "evidence": value["evidence"]}, set(job["source_ids"]))
        if not any(ref["source_id"] == source["id"] for ref in value["evidence"]):
            raise BrainError("current_message_intent_evidence_required")
        if value["disposition"] not in (None, "not_company", "not_useful", "not_now", "already_answered") or (value["delegate_to"] is not None and (not isinstance(value["delegate_to"], str) or not value["delegate_to"].strip())):
            raise BrainError("invalid_message_disposition")
        kind = value["kind"]
        no_graph = kind in ("dismiss", "delegate", "chatter", "unclear")
        if no_graph and (task_ids or any(result.get(key) for key in ("tasks", "nodes", "context_updates", "recognition_markers", "map_changes", "question_answers")) or result.get("next_question") is not None):
            raise BrainError("nonbusiness_intent_cannot_rewrite_map")
        if kind in ("dismiss", "delegate") and (not question_ids or (kind == "delegate" and not value["delegate_to"])):
            raise BrainError("targeted_question_disposition_required")
        if kind in ("answer", "correction"):
            if result.get("map_changes"):
                if kind != "correction" or not task_ids or result.get("tasks"):
                    raise BrainError("structural_correction_scope_required")
            elif {task.get("task_id") for task in result.get("tasks", [])} != set(task_ids):
                raise BrainError("selected_message_tasks_required")
            if kind == "correction":
                supplied = {ref["id"]: ref["version"] for ref in (scope or {}).get("tasks", [])}
                for tid in task_ids:
                    current = state.tasks[tid]
                    if current.get("superseded_by") or source["base_versions"].get(tid) != current["version"] or supplied.get(tid) != current["version"]:
                        raise BrainError("stale_message_correction")
        elif kind == "context" and (task_ids or result.get("tasks") or result.get("map_changes") or result.get("nodes")):
            raise BrainError("context_message_cannot_rewrite_tasks")
        job.update(task_ids=task_ids, selected_task_ids=task_ids, question_ids=question_ids,
            context_questions=[qid for qid in question_ids if state.questions[qid].get("target_kind", "").startswith("context")], message_intent=copy.deepcopy(value))
        dispositions = []
        for qid in question_ids:
            question = state.questions[qid]
            if kind in ("dismiss", "delegate"):
                disposition = {"kind": kind, "disposition": value["disposition"], "delegate_to": value["delegate_to"], "source_id": source["id"], "uncertainty_basis": uncertainty_basis(question)}
                state.put("questions", {**question, "response_disposition": disposition, **({"status": "awaiting_person"} if kind == "delegate" else {})})
                if kind == "dismiss" and value["disposition"] == "not_company" and question.get("target_kind") == "context":
                    state.put("questions", {**state.questions[qid], "status": "resolved", "resolved_by": source["id"], "resolution_kind": "not_company"})
                dispositions.append({"question_id": qid, **disposition})
                if kind == "dismiss" and value["disposition"] == "not_company" and question.get("target_kind") == "answer_assignment":
                    original_id = question["unassigned_answer_id"]
                    for related in state.jobs.values():
                        if related["id"] != job["id"] and related["status"] != "applied" and (related.get("unassigned_answer_id") == original_id or related.get("disambiguation_question_id") == qid or related["id"] == identifier(original_id, "interpret")):
                            related.update(status="applied", receipt={"state": "dismissed_attributed", "source_id": source["id"], "question_id": qid}, error_code=None)
            elif task_ids and question.get("target_kind") == "answer_assignment":
                state.put("questions", {**question, "status": "resolved", "resolved_by": source["id"]})
                original_job = state.jobs.get(identifier(question["unassigned_answer_id"], "interpret"))
                if original_job and original_job["id"] != job["id"]:
                    original_job.update(status="applied", receipt={"state": "assigned_by_message", "source_id": source["id"], "task_ids": task_ids})
        if kind == "unclear":
            qid = self._ask_answer_assignment(state, source, job["candidate_question_ids"])
            question = state.questions[qid]
            names = [state.tasks[tid]["name"] for tid in candidates]
            state.put("questions", {**question, "candidate_task_ids": sorted(candidates),
                "prompt": question["prompt"] + (" Tarefas que podem ser corrigidas: " + "; ".join(names[:5]) + "." if names else "")})
            job["disambiguation_question_id"] = qid
        return {"kind": kind, "task_ids": task_ids, "question_ids": question_ids, "response_disposition": dispositions}
