"""Evidence-preserving company work graph, shared by desktop and Nora workers."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from .stores import AccessDenied, BINARY_PREFIX, BrainError, ConcurrentWrite, IntegrityError, canonical, digest
from .policy import PolicyMixin, controls_for, screenshots_for
from .messages import MessageMixin
from .records import RecordMixin
from .knowledge import (seed_claims, apply_context, apply_markers, payment_claims, refresh_payment,
                       payment_gaps, marker_readback, recognition_gap, plan_context_conflict_repairs,
                       grounded_name, conflict_key, MARKER_KINDS, GENERIC_MARKERS, _marker_text, _marker_generic_key)
from .quotes import awaiting_manual_interview, locate_source_span, manual_interview_route, quote_not_found

FIELDS = ("trigger", "performer", "input", "action", "result", "purpose", "placement", "handoff", "tracking_record")
TOPICS = ("activities", "people", "stages", "tracking", "documents")
KINDS = ("organization", "responsibility", "job", "process", "task", "person", "tracking_record", "project", "work_object")
EDGE_KINDS = ("contains", "performed_by", "handoff", "depends_on", "tracks", "pays_for")
DERIVED_LINKS = (("performer", "person", "performed_by", False),
                 ("placement", "process", "contains", True),
                 ("handoff", "responsibility", "handoff", False),
                 ("tracking_record", "tracking_record", "tracks", False))
LABELS = {"trigger": "O que inicia a tarefa", "performer": "Quem executa", "input": "Entrada", "action": "Ação", "result": "Resultado observável", "purpose": "Finalidade", "placement": "Onde se encaixa", "handoff": "Repasse", "tracking_record": "Onde é acompanhado"}


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(namespace, value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(namespace) + ":" + str(value)))


def valid_uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise BrainError("uuid_required") from error


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise BrainError("nonempty_text_required")
    return value


class _State:
    def __init__(self, snapshot):
        self.files = dict(snapshot.files)
        self.meta = json.loads(self.files.get("meta/index.json", "{}"))
        self.company = self.meta.get("company")
        self.context = self.meta.get("context", {})
        self.context_claims = seed_claims(self.context, self.meta.get("context_claims", []))
        self.recognition_markers = self.meta.get("recognition_markers", [])
        self.company_records = self.meta.get("company_records", {})
        self.interview = self.meta.get("interview", {})
        self.jobs = self.meta.get("jobs", {})
        self.nora = self.meta.get("nora", {})
        self.attachments = self.meta.get("attachments", {})
        self.sources = {json.loads(text)["id"]: json.loads(text) for path, text in self.files.items() if path.startswith("sources/")}
        self.groups = {}
        for group in ("tasks", "nodes", "edges", "questions"):
            self.groups[group] = {key: json.loads(self.files[f"records/{group}/{key}/v{version:06}.json"])
                                  for key, version in self.meta.get(group, {}).items()}
        self.events = [json.loads(self.files[path]) for path in sorted(self.files) if path.startswith("ledger/")]
        if self.events:
            actual = digest(canonical({k: v for k, v in self.files.items() if not k.startswith(("ledger/", "views/", "notes/")) and k != "README.md"}))
            if self.events[-1].get("state_sha256") != actual:
                raise IntegrityError("state_not_supported_by_ledger")

    @property
    def tasks(self):
        return self.groups["tasks"]

    @property
    def questions(self):
        return self.groups["questions"]

    def put(self, group, record):
        target = self.groups[group]
        previous = target.get(record["id"])
        record = copy.deepcopy(record)
        record.pop("version", None)
        old = {k: v for k, v in (previous or {}).items() if k != "version"}
        if group == "questions":
            for key in ("response_disposition", "application", "resend_request_id", "priority_hint"):
                if previous and key in previous and key not in record:
                    record[key] = copy.deepcopy(previous[key])
            if previous and previous.get("status") == "awaiting_person" and record.get("status") == "pending":
                record["status"] = "awaiting_person"
            from .evolution import decorate_question
            record = decorate_question(self, record)
            # Nora freezes this revision, not our storage-record version. Every
            # projected change must advance it, including evidence and status.
            proposed = {key: value for key, value in record.items() if key != "revision"}
            old_projection = {key: value for key, value in old.items() if key != "revision"}
            record["revision"] = (previous or {}).get("revision", 0) + (proposed != old_projection)
        if old == record:
            return previous
        record["version"] = (previous or {}).get("version", 0) + 1
        target[record["id"]] = record
        return record

    def add_source(self, source):
        previous = self.sources.get(source["id"])
        if previous and previous != source:
            raise IntegrityError("immutable_source_conflict")
        self.sources[source["id"]] = source

    def dump(self):
        meta = {"company": self.company, "context": self.context, "interview": self.interview,
                "jobs": self.jobs, "nora": self.nora, "attachments": self.attachments,
                "context_claims": self.context_claims, "recognition_markers": self.recognition_markers, "company_records": self.company_records}
        for group, records in self.groups.items():
            meta[group] = {key: value["version"] for key, value in records.items()}
            for key, value in records.items():
                path = f"records/{group}/{key}/v{value['version']:06}.json"
                content = canonical(value)
                if path in self.files and self.files[path] != content:
                    raise IntegrityError("immutable_record_conflict")
                self.files[path] = content
        for key, source in self.sources.items():
            path = f"sources/{key}.json"
            content = canonical(source)
            if path in self.files and self.files[path] != content:
                raise IntegrityError("immutable_source_conflict")
            self.files[path] = content
        self.files["meta/index.json"] = canonical(meta)
        return self.files


class Brain(PolicyMixin, MessageMixin, RecordMixin):
    def __init__(self, store, retries=6):
        self.store, self.retries = store, retries

    def _founder(self, state, actor):
        if actor["id"] not in state.company.get("founders", []):
            raise AccessDenied("founder_authority_required")

    def _worker(self, state, actor):
        if actor["id"] not in state.company.get("semantic_workers", []):
            raise AccessDenied("configured_semantic_worker_required")

    def _transaction(self, kind, event_id, payload, operation, initialize=False):
        valid_uuid(event_id)
        payload_hash = digest(canonical(payload))
        for _ in range(self.retries):
            snapshot = self.store.read()
            actor = self.store.authenticated_actor()
            if "write" not in actor["permissions"]:
                raise AccessDenied("repository_write_required")
            state = _State(snapshot)
            if not initialize and not state.company:
                raise BrainError("brain_not_initialized")
            previous = next((event for event in state.events if event["id"] == event_id), None)
            if previous:
                if previous["request_sha256"] != payload_hash or previous["actor"] != actor["id"] or previous["kind"] != kind:
                    raise BrainError("idempotency_payload_conflict")
                return copy.deepcopy(previous["result"])
            result = operation(state, actor)
            canonical(result)  # callbacks must return JSON, never live objects/effects
            files = state.dump()
            if files == snapshot.files:
                return result
            event = {"id": event_id, "sequence": len(state.events) + 1, "kind": kind,
                     "actor": actor["id"], "provider": actor["provider"], "at": now(),
                     "request_sha256": payload_hash, "result": result,
                     "previous_hash": state.events[-1]["hash"] if state.events else None,
                     "state_sha256": digest(canonical({k: v for k, v in files.items() if not k.startswith(("ledger/", "views/", "notes/")) and k != "README.md"}))}
            if kind == "analysis":
                inference = payload.get("metadata", {})
                event.update(storage_provider=actor["provider"], provider=inference.get("execution_provider") or "unknown", inference=copy.deepcopy(inference))
            event["hash"] = digest(canonical(event))
            files[f"ledger/{event['sequence']:08}-{event_id}.json"] = canonical(event)
            from .render import render
            rendered = render(state, self.store)
            for key in [key for key in files if key.startswith("notes/") and key not in rendered]:
                del files[key]
            files.update(rendered)
            try:
                self.store.commit(snapshot.head, files, "company-pilot: " + kind)
                return result
            except ConcurrentWrite:
                continue
        raise ConcurrentWrite("retry_limit_reached")

    def initialize(self, company_name, synthetic=True, screenshots=None, screenshots_allowed=None, event_id=None):
        if screenshots_allowed is not None and screenshots is None:
            screenshots = {"founder": bool(screenshots_allowed), "team": bool(screenshots_allowed)}
        screenshots = screenshots or {"founder": True, "team": True}
        if set(screenshots) != {"founder", "team"} or any(type(v) is not bool for v in screenshots.values()):
            raise BrainError("separate_founder_team_screenshot_choices_required")
        _text(company_name)
        event_id = event_id or identifier(company_name, "initialize")
        payload = {"name": company_name, "synthetic": synthetic, "screenshots": screenshots}
        def apply(state, actor):
            if state.company:
                raise BrainError("brain_already_initialized")
            if "admin" not in actor["permissions"]:
                raise AccessDenied("owner_operator_initialization_required")
            if self.store.development and not synthetic:
                raise AccessDenied("development_adapter_accepts_synthetic_only")
            state.company = {"id": identifier(company_name, event_id), "name": company_name,
                             "synthetic": bool(synthetic), "founders": [actor["id"]],
                             "semantic_workers": [actor["id"]], "nora_actors": [actor["id"]],
                             "screenshots": screenshots, "policy_version": 1, "context_version": 1,
                             "personal_exclusions": [], "record_refs": [], "tool_hints": [],
                             "watchdog_actors": [],
                             "access_boundary": "repository", "access_plan": None}
            return {"company_id": state.company["id"], "state": "ready", "synthetic": bool(synthetic)}
        return self._transaction("initialize", event_id, payload, apply, initialize=True)

    def _read_with_actor(self):
        read = getattr(self.store, "read_with_actor", None)
        if read is not None:
            return read()
        return self.store.read(), self.store.authenticated_actor()

    def snapshot(self):
        snapshot, actor = self._read_with_actor()
        return self._snapshot(snapshot, actor)

    def _snapshot(self, snapshot, actor):
        state = _State(snapshot)
        if not state.company:
            raise BrainError("brain_not_initialized")
        current_time = datetime.now(timezone.utc)
        jobs = []
        for job in state.jobs.values():
            if job["status"] != "applied":
                jobs.append({**copy.deepcopy(job), "age_seconds": max(0, int((current_time - datetime.fromisoformat(job["created_at"])).total_seconds()))})
        return {"company": copy.deepcopy(state.company), "context": copy.deepcopy(state.context),
                "context_claims": copy.deepcopy(state.context_claims), "recognition_markers": copy.deepcopy(state.recognition_markers),
                "company_records": copy.deepcopy(list(state.company_records.values())),
                "interview": copy.deepcopy(state.interview), "tasks": copy.deepcopy(list(state.tasks.values())),
                "nodes": copy.deepcopy(list(state.groups["nodes"].values())), "edges": copy.deepcopy(list(state.groups["edges"].values())),
                "questions": copy.deepcopy(list(state.questions.values())), "sources": copy.deepcopy(list(state.sources.values())),
                "coverage": [{"source_id": source["id"], "actor_id": source["actor"], "occurred_at": source.get("safe_metadata", {}).get("occurred_at"), "reason": "withheld", "status": "coverage_only", "coverage": source.get("safe_metadata", {}).get("coverage")} for source in state.sources.values() if source.get("safe_metadata", {}).get("privacy_disposition") == "withheld"],
                "pending_jobs": jobs, "nora": copy.deepcopy(state.nora), "head": snapshot.head,
                "evidence_level": "E1" if actor["provider"] == "local_fixture" else "remote_store"}

    def pending_jobs(self):
        state = _State(self.store.read())
        self._worker(state, self.store.authenticated_actor())
        current = now()
        return [job for job in self.snapshot()["pending_jobs"] if job["status"] == "pending"
                and job.get("next_attempt_at", current) <= current and not awaiting_manual_interview(job)]

    def analysis_queue(self):
        """Every pending analysis, including backoff and repeated interview refusals.

        Only the configured semantic operator may read this queue. The founder's
        device stores captures and answers; it does not interpret them.
        """
        state = _State(self.store.read())
        self._worker(state, self.store.authenticated_actor())
        return [job for job in self.snapshot()["pending_jobs"] if job["status"] == "pending"]

    def assign_external_analyst(self, login, event_id):
        """Hand analysis to one operator and make queued jobs eligible immediately."""
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?", login):
            raise BrainError("semantic_operator_login_required")

        def apply(state, actor):
            self._founder(state, actor)
            state.company["semantic_workers"] = [login]
            state.company["policy_version"] += 1
            current = now()
            reset = []
            for job in state.jobs.values():
                if job.get("status") != "pending":
                    continue
                job["next_attempt_at"] = current
                reset.append(job["id"])
            return {"semantic_workers": [login], "analysis": "external", "backoff_reset": sorted(reset),
                    "policy_version": state.company["policy_version"]}

        return self._transaction("external_analyst", event_id, {"login": login}, apply)

    def device_policy(self):
        snapshot = self.snapshot()
        company, actor = snapshot["company"], self.store.authenticated_actor()
        role = "founder" if actor["id"] in company["founders"] else "team"
        issued = datetime.now(timezone.utc)
        controls = controls_for(company, actor["id"])
        markers = marker_readback(snapshot["context"], snapshot["recognition_markers"])
        return {"schema_version": 1, "company_id": company["id"], "policy_version": company["policy_version"],
                "context_version": company["context_version"], "issued_at": issued.isoformat(),
                "expires_at": (issued + timedelta(minutes=5)).isoformat(), "role": role,
                "screenshots": company["screenshots"], "screenshots_allowed": screenshots_for(company, actor["id"]),
                "controls": controls, "recognition_markers": markers,
                "work_signals": list(dict.fromkeys(marker["value"] for marker in markers)),
                "personal_exclusions": company["personal_exclusions"], "record_refs": company["record_refs"],
                "tool_hints": company["tool_hints"], "synthetic": company["synthetic"]}

    def update_policy(self, changes, event_id):
        allowed = {"screenshots", "personal_exclusions", "record_refs", "tool_hints", "semantic_workers", "founders", "nora_actors", "watchdog_actors"}
        if set(changes) - allowed:
            raise BrainError("unknown_policy_field")
        def apply(state, actor):
            self._founder(state, actor)
            if "screenshots" in changes and (set(changes["screenshots"]) != {"founder", "team"} or any(type(v) is not bool for v in changes["screenshots"].values())):
                raise BrainError("separate_founder_team_screenshot_choices_required")
            for key in allowed - {"screenshots"}:
                if key in changes and (not isinstance(changes[key], list) or not all(isinstance(x, str) for x in changes[key])):
                    raise BrainError("policy_string_list_required")
            if "founders" in changes and not changes["founders"]:
                raise BrainError("founder_authority_cannot_be_empty")
            state.company.update(copy.deepcopy(changes))
            state.company["policy_version"] += 1
            return {"policy_version": state.company["policy_version"]}
        return self._transaction("policy", event_id, changes, apply)

    def access_plan(self, requested):
        if not hasattr(self.store, "access_plan"):
            return {"applied": False, "evidence_level": "E1", "requests": requested, "fixture_only": True}
        return self.store.access_plan(requested)

    def upload_attachment(self, event_id, data, sha256, media_type="image/png"):
        valid_uuid(event_id)
        if not isinstance(data, bytes) or len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != sha256:
            raise BrainError("attachment_hash_or_size_invalid")
        if media_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix = "png"
        elif media_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"):
            suffix = "jpg"
        else:
            raise BrainError("attachment_type_invalid")
        def apply(state, actor):
            role = "founder" if actor["id"] in state.company["founders"] else "team"
            if not screenshots_for(state.company, actor["id"]):
                raise AccessDenied("screenshots_disabled")
            attachment_id = identifier(actor["id"], event_id + ":attachment")
            path = f"attachments/{sha256}.{suffix}"
            state.files[path] = BINARY_PREFIX + base64.b64encode(data).decode("ascii")
            state.attachments[attachment_id] = {"id": attachment_id, "event_id": event_id, "actor": actor["id"],
                                                "sha256": sha256, "media_type": media_type, "path": path}
            return {"attachment_id": attachment_id, "sha256": sha256, "inspect_url": self.store.inspect_url(path)}
        return self._transaction("attachment", identifier(event_id, "upload"), {"event_id": event_id, "sha256": sha256, "media_type": media_type}, apply)

    def _source(self, state, actor, source_id, kind, text, **extra):
        source = {"id": source_id, "kind": kind, "text": text, "sha256": digest(text),
                  "actor": actor["id"], "provider": actor["provider"], "received_at": now(),
                  "synthetic": state.company["synthetic"], **extra}
        if kind == "analysis":
            inference = extra.get("interpretation", {})
            source.update(storage_provider=actor["provider"], provider=inference.get("execution_provider") or "unknown")
        state.add_source(source)
        return source

    def _queue(self, state, source, kind, task_ids=()):
        job_id = identifier(source["id"], "interpret")
        state.jobs[job_id] = {"id": job_id, "kind": kind, "source_ids": [source["id"]], "task_ids": list(task_ids),
                              "status": "pending", "created_at": now(), "attempts": 0, "error_code": None}
        return job_id

    def _context_conflict(self, state, topic, previous, candidate, source_id, origin=None):
        field = {"activities": "purpose", "people": "performer", "stages": "placement", "tracking": "tracking_record", "documents": "input"}[topic]
        qid = identifier(state.company["id"], "context-conflict:" + topic)
        old_question = state.questions.get(qid)
        sources = sorted({ref["source_id"] for claim in (previous, candidate) for ref in claim["evidence"]})
        record = {"topic": topic, "previous": previous, "reported_candidate": candidate, "question_id": qid,
                  "status": "pending", "source_id": source_id}
        if origin:
            record["origin"] = origin
        state.interview.setdefault("context_conflicts", []).append(record)
        state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": state.company["id"],
            "target_kind": "context_conflict", "context_topic": topic, "base_version": state.company["context_version"],
            "task_version": state.company["context_version"], "missing_links": [field], "missing_fields": [field],
            "uncertainty_ids": [qid], "context_key": "company-context:" + topic,
            "activity_summary": "Relatos diferentes sobre o contexto da empresa",
            "prompt": f"Há dois relatos sobre a empresa: «{previous['value']}» e «{candidate['value']}». Qual descreve o trabalho atual e o que precisa ser corrigido?",
            "status": "pending", "created_at": (old_question or {}).get("created_at", now()),
            "observation_ids": sources, "evidence_ids": sources, "revisit_evidence_ids": [source_id]})
        return qid

    def _interview_gap(self, state, topic, source, mode, candidate_claims=None, related_sources=()):
        qid = identifier(state.company["id"], "interview-gap:" + topic)
        previous = state.questions.get(qid)
        evidence_ids = sorted(set((previous or {}).get("evidence_ids", []) + [source["id"]] + list(related_sources)))
        field = {"activities": "purpose", "people": "performer", "stages": "placement", "tracking": "tracking_record", "documents": "input"}[topic]
        names = {"activities": "atividades da empresa", "people": "pessoas e responsabilidades", "stages": "etapas do trabalho", "tracking": "acompanhamento de andamento, pagamentos e saldos", "documents": "documentos e registros úteis"}
        disposition = (previous or {}).get("response_disposition", {})
        awaiting = (previous or {}).get("status") == "awaiting_person" and disposition.get("kind") == "delegate"
        state.interview.setdefault("deferred_topics", {})[topic] = {
            "status": "awaiting_person" if awaiting else "pending", "mode": mode, "source_id": source["id"], "question_id": qid,
            "literal_text": source["text"], "candidate_claims": copy.deepcopy(candidate_claims or []),
            **({"delegate_to": disposition["delegate_to"]} if awaiting else {}),
        }
        state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": state.company["id"],
            "target_kind": "context_deferred", "context_topic": topic, "base_version": state.company["context_version"],
            "task_version": state.company["context_version"], "missing_links": [field], "missing_fields": [field],
            "uncertainty_ids": [qid], "context_key": "interview:" + topic,
            "activity_summary": "Contexto ainda não esclarecido: " + names[topic],
            "prompt": f"Ficou pendente esclarecer {names[topic]}. Você informou «{source['text'][:300]}». O que já pode confirmar, inclusive se algo não se aplica?",
            "status": "pending", "created_at": (previous or {}).get("created_at", now()),
            "observation_ids": evidence_ids, "evidence_ids": evidence_ids,
            "revisit_evidence_ids": [source["id"]], "candidate_claims": copy.deepcopy(candidate_claims or [])})
        return qid

    def _set_interview_context(self, state, topic, claim, source, mode="replace", replaces=(), resolve_conflicts=True, keep_siblings=False):
        if keep_siblings:
            mode = "add"
        apply_context(self, state, topic, claim, source, mode, replaces, explicit_replace=(mode == "replace" and source["kind"] == "interview_correction" and not keep_siblings))
        deferred = state.interview.get("deferred_topics", {}).get(topic)
        if deferred and deferred["status"] in ("pending", "awaiting_person"):
            deferred.update(status="resolved", resolved_by=source["id"])
            qid = deferred["question_id"]
            state.put("questions", {**state.questions[qid], "status": "resolved", "resolved_by": source["id"]})
        for conflict in state.interview.get("context_conflicts", []):
            if resolve_conflicts and conflict["topic"] == topic and conflict["status"] == "pending":
                conflict.update(status="resolved", resolved_by=source["id"], selected=claim)
                qid = conflict["question_id"]
                state.put("questions", {**state.questions[qid], "status": "resolved", "resolved_by": source["id"]})

    def interview_correct(self, topic, text, event_id):
        """Explicit founder correction, including after setup is complete."""
        if topic not in TOPICS:
            raise BrainError("unknown_context_topic")
        _text(text)
        def apply(state, actor):
            self._founder(state, actor)
            raw = self._source(state, actor, identifier(state.company["id"], "interview-correct:" + event_id),
                "interview_correction", text, speaker=actor["id"], context_topic=topic,
                previous_context=copy.deepcopy(state.context.get(topic)))
            job_id = self._queue(state, raw, "interview")
            state.jobs[job_id]["interview_action"] = {"mode": "correct", "topic": topic}
            return {"source_id": raw["id"], "job_id": job_id, "topic": topic, "state": "pending_analysis"}
        return self._transaction("interview_correct", event_id, {"topic": topic, "text": text}, apply)

    def interview_defer(self, topic, text, event_id, *, delegate_to=None):
        """Record an explicitly unknown topic without treating it as a fact."""
        if topic not in TOPICS:
            raise BrainError("unknown_context_topic")
        _text(text)
        if delegate_to is not None:
            delegate_to = _text(delegate_to)
        def apply(state, actor):
            self._founder(state, actor)
            previous = copy.deepcopy(state.context.get(topic))
            raw = self._source(state, actor, identifier(state.company["id"], "interview-defer:" + event_id),
                "interview_deferred", text, speaker=actor["id"], context_topic=topic, previous_context=previous)
            related = []
            for job in state.jobs.values():
                source = state.sources[job["source_ids"][0]]
                job_topic = job.get("interview_action", {}).get("topic") or source.get("question", {}).get("topic")
                if job["kind"] == "interview" and job["status"] == "pending":
                    if job.get("interview_action", {}).get("mode") == "correct":
                        # Scoped corrections for other topics remain untouched.
                        if job_topic == topic:
                            related.extend(job["source_ids"])
                            job.update(status="applied", receipt={"state": "deferred", "source_id": raw["id"]}, error_code=None)
                    else:
                        # Every older plain answer can contain this topic, whatever
                        # question prompted it. Keep all its other facts queued.
                        related.extend(job["source_ids"])
                        job["excluded_context_topics"] = sorted(set(job.get("excluded_context_topics", []) + [topic]))
            # Only the current read model is withdrawn. Literal sources, previous
            # claims, versions and the reason remain retained and inspectable.
            state.context.pop(topic, None)
            for record in state.context_claims:
                if record["topic"] == topic and record["status"] == "active":
                    record.update(status="deferred", deferred_by=raw["id"])
            state.interview.setdefault("context_history", []).append({"topic": topic, "previous": previous,
                "selected": None, "source_id": raw["id"], "speaker": actor["id"], "at": now()})
            state.company["context_version"] += 1
            qid = self._interview_gap(state, topic, raw, "deferred", related_sources=related)
            if delegate_to:
                from .messages import uncertainty_basis
                question = state.questions[qid]
                disposition = {"kind": "delegate", "delegate_to": delegate_to, "disposition": None, "source_id": raw["id"], "uncertainty_basis": uncertainty_basis(question)}
                state.put("questions", {**question, "status": "awaiting_person", "response_disposition": disposition})
                state.interview["deferred_topics"][topic].update(status="awaiting_person", delegate_to=delegate_to)
            deferred = state.interview["deferred_topics"][topic]
            awaiting = deferred["status"] == "awaiting_person"
            return {"source_id": raw["id"], "question_id": qid, "topic": topic, "state": "awaiting_person" if awaiting else "deferred_unknown", **({"delegate_to": deferred["delegate_to"]} if awaiting else {})}
        return self._transaction("interview_defer", event_id, {"topic": topic, "text": text, **({"delegate_to": delegate_to} if delegate_to else {})}, apply)

    def _ask_answer_assignment(self, state, answer, candidates):
        qid = identifier(answer["id"], "disambiguate")
        previous = state.questions.get(qid)
        names = sorted({state.questions[key]["activity_summary"] for key in candidates})
        prompt = f"A resposta «{answer['text'][:300]}» se refere a qual atividade da empresa?"
        if names:
            prompt += " As atividades candidatas são: " + "; ".join(names[:5]) + ". Se for outra, descreva qual."
        state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": state.company["id"],
            "target_kind": "answer_assignment", "unassigned_answer_id": answer["id"], "candidate_question_ids": candidates,
            "base_version": state.company["context_version"], "task_version": state.company["context_version"],
            "missing_links": ["placement"], "missing_fields": ["placement"], "uncertainty_ids": [qid],
            "context_key": "answer:" + answer["id"], "activity_summary": "Resposta sem ligação confirmada a uma atividade",
            "prompt": prompt, "status": "pending", "created_at": (previous or {}).get("created_at", now()),
            "observation_ids": [answer["id"]], "evidence_ids": [answer["id"]], "revisit_evidence_ids": []})
        return qid

    def ingest_observation(self, payload, *, _batch=None):
        allowed = {"schema_version", "event_id", "device_id", "occurred_at", "policy_version", "source", "classification", "content", "synthetic", "screenshot"}
        if set(payload) - allowed or type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
            raise BrainError("invalid_observation_schema_or_identity_override")
        event_id, device_id = valid_uuid(payload.get("event_id")), valid_uuid(payload.get("device_id"))
        text = _text(payload.get("content", {}).get("text"))
        if len(text.encode()) > 256 * 1024 or digest(text) != payload["content"].get("sha256"):
            raise BrainError("observation_text_hash_or_size_invalid")
        try:
            if datetime.fromisoformat(payload["occurred_at"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except (KeyError, TypeError, ValueError) as error:
            raise BrainError("timestamp_with_timezone_required") from error
        if payload.get("classification", {}).get("label") != "company":
            raise BrainError("classified_company_only")
        _text(payload.get("source", {}).get("kind"))
        _text(payload.get("source", {}).get("app"))
        _text(payload.get("classification", {}).get("reason"))
        def apply(state, actor):
            source_id = identifier(state.company["id"], actor["id"] + ":" + device_id + ":" + event_id)
            previous = state.sources.get(source_id)
            if previous:
                if previous.get("observation") != payload:
                    raise BrainError("idempotency_payload_conflict")
                return {"observation_id": source_id, "job_id": identifier(source_id, "interpret"), "state": "pending_analysis", "inspect_url": self.store.inspect_url(f"views/sources/{source_id}.md")}
            controls = controls_for(state.company, actor["id"])
            if not controls["capture_allowed"]:
                raise AccessDenied(controls["reason"])
            if state.company["synthetic"] != bool(payload.get("synthetic")):
                raise AccessDenied("synthetic_real_boundary_mismatch")
            if payload.get("policy_version") != state.company["policy_version"]:
                raise BrainError("refresh_device_policy")
            attachment = None
            if payload.get("screenshot"):
                role = "founder" if actor["id"] in state.company["founders"] else "team"
                attachment = state.attachments.get(payload["screenshot"].get("attachment_id"))
                if not screenshots_for(state.company, actor["id"]) or not attachment or attachment["actor"] != actor["id"] or attachment["event_id"] != event_id or attachment["sha256"] != payload["screenshot"].get("sha256"):
                    raise AccessDenied("attachment_policy_or_owner_mismatch")
            source = self._source(state, actor, source_id, "observation", text, observation=copy.deepcopy(payload), attachment=attachment)
            job_id = self._queue(state, source, "observation")
            return {"observation_id": source_id, "job_id": job_id, "state": "pending_analysis", "inspect_url": self.store.inspect_url(f"views/sources/{source_id}.md")}
        if _batch is not None:
            return apply(*_batch)
        return self._transaction("observation", identifier(actor_key(self.store), event_id + ":" + device_id), payload, apply)

    send_observation = ingest_observation

    def ingest_batch(self, payloads, event_id=None):
        if not isinstance(payloads, list) or not payloads or len(payloads) > 50:
            raise BrainError("observation_batch_size_invalid")
        def apply(state, actor):
            return [self.ingest_observation(payload, _batch=(state, actor)) for payload in payloads]
        return self._transaction("observation_batch", event_id or identifier(actor_key(self.store), digest(canonical(payloads))), payloads, apply)

    send_observations = ingest_batch

    def ingest_context_gap(self, payload):
        """Intake only safe coverage metadata, never unknown title/body/image."""
        allowed = {"schema_version", "event_id", "device_id", "occurred_at", "app", "surface_kind", "reason", "synthetic", "context_key", "coverage", "privacy_disposition"}
        if set(payload) - allowed or payload.get("schema_version") != 1 or payload.get("surface_kind") != "desktop_window":
            raise BrainError("safe_context_gap_schema_required")
        event_id, device_id = valid_uuid(payload.get("event_id")), valid_uuid(payload.get("device_id"))
        app = _text(payload.get("app"))
        if len(app) > 200 or "\n" in app:
            raise BrainError("app_identity_required")
        safe = {key: payload.get(key) for key in ("schema_version", "event_id", "device_id", "occurred_at", "app", "surface_kind", "synthetic")}
        privacy = payload.get("privacy_disposition")
        if privacy is not None:
            if privacy not in ("personal", "excluded", "withheld"):
                raise BrainError("explicit_privacy_disposition_required")
            safe["privacy_disposition"] = privacy
            safe["app"] = app = "Aplicativo não compartilhado"
        if "context_key" in payload:
            if not isinstance(payload["context_key"], str) or not re.fullmatch(r"[0-9a-f]{64}", payload["context_key"]):
                raise BrainError("opaque_context_key_required")
            safe["context_key"] = payload["context_key"]
        if "coverage" in payload:
            coverage = payload["coverage"]
            if not isinstance(coverage, dict) or set(coverage) != {"period_start", "period_end", "observed_samples", "distinct_windows", "expired_unresolved"} or any(type(coverage[key]) is not int or not 0 <= coverage[key] <= 1000000 for key in ("observed_samples", "distinct_windows", "expired_unresolved")):
                raise BrainError("safe_coverage_summary_required")
            try:
                start, end = [datetime.fromisoformat(coverage[key].replace("Z", "+00:00")) for key in ("period_start", "period_end")]
                if start.tzinfo is None or end.tzinfo is None or start > end:
                    raise ValueError()
            except (TypeError, AttributeError, ValueError) as error:
                raise BrainError("ordered_coverage_period_required") from error
            safe["coverage"] = copy.deepcopy(coverage)
        def apply(state, actor):
            controls = controls_for(state.company, actor["id"])
            if not controls["capture_allowed"]:
                raise AccessDenied(controls["reason"])
            if state.company["synthetic"] != bool(payload.get("synthetic")):
                raise AccessDenied("synthetic_real_boundary_mismatch")
            if privacy in ("personal", "excluded"):
                return {"state": "excluded", "reason": "participant_privacy_disposition", "privacy_disposition": privacy}
            source_id = identifier(actor["id"], device_id + ":gap:" + event_id)
            key = actor["id"] + ":" + device_id + ":" + safe["context_key"] if "context_key" in safe else app.casefold()
            qid = identifier(state.company["id"], "context-gap:" + key)
            if privacy == "withheld":
                if source_id not in state.sources:
                    self._source(state, actor, source_id, "coverage_gap", "Parte da cobertura não foi compartilhada. Não se conclui se era pessoal ou da empresa; não há pergunta ao fundador.", safe_metadata={**safe, "founder_question_eligible": False})
                if qid in state.questions and state.questions[qid]["status"] not in ("resolved", "superseded"):
                    state.put("questions", {**state.questions[qid], "status": "superseded", "superseded_reason": "withheld_coverage_only", "coverage_source_id": source_id})
                return {"source_id": source_id, "state": "coverage_only", "reason": "withheld", "inspect_url": self.store.inspect_url("views/cobertura.md")}
            if source_id in state.sources:
                return {"source_id": source_id, "question_id": qid, "state": "pending_context", "inspect_url": self.store.inspect_url(f"views/questions/{qid}.md")}
            text = f"Ainda não foi possível conectar a atividade do aplicativo «{app}» ao trabalho da empresa. Nenhum título, texto de janela ou screenshot foi enviado."
            raw = self._source(state, actor, source_id, "coverage_gap", text, safe_metadata=safe)
            previous = state.questions.get(qid)
            sources = sorted(set((previous or {}).get("evidence_ids", []) + [raw["id"]]))
            summary = safe.get("coverage", (previous or {}).get("coverage"))
            old_summary = (previous or {}).get("coverage")
            if old_summary and safe.get("coverage"):
                if old_summary["period_start"] != summary["period_start"]:
                    raise BrainError("coverage_period_identity_changed")
                incoming_end = datetime.fromisoformat(summary["period_end"].replace("Z", "+00:00"))
                previous_end = datetime.fromisoformat(old_summary["period_end"].replace("Z", "+00:00"))
                if incoming_end < previous_end:
                    summary = old_summary
                elif incoming_end == previous_end:
                    summary = {**summary, **{key: max(summary[key], old_summary[key]) for key in ("observed_samples", "distinct_windows", "expired_unresolved")}}
            state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": state.company["id"],
                "target_kind": "context", "context_topic": "activities", "app": app,
                "base_version": state.company["context_version"], "task_version": state.company["context_version"],
                "missing_links": ["placement"], "missing_fields": ["placement"], "uncertainty_ids": [qid],
                "observation_ids": sources, "evidence_ids": sources, "context_key": "context:" + key,
                "coverage": summary, "actor_id": actor["id"], "device_id": device_id,
                "activity_summary": "Contexto de trabalho ainda desconhecido no aplicativo " + app,
                "prompt": f"O aplicativo «{app}» é usado no trabalho da empresa? Em que atividade e por quais nomes de documentos ou registros podemos reconhecer esse trabalho?",
                "status": previous["status"] if previous and "context_key" in safe and previous["status"] == "resolved" else "pending", "created_at": (previous or {}).get("created_at", now()), "revision": 1, "revisit_evidence_ids": []})
            return {"source_id": source_id, "question_id": qid, "state": "pending_context", "inspect_url": self.store.inspect_url(f"views/questions/{qid}.md")}
        return self._transaction("context_gap", identifier(actor_key(self.store), event_id + ":gap:" + device_id), safe, apply)

    record_coverage_gap = ingest_context_gap

    def record_answer(self, question_ids, text, event_id, base_versions=None, source=None, candidate_question_ids=None):
        _text(text)
        if not isinstance(question_ids, list):
            raise BrainError("question_id_list_required")
        source = source or {"kind": "founder"}
        candidates = candidate_question_ids or []
        if not isinstance(candidates, list) or len(candidates) > 50:
            raise BrainError("bounded_candidate_question_ids_required")
        payload = {"question_ids": question_ids, "text": text, "base_versions": base_versions, "source": source, "candidate_question_ids": candidates}
        def apply(state, actor):
            speaker = actor["id"]
            if source.get("kind") == "nora":
                if actor["id"] not in state.company["nora_actors"]:
                    raise AccessDenied("nora_attestor_required")
                route = state.nora.get("route") or {}
                if not route or not route.get("enabled") or source.get("route_id") not in (route.get("id"), route.get("route_id")) or any(source.get(key) != route.get(target) for key, target in (("account", "account"), ("conversation", "conversation"), ("sender", "founder_sender"))) or not source.get("message_id"):
                    raise AccessDenied("verified_founder_route_required")
                speaker = route.get("founder_actor_id")
                if speaker not in state.company["founders"]:
                    raise AccessDenied("route_founder_not_authorized")
            else:
                self._founder(state, actor)
            if any(qid not in state.questions for qid in question_ids + candidates):
                raise BrainError("unknown_question")
            if any(state.questions[qid].get("target_kind") == "answer_assignment" for qid in candidates):
                raise BrainError("candidate_cannot_be_disambiguation_question")
            task_ids = sorted({state.questions[qid]["task_id"] for qid in question_ids if state.questions[qid]["task_id"] in state.tasks})
            candidate_task_ids = {state.questions[qid]["task_id"] for qid in candidates if state.questions[qid]["task_id"] in state.tasks}
            context_questions = [qid for qid in question_ids if state.questions[qid].get("target_kind") in ("context", "context_conflict", "context_deferred")]
            # Freeze business-context bases at receipt, before asynchronous
            # interpretation or answer assignment can observe a newer decision.
            context_bases = {state.questions[qid]["context_topic"]: copy.deepcopy(state.context.get(state.questions[qid]["context_topic"]))
                             for qid in question_ids + candidates if state.questions[qid].get("target_kind") in ("context_deferred", "context_conflict")}
            context_question_bases = {topic: {qid: question["revision"] for qid, question in state.questions.items()
                if question.get("context_topic") == topic and question.get("target_kind") in ("context_deferred", "context_conflict")}
                for topic in context_bases}
            raw = self._source(state, actor, identifier(state.company["id"], "answer:" + event_id), "answer", text,
                               speaker=speaker, attested_by=actor["id"], channel=copy.deepcopy(source),
                               question_ids=question_ids, context_questions=context_questions, candidate_question_ids=candidates,
                               context_bases=context_bases, context_question_bases=context_question_bases,
                               base_versions=base_versions or {tid: state.tasks[tid]["version"] for tid in set(task_ids) | candidate_task_ids})
            job_id = self._queue(state, raw, "interview" if context_questions and not task_ids else "answer", task_ids)
            assignment_questions = [state.questions[qid] for qid in question_ids if state.questions[qid].get("target_kind") == "answer_assignment"]
            if not question_ids:
                qid = self._ask_answer_assignment(state, raw, candidates)
                state.jobs[job_id].update(kind="disambiguation", status="pending" if candidates else "awaiting_disambiguation",
                    unassigned_answer_id=raw["id"], candidate_question_ids=candidates, disambiguation_question_id=qid)
            elif len(question_ids) == 1 and len(assignment_questions) == 1:
                question = assignment_questions[0]
                original_id = question["unassigned_answer_id"]
                available = candidates or question["candidate_question_ids"] or [key for key, item in state.questions.items() if item.get("target_kind") != "answer_assignment"][-50:]
                state.jobs[job_id].update(kind="disambiguation", source_ids=[raw["id"], original_id],
                    unassigned_answer_id=original_id, candidate_question_ids=available, disambiguation_question_id=question["id"])
            return {"answer_id": raw["id"], "job_id": job_id, "state": state.jobs[job_id]["status"], "speaker": speaker, "attested_by": actor["id"],
                    "disambiguation_question_id": state.jobs[job_id].get("disambiguation_question_id")}
        return self._transaction("answer", event_id, payload, apply)

    def _apply_answer_assignments(self, state, actor, job, result, allowed):
        assignments = result.get("answer_assignments", [])
        if len(assignments) > 1:
            raise BrainError("one_answer_assignment_required")
        qid = job["disambiguation_question_id"]
        if not assignments:
            question = state.questions[qid]
            meaningful = [sid for sid in job["source_ids"] if sid != job["unassigned_answer_id"]]
            state.put("questions", {**question, "status": "pending",
                "evidence_ids": sorted(set(question["evidence_ids"] + meaningful)),
                "revisit_evidence_ids": sorted(set(question.get("revisit_evidence_ids", []) + meaningful))})
            return []
        item = assignments[0]
        original_id = job["unassigned_answer_id"]
        chosen = item.get("question_ids", [])
        if item.get("answer_id") != original_id or not chosen or not set(chosen) <= set(job["candidate_question_ids"]):
            raise BrainError("answer_assignment_outside_candidates")
        if len({state.questions[key]["task_id"] for key in chosen}) != 1:
            raise BrainError("ambiguous_multi_activity_assignment")
        self._claim(state, {"value": "Vínculo da resposta", "basis": "reported", "evidence": item["evidence"]}, allowed)
        original = state.sources[original_id]
        assignment_id = identifier(job["id"], "answer-assignment")
        assignment = self._source(state, actor, assignment_id, "answer_assignment", canonical(item),
            speaker=original["speaker"], original_answer_id=original_id, question_ids=chosen,
            clarification_source_ids=job["source_ids"])
        tasks = sorted({state.questions[key]["task_id"] for key in chosen if state.questions[key]["task_id"] in state.tasks})
        context_questions = [key for key in chosen if state.questions[key].get("target_kind") in ("context", "context_conflict", "context_deferred")]
        next_job_id = identifier(assignment_id, "interpret")
        state.jobs[next_job_id] = {"id": next_job_id, "kind": "interview" if context_questions and not tasks else "answer",
            "source_ids": list(dict.fromkeys([original_id] + job["source_ids"] + [assignment_id])),
            "task_ids": tasks, "question_ids": chosen, "context_questions": context_questions,
            "status": "pending", "created_at": state.jobs[identifier(original_id, "interpret")]["created_at"],
            "attempts": 0, "error_code": None}
        original_job = state.jobs[identifier(original_id, "interpret")]
        if original_job["id"] != job["id"]:
            original_job.update(status="applied", receipt={"state": "assigned", "next_job_id": next_job_id})
        state.put("questions", {**state.questions[qid], "status": "resolved", "assignment_source_id": assignment_id})
        return [{"answer_id": original_id, "question_ids": chosen, "next_job_id": next_job_id}]

    def interview_next(self):
        current, actor = self._read_with_actor()
        snapshot = self._snapshot(current, actor)
        self._founder(_State(current), actor)
        deferred = [topic for topic, value in snapshot["interview"].get("deferred_topics", {}).items() if value["status"] in ("pending", "awaiting_person")]
        context_questions = [question["id"] for question in snapshot["questions"] if question.get("target_kind", "").startswith("context") and question["status"] not in ("resolved", "superseded", "awaiting_person")]
        awaiting_questions = [question["id"] for question in snapshot["questions"] if question.get("target_kind", "").startswith("context") and question["status"] == "awaiting_person"]
        missing = [topic for topic in TOPICS if not snapshot["context"].get(topic) and topic not in deferred]
        progress = {"deferred_topics": sorted(deferred), "pending_context_question_ids": context_questions,
                    "awaiting_person_question_ids": awaiting_questions,
                    "business_context_status": "pending_clarification" if context_questions else "awaiting_person" if awaiting_questions else "collecting" if missing else "known_topics_recorded"}
        gap = recognition_gap(snapshot)
        if gap:
            progress["recognition"] = gap
        sources = {source["id"]: source for source in snapshot["sources"]}
        jobs = [job for job in snapshot["pending_jobs"] if job["kind"] == "interview" and job["status"] == "pending"
                and sources[job["source_ids"][0]].get("question", {}).get("topic") not in job.get("excluded_context_topics", [])]
        if jobs:
            route = manual_interview_route(jobs[:1])
            message = route["message"] if route else (
                "Sua resposta já está guardada. O operador externo lê o trabalho e registra o entendimento. Este computador não chama o modelo.")
            pending = {**progress, "business_context_status": "pending_analysis", "state": "pending_analysis", "job_id": jobs[0]["id"], "question": None,
                       "founder_message": message}
            if route:
                pending.update(recommended_route="manual", manual_commands=route["commands"], rejection_reason=route.get("rejection_reason"))
            return pending
        if not missing:
            return {**progress, "state": "complete", "question": None}
        followup = snapshot["interview"].get("next_question")
        if followup and followup["topic"] in missing:
            return {**progress, "state": "question", **followup, "question": followup["text"]}
        prompts = {"activities": "O que a empresa faz e qual trabalho precisa acontecer para entregar ao cliente?", "people": "Quem participa desse trabalho e quais responsabilidades cada pessoa assume?", "stages": "Como esse trabalho avança e como vocês sabem que cada etapa terminou?", "tracking": "Onde vocês acompanham andamento e, quando houver, pagamentos e saldos?", "documents": "Quais documentos ou registros existentes ajudam a entender esse trabalho?"}
        return {**progress, "state": "question", "topic": missing[0], "question": prompts[missing[0]], "text": prompts[missing[0]]}

    def repair_context_conflicts(self, event_id=None, *, dry_run=False):
        """Turn false context conflicts into active claims. Explicit conflicts stay pending.

        Dry-run only reads. An apply appends a ledger event and does not rewrite older ones.
        """
        if dry_run:
            current, actor = self._read_with_actor()
            state = _State(current)
            self._founder(state, actor)
            plan = plan_context_conflict_repairs(state)
            return {"state": "dry_run", "dry_run": True, "repairable": plan["repairable"], "skipped": plan["skipped"]}
        if not event_id:
            raise BrainError("uuid_required")

        def apply(state, actor):
            self._founder(state, actor)
            plan = plan_context_conflict_repairs(state)
            if not plan["repairable"]:
                return {"state": "nothing_to_repair", "dry_run": False, "repaired": [], "skipped": plan["skipped"]}
            topics = ", ".join(sorted({item["topic"] for item in plan["repairable"]}))
            repair = self._source(state, actor, identifier(event_id, "context-repair"), "context_repair",
                "Reparo 0.1.2: conflitos de contexto sem mode conflict, ou correções enfileiradas com o assunto ainda vazio, passam a valer os dois lados com as fontes originais. Assuntos: " + topics + ".",
                speaker=actor["id"])
            wanted = {item["key"]: item for item in plan["repairable"]}
            repaired = []
            questions = set()
            for conflict in state.interview.get("context_conflicts", []):
                item = wanted.get(conflict_key(conflict))
                if item is None or conflict.get("status") != "pending":
                    continue
                added = 0
                for claim in (conflict["previous"], conflict["reported_candidate"]):
                    if self._ensure_context_claim(state, conflict["topic"], claim):
                        added += 1
                conflict.update(status="resolved", resolved_by=repair["id"], resolution="repair_" + item["reason"],
                                selected=copy.deepcopy(conflict["reported_candidate"]))
                questions.add(conflict["question_id"])
                repaired.append({**item, "claims_added": added})
            for qid in questions:
                if any(item.get("question_id") == qid and item.get("status") == "pending" for item in state.interview.get("context_conflicts", [])):
                    continue
                question = state.questions.get(qid)
                if question and question.get("status") != "resolved":
                    state.put("questions", {**question, "status": "resolved", "resolved_by": repair["id"]})
            state.company["context_version"] += 1
            return {"state": "repaired", "dry_run": False, "source_id": repair["id"], "repaired": repaired, "skipped": plan["skipped"]}

        return self._transaction("repair_context_conflicts", event_id, {"dry_run": False}, apply)

    def _ensure_context_claim(self, state, topic, claim):
        active = [record for record in state.context_claims if record["topic"] == topic and record["status"] == "active"]
        if any(record["claim"].get("value") == claim["value"] for record in active):
            return False
        projected = state.context.get(topic)
        if projected and projected.get("value") == claim["value"]:
            return False
        evidence_source = None
        for ref in claim.get("evidence") or []:
            evidence_source = state.sources.get(ref.get("source_id"))
            if evidence_source:
                break
        if evidence_source is None:
            return False
        apply_context(self, state, topic, copy.deepcopy(claim), evidence_source, "add")
        return True

    def confirm_recognition_names(self, items, event_id):
        """Confirm literal names the founder chose. Does not scan or confirm on its own."""
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise BrainError("bounded_recognition_markers_required")

        def apply(state, actor):
            self._founder(state, actor)
            prepared = []
            allowed = set()
            sources = list(state.sources.values())
            for item in items:
                if not isinstance(item, dict) or set(item) != {"value", "kind"} or item["kind"] not in MARKER_KINDS or not isinstance(item["value"], str):
                    raise BrainError("invalid_grounded_marker")
                name = item["value"].strip()
                generic = _marker_generic_key(name)
                if not generic or not 3 <= len(name) <= 200:
                    raise BrainError("invalid_grounded_marker")
                if generic in {_marker_generic_key(marker) for marker in GENERIC_MARKERS}:
                    raise BrainError("app_only_or_generic_marker_not_authorized")
                found = grounded_name(sources, name)
                if found is None:
                    raise BrainError("marker_name_must_be_literal_in_source")
                source, quote = found
                if any(marker["status"] == "active" and marker["kind"] == item["kind"] and _marker_text(marker["value"]) == _marker_text(name) for marker in state.recognition_markers):
                    continue
                allowed.add(source["id"])
                prepared.append({"value": name, "kind": item["kind"], "evidence": [{"source_id": source["id"], "quote": quote}], "operation": "add"})
            if not prepared:
                return {"state": "already_confirmed", "names": [item["value"].strip() for item in items]}
            confirmation = self._source(state, actor, identifier(event_id, "confirm-names"), "recognition_confirmation",
                "O fundador confirmou nomes para reconhecer janelas de trabalho.", speaker=actor["id"], names=[item["value"] for item in prepared])
            apply_markers(self, state, prepared, confirmation, allowed)
            return {"state": "confirmed", "names": [item["value"] for item in prepared], "source_id": confirmation["id"]}

        return self._transaction("confirm_recognition_names", event_id, {"items": items}, apply)

    def interview_answer(self, text, event_id):
        state = _State(self.store.read())
        previous = next((event for event in state.events if event["id"] == event_id), None)
        if previous:
            raw = state.sources.get(identifier(state.company["id"], "interview:" + event_id), {})
            if previous["actor"] != actor_key(self.store) or raw.get("text") != text:
                raise BrainError("idempotency_payload_conflict")
            return previous["result"]
        question = self.interview_next()
        if question["state"] != "question":
            raise BrainError("interview_wait_for_question")
        def apply(state, actor):
            self._founder(state, actor)
            raw = self._source(state, actor, identifier(state.company["id"], "interview:" + event_id), "interview", _text(text), question=question, speaker=actor["id"])
            job_id = self._queue(state, raw, "interview")
            return {"source_id": raw["id"], "job_id": job_id, "state": "pending_analysis"}
        return self._transaction("interview", event_id, {"text": text, "question": question}, apply)

    def _claim(self, state, claim, allowed_sources):
        if not isinstance(claim, dict) or set(claim) != {"value", "basis", "evidence"} or claim["basis"] not in ("observed", "reported", "inferred") or (claim["value"] is not None and not isinstance(claim["value"], str)):
            raise BrainError("invalid_field_claim")
        if not isinstance(claim["evidence"], list):
            raise BrainError("invalid_claim_evidence")
        spans = []
        failures = []
        for ref in claim["evidence"]:
            if not isinstance(ref, dict) or set(ref) != {"source_id", "quote"} or ref["source_id"] not in allowed_sources or ref["source_id"] not in state.sources:
                raise BrainError("unavailable_or_out_of_scope_source")
            evidence = state.sources[ref["source_id"]]
            quote = ref.get("quote")
            if evidence["kind"] in ("analysis", "answer_assignment") or not isinstance(quote, str):
                failures.append({"source_id": ref["source_id"], "quote": quote if isinstance(quote, str) else "", "reason": "source_quote_not_found"})
                spans.append(None)
                continue
            span = locate_source_span(evidence["text"], quote)
            if not span:
                failures.append({"source_id": ref["source_id"], "quote": quote, "reason": "source_quote_not_found"})
            spans.append(span)
            if span and not failures and claim["basis"] == "observed" and evidence["kind"] not in ("observation", "company_record"):
                raise BrainError("observed_claim_requires_observation")
        if failures:
            raise quote_not_found(failures)
        for ref, span in zip(claim["evidence"], spans):
            ref["quote"] = span
        if claim["value"] and claim["basis"] != "inferred" and not claim["evidence"]:
            raise BrainError("supported_claim_requires_evidence")
        return copy.deepcopy(claim)

    def _field_claim(self, state, claim, allowed_sources, field=None):
        value = self._claim(state, claim, allowed_sources)
        if field == "performer" and value["value"] and value["basis"] == "observed" and all(state.sources[ref["source_id"]]["kind"] == "company_record" for ref in value["evidence"]):
            raise BrainError("record_cannot_witness_person_execution")
        return value

    def _refresh_task(self, state, task):
        if "performer" in task["fields"]:
            self._field_claim(state, task["fields"]["performer"], set(state.sources), "performer")
        from .evolution import normalize_issues
        normalize_issues(task)
        from .people import _ROLES, _key, person_identity
        performer = task["fields"].get("performer", {})
        raw_name = performer.get("value") if isinstance(performer, dict) else ""
        if isinstance(raw_name, str) and _key(raw_name) in _ROLES:
            task["fields"]["performer"] = {"value": None, "basis": "inferred", "evidence": []}
            performer = task["fields"]["performer"]
        task["performer_identity"] = person_identity(performer.get("value"), company_id=state.company["id"], nodes=state.groups["nodes"].values(), context_claims=state.context_claims)
        previous = state.tasks.get(task["id"])
        from .semantics import field_context
        field_context(state, task, previous)
        task["missing_fields"] = [field for field in FIELDS if not task["fields"].get(field, {}).get("value") or task["fields"][field]["basis"] == "inferred" or not task["fields"][field]["evidence"] or field in task.get("conflicts", {})]
        if task["performer_identity"]["status"] in ("ambiguous", "compound") and "performer" not in task["missing_fields"]:
            task["missing_fields"].append("performer")
        task["payment_missing"] = payment_gaps(state, task)
        task["status"] = "provisional" if task["missing_fields"] or task["payment_missing"] or task.get("consistency_issues") or task.get("unconfirmed_links") or any(item.get("status") == "awaiting_clarification" for item in task.get("issue_replays", [])) else "established"
        task = state.put("tasks", task)
        refresh_payment(state, copy.deepcopy(task))
        # Learned, supported fields create explicit connections without departments.
        for field, node_kind, edge_kind, reverse in DERIVED_LINKS:
            claim = task["fields"].get(field, {})
            if previous and previous["fields"].get(field) == claim and (field in previous.get("missing_fields", [])) == (field in task["missing_fields"]):
                # Unrelated answers/names are not new relation evidence. Do not
                # rewrite shared nodes or revive an explicitly removed edge.
                continue
            value = claim.get("value")
            if not value or field in task["missing_fields"] or value.casefold().rstrip(".") in ("não se aplica", "nenhum", "nenhuma", "não há", "sem repasse", "sem registro"):
                for edge in list(state.groups["edges"].values()):
                    if edge.get("derived_field") == field and edge.get("task_id") == task["id"] and edge.get("active"):
                        state.put("edges", {**edge, "active": False})
                continue
            identity = task.get("performer_identity") if field == "performer" else None
            if identity and not identity["node_id"]:
                continue
            node_id = identity["node_id"] if identity else identifier(state.company["id"], node_kind + ":" + value.casefold())
            value = identity["name"] if identity else value
            first, second = (node_id, task["id"]) if reverse else (task["id"], node_id)
            edge_id = identifier(first, edge_kind + ":" + second)
            edge = state.groups["edges"].get(edge_id)
            if edge and edge.get("explicit_disconnect") and not edge["active"]:
                old_refs = {canonical(ref) for ref in edge.get("disconnected_evidence", [])}
                cutoff = state.sources[edge["change_source"]]["received_at"]
                newer = [ref for ref in claim["evidence"] if canonical(ref) not in old_refs
                         and state.sources[ref["source_id"]]["received_at"] > cutoff]
                if not newer:
                    continue
            # A node's identity survives an explicit name correction. Relation
            # evidence lives on the task/edge; deriving it cannot rename a node.
            if node_id not in state.groups["nodes"]:
                support = claim["evidence"] + (identity.get("evidence", []) if identity else [])
                state.put("nodes", {"id": node_id, "kind": node_kind, "name": value, "evidence": list({canonical(ref): ref for ref in support}.values()), "superseded_by": []})
            if identity:
                for alias_id in identity["alias_node_ids"]:
                    alias = state.groups["nodes"].get(alias_id)
                    if alias and alias_id != node_id and not alias.get("superseded_by"):
                        state.put("nodes", {**alias, "superseded_by": [node_id], "identity_evidence": identity["evidence"]})
            for edge in list(state.groups["edges"].values()):
                if edge.get("derived_field") == field and edge.get("task_id") == task["id"] and edge["id"] != edge_id and edge.get("active"):
                    state.put("edges", {**edge, "active": False})
            state.put("edges", {"id": edge_id, "from": first, "to": second, "kind": edge_kind,
                                "derived_field": field, "task_id": task["id"], "evidence": claim["evidence"], "active": True})
        for field in FIELDS:
            qid = identifier(task["id"], "gap:" + field)
            previous = state.questions.get(qid)
            if field not in task["missing_fields"]:
                if previous and previous["status"] != "resolved":
                    state.put("questions", {**previous, "status": "resolved", "base_version": task["version"]})
                continue
            observations = [sid for sid in task["evidence_ids"] if state.sources[sid]["kind"] in ("observation", "company_record")]
            relevant = {ref["source_id"] for ref in task["fields"].get(field, {}).get("evidence", [])}
            relevant.update(task.get("conflicts", {}).get(field, {}).get("source_ids", []))
            state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": task["id"], "base_version": task["version"],
                                     "task_version": task["version"], "missing_links": [field], "missing_fields": [field],
                                     "uncertainty_ids": [qid], "observation_ids": observations, "context_key": task["id"],
                                     "activity_summary": task["name"], "prompt": f"Na atividade «{task['name']}», falta esclarecer: {LABELS[field].lower()}.",
                                     "status": "pending", "created_at": (previous or {}).get("created_at", now()),
                                     "evidence_ids": task["evidence_ids"], "conflict": task.get("conflicts", {}).get(field),
                                     "relevant_answer_ids": sorted(sid for sid in relevant if state.sources[sid]["kind"] == "answer"),
                                     **({"response_disposition": previous["response_disposition"]} if previous and previous.get("response_disposition") else {}),
                                     "revisit_evidence_ids": task.get("conflicts", {}).get(field, {}).get("source_ids", []), "revision": (previous or {}).get("revision", 1)})
        return task

    def apply_analysis(self, job_id, result, metadata=None, map_scope=None):
        initial = _State(self.store.read())
        job = initial.jobs.get(job_id)
        if not job:
            raise BrainError("unknown_analysis_job")
        if job["status"] == "applied":
            return job["receipt"]
        metadata = {key: value for key, value in (metadata or {}).items() if key in ("model", "prompt_version", "schema_version", "input_sha256", "execution_provider", "execution_model", "execution_origin", "execution_identity_basis", "reported_provider", "reported_model")}
        if any(value is not None and key != "schema_version" and (not isinstance(value, str) or len(value) > 256) for key, value in metadata.items()):
            raise BrainError("invalid_execution_identity")
        payload = {"job_id": job_id, "result": result, "metadata": metadata, "map_scope": map_scope}
        def apply(state, actor):
            self._worker(state, actor)
            job = state.jobs[job_id]
            if job["status"] == "applied":
                return job["receipt"]
            if job["status"] == "unassigned":
                raise BrainError("ambiguous_answer_requires_assignment")
            if not isinstance(result, dict) or set(result) - {"tasks", "nodes", "context_updates", "next_question", "answer_assignments", "map_changes", "message_intent", "recognition_markers", "record_extraction", "question_answers", "attention_requests", "outcome"}:
                raise BrainError("invalid_analysis_result")
            if result.get("outcome") not in (None, "no_work") or (result.get("outcome") and job["kind"] != "observation"):
                raise BrainError("invalid_observation_outcome")
            allowed = set(job["source_ids"])
            for value in state.context.values():
                allowed.update(ref["source_id"] for ref in value.get("evidence", []))
            source = state.sources[job["source_ids"][0]]
            if job["kind"] == "observation":
                from .evolution import observation_scope
                observation_scope(state, job, map_scope, result)
            if job["kind"] == "company_record":
                catalog = state.company_records.get(source["logical_record_id"])
                if not catalog:
                    raise BrainError("company_record_catalog_scope_required")
                if catalog["current_source_id"] != source["id"]:
                    receipt = {"job_id": job_id, "state": "superseded_source_revision", "source_scope": "historic", "tasks": [],
                        "historic_source_id": source["id"], "current_source_id": catalog["current_source_id"], "source_ids": job["source_ids"]}
                    job.update(status="applied", receipt=receipt, error_code=None)
                    self._source(state, actor, identifier(job_id, "model-result"), "analysis", canonical(result), input_source_ids=job["source_ids"], interpretation=metadata, source_scope="historic")
                    return receipt
                extraction = result.get("record_extraction")
                if not isinstance(extraction, dict) or set(extraction) != {"complete", "limitations"} or type(extraction["complete"]) is not bool or not isinstance(extraction["limitations"], list) or any(not isinstance(item, str) for item in extraction["limitations"]) or (not source["extract_complete"] and (extraction["complete"] or not extraction["limitations"])) or len(result.get("tasks", [])) > 50:
                    raise BrainError("record_extract_limits_must_remain_explicit")
                from .evolution import candidate_scope
                candidate_scope(state, job, map_scope, result)
            message_receipt = self._apply_message_intent(state, actor, job, result, source, map_scope) if job["kind"] == "message" else None
            if job["kind"] != "message" and result.get("message_intent"):
                raise BrainError("message_intent_requires_message_job")
            for task_id in job["task_ids"]:
                allowed.update(state.tasks[task_id]["evidence_ids"])
            if map_scope:
                allowed.update(sid for sid in map_scope.get("source_ids", []) if sid in state.sources)
            if source["kind"] == "interview_correction" and (result.get("tasks") or result.get("nodes")):
                raise BrainError("context_correction_cannot_rewrite_work_graph")
            for qid in job.get("question_ids", source.get("question_ids", [])):
                allowed.update(state.questions[qid]["evidence_ids"])
            for qid in job.get("candidate_question_ids", []):
                allowed.update(state.questions[qid]["evidence_ids"])
            assigned = []
            if job["kind"] == "disambiguation":
                if result.get("tasks") or result.get("nodes") or result.get("context_updates") or result.get("next_question") or result.get("attention_requests"):
                    raise BrainError("disambiguation_cannot_rewrite_map")
                assigned = self._apply_answer_assignments(state, actor, job, result, allowed)
            elif result.get("answer_assignments"):
                raise BrainError("answer_assignment_requires_dedicated_job")
            map_receipt = None
            if result.get("map_changes"):
                map_receipt = self._apply_answer_map_changes(state, job, result, source, map_scope)
            changed = []
            source_task_versions = {tid: task["version"] for tid, task in state.tasks.items()}
            from .evolution import progress_decision, question_answers, resolve_issues, scoped_field_answer, answer_outcomes
            for candidate_index, candidate in enumerate(result.get("tasks", [])):
                if set(candidate["fields"]) != set(FIELDS):
                    raise BrainError("canonical_task_fields_required")
                task_id = valid_uuid(candidate["task_id"]) if candidate.get("task_id") else identifier(source["id"], "task:" + str(candidate_index) if job["kind"] == "company_record" or candidate_index else "task")
                if job["kind"] in ("answer", "message") and task_id not in job["task_ids"]:
                    raise BrainError("answer_task_out_of_scope")
                previous = state.tasks.get(task_id)
                if candidate.get("task_id") and previous is None:
                    raise BrainError("unknown_task_refinement")
                if previous:
                    allowed.update(previous["evidence_ids"])
                if previous and previous.get("superseded_by"):
                    raise BrainError("task_superseded_reconciliation_required")
                task = copy.deepcopy(previous or {"id": task_id, "kind": "task", "name": _text(candidate["name"]), "fields": {}, "conflicts": {}, "evidence_ids": [], "superseded_by": []})
                issue_resolution = resolve_issues(self, state, task, candidate.get("issue_resolutions", []), job, source, allowed, message_receipt)
                for field in FIELDS:
                    claim = self._field_claim(state, candidate["fields"][field], allowed, field)
                    old = task["fields"].get(field)
                    if claim["value"] is None and old:
                        continue
                    primary_support = any(ref["source_id"] == source["id"] for ref in claim["evidence"])
                    explicit_field_correction = primary_support and message_receipt and (message_receipt["kind"] == "correction" or issue_resolution or scoped_field_answer(state, job, source, task_id, field))
                    progress = progress_decision(self, state, task, field, old, claim, candidate["field_updates"][field], source, allowed) if field in candidate.get("field_updates", {}) else None
                    if progress in ("historic", "same"):
                        continue
                    if progress == "equivalent":
                        refs = {canonical(ref): ref for ref in old["evidence"] + candidate["field_updates"][field]["prior_evidence"] + claim["evidence"]}
                        task["fields"][field] = {**old, "evidence": list(refs.values())}
                        task["conflicts"].pop(field, None)
                        continue
                    if explicit_field_correction or progress == "replace":
                        task["fields"][field] = claim
                        task["conflicts"].pop(field, None)
                    elif old and old.get("value") and claim["value"] and old["value"] != claim["value"] and old["basis"] != "inferred" and claim["basis"] != "inferred":
                        conflict = task["conflicts"].get(field, {})
                        alternatives = conflict.get("alternatives", []) + ([conflict["reported_candidate"]] if conflict.get("reported_candidate") else [])
                        task["conflicts"][field] = {"previous": old, "reported_candidate": claim, "source_ids": sorted(set(conflict.get("source_ids", []) + job["source_ids"])),
                            "alternatives": list({canonical(value): value for value in alternatives if value != claim and value != old}.values())}
                    else:
                        task["fields"][field] = claim
                task["evidence_ids"] = sorted(set(task["evidence_ids"] + job["source_ids"]) | {ref["source_id"] for claim in task["fields"].values() for ref in claim["evidence"]})
                from .evolution import carry_prior_statements
                carry_prior_statements(state, task)
                payment_claims(self, state, task, candidate.get("payment"), candidate.get("consistency_issues"), allowed,
                    replace=bool(message_receipt and (message_receipt["kind"] == "correction" or issue_resolution)), source_id=source["id"])
                updated = self._refresh_task(state, task)
                changed.append({"task_id": task_id, "version": updated["version"], "status": updated["status"]})
                for link in candidate.get("links", []):
                    if link.get("kind") not in EDGE_KINDS or link.get("target_id") not in {**state.tasks, **state.groups["nodes"]}:
                        raise BrainError("invalid_typed_connection")
                    self._claim(state, {"value": link["kind"], "basis": "reported", "evidence": link.get("evidence", [])}, allowed)
                    if link["target_id"] in state.tasks:
                        if not map_scope or link["target_id"] not in {ref["id"] for ref in map_scope.get("tasks", [])}:
                            raise BrainError("task_link_outside_supplied_scope")
                        version = next(ref["version"] for ref in map_scope["tasks"] if ref["id"] == link["target_id"])
                        if version != source_task_versions.get(link["target_id"]):
                            raise BrainError("stale_task_relation_scope")
                        if not any(ref["source_id"] == source["id"] for ref in link["evidence"]):
                            raise BrainError("current_task_relation_evidence_required")
                        support_missing = False
                        for claim in (task["fields"]["result"], state.tasks[link["target_id"]]["fields"]["input"]):
                            if not claim.get("value") or claim["basis"] == "inferred" or not claim["evidence"]:
                                support_missing = True
                        if support_missing and link["kind"] == "handoff":
                            note = {"target_id": link["target_id"], "reason": "a fonte ainda não mostra o resultado e a entrada deste repasse"}
                            omitted = task.setdefault("omitted_relations", [])
                            if note not in omitted:
                                omitted.append(note)
                            continue
                        if support_missing:
                            raise BrainError("task_output_input_support_required")
                    relation_review = {}
                    if link["target_id"] in state.tasks and link["kind"] == "handoff":
                        from .relations import validate_task_relation
                        basis = validate_task_relation(task, state.tasks[link["target_id"]], link, source)
                        if basis == "omit":
                            # The capture stays. Only this unsupported link is left off the map.
                            note = {"target_id": link["target_id"], "reason": "a fonte não mostra o mesmo objeto passando de uma atividade para a outra"}
                            omitted = task.setdefault("omitted_relations", [])
                            if note not in omitted:
                                omitted.append(note)
                            continue
                        if basis == "inferred" and link.get("basis") != "inferred":
                            relation_review = {"proposed_basis": link.get("basis", "reported"), "validation_reason": "handoff_object_or_direction_requires_confirmation"}
                        link = {**link, "basis": basis}
                    edge_id = identifier(task_id, link["kind"] + ":" + link["target_id"])
                    state.put("edges", {"id": edge_id, "from": task_id, "to": link["target_id"], "kind": link["kind"], "evidence": link["evidence"], "basis": link.get("basis", "reported"), "active": True, **relation_review})
                    if link["target_id"] in state.tasks:
                        from .evolution import track_task_link
                        track_task_link(self, state, task_id, edge_id, link, source)
                        changed[-1].update(version=state.tasks[task_id]["version"], status=state.tasks[task_id]["status"])
                if task.get("omitted_relations"):
                    current = copy.deepcopy(state.tasks[task_id])
                    current["omitted_relations"] = task["omitted_relations"]
                    refreshed = self._refresh_task(state, current)
                    changed[-1].update(version=refreshed["version"], status=refreshed["status"])
            correction = source["kind"] == "interview_correction"
            flexible = job["kind"] == "interview" and not correction
            omitted_quotes = []
            grounded_claims = []

            def claim_for(payload):
                if not flexible:
                    return self._claim(state, payload, allowed)
                try:
                    value = self._claim(state, payload, allowed)
                except BrainError as error:
                    if str(error) != "source_quote_not_found":
                        raise
                    omitted_quotes.extend(getattr(error, "quote_failures", None) or [])
                    return None
                if value.get("evidence"):
                    grounded_claims.append(value)
                return value

            correction_applied = False
            if correction:
                topic = source["context_topic"]
                if any(update.get("topic") != topic for update in result.get("context_updates", [])):
                    raise BrainError("correction_topic_mismatch")
                claims = [self._claim(state, {key: update[key] for key in ("value", "basis", "evidence")}, allowed) for update in result.get("context_updates", [])]
                supported = [claim for claim in claims if claim["value"] and claim["basis"] != "inferred"]
                if len({claim["value"] for claim in supported}) == 1:
                    selected = supported[0]
                    previous = state.context.get(topic)
                    queued = source.get("previous_context")
                    # A correction captured while the topic was empty is another fact.
                    # One captured against a summary that later changed stays a conflict.
                    queued_empty = not isinstance(queued, dict) or not queued.get("value")
                    if not queued_empty and previous != queued and previous and previous["value"] != selected["value"]:
                        self._context_conflict(state, topic, previous, selected, source["id"], origin="correction_changed")
                    else:
                        siblings = [record for record in state.context_claims if record["topic"] == topic and record["status"] == "active"]
                        alongside = queued_empty and bool(siblings)
                        self._set_interview_context(state, topic, selected, source,
                            keep_siblings=len(siblings) > 1 or alongside, resolve_conflicts=not alongside)
                        correction_applied = True
                else:
                    state.company["context_version"] += 1
                    self._interview_gap(state, topic, source, "correction_unresolved", candidate_claims=claims)
            for update in (() if correction else result.get("context_updates", [])):
                topic = update.get("topic")
                if topic not in TOPICS:
                    raise BrainError("unknown_context_topic")
                if topic in job.get("excluded_context_topics", []):
                    continue
                claim = claim_for({key: update[key] for key in ("value", "basis", "evidence")})
                if claim is None:
                    continue
                if claim["value"] and claim["basis"] != "inferred":
                    context_questions = job.get("context_questions", source.get("context_questions", []))
                    if context_questions:
                        targeted = [state.questions[qid] for qid in context_questions if state.questions[qid].get("target_kind") in ("context_deferred", "context_conflict") and state.questions[qid]["context_topic"] == topic]
                        previous = state.context.get(topic)
                        bases = source.get("context_bases", {})
                        # A reply is not authority to silently replace a later
                        # correction. Legacy answers lack bases: resolved targets
                        # are conservatively treated as stale in that case.
                        question_bases = source.get("context_question_bases", {})
                        current_questions = {qid: question["revision"] for qid, question in state.questions.items()
                            if question.get("context_topic") == topic and question.get("target_kind") in ("context_deferred", "context_conflict")}
                        stale = (topic in bases and previous != bases[topic]) or (topic not in bases and any(q["status"] == "resolved" for q in targeted))
                        stale = stale or (topic in question_bases and current_questions != question_bases[topic])
                        if targeted and stale:
                            if previous and previous["value"] != claim["value"]:
                                self._context_conflict(state, topic, previous, claim, source["id"], origin="stale_answer")
                                state.company["context_version"] += 1
                            # Matching an old value does not resolve a newer doubt.
                            # Attribution of the old source is not fresh evidence.
                            continue
                        deferred = [qid for qid in context_questions if state.questions[qid].get("target_kind") == "context_deferred" and state.questions[qid]["context_topic"] == topic]
                        if deferred:
                            unresolved_correction = state.interview.get("deferred_topics", {}).get(topic, {}).get("mode") == "correction_unresolved" and state.interview["deferred_topics"][topic]["status"] == "pending"
                            mode = update.get("mode", "replace" if unresolved_correction else "add")
                            replacing = update.get("replaces", [record["id"] for record in state.context_claims if record["topic"] == topic and record["status"] == "active"] if unresolved_correction else [])
                            self._set_interview_context(state, topic, claim, source, mode=mode, replaces=replacing, resolve_conflicts=False)
                            continue
                        conflicts = [qid for qid in context_questions if state.questions[qid].get("target_kind") == "context_conflict" and state.questions[qid]["context_topic"] == topic]
                        if conflicts:
                            apply_context(self, state, topic, claim, source, "replace", explicit_replace=True)
                            for qid in conflicts:
                                state.put("questions", {**state.questions[qid], "status": "resolved"})
                                for conflict in state.interview.get("context_conflicts", []):
                                    if conflict["question_id"] == qid and conflict["status"] == "pending":
                                        conflict.update(status="resolved", resolved_by=source["id"], selected=claim)
                            state.company["context_version"] += 1
                            continue
                        ordinary = [qid for qid in context_questions if state.questions[qid].get("target_kind") == "context"]
                        if not ordinary:
                            continue
                        # Preserve an explicit founder explanation as a learned signal;
                        # it is not an app-only allowlist or an invented company task.
                        signal_key = "work-signal:" + source["id"] + ":" + topic + ":" + digest(canonical(claim))[:12]
                        state.context[signal_key] = claim
                        for qid in ordinary:
                            state.put("questions", {**state.questions[qid], "status": "resolved"})
                        state.company["context_version"] += 1
                        continue
                    if update.get("mode"):
                        apply_context(self, state, topic, claim, source, update["mode"], update.get("replaces", []))
                        continue
                    # A missing mode is add: complementary facts keep their sources.
                    previous = state.context.get(topic)
                    deferred = state.interview.get("deferred_topics", {}).get(topic, {})
                    if deferred.get("status") == "pending" and (not previous or previous.get("value") == claim["value"]):
                        self._set_interview_context(state, topic, claim, source)
                    else:
                        apply_context(self, state, topic, claim, source, "add")
            question_answers(self, state, result.get("question_answers", []), job, source, allowed, map_scope)
            from .semantics import apply_attention
            apply_attention(self, state, result.get("attention_requests", []), job, source, allowed)
            markers = result.get("recognition_markers", [])
            if flexible and isinstance(markers, list):
                kept = []
                for marker in markers:
                    if not isinstance(marker, dict):
                        kept.append(marker)
                        continue
                    probed = claim_for({"value": marker.get("value") if isinstance(marker.get("value"), str) else None,
                                        "basis": "reported", "evidence": marker.get("evidence")})
                    if probed is not None:
                        kept.append(marker)
                markers = kept
            apply_markers(self, state, markers, source, allowed)
            for node in (() if correction and not correction_applied else result.get("nodes", [])):
                if node["kind"] not in KINDS or node["kind"] == "task":
                    raise BrainError("invalid_graph_node_kind")
                if claim_for({"value": node["name"], "basis": "reported", "evidence": node["evidence"]}) is None:
                    continue
                nid = identifier(state.company["id"], node["kind"] + ":" + node["name"].casefold())
                name, refs = node["name"], node["evidence"]
                if node["kind"] == "person":
                    from .people import person_identity
                    identity = person_identity(name, company_id=state.company["id"], nodes=state.groups["nodes"].values(), context_claims=state.context_claims)
                    if not identity["node_id"]:
                        raise BrainError("individual_person_identity_required")
                    nid, name = identity["node_id"], identity["name"]
                    refs = list({canonical(ref): ref for ref in refs + identity["evidence"]}.values())
                previous_node = state.groups["nodes"].get(nid)
                state.put("nodes", {"id": nid, "kind": node["kind"], "name": previous_node["name"] if previous_node else name, "evidence": refs, "superseded_by": []})
            if flexible and omitted_quotes and not grounded_claims:
                raise quote_not_found(omitted_quotes)
            question = result.get("next_question")
            if question is not None:
                if question.get("topic") not in TOPICS:
                    raise BrainError("invalid_interview_followup_topic")
                _text(question.get("text"))
            if not correction:
                state.interview["next_question"] = question
            waiting = job["kind"] == "disambiguation" and not assigned
            waiting = waiting or (message_receipt and message_receipt["kind"] == "unclear")
            receipt = {"job_id": job_id, "state": "awaiting_clarification" if waiting or (correction and not correction_applied) else "applied", "tasks": changed, "source_ids": job["source_ids"], "answer_assignments": assigned}
            outcomes = answer_outcomes(state, job, source, message_receipt)
            if outcomes:
                receipt["question_outcomes"] = outcomes
                if any(item["status"] == "unresolved" for item in outcomes):
                    receipt["state"] = "partially_applied" if any(item["status"] == "resolved" for item in outcomes) else "awaiting_clarification"
            if map_receipt is not None:
                receipt["map_changes"] = map_receipt
                receipt["founder_speaker"] = source["speaker"]
                receipt["attested_by"] = actor["id"]
            if message_receipt is not None:
                receipt["message_intent"] = message_receipt
                receipt["response_disposition"] = message_receipt["response_disposition"]
                receipt["selected_task_ids"] = message_receipt["task_ids"]
                receipt["selected_question_ids"] = message_receipt["question_ids"]
            if job["kind"] == "company_record":
                receipt["record_extraction"] = result["record_extraction"]
            if result.get("outcome") == "no_work":
                receipt["outcome"] = "no_work"
            if job.get("excluded_context_topics"):
                receipt["deferred_topics"] = job["excluded_context_topics"]
            if omitted_quotes:
                from .quotes import bound_quote_failures
                receipt["omitted_quotes"] = bound_quote_failures(omitted_quotes)
            job.update(status="awaiting_disambiguation" if waiting else "applied", receipt=receipt, error_code=None)
            job.pop("quote_failures", None)
            job.pop("rejection_reason", None)
            job.pop("reason", None)
            self._source(state, actor, identifier(job_id, "model-result"), "analysis", canonical(result), input_source_ids=job["source_ids"], interpretation=metadata)
            return receipt
        return self._transaction("analysis", identifier(job_id, "apply"), payload, apply)

    def mark_job_error(self, job_id, code, *, rejection_reason=None, quote_failures=None):
        from .diagnostics import rejection_label
        from .quotes import bound_quote_failures
        label = rejection_label(rejection_reason) if rejection_reason is not None else None
        if rejection_reason is not None and label is None:
            raise BrainError("closed_rejection_reason_required")
        if not isinstance(code, str) or len(code) > 80:
            raise BrainError("stable_error_code_required")
        bounded_failures = bound_quote_failures(quote_failures) if quote_failures else None
        def apply(state, actor):
            self._worker(state, actor)
            job = state.jobs[job_id]
            if job["status"] != "applied":
                attempts = job["attempts"] + 1
                job.update(status="pending", error_code=code, attempts=attempts,
                           next_attempt_at=(datetime.now(timezone.utc) + timedelta(seconds=min(3600, 60 * (2 ** min(attempts - 1, 6))))).isoformat())
                if label:
                    job.update(rejection_reason=rejection_reason, reason=label)
                else:
                    job.pop("rejection_reason", None)
                    job.pop("reason", None)
                if bounded_failures:
                    job["quote_failures"] = bounded_failures
            return {"job_id": job_id, "state": job["status"], "error_code": job["error_code"]}
        return self._transaction("analysis_error", str(uuid.uuid4()), {"job_id": job_id, "code": code, **({"rejection_reason": rejection_reason} if rejection_reason else {})}, apply)

    def transact_nora(self, callback, event_id=None):
        def apply(state, actor):
            if actor["id"] not in state.company["nora_actors"]:
                raise AccessDenied("configured_nora_worker_required")
            return callback(state.nora)
        return self._transaction("nora_state", event_id or str(uuid.uuid4()), {"operation": "nora_callback"}, apply)

    def change_map(self, operations, event_id, reason):
        _text(reason)
        if not isinstance(operations, list) or not operations:
            raise BrainError("map_operations_required")
        def apply(state, actor):
            self._founder(state, actor)
            raw = self._source(state, actor, identifier(event_id, "change"), "correction", reason + "\n" + canonical(operations), speaker=actor["id"])
            return self._apply_map_operations(state, operations, raw, event_id)
        return self._transaction("map_change", event_id, {"operations": operations, "reason": reason}, apply)


    def _implicit_map_refs(self, state, task_id, changes):
        """Existing relation objects read/changed by these field updates."""
        previous = state.tasks.get(task_id)
        refs = set()
        for field, node_kind, edge_kind, reverse in DERIVED_LINKS:
            if field not in changes:
                continue
            claim = changes[field]
            supported = bool(claim["value"] and claim["basis"] != "inferred" and claim["evidence"])
            if previous and previous["fields"].get(field) == claim and (field in previous.get("missing_fields", [])) == (not supported):
                continue
            refs.update(edge["id"] for edge in state.groups["edges"].values() if edge.get("derived_field") == field and edge.get("task_id") == task_id)
            if not supported or claim["value"].casefold().rstrip(".") in ("não se aplica", "nenhum", "nenhuma", "não há", "sem repasse", "sem registro"):
                continue
            if field == "performer":
                from .people import person_identity
                identity = person_identity(claim["value"], company_id=state.company["id"], nodes=state.groups["nodes"].values(), context_claims=state.context_claims)
                if not identity["node_id"]:
                    continue
                node_id = identity["node_id"]
                refs.update(alias_id for alias_id in identity["alias_node_ids"] if alias_id in state.groups["nodes"])
            else:
                node_id = identifier(state.company["id"], node_kind + ":" + claim["value"].casefold())
            if node_id in state.groups["nodes"]:
                refs.add(node_id)
            if task_id:
                first, second = (node_id, task_id) if reverse else (task_id, node_id)
                edge_id = identifier(first, edge_kind + ":" + second)
                if edge_id in state.groups["edges"]:
                    refs.add(edge_id)
        return refs

    def _apply_answer_map_changes(self, state, job, result, source, scope):
        """Validate a data-only plan against the worker's supplied snapshot."""
        from ..inference.schema import validate_analysis
        try:
            validate_analysis(result)
        except ValueError as error:
            raise BrainError("invalid_map_change_shape") from error
        if job["kind"] not in ("answer", "message") or not job["task_ids"] or source["kind"] != "answer" or source.get("speaker") not in state.company["founders"]:
            raise AccessDenied("verified_founder_answer_required")
        if any(result.get(key) for key in ("tasks", "nodes", "context_updates", "answer_assignments", "question_answers", "recognition_markers", "attention_requests")) or result.get("next_question") is not None:
            raise BrainError("structural_analysis_must_be_exclusive")
        if not isinstance(scope, dict) or set(scope) != {"tasks", "nodes", "edges", "source_ids"}:
            raise BrainError("trusted_map_scope_required")
        supplied = {}
        for group in ("tasks", "nodes", "edges"):
            if not isinstance(scope[group], list):
                raise BrainError("invalid_map_scope")
            for item in scope[group]:
                if not isinstance(item, dict) or set(item) != {"id", "version"} or type(item["version"]) is not int or item["version"] < 1 or item["id"] in supplied:
                    raise BrainError("invalid_map_scope")
                supplied[item["id"]] = (group, item["version"])
        if not isinstance(scope["source_ids"], list) or any(sid not in state.sources for sid in scope["source_ids"]):
            raise BrainError("invalid_map_scope_sources")
        allowed = set(scope["source_ids"])
        if not set(job["source_ids"]) <= allowed:
            raise BrainError("answer_source_outside_scope")
        operations = copy.deepcopy(result["map_changes"])
        existing = {key: value for group in ("tasks", "nodes", "edges") for key, value in state.groups[group].items()}
        new_ids = set()
        selected_tasks = set(job["task_ids"])
        new_task_ids = set()
        incident_nodes = {endpoint for edge in state.groups["edges"].values() if edge["from"] in selected_tasks or edge["to"] in selected_tasks
                          for endpoint in (edge["from"], edge["to"]) if endpoint in state.groups["nodes"]}
        for index, operation in enumerate(operations):
            evidence = operation["evidence"]
            self._claim(state, {"value": operation["op"], "basis": "reported", "evidence": evidence}, allowed)
            if not any(ref["source_id"] == source["id"] for ref in evidence):
                raise BrainError("founder_intent_evidence_required")
            kind = operation["op"]
            writable_tasks = selected_tasks | new_task_ids
            if kind in ("rename", "reassign", "correct", "split"):
                target = operation["id"]
                if target in state.tasks and target not in writable_tasks:
                    raise BrainError("map_write_outside_selected_tasks")
                if target in state.groups["nodes"] and target not in incident_nodes and target not in new_ids:
                    raise BrainError("map_write_outside_selected_graph")
            elif kind == "merge" and not set(operation["ids"]) <= writable_tasks:
                raise BrainError("map_write_outside_selected_tasks")
            elif kind == "connect" and not {operation["from"], operation["to"]} & writable_tasks:
                raise BrainError("map_write_outside_selected_graph")
            elif kind == "disconnect":
                edge = state.groups["edges"].get(operation["id"])
                if edge and not {edge["from"], edge["to"]} & writable_tasks:
                    raise BrainError("map_write_outside_selected_graph")
            refs = set()
            if kind in ("rename", "reassign", "correct", "split", "disconnect"):
                refs.add(operation["id"])
                if kind == "disconnect" and state.groups["edges"].get(operation["id"], {}).get("payment_field"):
                    refs.add(state.groups["edges"][operation["id"]]["task_id"])
            elif kind == "merge":
                refs.update(operation["ids"])
            elif kind == "connect":
                refs.update((operation["from"], operation["to"]))
                eid = identifier(operation["from"], operation["kind"] + ":" + operation["to"])
                if eid in existing:
                    refs.add(eid)
            if kind in ("split", "merge"):
                predecessors = refs.copy()
                if any(tid not in state.tasks for tid in predecessors):
                    raise BrainError("task_required")
                refs.update(edge["id"] for edge in state.groups["edges"].values() if edge["from"] in predecessors or edge["to"] in predecessors)
            if kind in ("reassign", "correct"):
                changes = {"performer": operation["performer"]} if kind == "reassign" else operation["fields"]
                refs.update(self._implicit_map_refs(state, operation["id"], changes))
            elif kind == "add" and operation["kind"] == "task":
                refs.update(self._implicit_map_refs(state, None, operation["fields"]))
            elif kind in ("split", "merge"):
                for successor in operation["successors"]:
                    refs.update(self._implicit_map_refs(state, None, successor["fields"]))
            expected_items = operation["expected_versions"]
            expected = {item["id"]: item["version"] for item in expected_items}
            if len(expected) != len(expected_items) or set(expected) != refs - new_ids:
                raise BrainError("complete_expected_versions_required")
            for ref in refs - new_ids:
                if ref not in supplied or ref not in existing:
                    raise BrainError("map_target_outside_scope")
                group, version = supplied[ref]
                if ref not in state.groups[group]:
                    raise BrainError("map_target_outside_scope")
                if expected[ref] != version or existing[ref]["version"] != version:
                    raise BrainError("stale_map_change")
                if existing[ref].get("superseded_by"):
                    raise BrainError("task_superseded_reconciliation_required")
                if group == "tasks" and ref in job["task_ids"] and source.get("base_versions", {}).get(ref) != version:
                    raise BrainError("stale_founder_answer")
            items = [operation] if kind == "add" else operation.get("successors", [])
            for item_index, item in enumerate(items):
                item["id"] = valid_uuid(item.get("id") or identifier(job["id"], f"map:{index}:{item_index}"))
                if item["id"] in existing or item["id"] in new_ids:
                    raise BrainError("successor_already_exists")
                new_ids.add(item["id"])
                if kind in ("split", "merge") or kind == "add" and operation["kind"] == "task":
                    new_task_ids.add(item["id"])
                if kind in ("split", "merge") and not set(item["evidence_ids"]) <= allowed:
                    raise BrainError("successor_evidence_outside_scope")
                for field, claim in item.get("fields", {}).items():
                    validated = self._field_claim(state, claim, allowed, field)
                    if kind in ("split", "merge") and not {ref["source_id"] for ref in validated["evidence"]} <= set(item["evidence_ids"]) | {source["id"]}:
                        raise BrainError("successor_claim_evidence_not_allocated")
            for field, claim in (operation.get("fields", {}).items() if kind == "correct" else [("performer", operation["performer"])] if kind == "reassign" else []):
                self._field_claim(state, claim, allowed, field)
        return self._apply_map_operations(state, operations, source, job["id"], lambda claim, field=None: self._field_claim(state, claim, allowed, field), job["source_ids"])

    def _apply_map_operations(self, state, operations, raw, event_id, claim_validator=None, supporting_source_ids=()):
        """Pure transaction-local applier shared by direct and semantic changes."""
        changed = []
        supporting = {raw["id"], *supporting_source_ids}
        def claim(value, field=None):
            if claim_validator is not None:
                return claim_validator(value, field)
            return {"value": value, "basis": "reported", "evidence": [{"source_id": raw["id"], "quote": raw["text"]}]}
        for operation in operations:
            kind = operation.get("op")
            if kind == "add":
                node_kind = operation.get("kind", "task")
                if node_kind not in KINDS:
                    raise BrainError("invalid_graph_node_kind")
                node_id = valid_uuid(operation.get("id") or identifier(event_id, str(len(changed))))
                group = "tasks" if node_kind == "task" else "nodes"
                if node_id in state.groups[group]:
                    raise BrainError("node_exists")
                record = {"id": node_id, "kind": node_kind, "name": _text(operation["name"]), "evidence_ids": sorted(supporting), "superseded_by": []}
                if group == "tasks":
                    record.update(fields={field: claim(operation.get("fields", {}).get(field), field) if operation.get("fields", {}).get(field) else {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}, conflicts={})
                    if claim_validator is not None:
                        record["evidence_ids"] = sorted(supporting | {ref["source_id"] for field in record["fields"].values() for ref in field["evidence"]})
                    self._refresh_task(state, record)
                else:
                    state.put(group, record)
                changed.append(node_id)
            elif kind in ("rename", "reassign", "correct"):
                tid = operation["id"]
                group = "tasks" if tid in state.tasks else "nodes"
                record = copy.deepcopy(state.groups[group][tid])
                if operation.get("expected_version", record["version"]) != record["version"]:
                    raise BrainError("stale_map_change")
                if kind == "rename":
                    record["name"] = _text(operation["name"])
                else:
                    if group != "tasks":
                        raise BrainError("task_required")
                    changes = {"performer": operation["performer"]} if kind == "reassign" else operation["fields"]
                    if set(changes) - set(FIELDS):
                        raise BrainError("unknown_task_field")
                    for field, value in changes.items():
                        record["fields"][field] = claim(value if claim_validator is not None else _text(value), field)
                        record["conflicts"].pop(field, None)
                record["evidence_ids"] = sorted(set(record.get("evidence_ids", [])) | supporting)
                if group == "tasks" and claim_validator is not None:
                    record["evidence_ids"] = sorted(set(record["evidence_ids"]) | {ref["source_id"] for field in record["fields"].values() for ref in field["evidence"]})
                self._refresh_task(state, record) if group == "tasks" else state.put(group, record)
                changed.append(tid)
            elif kind in ("split", "merge"):
                predecessors = operation.get("ids") or [operation["id"]]
                records = [state.tasks[tid] for tid in predecessors]
                successors = operation["successors"]
                if len(set(predecessors)) != len(predecessors) or (kind == "split" and (len(predecessors) != 1 or len(successors) < 2)) or (kind == "merge" and (len(predecessors) < 2 or len(successors) != 1)):
                    raise BrainError("invalid_split_merge_cardinality")
                needed = {sid for record in records for sid in record["evidence_ids"]}
                allocated = {sid for item in successors for sid in item.get("evidence_ids", [])}
                if not needed <= allocated or not allocated <= set(state.sources):
                    raise BrainError("complete_evidence_allocation_required")
                successor_ids = []
                for index, item in enumerate(successors):
                    sid = valid_uuid(item.get("id") or identifier(event_id, "successor:" + str(index)))
                    if sid in state.tasks:
                        raise BrainError("successor_already_exists")
                    fields = item.get("fields", {})
                    if set(fields) - set(FIELDS):
                        raise BrainError("unknown_task_field")
                    record = {"id": sid, "kind": "task", "name": _text(item["name"]),
                              "fields": {field: claim(fields[field], field) if field in fields else {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS},
                              "conflicts": {}, "evidence_ids": sorted(set(item["evidence_ids"]) | supporting), "predecessors": predecessors, "superseded_by": []}
                    self._refresh_task(state, record)
                    successor_ids.append(sid)
                for record in records:
                    state.put("tasks", {**record, "superseded_by": successor_ids})
                    for question in list(state.questions.values()):
                        if question["task_id"] == record["id"] and question["status"] != "resolved":
                            state.put("questions", {**question, "status": "superseded"})
                for edge in list(state.groups["edges"].values()):
                    if edge["from"] in predecessors or edge["to"] in predecessors:
                        state.put("edges", {**edge, "active": False, "reconciliation_required": True})
                changed.extend(successor_ids)
            elif kind == "connect":
                if operation.get("kind") not in EDGE_KINDS or any(operation.get(key) not in {**state.tasks, **state.groups["nodes"]} for key in ("from", "to")):
                    raise BrainError("invalid_typed_connection")
                edge_id = identifier(operation["from"], operation["kind"] + ":" + operation["to"])
                previous_edge = state.groups["edges"].get(edge_id, {})
                payment_meta = {key: previous_edge[key] for key in ("payment_field", "task_id") if key in previous_edge} if previous_edge.get("payment_field") else {}
                state.put("edges", {"id": edge_id, "from": operation["from"], "to": operation["to"], "kind": operation["kind"], "evidence": operation["evidence"] if claim_validator is not None else claim(raw["text"])["evidence"], "active": True, **payment_meta})
                if payment_meta:
                    payment_task = copy.deepcopy(state.tasks[payment_meta["task_id"]])
                    payment_task["evidence_ids"] = sorted(set(payment_task["evidence_ids"]) | supporting)
                    self._refresh_task(state, payment_task)
                changed.append(edge_id)
            elif kind == "disconnect":
                edge = state.groups["edges"][operation["id"]]
                task = state.tasks.get(edge.get("task_id"), {})
                relation = task.get("fields", {}).get(edge.get("derived_field"), {})
                state.put("edges", {**edge, "active": False, "change_source": raw["id"],
                    "explicit_disconnect": True, "disconnected_evidence": copy.deepcopy(relation.get("evidence", []))})
                if edge.get("payment_field") and edge.get("task_id") in state.tasks:
                    payment_task = copy.deepcopy(state.tasks[edge["task_id"]])
                    payment_task["evidence_ids"] = sorted(set(payment_task["evidence_ids"]) | supporting)
                    self._refresh_task(state, payment_task)
                changed.append(edge["id"])
            else:
                raise BrainError("unsupported_map_operation")
        return {"state": "applied", "changed_ids": changed, "source_id": raw["id"]}


def actor_key(store):
    return store.authenticated_actor()["id"]
