"""Read-only shared E14 proof against a frozen copy of the core owner's candidate."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    source = args.brain_root / "company_pilot"
    files = [*sorted((source / "brain").glob("*.py")), *sorted((source / "inference").glob("*.py"))]
    frozen = {str(p.relative_to(source)): p.read_bytes() for p in files}
    with tempfile.TemporaryDirectory(prefix="grok2-desktop-core-") as folder:
        snapshot = Path(folder)
        shutil.copytree(repo / "company_pilot", snapshot / "company_pilot", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(repo / "install", snapshot / "install", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name, data in frozen.items():
            (snapshot / "company_pilot" / name).write_bytes(data)
        if any(p.read_bytes() != frozen[str(p.relative_to(source))] for p in files):
            raise ValueError("core_candidate_changed_during_snapshot")
        result = subprocess.run([sys.executable, str(repo / "tests/desktop/grok2_proof.py"), "--output", str(args.output.resolve()), "--require-canonical"],
            cwd=snapshot, env={**os.environ, "PYTHONPATH": str(snapshot)}, text=True, capture_output=True)
    if args.output.exists():
        report = json.loads(args.output.read_text())
        report["composition"] = {"kind": "frozen read-only WIP composition, not an integrated/accepted product pin",
            "core_files_sha256": {name: sha256(data).hexdigest() for name, data in frozen.items()},
            "stable_snapshot": True, "exit_code": result.returncode}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__": raise SystemExit(main())
