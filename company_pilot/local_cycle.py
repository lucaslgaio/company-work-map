"""Read captures the founder supplies. Nothing is sent and nothing is invented."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from company_pilot.brain import Brain, LocalStore
from company_pilot.brain.readout import shown_time
from company_pilot.nora.presentation import compose_message
from company_pilot.offline_analysis import analyze_observation

EMPTY_MESSAGE = """Ainda não há captura para ler.

Grave as janelas da sua empresa num arquivo JSON e rode de novo, na raiz do repositório:

python3 -m company_pilot.local_cycle --root "$PWD/.company-pilot-local/cycle" --captures "$PWD/.company-pilot-local/capturas.json" --company "NOME_DA_EMPRESA" --founder "SEU_NOME"

Cada item do arquivo precisa de when, app, record_ref e text, com o texto real da janela. Nada foi inventado e nenhuma mensagem foi enviada.
"""


def export_founder_files(root, files):
    """Write every page the founder is told to open, plus the records those pages cite."""
    root = Path(root)
    written = []
    for path, content in files.items():
        if path == "README.md" or path.startswith(("views/", "notes/", "records/", "ledger/", "sources/")):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(str(target))
    return written


def _observation(capture):
    text = capture["text"]
    return {"schema_version": 1, "event_id": str(uuid4()), "device_id": str(uuid4()),
            "occurred_at": capture["when"], "policy_version": 1,
            "source": {"kind": "active_window", "app": capture["app"], "record_ref": capture["record_ref"], "origin": "local-cycle"},
            "classification": {"label": "company", "reason": "janela informada pelo fundador", "context_version": 1},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True}


def _scope(brain, job_id, decision):
    snap = brain.snapshot()
    job = next(item for item in snap["pending_jobs"] if item["id"] == job_id)
    ids = {task["task_id"] for task in decision["tasks"] if task.get("task_id")}
    for task in decision["tasks"]:
        for link in task.get("links") or []:
            ids.add(link["target_id"])
    chosen = [task for task in snap["tasks"] if task["id"] in ids]
    sources = set(job.get("source_ids") or [])
    for task in chosen:
        sources.update(task.get("evidence_ids") or [])
    if not chosen:
        return None
    return {"tasks": [{"id": task["id"], "version": task["version"]} for task in chosen],
            "nodes": [], "edges": [], "source_ids": sorted(sources)}


def _run_decision(brain, job_id, decision):
    if decision is None:
        return None
    return brain.apply_analysis(job_id, decision,
                                metadata={"execution_provider": "offline-capture", "execution_model": "offline-capture",
                                          "execution_origin": "local_cycle", "execution_identity_basis": "capture_text"},
                                map_scope=_scope(brain, job_id, decision))


def nora_preview(founder_name, source, task_name, question):
    """WhatsApp-shaped text from this capture's own clock. It is not sent."""
    when = shown_time((source.get("observation") or {}).get("occurred_at"))
    app = ((source.get("observation") or {}).get("source") or {}).get("app") or "planilha"
    ask = question if str(question).rstrip().endswith("?") else "Quem executa esse trabalho?"
    return compose_message(f"{founder_name}, ", f"Vi em {when}, no {app}, que «{task_name}» ficou sem responsável.", ask, "\nPode responder por aqui.")


def run_local_cycle(root, captures=None, company_name=None, founder_name=None):
    """Ingest only the captures given. An empty list writes nothing and invents nothing."""
    if not captures:
        print(EMPTY_MESSAGE, end="")
        return {"captures": 0, "message": EMPTY_MESSAGE, "paths": [], "nora_message": ""}
    if not company_name or not founder_name:
        raise ValueError("company_and_founder_required")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    brain = Brain(LocalStore(root, actor=founder_name))
    brain.initialize(company_name, synthetic=True)
    messages = []
    for capture in captures:
        incoming = brain.ingest_observation(_observation(capture))
        source = next(item for item in brain.snapshot()["sources"] if item["id"] == incoming["observation_id"])
        decision = analyze_observation(source["text"], source["id"], brain.snapshot()["tasks"])
        _run_decision(brain, incoming["job_id"], decision)
        task = next((item for item in brain.snapshot()["tasks"] if not item.get("superseded_by")
                     and source["id"] in (item.get("evidence_ids") or [])), None)
        if task is None:
            continue
        performer = (task["fields"].get("performer") or {}).get("value")
        pending = [item for item in brain.snapshot()["questions"]
                   if item.get("task_id") == task["id"] and item["status"] == "pending" and "performer" in item.get("missing_links", [])]
        if pending and not performer:
            messages.append(nora_preview(founder_name, source, task["name"], "Quem executa esse trabalho?"))
    files = brain.store.read().files
    written = export_founder_files(root, files)
    paths = [str(root / name) for name in ("views/mapa.md", "views/insights.md", "views/leitura-segunda.md", "notes/indice.md")]
    return {"captures": len(captures), "company": company_name, "nora_message": "\n\n".join(messages),
            "paths": paths, "files_on_disk": written, "message": ""}


def main():
    parser = argparse.ArgumentParser(description="Lê capturas que você informar. Não envia mensagem, não liga captura e não inventa empresa.")
    parser.add_argument("--root", required=True, help="Pasta do cérebro local.")
    parser.add_argument("--captures", help="Arquivo JSON com as janelas da sua empresa.")
    parser.add_argument("--company", help="Nome da sua empresa, o mesmo usado no init.")
    parser.add_argument("--founder", help="Seu nome, como deve aparecer na mensagem.")
    args = parser.parse_args()
    captures = None
    if args.captures:
        captures = json.loads(Path(args.captures).read_text(encoding="utf-8"))
        if not isinstance(captures, list):
            raise SystemExit("O arquivo de capturas precisa ser uma lista. Nada foi lido.")
    try:
        result = run_local_cycle(args.root, captures, args.company, args.founder)
    except ValueError:
        raise SystemExit("Informe --company e --founder junto com --captures. O ciclo não escolhe esses nomes.")
    if result["captures"] == 0:
        return
    if result["nora_message"]:
        print("Mensagem da Nora, no formato do WhatsApp, não enviada:\n")
        print(result["nora_message"])
        print()
    else:
        print("Nenhuma pergunta nova saiu destas capturas. Nenhuma mensagem foi enviada.\n")
    print("Abra estes arquivos:\n")
    for path in result["paths"]:
        print(path)


if __name__ == "__main__":
    main()
