"""Capture actual synthetic combined-flow artefacts from the acceptance checks."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = runpy.run_path(str(ROOT / "tests/cli/test_outcome_integration.py"))["OutcomeIntegrationTests"]
    outcomes = {}
    for name in ("test_requested_effective_access_individual_control_and_unsupported_choice",
                 "test_source_versions_actual_brain_scheduled_question_and_answer_to_map"):
        case, result = cases(name), unittest.TestResult()
        case.run(result)
        if not result.wasSuccessful():
            for _case, error in result.errors + result.failures:
                print(error, file=sys.stderr)
            return 1
        outcomes[name] = case.proof
    args.output.mkdir(parents=True, exist_ok=True)
    for name, proof in outcomes.items():
        (args.output / (name + ".json")).write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
        if "task_markdown" in proof:
            (args.output / "workshop-task.md").write_text(proof["task_markdown"])
            (args.output / "nora-message.txt").write_text(proof["nora_message"] + "\n")
    files = [path for path in ROOT.rglob("*") if path.is_file() and
             path.relative_to(ROOT).parts[0] in {"company_pilot", "contracts", "install", "tests", "tools"} and
             "__pycache__" not in path.parts and path.suffix != ".pyc"]
    pin = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt = {"source_pin": pin, "evidence_level": "E1", "cases": list(outcomes),
               "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
               "not_proven": ["real model decisions", "native/background capture", "real source/storage/account/channel access", "corpus scale", "human acceptance"]}
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"source_pin": pin, "actual_case_count": len(outcomes), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
