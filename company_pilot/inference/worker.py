"""One founder-configured worker consumes the durable company queue."""

import copy
from datetime import datetime
import re
import unicodedata

from .claude import InterpreterError


def _evidence_ids(value):
    """Collect supplied references, including field claims and incident edges."""
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get("source_id"), str):
            found.add(value["source_id"])
        for key in ("evidence_ids", "source_ids"):
            found.update(item for item in value.get(key, []) if isinstance(item, str))
        for child in value.values():
            found.update(_evidence_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_evidence_ids(child))
    return found


def _version_ref(record):
    if (not isinstance(record.get("id"), str) or type(record.get("version")) is not int
            or record["version"] < 1):
        raise ValueError("invalid_map_scope_record")
    return {"id": record["id"], "version": record["version"]}


def _answer_map(context, job, sources):
    """Bound candidates while retaining every incident predecessor edge version."""
    targets = set(job.get("task_ids", []))
    all_tasks = context.get("tasks", [])
    tasks = [task for task in all_tasks if task["id"] in targets]
    tasks += [task for task in all_tasks if task["id"] not in targets
              and not task.get("superseded_by")][-20:]
    task_ids = {task["id"] for task in tasks}
    all_edges = context.get("edges", [])
    incident = [edge for edge in all_edges if edge.get("from") in task_ids
                or edge.get("to") in task_ids]
    neighbors = {edge.get(endpoint) for edge in incident for endpoint in ("from", "to")}
    target_neighbors = {edge.get(endpoint) for edge in incident
                        if edge.get("from") in targets or edge.get("to") in targets
                        for endpoint in ("from", "to")}
    answer_text = " ".join(sources.get(sid, {}).get("text", "")
                           for sid in job.get("source_ids", []))
    field_values = " ".join(claim.get("value") or "" for task in tasks
                            for claim in task.get("fields", {}).values())
    mentioned = (answer_text + " " + field_values).casefold()
    nodes = [node for node in context.get("nodes", []) if node["id"] in neighbors or
             (node.get("name") and node["name"].casefold() in mentioned)]
    nodes.sort(key=lambda node: (node["id"] not in target_neighbors,
                                not (node.get("name") and node["name"].casefold() in mentioned)))
    nodes = nodes[:20]
    supplied_ids = task_ids | {node["id"] for node in nodes}
    edges = [edge for edge in all_edges if edge.get("from") in task_ids or
             edge.get("to") in task_ids or
             (edge.get("from") in supplied_ids and edge.get("to") in supplied_ids)]
    return tasks, nodes, edges


def _message_map(context, job, sources):
    """Use receipt candidates exactly; task hints and recency never widen them."""
    refs = job.get("candidate_task_versions")
    if type(refs) is not list or len(refs) > 50:
        raise ValueError("invalid_message_candidates")
    refs = [_version_ref(ref) for ref in refs]
    ids = [ref["id"] for ref in refs]
    if len(set(ids)) != len(ids):
        raise ValueError("invalid_message_candidates")
    available = {task["id"]: task for task in context.get("tasks", [])}
    narrowed = {**context, "tasks": [available[task_id] for task_id in ids]}
    return _answer_map(narrowed, {**job, "task_ids": ids}, sources)


def _active_records(context, key):
    return [record for record in context.get(key, []) if record.get("status", "active") == "active"]


def _record_tasks(context, job, sources):
    """Prefer tasks citing this logical record, keeping an active twenty-task cap."""
    record_ids = {sources[sid].get("logical_record_id") for sid in job.get("source_ids", [])
                  if sid in sources and sources[sid].get("logical_record_id")}
    active = [task for task in context.get("tasks", []) if not task.get("superseded_by")]
    matched = [task for task in active if any(sources.get(sid, {}).get("logical_record_id") in record_ids
                                            for sid in _evidence_ids(task))]
    matched = matched[-20:]
    matched_ids = {task["id"] for task in matched}
    remaining = 20 - len(matched)
    others = [task for task in active if task["id"] not in matched_ids]
    return matched + (others[-remaining:] if remaining else [])


def _record_keys(source):
    """Use frozen record/thread identifiers, never a person/project name."""
    result = set()
    meta = source.get("observation", {}).get("source", {})
    if isinstance(meta, dict):
        for key in ("record_ref", "thread_id", "conversation_id"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                namespace = "" if key == "record_ref" else meta.get("system", meta.get("app", ""))
                result.add((key, namespace, value.strip()))
    if source.get("logical_record_id"):
        result.add(("logical_record_id", "", source["logical_record_id"]))
    return result


def _words(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))))


def _observation_tasks(context, job, sources):
    """Keep bounded record, recent and supported object candidates; write nothing."""
    active = [task for task in context.get("tasks", []) if not task.get("superseded_by")]
    order = {task["id"]: index for index, task in enumerate(active)}
    current = [sources[sid] for sid in job.get("source_ids", []) if sid in sources]
    keys = {key for source in current for key in _record_keys(source)}
    text = " " + _words(" ".join(source.get("text", "") for source in current)) + " "
    identity_names = {_words(node["name"]) for node in context.get("nodes", [])
                      if node.get("kind") in ("person", "project", "organization") and isinstance(node.get("name"), str)}

    def newest(task):
        stamps = []
        for sid in _evidence_ids(task):
            value = sources.get(sid, {}).get("received_at")
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is not None:
                    stamps.append(stamp.timestamp())
            except (AttributeError, TypeError, ValueError):
                pass
        return (max(stamps, default=float("-inf")), order[task["id"]])

    def supported_object(task):
        for field in ("input", "result"):
            claim = task.get("fields", {}).get(field, {})
            value = claim.get("value")
            if not isinstance(value, str) or claim.get("basis") not in ("observed", "reported"):
                continue
            phrase = _words(value)
            if (len(phrase.split()) < 2 or phrase in identity_names or
                    phrase in ("nao se aplica", "nao ha", "sem registro", "sem repasse")):
                continue
            evidence = claim.get("evidence", [])
            if evidence and all(ref.get("source_id") in sources and isinstance(ref.get("quote"), str)
                                and ref["quote"] and ref["quote"] in sources[ref["source_id"]].get("text", "")
                                for ref in evidence) and " " + phrase + " " in text:
                return True
        return False

    recent = sorted(active, key=newest, reverse=True)
    related = [task for task in recent if keys.intersection({key for sid in _evidence_ids(task)
                                                           for key in _record_keys(sources.get(sid, {}))})]
    handoffs = [task for task in recent if supported_object(task)]
    chosen = []
    seen = set()
    for task in related[:10] + recent[:5] + handoffs[:5] + related + handoffs + recent:
        if task["id"] not in seen:
            chosen.append(task)
            seen.add(task["id"])
        if len(chosen) == 20:
            break
    return chosen


def _accepted_comparison_sources(context, sources, required):
    """Only successful, non-historic analyses admit optional comparison inputs."""
    accepted = {source_id for audit in context.get("sources", [])
                if audit.get("kind") == "analysis" and audit.get("source_scope") != "historic"
                for source_id in audit.get("input_source_ids", [])}
    candidates = [source["id"] for source in sources.values() if source["id"] in accepted
                  and source["id"] not in required and source.get("kind") not in ("analysis", "answer_assignment")]
    return candidates[-50:]


def scope_for_apply(job, map_scope, result):
    """The same scope the worker would pass into apply_analysis, or 'invalid'."""
    needs = job["kind"] in ("message", "company_record") or (
        job["kind"] == "observation" and map_scope and set(map_scope["source_ids"]) - set(job["source_ids"]))
    if needs:
        return map_scope if map_scope is not None else "invalid"
    if result.get("map_changes"):
        if job["kind"] != "answer" or not job.get("task_ids") or map_scope is None:
            return "invalid"
        return map_scope
    return None


def prepare_context(brain, job):
    """Build the exact context and map scope run_once would send to an interpreter."""
    context = brain.snapshot()
    sources = {source["id"]: source for source in context.get("sources", [])}
    if job["kind"] == "company_record" and job.get("source_ids"):
        primary = sources.get(job["source_ids"][0], {})
        logical_id = primary.get("logical_record_id")
        catalog = context.get("company_records", [])
        entry = (catalog.get(logical_id) if isinstance(catalog, dict) else
                 next((item for item in catalog if logical_id and
                       item.get("id", item.get("logical_record_id")) == logical_id), None))
        if entry is not None and entry.get("current_source_id") != primary.get("id"):
            return {"historic": True, "context": None, "map_scope": None}
    wanted = set(job.get("source_ids", []))
    if job["kind"] == "message":
        explicit_questions = set(job.get("question_ids", []))
        question_ids = set(job.get("candidate_question_ids", [])) | explicit_questions
        questions = [question for question in context.get("questions", [])
                     if question["id"] in question_ids and question.get("status") != "superseded"
                     and (question.get("status") != "resolved" or question["id"] in explicit_questions)]
    else:
        question_ids = {qid for sid in wanted for qid in sources.get(sid, {}).get("question_ids", [])}
        question_ids.update(job.get("candidate_question_ids", []))
        questions = [question for question in context.get("questions", []) if question["id"] in question_ids]
    wanted.update(_evidence_ids(questions))
    context_fields = context.get("context", {})
    wanted.update(_evidence_ids(context_fields))
    active_context = {}
    for key in ("context_claims", "recognition_markers"):
        if key in context:
            active_context[key] = _active_records(context, key)
            wanted.update(_evidence_ids(active_context[key]))
    tasks = context.get("tasks", [])
    map_scope = None
    if job["kind"] in ("answer", "message"):
        tasks, nodes, edges = (_message_map(context, job, sources) if job["kind"] == "message"
                               else _answer_map(context, job, sources))
        wanted.update(_evidence_ids([tasks, nodes, edges]))
        map_scope = {"tasks": [_version_ref(task) for task in tasks],
                     "nodes": [_version_ref(node) for node in nodes],
                     "edges": [_version_ref(edge) for edge in edges],
                     "source_ids": sorted(wanted & sources.keys())}
    else:
        tasks = (_record_tasks(context, job, sources) if job["kind"] == "company_record"
                 else _observation_tasks(context, job, sources) if job["kind"] == "observation"
                 else [task for task in tasks if not task.get("superseded_by")][-20:])
        wanted.update(_evidence_ids(tasks))
        if job["kind"] == "company_record":
            map_scope = {"tasks": [_version_ref(task) for task in tasks], "nodes": [], "edges": [],
                         "source_ids": sorted(wanted & sources.keys())}
        if job["kind"] == "observation":
            task_ids = {task["id"] for task in tasks}
            questions = [copy.deepcopy(question) for question in context.get("questions", [])
                         if question.get("status") == "pending" and question.get("task_id") in task_ids][:100]
            for question in questions:
                if "known_facts" in question:
                    question["known_facts"] = [fact for fact in question["known_facts"]
                                               if fact.get("task_id") in task_ids]
            wanted.update(_evidence_ids(questions))
            wanted.update(_accepted_comparison_sources(context, sources, wanted))
            map_scope = {"tasks": [_version_ref(task) for task in tasks], "nodes": [], "edges": [],
                         "source_ids": sorted(wanted & sources.keys())}
    context = {key: context[key] for key in ("company", "context", "interview", "nodes") if key in context} | {
        "tasks": tasks, "sources": [sources[key] for key in sorted(wanted) if key in sources], "questions": questions,
    } | active_context
    if map_scope is not None and (job["kind"] != "observation" or
            set(map_scope["source_ids"]) - set(job["source_ids"])):
        if job["kind"] in ("answer", "message"):
            context.update(nodes=nodes, edges=edges)
        context["map_scope"] = copy.deepcopy(map_scope)
    return {"historic": False, "context": copy.deepcopy(context), "map_scope": map_scope}


class SemanticWorker:
    def __init__(self, brain, interpreter):
        self.brain = brain
        self.interpreter = interpreter

    def _pending(self, job_id, code, rejection_reason=None, quote_failures=None):
        extra = {}
        if rejection_reason:
            extra["rejection_reason"] = rejection_reason
        if quote_failures:
            extra["quote_failures"] = quote_failures
        try:
            receipt = self.brain.mark_job_error(job_id, code, **extra)
        except Exception:
            # A failed error receipt must never masquerade as an applied job.
            return {"status": "pending", "job_id": job_id,
                    "error_code": code, "error_recorded": False}
        return {"status": "pending", "job_id": job_id,
                "error_code": code, "error_recorded": True, "receipt": receipt, **({"rejection_reason": rejection_reason} if rejection_reason else {})}

    @staticmethod
    def _rejection(error):
        from ..brain.diagnostics import is_rejection_code
        code = getattr(error, "code", None)
        if not isinstance(code, str):
            code = str(error)
        if not is_rejection_code(code):
            return None, None
        failures = getattr(error, "quote_failures", None) if code == "source_quote_not_found" else None
        return code, failures

    def _completion(self, job_id, receipt):
        if any(item.get("status") == "unresolved" for item in receipt.get("question_outcomes", [])):
            return {"status": "awaiting_clarification", "application_status": receipt["state"], "job_id": job_id, "receipt": receipt}
        return {"status": "applied", "job_id": job_id, "receipt": receipt}

    def _historic_record(self, job_id):
        result = {"tasks": [], "context_updates": [], "nodes": [], "next_question": None,
                  "record_extraction": {"complete": False,
                                        "limitations": ["Fonte histórica; existe revisão posterior."]}}
        try:
            receipt = self.brain.apply_analysis(job_id, result)
        except Exception as error:
            reason, failures = self._rejection(error)
            return self._pending(job_id, "analysis_rejected", reason, failures)
        return self._completion(job_id, receipt)

    def run_once(self):
        """Apply at most one job. Failed jobs remain owned by the durable queue."""
        try:
            jobs = self.brain.pending_jobs()
        except Exception:
            return {"status": "pending", "error_code": "queue_unavailable"}
        if not jobs:
            try:
                waiting = [job for job in self.brain.snapshot().get("pending_jobs", [])
                           if job.get("status") == "awaiting_disambiguation"]
            except Exception:
                waiting = []
            if waiting:
                return {"status": "awaiting_clarification", "job_ids": [job["id"] for job in waiting],
                        "question_ids": [job["disambiguation_question_id"] for job in waiting if job.get("disambiguation_question_id")]}
            return {"status": "idle"}
        job = jobs[0]
        job_id = job["id"]
        try:
            prepared = prepare_context(self.brain, job)
        except Exception:
            return self._pending(job_id, "context_unavailable")
        if prepared["historic"]:
            return self._historic_record(job_id)
        context, map_scope = prepared["context"], prepared["map_scope"]
        try:
            result = self.interpreter.interpret(copy.deepcopy(job), context)
        except InterpreterError as error:
            return self._pending(job_id, error.code)
        except Exception:
            return self._pending(job_id, "interpretation_failed")
        try:
            metadata = getattr(self.interpreter, "last_metadata", None)
            kwargs = {"metadata": metadata} if metadata else {}
            scope = scope_for_apply(job, map_scope, result)
            if scope == "invalid":
                return self._pending(job_id, "invalid_analysis")
            if scope is not None:
                kwargs["map_scope"] = scope
            receipt = self.brain.apply_analysis(job_id, result, **kwargs)
        except Exception as error:
            reason, failures = self._rejection(error)
            return self._pending(job_id, "analysis_rejected", reason, failures)
        return self._completion(job_id, receipt)
