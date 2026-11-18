"""E1 route-setup proof against an isolated copy of the actual composed root SDK.

Usage: python3 composed_setup_probe.py PRODUCT_ROOT
Copies source code only, overlays this branch's Nora package, uses LocalStore with
synthetic=True and forbids inference imports. No account, gateway or sender runs.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PROGRAM = r'''
import builtins
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from company_pilot.brain import Brain, LocalStore
from company_pilot.operator import write_private_json
from company_pilot.nora.setup import main

root = Path.cwd() / "fixture"
brain = Brain(LocalStore(root / "brain"))
brain.initialize("Empresa sintética de vínculo Nora", synthetic=True)
company = brain.snapshot()["company"]
assert company["synthetic"] is True
config_path, route_path = root / "config.json", root / "route.json"
config = {"schema_version": 1, "mode": "synthetic", "store": {"kind": "local", "root": str(root / "brain")},
          "nora": {"enabled": False, "route_verified": False, "openclaw_executable": "/not-executed/openclaw"}}
write_private_json(config_path, config)
route = {"company_id": company["id"], "id": str(uuid4()), "revision": 1,
         "account": "synthetic-account", "conversation": "100000000000@g.us",
         "founder_sender": "synthetic-native-founder", "founder_actor_id": company["founders"][0]}
write_private_json(route_path, route)
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if "inference" in name.split("."):
        raise AssertionError("Setup must not import any interpreter")
    return original_import(name, *args, **kwargs)
def invoke(*args):
    out, err = StringIO(), StringIO()
    with patch("builtins.__import__", side_effect=guarded_import):
        code = main(["--config", str(config_path), *args], out=out, err=err)
    assert not err.getvalue(), err.getvalue()
    value = json.loads(out.getvalue())
    assert "synthetic-native-founder" not in out.getvalue()
    assert value["message_sent"] is False and value["service_started"] is False
    return code, value
event1 = str(uuid4())
args = ["bind", "--route-file", str(route_path), "--request-reference", "fixture:request-1", "--event-id", event1]
code, result = invoke(*args)
assert code == 0 and result["state"] == "prepared_inactive"
assert brain.snapshot()["nora"]["route"]["enabled"] is False
head = brain.snapshot()["head"]
assert invoke(*args)[1]["state"] == "already_bound"
assert brain.snapshot()["head"] == head
route.update(revision=2, enabled=True)
write_private_json(route_path, route)
event2 = str(uuid4())
code, result = invoke("bind", "--route-file", str(route_path), "--request-reference", "fixture:request-2",
    "--event-id", event2, "--enable", "--verification-reference", "fixture:binding-proof")
assert code == 0 and result["state"] == "bound"
assert json.loads(config_path.read_text())["nora"]["enabled"] is True
assert brain.snapshot()["nora"]["route"]["enabled"] is True
# Simulate loss of the response AFTER the actual canonical SDK commits its ledger.
route.update(revision=3, enabled=False)
write_private_json(route_path, route)
event3 = str(uuid4())
real_transact = Brain.transact_nora
def lost_receipt(self, callback, event_id=None):
    real_transact(self, callback, event_id=event_id)
    raise TimeoutError("synthetic post-commit loss")
with patch.object(Brain, "transact_nora", lost_receipt):
    code, result = invoke("bind", "--route-file", str(route_path), "--request-reference", "fixture:request-3", "--event-id", event3)
assert code == 3 and result["state"] == "unknown"
assert json.loads(config_path.read_text())["nora"]["enabled"] is False
committed_head = brain.snapshot()["head"]
code, result = invoke("reconcile", "--event-id", event3)
assert code == 0 and result["state"] == "prepared_inactive"
assert brain.snapshot()["head"] == committed_head
print(json.dumps({"evidence_level":"E1_synthetic_composed_sdk", "default_inactive":True,
    "idempotent_head_preserved":True, "explicit_enable_fixture_only":True,
    "post_commit_unknown_reconciled_without_remote_retry":True,
    "model_imports":0, "real_messages":0, "real_account_actions":0}))
'''


def main():
    product = Path(sys.argv[1]).resolve()
    own = Path(__file__).resolve().parents[2]
    if not (product / "company_pilot/brain/engine.py").is_file():
        raise SystemExit("Expected the composed product source, not a runtime data directory")
    with tempfile.TemporaryDirectory(prefix="nora-composed-setup-") as temporary:
        stage = Path(temporary)
        shutil.copytree(product / "company_pilot", stage / "company_pilot", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(own / "company_pilot/nora", stage / "company_pilot/nora", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        files = ("cli.py", "operator.py", "brain/engine.py", "brain/stores.py", "nora/setup.py")
        hashes = {name: sha256((stage / "company_pilot" / name).read_bytes()).hexdigest() for name in files}
        environment = dict(os.environ, PYTHONPATH=str(stage))
        result = subprocess.run([sys.executable, "-c", PROGRAM], cwd=stage, env=environment,
                                text=True, capture_output=True, check=False)
        if result.returncode:
            print(result.stdout, end="")
            print(result.stderr, file=sys.stderr, end="")
            raise SystemExit(result.returncode)
        print(json.dumps({"source_sha256": hashes, "result": json.loads(result.stdout)}, indent=2))


if __name__ == "__main__":
    main()
