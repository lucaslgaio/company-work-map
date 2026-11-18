"""Read a capture and propose only what its own text says. No model and no planted links."""
from __future__ import annotations

import re

from company_pilot.brain import FIELDS

# Demo reader only. The founder's model, using the analysis prompt, reads a real week.


def _lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def _field(text, label):
    match = re.search(r"(?m)^" + re.escape(label) + r":\s*(.+)$", text)
    return match.group(1).strip() if match else None


def _claim(source_id, text, value):
    if value not in text:
        return None
    return {"value": value, "basis": "observed", "evidence": [{"source_id": source_id, "quote": value if value in text else text}]}


def _empty_fields():
    return {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}


def _title(value):
    text = " ".join(value.split())
    if not text:
        return ""
    return text[0].upper() + text[1:]


def analyze_observation(text, source_id, existing_tasks):
    """Demo reader. One task, and a link only when the sentence names both sides.

    A wrong consumer is worse than no link. The real week is read by the founder's model.
    """
    fields = _empty_fields()
    activity = _field(text, "Atividade") or _field(text, "Trabalho")
    spoken = activity if activity and activity in text else ""
    actor = re.search(
        r"\b([A-ZÁÉÍÓÚÂÊÔÃÕ][\wÀ-ÿ]+(?:\s+(?:de|da|do|dos|das)\s+[A-ZÁÉÍÓÚÂÊÔÃÕ][\wÀ-ÿ]+)?)\s+vai\s+"
        r"((?:cotar|conferir|liberar|avisar|atualizar|enviar|montar)\s+.+?)(?:\s+a partir\b|[.,!\n]|$)",
        text)
    open_work = re.search(r"(?i)\bquem\s+((?:cotar|conferir|liberar|avisar|atualizar|enviar|montar)\s+.+?)(?:\s+a partir\b|[.,!\n]|$)", text)
    if not activity and actor and actor.group(2).strip() in text:
        spoken = actor.group(2).strip()
        activity = _title(spoken)
    elif not activity and open_work and open_work.group(1).strip() in text:
        spoken = open_work.group(1).strip()
        activity = _title(spoken)
    for label, field in (("Ação", "action"), ("Resultado pronto", "result"), ("Entrada", "input"), ("Responsável", "performer")):
        value = _field(text, label)
        if value and value in text:
            fields[field] = {"value": value, "basis": "observed", "evidence": [{"source_id": source_id, "quote": value}]}
    if activity and spoken and spoken in text and not fields["action"].get("value"):
        fields["action"] = {"value": activity, "basis": "observed", "evidence": [{"source_id": source_id, "quote": spoken}]}
    ready = re.search(r"(?i)(lista de material pronta|medi[cç][aã]o 02 conferida|cronograma atualizado|aditivo 01 escrito)", text)
    owned = ready and any((((task.get("fields") or {}).get("result") or {}).get("value") or "").casefold() == ready.group(1).casefold()
                          for task in existing_tasks if not task.get("superseded_by"))
    received = re.search(r"(?i)(?:a partir|parte) d[aeo]\s+([^.!\n]+)", text)
    if received and received.group(1).strip() in text and not fields["input"].get("value"):
        fields["input"] = {"value": received.group(1).strip(), "basis": "observed",
                           "evidence": [{"source_id": source_id, "quote": received.group(1).strip()}]}
    elif ready and not owned and not fields["result"].get("value") and f"parte da {ready.group(1)}" not in text.casefold():
        fields["result"] = {"value": ready.group(1), "basis": "observed", "evidence": [{"source_id": source_id, "quote": ready.group(1)}]}
    if actor and actor.group(0) in text and not fields["performer"].get("value"):
        fields["performer"] = {"value": actor.group(1), "basis": "reported",
                               "evidence": [{"source_id": source_id, "quote": actor.group(0)}]}
    tasks = []
    if activity and spoken and spoken in text:
        tasks.append({"task_id": None, "name": activity, "fields": fields, "links": []})
    _attach_transfers(text, source_id, existing_tasks, tasks, activity, fields)
    if not tasks:
        return None
    return {"tasks": tasks, "nodes": [], "context_updates": [], "next_question": None}


def _attach_transfers(text, source_id, existing_tasks, tasks, activity, fields):
    """Link only when one sentence names the result and exactly one existing task."""
    if not re.search(r"\b(?:mandei|mandou|manda|enviei|enviou|envia|encaminhei|encaminhou|passei|passou)\b", text, re.IGNORECASE):
        return
    active = [task for task in existing_tasks if not task.get("superseded_by") and task.get("id")]
    result = fields["result"].get("value")
    for sentence in [piece.strip() for piece in re.split(r"\n|(?<=[.!])\s+", text) if piece.strip()]:
        if sentence not in text or not re.search(r"\b(?:mandei|mandou|manda|enviei|enviou|envia|encaminhei|encaminhou|passei|passou)\b", sentence, re.IGNORECASE):
            continue
        consumers = [task for task in active if task["name"] and task["name"].casefold() in sentence.casefold()]
        if len(consumers) != 1:
            continue
        consumer = consumers[0]
        producer = next((task for task in active if (task["fields"].get("result") or {}).get("value")
                         and (task["fields"]["result"]["value"].casefold() in sentence.casefold())), None)
        producer_candidate = tasks[0] if producer is None and result and result.casefold() in sentence.casefold() and tasks else None
        if producer is None and producer_candidate is None:
            continue
        if producer and producer["id"] == consumer["id"]:
            continue
        _add_link(tasks, producer, producer_candidate, consumer, source_id, sentence)
        return


def _add_link(tasks, producer, producer_candidate, consumer, source_id, sentence):
    link = {"kind": "handoff", "target_id": consumer["id"], "basis": "observed",
            "evidence": [{"source_id": source_id, "quote": sentence}]}
    if producer_candidate is not None:
        producer_candidate["links"].append(link)
        return
    current = next((task for task in tasks if task.get("task_id") == producer["id"]), None)
    if current:
        current.setdefault("links", []).append(link)
        return
    copied = {"task_id": producer["id"], "name": producer["name"],
              "fields": {field: producer["fields"].get(field) or _empty_fields()[field] for field in FIELDS},
              "links": [link]}
    tasks.append(copied)


def _with_performer(task, source_id, name, quote):
    fields = {field: task["fields"].get(field) or {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
    fields["performer"] = {"value": name, "basis": "reported",
                           "evidence": [{"source_id": source_id, "quote": quote}] if name else []}
    return {"tasks": [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}],
            "nodes": [], "context_updates": [], "next_question": None}


def analyze_reply(text, source_id, task):
    """A reply names who does the work, including a short natural answer."""
    raw = " ".join(str(text or "").split())
    folded = raw.casefold().strip(" .!?")
    if not raw or raw not in text and folded not in text.casefold():
        return None
    if folded in {"não sei", "nao sei", "não sabemos", "nao sabemos", "ninguém", "ninguem"}:
        return _with_performer(task, source_id, None, raw)
    if re.search(r"\bos dois\b", folded):
        return _with_performer(task, source_id, "os dois", raw)
    patterns = (
        r"(?:quem executa é o|é o)\s+([A-Za-zÀ-ÿ][\wÀ-ÿ]*(?:\s+[A-Za-zÀ-ÿ][\wÀ-ÿ]*)?)",
        r"\bo\s+([A-Za-zÀ-ÿ][\wÀ-ÿ]*)\s+que faz\b",
        r"^([A-Za-zÀ-ÿ][\wÀ-ÿ]*)$",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match and match.group(0) in raw:
            return _with_performer(task, source_id, match.group(1), match.group(0) if match.group(0) in text else raw)
    return None
