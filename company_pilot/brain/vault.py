"""Obsidian-compatible notes for the company brain. Local search, no paid service."""
from __future__ import annotations

import re
import unicodedata

from .engine import FIELDS, LABELS
from .readout import shown_person, shown_time


def _fold(value):
    folded = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(char for char in folded if not unicodedata.combining(char))


def _slug(title, identifier):
    words = re.sub(r"[^\wÀ-ÿ]+", " ", title, flags=re.UNICODE).strip()
    short = words[:60].strip() or "nota"
    return f"{short}--{identifier[:8]}"


def _yaml(value):
    text = " ".join(str(value or "").split())
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _confidence(value):
    labels = {
        "pending": "pendente", "resolved": "resolvida", "observed": "observado",
        "reported": "relatado", "inferred": "inferido", "provisional": "provisória",
        "established": "estabelecida",
    }
    text = str(value or "").strip()
    return labels.get(text, text or "sem base")


def _frontmatter(kind, identifier, source, created, updated, confidence):
    rows = {
        "type": kind,
        "id": identifier,
        "fonte": source or "",
        "created": created or "",
        "updated": updated or "",
        "confidence": _confidence(confidence),
    }
    lines = ["---"] + [f"{key}: {_yaml(value)}" for key, value in rows.items()] + ["---", ""]
    return "\n".join(lines)


def _private(source):
    privacy = (source.get("safe_metadata") or {}).get("privacy_disposition")
    return source.get("kind") == "coverage_gap" or privacy in ("personal", "excluded", "withheld")


def _note(path, kind, identifier, title, body, source, created, updated, confidence, links):
    linked = "\n".join(f"- [[{target}]]" for target in links)
    text = _frontmatter(kind, identifier, source, created, updated, confidence)
    text += f"# {title}\n\n{body.strip()}\n"
    if linked:
        text += "\n## Ligações\n\n" + linked + "\n"
    text += "\n## O que aponta para esta nota\n\n"
    return path, text, _slug(title, identifier)


def _span(state, source_ids):
    stamps = []
    for sid in source_ids or []:
        source = state.sources.get(sid) or {}
        at = (source.get("observation") or {}).get("occurred_at")
        if at:
            stamps.append(at)
    if not stamps:
        return "", ""
    stamps.sort()
    return shown_time(stamps[0]), shown_time(stamps[-1])


def write_vault(state):
    """One typed note per captured company item. Personal and withheld captures produce none."""
    files = {}
    titles = {}
    sources = state.sources

    def add(path, kind, identifier, title, body, source, created, updated, confidence, links):
        path, text, slug = _note(path, kind, identifier, title, body, source, created, updated, confidence, links)
        files[path] = text
        titles[slug] = path
        return slug

    people = {}
    for node in state.groups["nodes"].values():
        if node.get("superseded_by") or node["kind"] != "person":
            continue
        evidence = node.get("evidence") or []
        source = evidence[0]["source_id"] if evidence else ""
        if source and source in sources and _private(sources[source]):
            continue
        related = [source] if source else []
        for task in state.tasks.values():
            if (task.get("performer_identity") or {}).get("node_id") == node["id"]:
                related.extend(task.get("evidence_ids") or [])
        created, updated = _span(state, related)
        slug = add(f"notes/pessoas/{_slug(shown_person(node['name']), node['id'])}.md", "pessoa", node["id"], shown_person(node["name"]),
                   "Pessoa observada nas fontes da empresa.", f"sources/{source}.md" if source else "",
                   created, updated or created, "observado", [])
        people[node["id"]] = slug

    capture_slugs = {}
    seen_capture_titles = set()
    for source in sources.values():
        if _private(source) or source.get("kind") != "observation":
            continue
        observed = (source.get("observation") or {}).get("source") or {}
        when = shown_time((source.get("observation") or {}).get("occurred_at"))
        who = shown_person(source.get("actor") or "")
        activity = next((line.split(":", 1)[1].strip() for line in source.get("text", "").splitlines() if line.startswith("Atividade:")), "")
        title = f"Captura de {when or who}" + (f" — {activity}" if activity else "")
        if title in seen_capture_titles:
            title = f"{title} ({source['id'][:4]})"
        seen_capture_titles.add(title)
        quote = source.get("text") or ""
        slug = add(f"notes/capturas/{_slug(title, source['id'])}.md", "captura", source["id"], title,
                   f"Quando: {when}\n\nQuem: {who}\n\nAplicativo: {observed.get('app') or ''}\n\n{quote[:1200]}",
                   f"sources/{source['id']}.md", when, when, "observado", [])
        capture_slugs[source["id"]] = slug
    task_slugs = {}
    for task in state.tasks.values():
        if task.get("superseded_by"):
            continue
        lines = []
        links = []
        performer = task.get("performer_identity") or {}
        if performer.get("node_id") in people:
            links.append(people[performer["node_id"]])
        for field in FIELDS:
            claim = task["fields"].get(field) or {}
            value = claim.get("value") or "Ainda não esclarecido"
            lines.append(f"**{LABELS[field]}:** {value}")
            for ref in claim.get("evidence") or []:
                if ref.get("source_id"):
                    lines.append(f"> {ref.get('quote', '')}")
        for edge in state.groups["edges"].values():
            if not edge.get("active") or task["id"] not in (edge.get("from"), edge.get("to")):
                continue
            other = edge["to"] if edge["from"] == task["id"] else edge["from"]
            other_task = state.tasks.get(other)
            if other_task and not other_task.get("superseded_by"):
                links.append(_slug(other_task["name"], other_task["id"]))
        for sid in task.get("evidence_ids") or []:
            if sid in capture_slugs and capture_slugs[sid] not in links:
                links.append(capture_slugs[sid])
        confidence = "estabelecida" if task["status"] == "established" else "provisória"
        source_id = next(iter(task.get("evidence_ids") or []), "")
        created, updated = _span(state, task.get("evidence_ids"))
        slug = add(f"notes/tarefas/{_slug(task['name'], task['id'])}.md", "tarefa", task["id"], task["name"],
                   "\n\n".join(lines), f"sources/{source_id}.md" if source_id else "",
                   created, updated or created, confidence, links)
        task_slugs[task["id"]] = slug
        for version in range(1, task["version"] + 1):
            record = f"records/tasks/{task['id']}/v{version:06}.json"
            version_title = f"{task['name']} — versão {version}"
            version_id = f"{task['id']}-v{version}"
            add(f"notes/versoes/{_slug(version_title, version_id)}.md", "versao", version_id, version_title,
                f"Versão {version} desta atividade. O registro completo permanece no arquivo exportado `{record}`.",
                record, created, updated or created, confidence, [slug])

    tools = {}
    sheets = {}
    for source in sources.values():
        if _private(source) or source.get("kind") != "observation":
            continue
        observed = (source.get("observation") or {}).get("source") or {}
        app = observed.get("app") or ""
        if app and app not in tools:
            tools[app] = source
        ref = observed.get("record_ref") or ""
        if re.search(r"planilha|cronograma|sheet", f"{ref} {source.get('text', '')}", re.I):
            first = (source.get("text") or "").splitlines()
            parts = [piece.strip(" -") for piece in (first[0].split("—") if first else []) if piece.strip(" -")]
            label = parts[1] if len(parts) >= 2 else "Planilha citada"
            sheets.setdefault(label, source)
    for app, source in tools.items():
        when = shown_time((source.get("observation") or {}).get("occurred_at"))
        tool_id = "tool-" + source["id"]
        add(f"notes/ferramentas/{_slug(app, tool_id)}.md", "ferramenta", tool_id, app,
            "Ferramenta vista numa captura da empresa.", f"sources/{source['id']}.md",
            when, when, "observado", [capture_slugs[source["id"]]] if source["id"] in capture_slugs else [])
    for label, source in sheets.items():
        when = shown_time((source.get("observation") or {}).get("occurred_at"))
        sheet_id = "sheet-" + source["id"]
        add(f"notes/planilhas/{_slug(label, sheet_id)}.md", "planilha", sheet_id, label,
            "Planilha citada numa fonte da empresa.", f"sources/{source['id']}.md",
            when, when, "observado", [capture_slugs[source["id"]]] if source["id"] in capture_slugs else [])
    for record in state.company_records.values():
        add(f"notes/documentos/{_slug(record['record_id'], record.get('current_source_id', record['record_id']))}.md",
            "documento", record.get("current_source_id") or record["record_id"], record["record_id"],
            f"Sistema: {record['source_system']}. Revisão: {record['revision']}.",
            f"sources/{record['current_source_id']}.md", "", "", "observado", [])
    grouped = {}
    for question in state.questions.values():
        if question.get("privacy_disposition") in ("personal", "excluded", "withheld"):
            continue
        grouped.setdefault(question.get("task_id") or question["id"], []).append(question)
    for key, questions in grouped.items():
        task = state.tasks.get(key)
        title = "Perguntas sobre " + (task["name"] if task else (questions[0].get("activity_summary") or "o trabalho"))
        body = "\n".join(f"- {item.get('prompt') or 'Pergunta em aberto'}" for item in questions)
        created, updated = _span(state, task.get("evidence_ids") if task else [])
        links = [task_slugs[key]] if key in task_slugs else []
        add(f"notes/perguntas/{_slug(title, key)}.md", "pergunta", key + "-perguntas", title,
            body, f"views/questions/{questions[0]['id']}.md", created, updated or created, questions[0].get("status") or "pendente", links)
    for source in sources.values():
        if source.get("kind") not in ("answer", "correction", "message") or _private(source):
            continue
        answer_title = f"Resposta de {shown_person(source.get('speaker') or '')}"
        related = []
        for qid in source.get("question_ids") or []:
            question = state.questions.get(qid) or {}
            task = state.tasks.get(question.get("task_id"))
            if task:
                related.extend(task.get("evidence_ids") or [])
        created, updated = _span(state, related)
        add(f"notes/respostas/{_slug(answer_title, source['id'])}.md", "resposta", source["id"],
            answer_title, source.get("text") or "",
            f"sources/{source['id']}.md", created, updated or created, "relatado", [])

    # Backlinks from the wikilinks already written.
    incoming = {slug: [] for slug in titles}
    for path, text in files.items():
        for slug in re.findall(r"\[\[([^\]]+)\]\]", text):
            if slug in incoming and titles[slug] != path:
                incoming[slug].append(path)
    for slug, path in titles.items():
        listed = "\n".join(f"- [[{_slug_from_path(other, titles)}]]" for other in sorted(set(incoming[slug])))
        files[path] = files[path] + (listed + "\n" if listed else "Nenhuma outra nota aponta para esta ainda.\n")

    index = ["# Índice do cérebro da empresa\n", "Notas em Markdown com ligações. Abra esta pasta num cofre Obsidian.\n"]
    for path in sorted(files):
        if path.startswith("notes/") and path.endswith(".md"):
            title = next((line[2:].strip() for line in files[path].splitlines() if line.startswith("# ")), path)
            index.append(f"- [{title}]({path[len('notes/'):]})\n")
    files["notes/indice.md"] = "".join(index)
    files["notes/grafo.md"] = _graph(state, task_slugs)
    files["notes/relacoes.md"] = _relations(state, {**task_slugs, **people})
    return files, task_slugs


def _slug_from_path(path, titles):
    for slug, note in titles.items():
        if note == path:
            return slug
    return path


def _graph(state, task_slugs):
    lines = ["# Grafo do trabalho\n", "```mermaid", "flowchart LR"]
    seen = set()
    for edge in state.groups["edges"].values():
        if not edge.get("active") or edge.get("from") not in task_slugs or edge.get("to") not in task_slugs:
            continue
        left = state.tasks[edge["from"]]["name"].replace('"', "'")[:40]
        right = state.tasks[edge["to"]]["name"].replace('"', "'")[:40]
        key = (left, edge["kind"], right)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'  A{len(seen)}["{left}"] -->|repassa| B{len(seen)}["{right}"]')
        if len(seen) >= 40:
            break
    if len(lines) == 3:
        lines.append("  vazio[Nenhuma ligação entre atividades ainda]")
    lines.append("```\n")
    return "\n".join(lines) + "\n"


def _relations(state, slugs):
    lines = ["# Índice de relações\n", "Cada linha cita as notas ligadas. A nota de cada atividade guarda a fonte.\n"]
    for edge in state.groups["edges"].values():
        if not edge.get("active"):
            continue
        left = slugs.get(edge.get("from"))
        right = slugs.get(edge.get("to"))
        if not left or not right:
            continue
        kinds = {"contains": "inclui", "performed_by": "executada por", "handoff": "repassa para",
                 "depends_on": "depende de", "tracks": "acompanhada em", "pays_for": "paga por"}
        lines.append(f"- [[{left}]] — {kinds.get(edge['kind'], 'ligação')} — [[{right}]]")
    if len(lines) == 2:
        lines.append("Nenhuma relação registrada ainda.")
    return "\n".join(lines) + "\n"


def search_notes(files, query, limit=8, ranker=None):
    """Local overlap search. A ranker may use the founder's own model; none is called by default."""
    tokens = [token for token in re.findall(r"\w+", _fold(query)) if len(token) >= 3]
    if not tokens:
        return []
    hits = []
    for path, text in files.items():
        if not str(path).startswith("notes/") or not str(path).endswith(".md"):
            continue
        folded = _fold(text)
        score = sum(folded.count(token) for token in tokens)
        if not score:
            continue
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path)
        excerpt = " ".join(text.split())[:240]
        hits.append({"path": path, "title": title, "score": score, "excerpt": excerpt})
    hits.sort(key=lambda item: (-item["score"], item["path"]))
    hits = hits[:limit]
    if ranker is not None:
        return ranker(query, hits)
    return hits
