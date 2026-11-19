"""The interpreter's structural boundary; evidence semantics belong to Brain."""

FIELD_NAMES = (
    "trigger", "performer", "input", "action", "result", "purpose",
    "placement", "handoff", "tracking_record",
)
TOPICS = ("activities", "people", "stages", "tracking", "documents")
BASES = ("observed", "reported", "inferred")
NODE_KINDS = (
    "responsibility", "job", "process", "person", "tracking_record", "project",
)
EDGE_KINDS = ("contains", "performed_by", "handoff", "depends_on", "tracks", "pays_for")


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


EVIDENCE_SCHEMA = _object({"source_id": {"type": "string"},
                           "quote": {"type": "string"}})
CLAIM_SCHEMA = _object({
    "value": {"type": ["string", "null"]},
    "basis": {"type": "string", "enum": list(BASES)},
    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
})
PARTIAL_FIELDS_SCHEMA = _object({name: CLAIM_SCHEMA for name in FIELD_NAMES}, required=[])
EXPECTED_VERSIONS_SCHEMA = {"type": "array", "items": _object({
    "id": {"type": "string"}, "version": {"type": "integer", "minimum": 1},
})}
SUCCESSOR_SCHEMA = _object({
    "id": {"type": ["string", "null"]}, "name": {"type": "string"},
    "fields": PARTIAL_FIELDS_SCHEMA,
    "evidence_ids": {"type": "array", "items": {"type": "string"}},
})


def _map_operation(op, properties):
    return _object({"op": {"type": "string", "enum": [op]}, **properties,
                    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
                    "expected_versions": EXPECTED_VERSIONS_SCHEMA})


MAP_CHANGES_SCHEMA = {"type": "array", "maxItems": 20, "items": {"anyOf": [
    _map_operation("add", {
        "kind": {"type": "string", "enum": ["task", "organization", *NODE_KINDS]},
        "id": {"type": ["string", "null"]}, "name": {"type": "string"},
        "fields": PARTIAL_FIELDS_SCHEMA,
    }),
    _map_operation("rename", {"id": {"type": "string"}, "name": {"type": "string"}}),
    _map_operation("reassign", {"id": {"type": "string"}, "performer": CLAIM_SCHEMA}),
    _map_operation("correct", {"id": {"type": "string"}, "fields": PARTIAL_FIELDS_SCHEMA}),
    _map_operation("split", {"id": {"type": "string"}, "successors": {
        "type": "array", "items": SUCCESSOR_SCHEMA,
    }}),
    _map_operation("merge", {
        "ids": {"type": "array", "items": {"type": "string"}},
        "successors": {"type": "array", "items": SUCCESSOR_SCHEMA},
    }),
    _map_operation("connect", {"from": {"type": "string"}, "to": {"type": "string"},
                                "kind": {"type": "string", "enum": list(EDGE_KINDS)}}),
    _map_operation("disconnect", {"id": {"type": "string"}}),
]}}
ANALYSIS_SCHEMA = _object({
    "tasks": {"type": "array", "items": _object({
        "task_id": {"type": ["string", "null"]},
        "name": {"type": "string"},
        "fields": _object({name: CLAIM_SCHEMA for name in FIELD_NAMES}),
        "links": {"type": "array", "items": _object({
            "kind": {"type": "string", "enum": list(EDGE_KINDS)},
            "target_id": {"type": "string"},
            "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
            "basis": {"type": "string", "enum": list(BASES)},
        }, required=["kind", "target_id", "evidence"])},
    })},
    "context_updates": {"type": "array", "items": _object({
        "topic": {"type": "string", "enum": list(TOPICS)},
        "value": {"type": "string"},
        "basis": {"type": "string", "enum": list(BASES)},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    })},
    "nodes": {"type": "array", "items": _object({
        "kind": {"type": "string", "enum": list(NODE_KINDS)},
        "name": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    })},
    "next_question": {"anyOf": [
        {"type": "null"},
        _object({"topic": {"type": "string", "enum": list(TOPICS)},
                 "text": {"type": "string"}}),
    ]},
})
ANALYSIS_SCHEMA["properties"]["answer_assignments"] = {
    "type": "array", "items": _object({
        "answer_id": {"type": "string"},
        "question_ids": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    }),
}
ANALYSIS_SCHEMA["properties"]["map_changes"] = MAP_CHANGES_SCHEMA
ANALYSIS_SCHEMA["properties"]["recognition_markers"] = {
    "type": "array", "items": _object({
        "value": {"type": "string"},
        "kind": {"type": "string", "enum": [
            "record_name", "record_ref", "company_alias", "business_account",
        ]},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        "operation": {"type": "string", "enum": ["add", "retire"]},
    }),
}
context_update = ANALYSIS_SCHEMA["properties"]["context_updates"]["items"]
# mode stays optional. The brain treats a missing mode as add.
context_update["properties"].update({
    "mode": {"type": "string", "enum": ["add", "replace", "conflict"]},
    "replaces": {"type": "array", "items": {"type": "string"}},
})
task_properties = ANALYSIS_SCHEMA["properties"]["tasks"]["items"]["properties"]
task_properties.update({
    "payment": _object({"object": CLAIM_SCHEMA, "tracking_record": CLAIM_SCHEMA}),
    "consistency_issues": {"type": "array", "items": _object({
        "description": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    })},
    "field_updates": _object({field: _object({
        "mode": {"type": "string", "enum": ["progress", "conflict", "equivalent"]},
        "effective_at": {"type": ["string", "null"]},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        "prior_evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    }, required=["mode", "effective_at", "evidence"]) for field in FIELD_NAMES}, required=[]),
    "issue_resolutions": {"type": "array", "maxItems": 20, "items": _object({
        "issue_ids": {"type": "array", "items": {"type": "string"}},
        "resolution": CLAIM_SCHEMA,
    })},
})
ANALYSIS_SCHEMA["properties"]["question_answers"] = {
    "type": "array", "maxItems": 20, "items": _object({
        "question_id": {"type": "string"}, "claim": CLAIM_SCHEMA,
    }),
}
ANALYSIS_SCHEMA["properties"]["attention_requests"] = {
    "type": "array", "maxItems": 10, "items": _object({
        "question_id": {"type": "string"}, "reason": CLAIM_SCHEMA,
    }),
}
ANALYSIS_SCHEMA["properties"]["message_intent"] = _object({
    "kind": {"type": "string", "enum": [
        "answer", "correction", "context", "dismiss", "delegate", "chatter", "unclear",
    ]},
    "task_ids": {"type": "array", "items": {"type": "string"}},
    "question_ids": {"type": "array", "items": {"type": "string"}},
    "delegate_to": {"type": ["string", "null"]},
    "disposition": {"type": ["string", "null"], "enum": [
        None, "not_company", "not_useful", "not_now", "already_answered",
    ]},
    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
})
ANALYSIS_SCHEMA["properties"]["record_extraction"] = _object({
    "complete": {"type": "boolean"},
    "limitations": {"type": "array", "items": {"type": "string"}},
})


def schema_for_job(job_kind=None):
    """Apply the multi-task record bound without narrowing legacy answer jobs."""
    if job_kind not in ("company_record", "observation"):
        return ANALYSIS_SCHEMA
    bounds = {"minItems": 1, "maxItems": 3} if job_kind == "observation" else {"maxItems": 50}
    return {**ANALYSIS_SCHEMA, "properties": {**ANALYSIS_SCHEMA["properties"],
            "tasks": {**ANALYSIS_SCHEMA["properties"]["tasks"], **bounds}}}


def validate_analysis(value, *, job_kind=None):
    """Raise ValueError for an invalid shape, without echoing model content."""
    def validate(item, schema):
        if "anyOf" in schema:
            for alternative in schema["anyOf"]:
                try:
                    validate(item, alternative)
                    return
                except ValueError:
                    pass
            raise ValueError("invalid_analysis")
        types = schema.get("type")
        types = [types] if isinstance(types, str) else types
        matches = {
            "object": type(item) is dict, "array": type(item) is list,
            "string": type(item) is str, "null": item is None,
            "integer": type(item) is int,
            "boolean": type(item) is bool,
        }
        if types and not any(matches.get(kind, False) for kind in types):
            raise ValueError("invalid_analysis")
        if "enum" in schema and item not in schema["enum"]:
            raise ValueError("invalid_analysis")
        if "minimum" in schema and item < schema["minimum"]:
            raise ValueError("invalid_analysis")
        if type(item) is dict and "properties" in schema:
            properties = schema["properties"]
            if not set(schema.get("required", [])) <= set(item) or set(item) - set(properties):
                raise ValueError("invalid_analysis")
            for name, child in item.items():
                validate(child, properties[name])
        if type(item) is list and "items" in schema:
            if len(item) > schema.get("maxItems", len(item)) or len(item) < schema.get("minItems", 0):
                raise ValueError("invalid_analysis")
            for child in item:
                validate(child, schema["items"])

    validate(value, schema_for_job(job_kind))
    return value
