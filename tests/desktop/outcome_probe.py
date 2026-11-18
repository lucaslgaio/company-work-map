"""Reproduce a32 outcomes with local synthetic surfaces; never invokes native/remote APIs."""
import contextlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from company_pilot.brain.stores import BrainError
from company_pilot.desktop.__main__ import main
from company_pilot.desktop.collector import Collector, NativeFailure, flush
from company_pilot.desktop.config import read_config
from company_pilot.desktop.github_transport import BrainTransport
from company_pilot.desktop.outbox import Outbox
from company_pilot.desktop.policy import Policy
from company_pilot.desktop.privacy import bind_private_policy
from company_pilot.desktop.preflight import platform_check, runtime_check
from company_pilot.desktop.transport import DirectoryFixtureTransport
from install.bootstrap import main as bootstrap

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
DEVICE = "00000000-0000-4000-8000-000000000001"


def command(args, transport=None, unsupported=False):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        if transport:
            stack.enter_context(patch("company_pilot.desktop.__main__.make_transport", return_value=transport))
            # This probe injects an existing brain. Real destination rejection
            # is exercised by tests/setup's actual CompanyEntry API fixture.
            stack.enter_context(patch("company_pilot.setup.CompanyEntry.guard"))
        if unsupported: stack.enter_context(patch("company_pilot.desktop.__main__.NativeAdapter", side_effect=NativeFailure("unsupported_platform")))
        code = main(args)
    return {"exit_code": code, "stdout": json.loads(out.getvalue()) if out.getvalue().strip() else None,
            "stderr": json.loads(err.getvalue()) if err.getvalue().strip() else None}


class Surface:
    def __init__(self):
        self.value = {"app_id": "com.google.Chrome", "title": "Nubank - Fatura do cartão pessoal", "record_ref": "",
                      "text": "Fatura atual R$ 2.310,44 | Supermercado | Farmácia | Streaming", "window_id": "nubank-pessoal",
                      "process_id": "p-com.google.Chrome", "secure_context": False, "evidence_level": "synthetic"}
        self.calls = {"context": 0, "text": 0, "image": 0}
    def context(self): self.calls["context"] += 1; return self.value.copy()
    def text(self, expected): self.calls["text"] += 1; return self.value.copy()
    def screenshot(self, expected): self.calls["image"] += 1; raise AssertionError("Image was not permitted")


def payloads(box):
    return [json.loads(r[0]) for r in box.db.execute("SELECT payload FROM events ORDER BY created_at")]


def run():
    proof = {"evidence_level": "E1 synthetic local fixtures and mocked provider/native construction",
             "not_demonstrated": ["native capture", "registered startup", "Windows execution", "GitHub upload", "actual company consent", "Nora delivery", "model inference"], "cases": {}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pp, cp = root / "policy.json", root / "config.json"
        local = Policy.parse({"schema_version": 1, "policy_id": "fixture", "version": 1, "expires_at": (NOW + timedelta(days=10000)).isoformat(),
                              "participant_kind": "team", "revoked": False, "context": {"work_markers": ["Fatura", "Inter Empresas"]}})
        pp.write_text(json.dumps(local.as_mapping()))
        proof["cases"]["configure"] = command(["configure", "--config", str(cp), "--policy", str(pp), "--fixture-brain", str(root / "receiver"), "--development", "--device-id", DEVICE])
        remote = replace(local, requires_local_exclusions=True, capture_allowed=True, control_reason="ready_synthetic_fixture", participation={})
        class Remote:
            def policy(self): return remote
            def probe(self): return {"online": False, "authenticated": False, "development_fixture": True}
        choices = root / "private-choices-input.json"
        choices.write_text(json.dumps({"participating": True, "screenshots": False, "reviewed_defaults": True, "personal_exclusions": ["Meu arquivo privado"]}))
        proof["cases"]["private_choices"] = command(["privacy", "--config", str(cp), "--choices-file", str(choices), "--development"], Remote())
        proof["cases"]["pending_ack_status"] = command(["status", "--config", str(cp), "--development"], Remote())
        assert proof["cases"]["pending_ack_status"]["stdout"]["controls"]["capture_allowed"] is False
        ack = json.loads((root / "participation-choices.json").read_text())
        remote = replace(remote, participation=ack)
        get_policy = lambda: bind_private_policy(remote, read_config(cp))
        proof["cases"]["acknowledged_status"] = command(["status", "--config", str(cp), "--development"], Remote())
        assert proof["cases"]["acknowledged_status"]["stdout"]["controls"]["capture_allowed"] is True
        native, now = Surface(), [NOW]
        box = Outbox(root / "outbox.sqlite")
        try:
            collector = Collector(DEVICE, native, box, get_policy, lambda: now[0], episode_seconds=0)
            result = collector.tick()
            private_absent = not box.status() and not box.quarantine_pending(NOW.timestamp()) and b"Nubank" not in (root / "outbox.sqlite").read_bytes()
            assert result["code"] == "personal_context" and private_absent and native.calls["text"] == native.calls["image"] == 0
            proof["cases"]["I022_P1_o5"] = {"capture": result, "title_absent_local_stores": private_absent, "native_calls": native.calls.copy(), "overlapping_company_marker": "Fatura"}
            native.value.update(title="Inter Empresas - Fatura de fornecedor", window_id="business-bank", text="Pagamento empresarial AVM 82")
            result = collector.tick()
            delivered = flush(box, DirectoryFixtureTransport(root / "receiver"), get_policy, lambda: now[0])
            received = [json.loads(p.read_text()) for p in (root / "receiver").glob("*.json")]
            assert result["classification"] == "company" and delivered["delivered"] == 1 and "Nubank" not in json.dumps(received)
            proof["cases"]["P1_business_banking_control"] = {"capture": result, "delivery": delivered, "local_fixture_payloads": received, "excluded_title_absent_payload": True}
            for index in range(2):
                native.value.update(title="Unknown private title " + str(index), window_id="unknown-" + str(index), text="UNKNOWN BODY NEVER READ")
                collector.tick()
            unknown = [p for p in payloads(box) if p["classification"]["state"] == "uncertain"]
            assert unknown[-1]["coverage"]["distinct_windows"] == 2 and "Unknown private" not in json.dumps(unknown)
            proof["cases"]["B4_o1_o6"] = {"safe_gap_payloads": unknown, "local_coverage": box.coverage_status()}
            native.value.update(title="Cronograma Obra AVM 82 - Planilhas Google", window_id="schedule", text="ORIGINAL BODY NEVER READ")
            collector.tick()
            now[0] += timedelta(minutes=10)
            native.value["text"] = "CURRENT permitted schedule; updated at re-observation"
            remote = replace(remote, context_version=2, work_markers=(*remote.work_markers, "Cronograma Obra"))
            promoted = collector.reclassify_quarantine()
            schedule = [p for p in payloads(box) if p["source"].get("window_id") == "schedule"]
            assert promoted == 1 and len(schedule) == 2 and schedule[-1]["observed_at"] == now[0].isoformat()
            assert "ORIGINAL BODY" not in json.dumps(schedule)
            proof["cases"]["I012_B3_B10_o1_o6"] = {"promoted": promoted, "metadata_then_current_evidence": schedule}
            now[0] += timedelta(days=2)
            native.value.update(title="Nubank - Fatura do cartão pessoal", window_id="nubank-pessoal")
            collector.tick()
            expirations = [p for p in payloads(box) if p["classification"]["reason"] == "unknown_metadata_expired"]
            assert len(expirations) == 2 and box.coverage_status()["expired_unresolved"] == 2
            assert "Unknown private" not in json.dumps(expirations) and "Nubank" not in json.dumps(expirations)
            proof["cases"]["B15_o1_o6"] = {"local_coverage": box.coverage_status(), "safe_expiry_updates": expirations}
        finally: box.close()
        proof["cases"]["I7_o6_doctor"] = command(["doctor", "--config", str(cp), "--development"], Remote(), unsupported=True)
        assert proof["cases"]["I7_o6_doctor"]["stdout"]["ready"] is False
        with patch("company_pilot.desktop.preflight.platform.system", return_value="Linux"), patch("company_pilot.desktop.preflight.sys.version_info", (3, 11, 9)):
            proof["cases"]["I1_o6"] = {"platform": platform_check(), "python": runtime_check()}
        errors = io.StringIO()
        with patch("install.bootstrap.platform_check", return_value={"supported": True}), patch("install.bootstrap.runtime_check", return_value={"supported": True}), patch("install.bootstrap.platform.system", return_value="Darwin"), patch("install.bootstrap.shutil.which", return_value=None), contextlib.redirect_stderr(errors):
            try: bootstrap(["--prefix", str(root / "not-installed")])
            except SystemExit as error: result = error.code
        assert result == 2 and not (root / "not-installed").exists()
        proof["cases"]["I6_o6"] = {"exit_code": result, "stderr": errors.getvalue(), "installation_created": False, "registered_agent": False}
        for scenario in ("state_branch_missing", "brain_not_initialized", "synthetic_real_boundary_mismatch"):
            class Store:
                def authenticated_actor(self): return {"provider": "local_fixture", "permissions": ["read", "write"], "id": "fixture"}
                def read(self): return SimpleNamespace(head="existing-head" if scenario == "brain_not_initialized" else None)
            class Brain:
                store = Store()
                def snapshot(self):
                    if scenario == "synthetic_real_boundary_mismatch": return {"company": {"synthetic": True}}
                    raise BrainError("brain_not_initialized")
            transport = BrainTransport(Brain())
            transport.expected_synthetic = False
            result = command(["configure", "--config", str(root / (scenario + ".json")), "--repo", "fixture/company", "--branch", "actual-state"], transport)
            assert result["stderr"]["code"] == scenario
            proof["cases"]["I5_" + scenario] = result
    repo = Path(__file__).resolve().parents[2]
    pins = [*sorted((repo / "company_pilot" / "desktop").glob("*.py")), *sorted((repo / "install").glob("*.*"))]
    proof["source_sha256"] = {str(p.relative_to(repo)): sha256(p.read_bytes()).hexdigest() for p in pins if p.is_file()}
    return proof


if __name__ == "__main__":
    json.dump(run(), sys.stdout, ensure_ascii=False, indent=2)
    print()
