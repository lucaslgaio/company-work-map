"""Founder-facing lanes, insights and the Monday readout. Portuguese, source-backed."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

_MONTHS = {1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril", 5: "maio", 6: "junho",
           7: "julho", 8: "agosto", 9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro"}


def shown_person(actor):
    """A name the founder recognizes. Internal ids stay off the page."""
    text = str(actor or "").strip()
    if not text or text in {"fixture-founder", "local_fixture"}:
        return "pessoa da equipe"
    if " " not in text and ("-" in text or re.fullmatch(r"[0-9a-f]{16,}", text)):
        return "pessoa da equipe"
    return text


def shown_time(value):
    """São Paulo wall time, in plain Portuguese. Raw UTC stays out of the page."""
    if not value:
        return ""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
    local = stamp.astimezone(ZoneInfo("America/Sao_Paulo"))
    return f"{local.day} de {_MONTHS[local.month]} de {local.year}, às {local:%H:%M}"


def chain_label(left, right):
    blob = f"{left} {right}".casefold()
    if "material" in blob or "compra" in blob:
        return "Compra"
    if "medi" in blob:
        return "Medição"
    if "cronograma" in blob or "andamento" in blob or "relatório" in blob or "relatorio" in blob:
        return "Andamento"
    if "aditivo" in blob or "escopo" in blob:
        return "Escopo"
    return "Repasse"


def _when(state, source_id):
    source = state.sources.get(source_id) or {}
    observed = (source.get("observation") or {}).get("occurred_at")
    return observed or source.get("received_at") or ""


def _person(state, task):
    identity = task.get("performer_identity") or {}
    node = state.groups["nodes"].get(identity.get("node_id"))
    if node and not node.get("superseded_by"):
        return node["name"]
    value = (task["fields"].get("performer") or {}).get("value")
    return value or "Pessoa ainda não esclarecida"


def lanes(state):
    groups = {}
    for task in state.tasks.values():
        if task.get("superseded_by"):
            continue
        groups.setdefault(_person(state, task), []).append(task)
    lines = ["# Mapa por pessoa\n", "Cada faixa lista o trabalho atribuído a uma pessoa. O cartão guarda a fonte.\n"]
    if not groups:
        lines.append("Ainda não há atividades para distribuir em faixas.\n")
    for name in sorted(groups):
        lines.append(f"\n## {shown_person(name) if name != 'Pessoa ainda não esclarecida' else name}\n")
        for task in groups[name]:
            result = (task["fields"].get("result") or {}).get("value") or "resultado ainda não esclarecido"
            lines.append(f"- [{task['name']}](tasks/{task['id']}.md): {result}")
    lines.append("\n## Cadeias\n")
    chains = []
    for edge in state.groups["edges"].values():
        if not edge.get("active") or edge.get("kind") != "handoff":
            continue
        origin = state.tasks.get(edge.get("from"))
        target = state.tasks.get(edge.get("to"))
        if not origin or not target or origin.get("superseded_by") or target.get("superseded_by"):
            continue
        chains.append((chain_label(origin["name"], target["name"]), origin["name"], target["name"]))
    if chains:
        for label, origin, target in chains:
            lines.append(f"- {label}: {origin} → {target}")
    else:
        lines.append("Nenhuma ligação entre atividades ainda.")
    return "\n".join(lines) + "\n"


def _observation_stamps(state, source_ids):
    stamps = []
    for sid in source_ids or []:
        source = state.sources.get(sid) or {}
        if source.get("kind") != "observation":
            continue
        at = (source.get("observation") or {}).get("occurred_at")
        if at:
            stamps.append(at)
    return sorted(stamps)


def _handoff_load(state):
    """Count who hands work on. The receiver is the other end of the same link."""
    counts = {}
    for edge in state.groups["edges"].values():
        if not edge.get("active") or edge.get("kind") != "handoff":
            continue
        task = state.tasks.get(edge.get("from"))
        if not task or task.get("superseded_by"):
            continue
        person = _person(state, task)
        if person == "Pessoa ainda não esclarecida":
            continue
        counts[person] = counts.get(person, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _repasses(count):
    return "1 repasse já ligado" if count == 1 else f"{count} repasses já ligados"


def _slowest(state):
    slowest = None
    for task in state.tasks.values():
        if task.get("superseded_by"):
            continue
        stamps = _observation_stamps(state, task.get("evidence_ids"))
        if len(stamps) < 2:
            continue
        span = stamps[-1] > stamps[0]
        if not span:
            continue
        if slowest is None or stamps[-1] > slowest[2] or (stamps[-1] == slowest[2] and stamps[0] < slowest[1]):
            slowest = (task, stamps[0], stamps[-1])
    return slowest


def _field_already_known(task, field, linked_tasks):
    if field == "handoff" and task and task["id"] in linked_tasks:
        return True
    claim = ((task or {}).get("fields") or {}).get(field) or {}
    return bool(claim.get("value"))


def questions_nora_would_ask(state, limit=3):
    """The few questions Nora would send. Empty schema fields are not a list."""
    linked = set()
    for edge in state.groups["edges"].values():
        if edge.get("active") and edge.get("kind") == "handoff":
            linked.add(edge.get("from"))
            linked.add(edge.get("to"))
    chosen = []
    for question in state.questions.values():
        if question["status"] in ("resolved", "superseded", "awaiting_person"):
            continue
        task = state.tasks.get(question.get("task_id"))
        fields = question.get("missing_fields") or []
        if task and fields and all(_field_already_known(task, field, linked) for field in fields):
            continue
        prompt = question.get("prompt") or ""
        if "também se aplica" in prompt.casefold() or "tambem se aplica" in prompt.casefold():
            continue
        gap = question.get("gap_kind") or ""
        if gap == "business_consistency" or question.get("issue_ids"):
            tier = 0
        elif "performer" in fields and task and not _field_already_known(task, "performer", linked):
            tier = 1
        elif "handoff" in fields and task and task["id"] not in linked:
            tier = 2
        elif gap.startswith("payment_"):
            tier = 2
        else:
            continue
        chosen.append((tier, question.get("activity_summary") or "", prompt, question))
    chosen.sort(key=lambda item: (item[0], item[1]))
    return chosen[:limit]


def insights(state):
    lines = ["# Leitura do trabalho\n", "Os horários abaixo são os das capturas, no fuso de São Paulo. O relógio de hoje não entra nesta conta.\n"]
    load = _handoff_load(state)
    lines.append("\n## Quem mais repassa trabalho\n")
    if load:
        leaders = [item for item in load if item[1] == load[0][1]]
        if len(leaders) == 1:
            name, count = leaders[0]
            lines.append(f"- {name} entrega {_repasses(count)}. É quem mais passa trabalho adiante.")
        else:
            names = " e ".join(name for name, _count in leaders)
            lines.append(f"- {names} empatam, com {_repasses(leaders[0][1])} cada um.")
        rest = [item for item in load if item not in leaders][:3]
        if rest:
            lines.append("- Em seguida: " + "; ".join(f"{person}, {_repasses(total)}" for person, total in rest) + ".")
    else:
        lines.append("Ainda não há repasse ligado, então ninguém carrega uma cadeia.")
    slow = _slowest(state)
    lines.append("\n## Passo mais longo\n")
    if slow:
        task, start, end = slow
        lines.append(f"- {task['name']} é o passo com maior intervalo entre capturas: de {shown_time(start)} a {shown_time(end)}. [Abrir cartão](tasks/{task['id']}.md).")
    else:
        lines.append("Nenhuma atividade tem duas capturas com horário, então não há passo mais longo para apontar.")
    lines.append("\n## Perguntas ainda abertas\n")
    asking = questions_nora_would_ask(state)
    if asking:
        lines.append("A Nora mandaria só estas:")
        for _tier, name, prompt, _question in asking:
            lines.append(f"- {name}: {prompt}")
    else:
        lines.append("A Nora não tem uma pergunta nova para mandar. Campo vazio de formulário não entra aqui, e um repasse que o mapa já mostra não é perguntado de novo.")
    return "\n".join(lines) + "\n"


def monday_readout(state):
    company = state.company.get("name") or "a empresa"
    lines = [f"# Leitura de segunda — {company}\n",
             "Resumo para o fundador, feito só com o que já está no mapa. Cada item aponta para o cartão ou para a fonte.\n"]
    active = [task for task in state.tasks.values() if not task.get("superseded_by")]
    load = _handoff_load(state)
    lines.append("\n## Quem carrega os repasses\n")
    if load:
        leaders = [item for item in load if item[1] == load[0][1]]
        if len(leaders) == 1:
            lines.append(f"- {leaders[0][0]} é quem mais passa trabalho adiante: {_repasses(leaders[0][1])}.")
        else:
            names = " e ".join(name for name, _count in leaders)
            lines.append(f"- {names} empatam como quem mais passa trabalho adiante: {_repasses(leaders[0][1])} cada um.")
    else:
        lines.append("Ninguém carrega um repasse ainda.")
    slow = _slowest(state)
    lines.append("\n## O passo mais longo entre capturas\n")
    if slow:
        task, start, end = slow
        lines.append(f"- {task['name']}, de {shown_time(start)} a {shown_time(end)}. [Cartão](tasks/{task['id']}.md).")
    else:
        lines.append("Ainda não há duas capturas datadas na mesma atividade.")
    lines.append("\n## O que ainda pede uma frase\n")
    asking = questions_nora_would_ask(state)
    if asking:
        lines.append("A Nora mandaria só estas:")
        for _tier, name, prompt, _question in asking:
            lines.append(f"- {name}: {prompt}")
    else:
        lines.append("A Nora não tem uma pergunta nova para mandar. Campo vazio de formulário não entra aqui.")
    lines.append("\n## Repasses\n")
    handoffs = [edge for edge in state.groups["edges"].values() if edge.get("active") and edge.get("kind") == "handoff" and edge.get("from") in state.tasks and edge.get("to") in state.tasks]
    if handoffs:
        for edge in handoffs[:12]:
            lines.append(f"- {state.tasks[edge['from']]['name']} → {state.tasks[edge['to']]['name']}")
    else:
        lines.append("Nenhum repasse entre atividades foi confirmado ainda.")
    lines.append("\n## Fontes\n")
    lines.append("Os cartões e as notas do cérebro citam o trecho de onde cada fato saiu.")
    return "\n".join(lines) + "\n"
