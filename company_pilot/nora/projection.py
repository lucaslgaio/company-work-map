"""Validate canonical evidence; read old accepted records without inventing facts."""
from copy import deepcopy


def normalize_evidence(refs, sources):
    result, seen = [], set()
    for ref in refs:
        source = sources.get(ref.get("source_id"))
        quote = ref.get("quote")
        if not source or not isinstance(quote, str) or not quote or quote not in source.get("text", ""):
            continue
        key = (source["id"], quote)
        if key in seen:
            continue
        seen.add(key)
        result.append({"source_id": source["id"], "quote": quote,
            "locator": ref.get("locator") or "sources/" + source["id"] + ".json",
            "actor": ref.get("actor") or source.get("actor"),
            "occurred_at": ref.get("occurred_at") or source.get("observation", {}).get("occurred_at") or source.get("received_at")})
    return tuple(result)


def question_support(raw, task, sources):
    refs = raw.get("evidence")
    if refs is None:  # Explicit empty canonical evidence is not replaced by a guess.
        if raw.get("gap_kind") == "business_consistency":
            refs = [ref for issue in task.get("consistency_issues", []) for ref in issue.get("evidence", [])]
        elif raw.get("conflict"):
            refs = [ref for key in ("previous", "reported_candidate") for ref in raw["conflict"].get(key, {}).get("evidence", [])]
        else:
            refs = [ref for field in ("action", "input", "result") for ref in task.get("fields", {}).get(field, {}).get("evidence", [])]
        if not refs:
            refs = [{"source_id": sid, "quote": sources[sid]["text"][:500]}
                    for sid in raw.get("evidence_ids", []) if sid in sources and sources[sid].get("text")]
    evidence = normalize_evidence(refs, sources)
    context = deepcopy(raw.get("observed_context") or {})
    if "observed_context" not in raw and evidence and raw.get("target_kind") != "context":
        source = sources[evidence[-1]["source_id"]]
        meta = source.get("observation", {}).get("source", {})
        context = {"actor": source.get("actor"), "occurred_at": evidence[-1].get("occurred_at"),
                   "app": meta.get("app"), "record_ref": meta.get("record_ref")}
    issues = []
    for issue in task.get("consistency_issues", []) if raw.get("gap_kind") == "business_consistency" else []:
        normalized = normalize_evidence(issue.get("evidence", []), sources)
        if normalized:
            issues.append({"id": issue.get("id"), "description": issue.get("description", ""), "evidence": normalized})
    return evidence, context, tuple(issues)


def known_fact_support(raw, sources):
    facts = []
    for fact in raw.get("known_facts", []):
        claim = fact.get("claim", {})
        refs = normalize_evidence(claim.get("evidence", []), sources)
        if claim.get("basis") in ("observed", "reported") and claim.get("value") and refs:
            facts.append({**deepcopy(fact), "claim": {**deepcopy(claim), "evidence": refs}})
    return tuple(facts)


def priority_support(raw, sources):
    hint = raw.get("priority_hint")
    if not isinstance(hint, dict):
        return None
    claim = hint.get("reason", hint)
    if not isinstance(claim, dict) or claim.get("basis") not in ("observed", "reported") or not claim.get("value"):
        return None
    refs = normalize_evidence(claim.get("evidence", []), sources)
    if not refs:
        return None
    return {"reason": {**deepcopy(claim), "evidence": refs}}
