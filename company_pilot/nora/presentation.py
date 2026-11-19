"""Deterministic founder copy and auditable priority, using authorized facts only."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from .models import Question, RoundItem

MONEY = re.compile(r"R\$\s*\d[\d.,]*", re.I)
ASKS = {"trigger": "O que faz esse trabalho começar?", "performer": "Quem executa esse trabalho?",
    "input": "O que é necessário para começar esse trabalho?", "action": "O que é feito nesse trabalho?",
    "result": "Qual resultado fica pronto?", "purpose": "Para que serve esse trabalho?",
    "placement": "A que etapa esse trabalho pertence?", "handoff": "Quem recebe o resultado desse trabalho?",
    "tracking_record": "Onde o andamento desse trabalho é acompanhado?"}


def display_text(value: str, limit: int = 600) -> str:
    """Clean a founder-visible sentence. The limit is unused: the fact is never cut or closed with an ellipsis."""
    del limit
    for identifier, name in {"com.google.Chrome": "Chrome", "com.apple.Safari": "Safari",
        "com.microsoft.Excel": "Excel", "com.microsoft.Word": "Word", "com.microsoft.edgemac": "Microsoft Edge",
        "com.microsoft.VSCode": "Visual Studio Code", "net.whatsapp.WhatsApp": "WhatsApp", "com.apple.Preview": "Pré-Visualização"}.items():
        value = value.replace(identifier, name)
    return " ".join(value.split())


def _plain(value, limit=180):
    if value is None:
        return ""
    value = re.sub(r"\bQ-[0-9A-F]{8}\b", "", str(value), flags=re.I)
    value = re.sub(r"\S*#[A-Za-z_][^\s,;:!?»]*", "registro", value)
    value = re.sub(r"(?:https?://|local-fixture:|views/|records/)\S+", "", value)
    value = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "", value, flags=re.I)
    return display_text(value.replace("?", ".").replace("..", "."), limit).strip(" .;:")


def _human_label(raw):
    if not isinstance(raw, str) or not raw.strip() or "#" in raw:
        return ""
    raw = raw.strip()
    if re.search(r"(?:https?://|local-fixture:|views/|records/|[0-9a-f]{24,})", raw, re.I):
        return ""
    if re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)+", raw):
        return ""  # Do not invent a display name by expanding an opaque slug.
    if "/" in raw or "\\" in raw:
        return ""
    return _plain(raw, 100)


def _record(q):
    label = _human_label((q.observed_context or {}).get("record_ref"))
    if label:
        return label
    return "" if q.target_kind == "context" else _human_label(q.activity_summary)


def askability(q: Question) -> str | None:
    if q.privacy_disposition in ("personal", "excluded"):
        return "excluded_by_recorded_privacy_decision"
    if q.privacy_disposition == "withheld":
        return "withheld_coverage_only"
    blob_early = " ".join((q.activity_summary or "", q.prompt or "")).casefold()
    if "ainda desconhecido no aplicativo" in blob_early or "nenhum título, texto de janela" in blob_early:
        return "coverage_gap_not_asked"
    if not q.evidence:
        return "authorized_evidence_missing"
    if q.target_kind == "context" and not _record(q):
        return "safe_identifiable_context_missing"
    blob = " ".join((q.activity_summary or "", q.prompt or "")).casefold()
    if "transcri" in blob and any(word in blob for word in ("privad", "pessoal", "particular")):
        return "private_note_not_asked"
    return None


def low_value_cross_apply(q: Question) -> bool:
    """A suggestion that another task's fact «também se aplica» without a shared object."""
    fact = related_fact(q)
    if not fact:
        return False
    from company_pilot.brain.semantics import normalized
    def tokens(value):
        return {token for token in normalized(value).split() if len(token) >= 5}
    return not tokens(fact["claim"]["value"]) & tokens(q.activity_summary or "")


def _tier(q):
    if q.priority_hint:
        return -1, "founder_priority", "O fundador indicou esta dúvida como prioridade."
    if q.gap_kind == "business_consistency":
        return 0, "business_consistency", "Uma decisão de negócio sustentada pelas fontes ainda precisa de confirmação."
    if q.conflict_ids or q.target_kind == "context_conflict":
        return 2, "field_conflict", "As fontes divergem sobre um campo do trabalho observado."
    if (q.gap_kind or "").startswith("payment_") or any(MONEY.search(ref.get("quote", "")) for ref in q.evidence):
        return 1, "financial_consequence", "A lacuna afeta um registro financeiro sustentado pelas fontes."
    if set(q.missing_links) & {"performer", "handoff"} and q.target_kind != "context":
        return 2, "responsibility_or_handoff", "Falta ligar uma responsabilidade ou repasse ao trabalho observado."
    if q.target_kind == "context":
        return 4, "unidentified_context", "O registro autorizado ainda não foi ligado a uma atividade."
    return 3, "missing_business_context", "Uma conexão do trabalho observado continua sem confirmação."


def rank_question(q, reminder, now):
    tier, _kind, _reason = _tier(q)
    age_days = max(0, (now - q.created_at).total_seconds()) / 86400
    # An unasked supported contradiction is never displaced by a generic gap or
    # reminder. Aging promotes other waiting work within the remaining bands.
    critical = tier <= 0 and not reminder
    aged_band = max(1, tier - int(age_days // 2))
    return (0 if critical else 1, tier if critical else aged_band, reminder, q.created_at, q.id)


FIELD_LABELS = {"trigger": "o início", "performer": "quem executa", "input": "a entrada", "action": "o que é feito",
    "result": "o resultado", "purpose": "a finalidade", "placement": "a etapa", "handoff": "quem recebe o resultado",
    "tracking_record": "o registro de acompanhamento"}


def related_fact(q):
    if q.conflict or q.gap_kind or q.target_kind != "task":
        return None
    field = next((field for field in ASKS if field in q.missing_links), None)
    return next((fact for fact in q.known_facts if fact.get("field") == field and
        fact.get("claim", {}).get("basis") in ("observed", "reported") and
        fact["claim"].get("value") and fact["claim"].get("evidence")), None)


def selection_reason(q, reminder, now, eligible_count):
    _tier_number, kind, reason = _tier(q)
    focus = None
    if q.issues:
        focus = min(q.issues, key=lambda issue: (not bool(MONEY.search(issue.get("description", ""))), issue.get("id", "")))
    evidence = tuple(focus.get("evidence", ())) if focus else q.evidence
    fact = related_fact(q)
    if q.priority_hint:
        evidence += tuple(q.priority_hint["reason"]["evidence"])
    if fact:
        seen = {(ref["source_id"], ref["quote"]) for ref in evidence}
        evidence += tuple(ref for ref in fact["claim"]["evidence"] if (ref["source_id"], ref["quote"]) not in seen)
    evidence = tuple({(ref["source_id"], ref["quote"]): ref for ref in evidence}.values())
    why = {"tier": kind, "reason": reason, "age_seconds": max(0, int((now-q.created_at).total_seconds())),
        "reminder": reminder, "eligible_count": eligible_count,
        "focus_issue_ids": [focus["id"]] if focus and focus.get("id") else list(q.issue_ids),
        "source_ids": sorted({ref["source_id"] for ref in evidence})}
    label = _human_label(q.activity_summary) or "este trabalho"
    if focus:
        why["focus_description"] = focus.get("description", "")
        why["reason"] = "Em «" + label + "», falta decidir: " + _plain(focus.get("description", ""), 220) + "."
    elif q.conflict:
        fields = ", ".join(FIELD_LABELS[field] for field in q.missing_links)
        why["reason"] = "Em «" + label + "», as fontes divergem sobre " + fields + "."
    else:
        fields = ", ".join(FIELD_LABELS[field] for field in q.missing_links)
        why["reason"] = "Em «" + label + "», falta esclarecer " + fields + "."
    if q.priority_hint:
        why["priority_hint"] = q.priority_hint
        why["reason"] = "O fundador priorizou «" + label + "»: " + _plain(q.priority_hint["reason"]["value"], 220) + "."
    if fact:
        why["context_fact"] = fact
        why["reason"] += " Há um fato sustentado no mesmo registro; falta confirmar a ligação com esta atividade."
    return why, evidence


def _clip_words(value, limit):
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    cut = value[:limit]
    return cut.rsplit(" ", 1)[0] if " " in cut else ""


def disagreement_message(name_a, text_a, name_b, text_b) -> str:
    """One short group follow-up when two authorized people disagree."""
    left = " ".join(str(text_a).split()).strip(" .")
    right = " ".join(str(text_b).split()).strip(" .")
    return f"{name_a} respondeu «{left}», e {name_b} respondeu «{right}». Qual dos dois vale?\nPode responder por aqui."


def compose_message(prefix, description, question, closing, limit=400) -> str:
    """The whole description stays. Nothing is replaced by an ellipsis."""
    del limit
    parts = [" ".join(str(part).split()) for part in (prefix, description, question) if part and str(part).strip()]
    return (" ".join(parts) + closing).strip()


def render(items: tuple[RoundItem, ...], slot: datetime, *, founder_name=None) -> str:
    if len(items) != 1:
        raise ValueError("one_semantic_question_per_message")
    item = items[0]
    q = item.question
    context = q.observed_context or {}
    evidence = item.evidence or q.evidence
    quote_text = " ".join(ref.get("quote", "") for ref in evidence)
    focus = (item.why_chosen or {}).get("focus_description") or quote_text
    amounts = list(dict.fromkeys(value.rstrip(".,") for value in MONEY.findall(focus)))
    material = q.gap_kind == "business_consistency" or bool(q.conflict_ids) or q.target_kind == "context_conflict"
    fact = related_fact(q)
    if q.conflict and len(q.missing_links) == 1:
        field = q.missing_links[0]
        claims = [q.conflict.get(key, {}) for key in ("previous", "reported_candidate")] + q.conflict.get("alternatives", [])
        values = list(dict.fromkeys(_plain(claim.get("value"), 55) for claim in claims if claim.get("value")))
        description = "Em «" + _plain(q.activity_summary, 75) + "», há relatos diferentes sobre " + FIELD_LABELS[field] + ": " + "; ".join("«" + value + "»" for value in values[:3]) + "."
        question = ASKS[field] if field == "performer" else "O que deve ser considerado correto sobre " + FIELD_LABELS[field] + "?"
    elif material and len(amounts) == 2:
        description = f"Há dois valores nesse registro: {amounts[0]} e {amounts[1]}."
        question = "Qual valor deve ser considerado?"
    elif material:
        description = "Os relatos não combinam: " + _plain(focus, 170) + "."
        question = "O que deve ser considerado correto nesse caso?"
    elif fact:
        field = fact["field"]
        description = "No mesmo registro, outro trabalho informa " + FIELD_LABELS[field] + ": «" + _plain(fact["claim"]["value"], 120) + "»."
        question = "Isso também se aplica a «" + _plain(q.activity_summary, 80) + "»?"
    elif q.target_kind == "context":
        description = ""
        question = "O que estava sendo feito nesse registro?"
    elif q.target_kind == "answer_assignment":
        description = "Sua resposta ficou registrada: " + _plain(quote_text, 140) + "."
        question = "A qual atividade você se referia?"
    elif q.gap_kind == "task_handoff" and q.prompt.count("?") == 1:
        description = ""
        question = _plain(q.prompt.rstrip("?"), 185) + "?"
    else:
        description = ""
        field = next((field for field in ASKS if field in q.missing_links), "purpose")
        question = ASKS[field]
    record = _record(q)
    name = _plain(founder_name, 60) if founder_name else ""
    prefix = (name + ", " if name else "") + ("sobre " + record + ": " if record else "")
    stamp = context.get("occurred_at") or (evidence[0].get("occurred_at") if evidence else None)
    if stamp:
        try:
            at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(ZoneInfo("America/Sao_Paulo"))
            prefix += f"no registro de {at:%d/%m às %H:%M}"
            prefix += ". "
        except (ValueError, TypeError):
            pass
    application = q.application or {}
    if application.get("status") == "unresolved":
        description = "Sua resposta foi preservada, mas este ponto ainda não ficou esclarecido. " + description
    closing = "\nPode responder por aqui."
    return compose_message(prefix, description, question, closing)
