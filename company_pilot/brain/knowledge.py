"""Source-backed current claims, recognition names and payment connections."""
import copy
import re
import unicodedata

from .stores import BrainError, canonical, digest

GENERIC_MARKERS = {"chrome", "gmail", "email", "e-mail", "banco", "planilha", "fatura", "browser", "safari", "edge", "whatsapp"}


def _marker_text(value):
    """Compare spelling only; raw source quotes remain untouched evidence."""
    folded = unicodedata.normalize("NFD", value).casefold()
    return " ".join("".join(char for char in folded if not unicodedata.combining(char)).split())


def _marker_generic_key(value):
    # Punctuation must not turn an app-only name into a company matcher.
    return "".join(char for char in _marker_text(value) if char.isalnum())


def _marker_in_quote(term, quote):
    # Keep internal punctuation significant and require the entire phrase at
    # word boundaries: "obra" cannot ground "Diário de obra", nor can
    # "Empresa Vital" ground "Empresa".
    return re.search(r"(?<!\w)" + re.escape(_marker_text(term)) + r"(?!\w)", _marker_text(quote)) is not None


def marker_readback(context, markers):
    active = [copy.deepcopy(marker) for marker in markers if marker["status"] == "active"]
    explicit = {marker["value"].casefold() for marker in markers}
    for key, claim in context.items():
        value = claim.get("value")
        if key.startswith("work-signal:") and value and claim.get("basis") != "inferred" and 3 <= len(value) <= 200 and value.casefold() not in GENERIC_MARKERS | explicit and any(value.casefold() in ref["quote"].casefold() for ref in claim.get("evidence", [])):
            active.append({"id": key, "value": value, "kind": "record_name", "evidence": copy.deepcopy(claim["evidence"]), "status": "active", "origin": "legacy_literal_claim"})
            explicit.add(value.casefold())
    return active


def seed_claims(context, existing):
    from .engine import TOPICS, identifier
    records = copy.deepcopy(existing)
    for topic in TOPICS:
        if context.get(topic) and not any(record["topic"] == topic for record in records):
            claim = copy.deepcopy(context[topic])
            records.append({"id": identifier(topic, digest(canonical(claim))), "topic": topic,
                            "claim": claim, "status": "active", "origin": "legacy_projection"})
    return records


def project_claims(state, topic):
    active = [record["claim"] for record in state.context_claims if record["topic"] == topic and record["status"] == "active"]
    if not active:
        state.context.pop(topic, None)
        return None
    if len(active) == 1:
        state.context[topic] = copy.deepcopy(active[0])
    else:
        refs = {canonical(ref): ref for claim in active for ref in claim["evidence"]}
        state.context[topic] = {"value": "\n".join(dict.fromkeys(claim["value"] for claim in active)),
            "basis": "reported" if any(claim["basis"] == "reported" for claim in active) else "observed",
            "evidence": list(refs.values())}
    return state.context[topic]


def apply_context(brain, state, topic, claim, source, mode="add", replaces=(), explicit_replace=False):
    from .engine import identifier, now
    if mode not in ("add", "replace", "conflict"):
        raise BrainError("context_update_mode_required")
    current = [record for record in state.context_claims if record["topic"] == topic and record["status"] == "active"]
    active_ids = {record["id"] for record in current}
    if mode == "replace":
        if source.get("speaker") not in state.company["founders"]:
            raise BrainError("founder_replacement_intent_required")
        selected = active_ids if explicit_replace else set(replaces)
        if (active_ids and not selected) or not selected <= active_ids or not any(ref["source_id"] == source["id"] for ref in claim["evidence"]):
            raise BrainError("explicit_current_claim_replacement_required")
        if not explicit_replace and "context_claim_ids" in source and not selected <= set(source["context_claim_ids"]):
            raise BrainError("replacement_outside_receipt_claims")
        for record in current:
            if record["id"] in selected:
                record.update(status="replaced", replaced_by_source=source["id"])
    elif replaces:
        raise BrainError("replacement_ids_require_replace_mode")
    previous = copy.deepcopy(state.context.get(topic))
    if mode == "conflict" and previous and previous["value"] != claim["value"]:
        brain._context_conflict(state, topic, previous, claim, source["id"])
        state.company["context_version"] += 1
        return previous
    cid = identifier(topic, digest(canonical(claim)))
    record = next((item for item in state.context_claims if item["id"] == cid), None)
    if record is None:
        state.context_claims.append({"id": cid, "topic": topic, "claim": copy.deepcopy(claim), "status": "active", "source_id": source["id"]})
    else:
        record.update(status="active", source_id=source["id"])
    selected = project_claims(state, topic)
    state.interview.setdefault("context_history", []).append({"topic": topic, "previous": previous,
        "selected": copy.deepcopy(selected), "source_id": source["id"], "speaker": source.get("speaker", source["actor"]), "at": now(), "mode": mode, "replaces": list(replaces)})
    state.company["context_version"] += 1
    return selected


def apply_markers(brain, state, values, source, allowed):
    from .engine import identifier
    if not isinstance(values, list) or len(values) > 100:
        raise BrainError("bounded_recognition_markers_required")
    for value in values:
        if not isinstance(value, dict) or set(value) != {"value", "kind", "evidence", "operation"} or value["kind"] not in ("record_name", "record_ref", "company_alias", "business_account") or value["operation"] not in ("add", "retire") or not isinstance(value["value"], str) or not 3 <= len(value["value"].strip()) <= 200:
            raise BrainError("invalid_grounded_marker")
        term = value["value"].strip()
        normalized = _marker_text(term)
        generic_key = _marker_generic_key(term)
        if not generic_key:
            raise BrainError("invalid_grounded_marker")
        if generic_key in {_marker_generic_key(marker) for marker in GENERIC_MARKERS}:
            raise BrainError("app_only_or_generic_marker_not_authorized")
        brain._claim(state, {"value": term, "basis": "reported", "evidence": value["evidence"]}, allowed)
        if not any(_marker_in_quote(term, ref["quote"]) for ref in value["evidence"]):
            raise BrainError("marker_name_must_be_literal_in_source")
        mid = identifier(state.company["id"], "marker:" + value["kind"] + ":" + normalized)
        previous = next((item for item in state.recognition_markers
                         if item["kind"] == value["kind"] and _marker_text(item["value"]) == normalized), None)
        # Old records used casefold-only IDs. Preserve those IDs and histories
        # when an accent/spacing variant updates or retires the same marker.
        if previous is not None:
            mid = previous["id"]
        if value["operation"] == "retire" and previous is None:
            raise BrainError("unknown_marker_retirement")
        if value["operation"] == "retire" and source.get("speaker") not in state.company["founders"]:
            raise BrainError("founder_marker_retirement_required")
        record = {"id": mid, "value": term, "kind": value["kind"], "evidence": copy.deepcopy(value["evidence"]),
            "source_id": source["id"], "status": "active" if value["operation"] == "add" else "retired"}
        if previous is None:
            state.recognition_markers.append(record)
        else:
            previous.setdefault("history", []).append({key: copy.deepcopy(previous[key]) for key in ("value", "evidence", "source_id", "status")})
            previous.update(record)
        state.company["context_version"] += 1


def payment_claims(brain, state, task, payment, issues, allowed, replace=False, source_id=None):
    if payment is not None:
        if not isinstance(payment, dict) or set(payment) != {"object", "tracking_record"}:
            raise BrainError("typed_payment_context_required")
        previous = task.get("payment", {})
        selected = {}
        for key, value in payment.items():
            claim = brain._claim(state, value, allowed)
            old = previous.get(key)
            if old and old["value"] and claim["value"] is None and not (replace and any(ref["source_id"] == source_id for ref in claim["evidence"])):
                selected[key] = old
            elif old and old["value"] and claim["value"] and old["value"] != claim["value"] and not replace:
                task.setdefault("consistency_issues", []).append({"description": f"Relatos diferentes sobre {key}: «{old['value']}» e «{claim['value']}»", "evidence": old["evidence"] + claim["evidence"]})
                selected[key] = old
            else:
                selected[key] = claim
        task["payment"] = selected
    if issues is not None:
        if not isinstance(issues, list) or len(issues) > 20:
            raise BrainError("bounded_consistency_issues_required")
        for issue in issues:
            if set(issue) != {"description", "evidence"}:
                raise BrainError("typed_consistency_issue_required")
            brain._claim(state, {"value": issue["description"], "basis": "reported", "evidence": issue["evidence"]}, allowed)
        from .evolution import merge_issues
        merge_issues(task, issues)


def payment_gaps(state, task):
    from .engine import identifier
    missing = []
    for field, node_kind, edge_kind in (("object", "work_object", "pays_for"), ("tracking_record", "tracking_record", "tracks")):
        claim = task.get("payment", {}).get(field)
        if claim is None:
            continue
        if not claim["value"] or claim["basis"] == "inferred" or not claim["evidence"]:
            missing.append(field)
            continue
        node_id = identifier(state.company["id"], node_kind + ":" + claim["value"].casefold())
        edge = state.groups["edges"].get(identifier(task["id"], edge_kind + ":" + node_id))
        if edge and not edge["active"] and edge.get("explicit_disconnect"):
            cutoff = state.sources[edge["change_source"]]["received_at"]
            if not any(state.sources[ref["source_id"]]["received_at"] > cutoff for ref in claim["evidence"]):
                missing.append(field)
    return missing


def refresh_payment(state, task):
    from .engine import identifier, now
    payment = task.get("payment")
    missing = []
    if payment is not None:
        for field, node_kind, edge_kind in (("object", "work_object", "pays_for"), ("tracking_record", "tracking_record", "tracks")):
            claim = payment[field]
            supported = bool(claim["value"] and claim["basis"] != "inferred" and claim["evidence"])
            if not supported:
                missing.append(field)
            node_id = identifier(state.company["id"], node_kind + ":" + claim["value"].casefold()) if supported else None
            eid = identifier(task["id"], edge_kind + ":" + node_id) if supported else None
            for edge in list(state.groups["edges"].values()):
                if edge.get("payment_field") == field and edge.get("task_id") == task["id"] and edge["id"] != eid and edge.get("active"):
                    state.put("edges", {**edge, "active": False})
            if supported:
                previous = state.groups["edges"].get(eid)
                if previous and previous.get("explicit_disconnect"):
                    cutoff = state.sources[previous["change_source"]]["received_at"]
                    if not any(state.sources[ref["source_id"]]["received_at"] > cutoff for ref in claim["evidence"]):
                        missing.append(field)
                        continue
                if node_id not in state.groups["nodes"]:
                    state.put("nodes", {"id": node_id, "kind": node_kind, "name": claim["value"], "evidence": claim["evidence"], "superseded_by": []})
                state.put("edges", {"id": eid, "from": task["id"], "to": node_id, "kind": edge_kind, "task_id": task["id"], "payment_field": field, "evidence": claim["evidence"], "active": True})
            qid = identifier(task["id"], "payment:" + field)
            previous_q = state.questions.get(qid)
            if field not in missing:
                if previous_q and previous_q["status"] != "resolved":
                    state.put("questions", {**previous_q, "status": "resolved"})
                continue
            label = "o serviço, compra ou obrigação que este pagamento quita" if field == "object" else "o registro que mostra o pagamento e o saldo"
            sources = task["evidence_ids"]
            state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": task["id"], "base_version": task.get("version", 1), "task_version": task.get("version", 1),
                "gap_kind": "payment_" + field, "missing_links": ["purpose" if field == "object" else "tracking_record"], "missing_fields": ["purpose" if field == "object" else "tracking_record"],
                "uncertainty_ids": [qid], "context_key": task["id"], "activity_summary": task["name"],
                "prompt": f"No pagamento «{task['name']}», falta ligar {label}. O que pode confirmar?", "status": "pending",
                "created_at": (previous_q or {}).get("created_at", now()), "evidence_ids": sources,
                "observation_ids": [sid for sid in sources if state.sources[sid]["kind"] in ("observation", "company_record")], "relevant_answer_ids": [], "revisit_evidence_ids": [],
                **({"response_disposition": previous_q["response_disposition"]} if previous_q and previous_q.get("response_disposition") else {})})
    task["payment_missing"] = missing
    if missing or task.get("consistency_issues"):
        task["status"] = "provisional"
    qid = identifier(task["id"], "consistency")
    old_question = state.questions.get(qid)
    if task.get("consistency_issues"):
        refs = [ref for issue in task["consistency_issues"] for ref in issue["evidence"]]
        sources = sorted({ref["source_id"] for ref in refs} | set(task["evidence_ids"]))
        state.put("questions", {"id": qid, "company_id": state.company["id"], "task_id": task["id"], "base_version": task["version"], "task_version": task["version"],
            "gap_kind": "business_consistency", "issue_ids": [issue["id"] for issue in task["consistency_issues"]], "missing_links": ["purpose"], "missing_fields": ["purpose"], "uncertainty_ids": [qid], "context_key": task["id"],
            "activity_summary": task["name"], "prompt": "Na atividade «" + task["name"] + "», os relatos ainda não combinam: " + "; ".join(issue["description"] for issue in task["consistency_issues"]) + ". O que deve ser corrigido?",
            "status": "pending", "created_at": (old_question or {}).get("created_at", now()), "evidence_ids": sources,
            "observation_ids": [sid for sid in sources if state.sources[sid]["kind"] in ("observation", "company_record")],
            "relevant_answer_ids": [sid for sid in sources if state.sources[sid]["kind"] == "answer"], "revisit_evidence_ids": sorted({ref["source_id"] for ref in refs})})
    elif old_question and old_question["status"] != "resolved":
        state.put("questions", {**old_question, "status": "resolved"})
    return task
