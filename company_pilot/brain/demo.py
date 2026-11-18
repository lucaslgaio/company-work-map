"""Opt-in real interpretation cycle on explicitly synthetic local evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from .engine import Brain
from .stores import LocalStore
from company_pilot.inference import ClaudeInterpreter, SemanticWorker


def run_cycle(root, interpreter):
    brain = Brain(LocalStore(root, actor="synthetic-operator"))
    brain.initialize("Agência de tradução — exemplo sintético", synthetic=True)
    text = ("Joana recebeu a fatura fictícia SYN-INV-24, inseriu sua referência e valor "
            "no formulário de transferência e salvou o comprovante SYN-REC-07. "
            "A captura não explica a finalidade do pagamento, em que processo se encaixa, "
            "quem recebe o resultado nem onde o saldo é acompanhado.")
    event_id = str(uuid.uuid4())
    received = brain.ingest_observation({"schema_version": 1, "event_id": event_id,
        "device_id": str(uuid.uuid4()), "occurred_at": "2026-11-16T12:00:00+00:00",
        "policy_version": 1, "source": {"kind": "synthetic", "app": "Synthetic Fixture", "record_ref": "SYN-INV-24", "origin": "fixture"},
        "classification": {"label": "company", "reason": "exemplo sintético", "context_version": 1},
        "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True})
    worker = SemanticWorker(brain, interpreter)
    first_run = worker.run_once()
    if first_run["status"] != "applied":
        return {"synthetic": True, "evidence_level": "E1", "status": "pending", "first_run": first_run}
    first = brain.snapshot()
    task = first["tasks"][0]
    if task["status"] != "provisional" or not first["questions"]:
        raise ValueError("synthetic_unknown_context_was_invented")
    response = ("A finalidade dessa transferência é pagar a tradução do projeto fictício Aurora. "
                "A tarefa fica no processo de pagamentos a fornecedores. Depois de salvar o comprovante, "
                "Joana o envia ao contador. O saldo do projeto é acompanhado na planilha fictícia de custos Aurora. "
                "Essas são as conexões de negócio que faltavam; os fatos já observados sobre entrada, ação, resultado, "
                "início e executora continuam válidos.")
    answer = brain.record_answer([q["id"] for q in first["questions"]], response, str(uuid.uuid4()))
    second_run = worker.run_once()
    final = brain.snapshot()
    current = next(t for t in final["tasks"] if t["id"] == task["id"])
    success = second_run["status"] == "applied" and current["status"] == "established"
    return {"synthetic": True, "evidence_level": "E1", "scope": "real model + local brain; no OS capture, remote storage or Nora channel",
            "status": "passed" if success else "pending", "first_run": first_run, "second_run": second_run,
            "observation_id": received["observation_id"], "answer_id": answer["answer_id"], "task_id": task["id"],
            "initial_status": task["status"], "final_status": current["status"], "initial_missing_fields": task["missing_fields"],
            "remaining_missing_fields": current["missing_fields"], "versions_retained": sorted(path for path in brain.store.read().files if path.startswith("records/tasks/")),
            "model_metadata": interpreter.last_metadata, "pending_jobs": final["pending_jobs"], "final_task": current}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    receipt = run_cycle(args.directory, ClaudeInterpreter())
    path = Path(args.receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
