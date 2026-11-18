"""Run the independent a34 probe with only product/output paths parameterized.

python3 THIS_FILE REVIEWER_VARIANTS PRODUCT_ROOT NEW_OUTPUT_DIRECTORY
Retains original/runtime source hashes; assertions and test cases are unchanged.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

source, product, output = [Path(value).resolve() for value in sys.argv[1:4]]
original = source.read_text()
runtime, count = re.subn(r"^PRODUCT = Path\(.*\)$", lambda _: "PRODUCT = Path(" + repr(str(product)) + ")", original, flags=re.M)
assert count == 1
runtime, count = re.subn(r"^OUT = Path\(__file__\).resolve\(\).parent$", lambda _: "OUT = Path(" + repr(str(output)) + ")", runtime, flags=re.M)
assert count == 1
output.mkdir(parents=True, exist_ok=False, mode=0o700)
script = output / "parameterized-variants.py"
script.write_text(runtime)
result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
(output / "stdout.txt").write_text(result.stdout)
(output / "stderr.txt").write_text(result.stderr)
receipt = {"original_probe": str(source), "original_probe_sha256": sha256(source.read_bytes()).hexdigest(),
    "executed_product": str(product), "runtime_probe_sha256": sha256(script.read_bytes()).hexdigest(),
    "parameterization": ["PRODUCT path", "OUT path"], "assertions_changed": False,
    "historical_target_label_inside_original_receipt_retained": True,
    "exit_code": result.returncode,
    "source_sha256": {name: sha256((product / name).read_bytes()).hexdigest() for name in
        ["company_pilot/operator.py", "company_pilot/nora/engine.py", "company_pilot/brain/messages.py"]}}
(output / "execution.json").write_text(json.dumps(receipt, indent=2) + "\n")
if (output / "variants-receipt.json").exists():
    receipt["results"] = json.loads((output / "variants-receipt.json").read_text())["results"]
print(json.dumps(receipt, indent=2))
if result.returncode and not (output / "variants-receipt.json").exists():
    print(result.stderr)
raise SystemExit(result.returncode)
