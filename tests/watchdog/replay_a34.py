"""Replay the independent a34 probes without changing their original files.

Only import/output root, report label and nullable archival readback are adapted.
The latter records a correctly refused transfer as zero published archive bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    source_probe = args.independent_root / "source-spec" / "probe.py"
    archive_probe = args.independent_root / "standards" / "probe_archive_gates.py"
    original = source_probe.read_text()
    code = original.replace("SNAP=Path('/private/tmp/company-pilot-root72df443-review')", "SNAP=Path(" + repr(str(repo)) + ")")
    code = code.replace("root='72df443'", "root='a36-working-source-hashes'")
    old = "            with archive.open(record['archive']) as stream:archived=stream.read()"
    new = "            archived=b''\n            if record is not None:\n                with archive.open(record['archive']) as stream:archived=stream.read()"
    if code == original or old not in code:
        raise ValueError("independent_probe_shape_changed")
    code = code.replace(old, new)
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="a36-independent-") as folder:
        script = Path(folder) / "probe.py"
        script.write_text(code)
        source = subprocess.run([sys.executable, str(script), "--assert-desired"], capture_output=True, text=True)
        (args.output / "source-probe.stdout").write_text(source.stdout)
        (args.output / "source-probe.stderr").write_text(source.stderr)
        receipt = script.with_name("receipt.json")
        if receipt.exists(): (args.output / "source-probe.json").write_bytes(receipt.read_bytes())
    gates = subprocess.run([sys.executable, str(archive_probe), str(repo)], capture_output=True, text=True)
    (args.output / "archive-probe.stdout").write_text(gates.stdout)
    (args.output / "archive-probe.stderr").write_text(gates.stderr)
    owned = [repo / "company_pilot" / "watchdog" / name for name in ("archive.py", "catalog.py", "drive.py", "__main__.py")]
    report = {"source_probe_exit": source.returncode, "archive_probe_exit": gates.returncode,
              "independent_probe_hashes": {str(p.relative_to(args.independent_root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (source_probe, archive_probe)},
              "owned_source_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in owned},
              "adaptations": ["import/source-hash root", "report root label", "absent refused archive is recorded as zero bytes, without dereference"],
              "boundary": "E1 local fixtures only; no provider/network/model/native actions"}
    (args.output / "replay.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if source.returncode == gates.returncode == 0 else 1


if __name__ == "__main__": raise SystemExit(main())
