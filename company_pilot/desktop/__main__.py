"""Desktop entry point: agent-context health controls readiness."""
import argparse
import json
import platform
from pathlib import Path
import sys
import time
import uuid
from .agent_health import native_ready, read_health, screen_recording_ready, write_health
from .collector import Collector, NativeFailure, PermanentDeliveryError, flush
from .config import policy_file, read_config, replace_config, write_config
from .native import NativeAdapter
from .outbox import Outbox
from .policy import instant, utcnow
from .privacy import bind_private_policy, configure_private_policy, control_status
from .preflight import failure_details, local_tools, platform_check, runtime_check
from .transport import make_transport


class FixtureNative:
    def __init__(self, path):
        self.surface = json.loads(Path(path).read_text(encoding="utf-8"))
        self.surface["evidence_level"] = "synthetic"
    def context(self): return self.surface.copy()
    def text(self, expected): return self.surface.copy()
    def screenshot(self, expected): raise NativeFailure("fixture_has_no_native_image")


def screen_capture_binary(system=None):
    """The process macOS must authorize. Python, Claude and Chrome do not take the image."""
    system = system or platform.system()
    name = "windows-capture.ps1" if system == "Windows" else "mac-capture"
    return (Path(__file__).resolve().parent / "native" / name)


def screen_recording_steps(binary):
    path = str(binary)
    missing = "" if binary.is_file() else " Se o arquivo ainda não existir, rode o instalador; ele compila esse executável. "
    return (
        "A gravação de tela não está autorizada para o programa que tira a imagem. "
        "O macOS só lista o que já foi adicionado; Claude e o Chrome não substituem este arquivo. "
        "Abra Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela, clique no botão +, "
        f"pressione Cmd+Shift+G, cole este caminho e confirme: {path}. "
        + missing +
        "Depois reinicie o serviço: launchctl kickstart -k gui/$(id -u)/com.company-pilot.desktop. "
        "A captura de texto continua sem essa permissão."
    )


def screenshot_report(config, policy, box, probe):
    """Whether images are actually stored, not only whether the policy allows them."""
    allowed = bool(policy is not None and policy.permits_image(utcnow()))
    granted = screen_recording_ready(probe) if probe else False
    if probe and probe.get("platform") == "macOS" and "screen_recording_granted" not in probe and probe.get("status"):
        granted = False
    stored = box.db.execute("SELECT count(*) FROM events WHERE image IS NOT NULL").fetchone()[0]
    buffered = box.db.execute("SELECT count(*) FROM episodes WHERE image IS NOT NULL").fetchone()[0]
    taking = bool(allowed and granted and (stored or buffered))
    binary = screen_capture_binary("Darwin" if probe and probe.get("platform") == "macOS" else None)
    if not allowed:
        step = "A política ainda não permite imagens. No config da empresa: setup screenshots --founder yes --team yes. No mesmo computador: setup consent --participate yes --screenshots yes --reviewed-defaults. Depois ative de novo a captura."
    elif probe and probe.get("platform") == "macOS" and probe.get("screen_recording_granted") is not True:
        step = screen_recording_steps(binary)
    elif taking:
        step = "As imagens estão sendo gravadas nesta fila e seguem com a captura para o repositório, em attachments/, ligadas à fonte."
    elif allowed and granted and config.get("capture_enabled") is True:
        step = "A permissão e a política permitem imagens. Esta fila ainda não tem uma imagem; a próxima janela de trabalho deve gerar uma."
    else:
        step = "A política permite imagens, mas o coletor ainda não gravou nenhuma nesta fila. Confira se a captura está ligada e se o serviço foi reiniciado."
    return {"policy_allowed": allowed, "screen_recording_granted": bool(probe.get("screen_recording_granted")) if probe and probe.get("platform") == "macOS" else granted,
            "images_stored": stored, "images_buffered": buffered, "actually_taking": taking,
            "text_capture_continues": True, "binary_path": str(binary), "next_step": step}


def diagnose(config, native, transport, get_policy, health_path):
    checks = {"runtime": True, "policy_current": False, "online_transport": False, "native_ready": False}
    checks["platform"], checks["python"] = platform_check(), runtime_check()
    checks["runtime"] = checks["python"]["supported"]
    checks["tools"] = local_tools(config, checks["platform"]["system"], injected_native=native is not None)
    try:
        policy = get_policy()
        checks.update(policy_current=policy.current(utcnow()), capture_controls=control_status(policy, utcnow()), screenshots_permitted=policy.permits_image(utcnow()))
    except (ValueError, OSError, PermanentDeliveryError) as error:
        checks["policy_error"] = failure_details(error, config)
    try:
        if not checks["platform"]["supported"]:
            raise NativeFailure("unsupported_platform")
        if native is None:
            native = NativeAdapter()
        checks["interactive_probe"] = native.probe()
    except NativeFailure as error:
        checks["interactive_probe"] = {"status": "native_probe_unavailable", **failure_details(error, config)}
    try:
        checks["transport"] = transport.probe()
        checks["online_transport"] = all(checks["transport"].get(k) is True for k in ("online", "authenticated", "can_contribute"))
        policy = get_policy()  # probe may have forced a newer company policy
        checks.update(policy_current=policy.current(utcnow()), capture_controls=control_status(policy, utcnow()), screenshots_permitted=policy.permits_image(utcnow()))
    except (ValueError, OSError, AttributeError, PermanentDeliveryError) as error:
        checks["transport"] = {"status": "online_transport_unavailable", **failure_details(error, config)}
    checks["agent_health"] = read_health(health_path, config)
    checks["native_ready"] = checks["platform"]["supported"] and checks["runtime"] and all(t["available"] for t in checks["tools"].values()) and checks["agent_health"].get("context_verified") is True and native_ready(checks["agent_health"].get("native_probe", {}))
    checks["screenshots_ready"] = checks.get("screenshots_permitted") is True and screen_recording_ready(checks.get("interactive_probe") or {})
    checks["agent_health"]["ready"] = checks["native_ready"]
    if not checks["native_ready"]:
        checks["agent_health"]["next_step"] = "Confira o registro do agente neste sistema e seu relato recente; permissões de uma sessão interativa não comprovam acesso em segundo plano."
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="Coletor local do piloto da empresa")
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--config", type=Path, required=True)
    configure.add_argument("--policy", type=Path)
    configure.add_argument("--fixture-brain", type=Path)
    configure.add_argument("--repo")
    configure.add_argument("--branch", default="company-state")
    configure.add_argument("--mirror-dir", type=Path)
    configure.add_argument("--synthetic", action="store_true")
    configure.add_argument("--device-id")
    configure.add_argument("--development", action="store_true")
    for name in ("doctor", "status", "screenshots", "run", "flush", "agent", "prepare-agent", "enable-capture"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--development", action="store_true")
        if name in {"run", "agent"}: command.add_argument("--once", action="store_true")
        if name == "run":
            command.add_argument("--fixture", type=Path)
            command.add_argument("--native", action="store_true")
            command.add_argument("--clock", help="Horário ISO apenas para --development --fixture --once")
    privacy = commands.add_parser("privacy")
    privacy.add_argument("--config", type=Path, required=True)
    privacy.add_argument("--choices-file", type=Path, required=True)
    privacy.add_argument("--development", action="store_true")
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 12) and args.command not in {"doctor", "status"}: raise ValueError("python_312_required")
        if args.command == "configure":
            path = args.config.resolve()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.exists(): raise ValueError("configuration_exists")
            policy_path = (args.policy or path.parent / "device-policy.json").resolve()
            if args.repo and not args.fixture_brain:
                from company_pilot.setup import CompanyEntry
                CompanyEntry().guard(args.repo)
                settings = {"kind": "github", "repo": args.repo, "branch": args.branch,
                            "mirror_dir": str((args.mirror_dir or path.parent / "verified-mirror").resolve()),
                            "policy_cache": str(policy_path), "synthetic": args.synthetic}
                transport = make_transport(settings)
                access = transport.probe()
                if not access.get("can_contribute"): raise ValueError("repository_write_access_required")
                replace_config(policy_path, transport.policy().as_mapping())
            elif args.fixture_brain and args.development and args.policy and not args.repo:
                policy_file(policy_path)
                settings = {"kind": "directory-fixture", "path": str(args.fixture_brain.resolve())}
                access = {"online": False, "development_fixture": True}
            else: raise ValueError("select_github_or_explicit_fixture")
            write_config(path, {"schema_version": 1, "device_id": str(uuid.UUID(args.device_id)) if args.device_id else str(uuid.uuid4()),
                                "policy_file": str(policy_path), "outbox_file": str(path.parent / "outbox.sqlite"),
                                "poll_seconds": 15, "capture_enabled": False, "transport": settings})
            print(json.dumps({"status": "configured", "access": access,
                              "configured_values": {"repository": settings.get("repo"), "state_branch": settings.get("branch"),
                                                    "mode": ("synthetic" if args.synthetic else "company") if args.repo else "development_fixture",
                                                    "device_id": read_config(path)["device_id"], "config_path": str(path)},
                              "capture_enabled": False,
                              "message": "Dispositivo configurado sem iniciar captura. Credencial pertence à própria conta GitHub deste computador."}, ensure_ascii=False))
            return 0
        config = read_config(args.config)
        transport = make_transport(config.get("transport", {}), args.development)
        remote_policy = transport.policy if hasattr(transport, "policy") else lambda: policy_file(Path(config["policy_file"]))
        get_policy = lambda: bind_private_policy(remote_policy(), read_config(args.config))
        if args.command == "privacy":
            choices = json.loads(args.choices_file.read_text(encoding="utf-8"))
            print(json.dumps(configure_private_policy(args.config, config, choices, remote_policy().version), ensure_ascii=False))
            return 0
        health_path = args.config.resolve().parent / "agent-health.json"
        box = Outbox(Path(config["outbox_file"]))
        try:
            if args.command in {"status", "screenshots"}:
                pending = len(box.quarantine_open())
                try:
                    current_policy = get_policy()
                    controls = control_status(current_policy, utcnow())
                except (ValueError, OSError, PermanentDeliveryError, ConnectionError):
                    current_policy, controls = None, {"capture_allowed": False, "reason": "policy_unavailable"}
                probe = {}
                try:
                    probe = NativeAdapter().probe()
                except (NativeFailure, ValueError, OSError):
                    probe = {}
                shots = screenshot_report(config, current_policy, box, probe)
                if args.command == "screenshots":
                    print(json.dumps(shots, ensure_ascii=False))
                    return 0
                print(json.dumps({"queue": box.status(), "agent": read_health(health_path, config), "capture_enabled": config.get("capture_enabled", False),
                                  "controls": controls, "coverage": box.coverage_status(), "quarantine_pending": pending,
                                  "screenshots": shots}, ensure_ascii=False))
                return 0
            if args.command == "prepare-agent":
                if not get_policy().valid(utcnow()): raise ValueError("policy_unavailable")
                config["capture_enabled"] = False
                replace_config(args.config, config)
                print(json.dumps({"status": "diagnostic_agent_prepared", "capture_enabled": False}))
                return 0
            if args.command in {"doctor", "enable-capture"}:
                checks = diagnose(config, None, transport, get_policy, health_path)
                ready = checks["policy_current"] and checks.get("capture_controls", {}).get("capture_allowed") is True and checks["online_transport"] and checks["native_ready"]
                if args.command == "enable-capture":
                    config["capture_enabled"] = bool(ready)
                    replace_config(args.config, config)
                print(json.dumps({"checks": checks, "queue": box.status(), "ready": ready,
                                  "message": "A permissão interativa é apenas informativa; a prontidão depende do diagnóstico recente do agente registrado."}, ensure_ascii=False))
                return 0 if ready else 2
            if args.command == "flush":
                print(json.dumps(flush(box, transport, get_policy)))
                return 0
            if args.command == "run":
                if args.clock and not (args.development and args.fixture and args.once and config["transport"]["kind"] == "directory-fixture"):
                    raise ValueError("fixture_clock_requires_development_once")
                if args.fixture and not args.development: raise ValueError("fixture_requires_development")
                if not args.fixture and not args.native: raise ValueError("explicit_native_configuration_required")
                native = FixtureNative(args.fixture) if args.fixture else NativeAdapter()
            else: native = NativeAdapter()
            clock = (lambda: instant(args.clock)) if args.command == "run" and args.clock else utcnow
            collector = Collector(config["device_id"], native, box, get_policy, clock=clock)
            while True:
                if args.command == "agent":
                    current = read_config(args.config)
                    if current["transport"] != config["transport"]: raise ValueError("restart_agent_after_reconfiguration")
                    try: probe = native.probe()
                    except NativeFailure as error: probe = {"status": str(error)}
                    health = write_health(health_path, current, probe)
                    if not current.get("capture_enabled") or not health["context_verified"] or not native_ready(probe):
                        if args.once: return 2
                        time.sleep(config.get("poll_seconds", 15))
                        continue
                result = {"capture": collector.tick(force_finalize=args.once), "delivery": flush(box, transport, get_policy, clock=clock),
                          "clock_source": "development_fixture" if clock is not utcnow else "system"}
                print(json.dumps(result), flush=True)
                if args.once: break
                time.sleep(config.get("poll_seconds", 15))
            return 0
        finally: box.close()
    except KeyboardInterrupt: return 130
    except (ValueError, KeyError, OSError, ImportError, NativeFailure, PermanentDeliveryError) as error:
        print(json.dumps({"status": "configuration_or_runtime_error", **failure_details(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
