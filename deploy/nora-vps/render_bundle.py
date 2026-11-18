"""Render a private, inactive staging bundle. Never install or execute anything.

Complete channel configuration and trusted real identities require later explicit
authorization. No OpenClaw profile, credential, systemd unit or approval is activated.
"""
import argparse
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent


def render_bundle(destination, *, openclaw_executable, code_commit,
                  repository="lucaslgaio/company-work-map", gateway_port=18849):
    executable = Path(openclaw_executable)
    if (not executable.is_absolute() or ".." in executable.parts
            or not re.fullmatch(r"/[A-Za-z0-9_./+-]+", str(executable))
            or str(executable).startswith(("/root/", "/Users/"))):
        raise ValueError("Supply a verified absolute Linux executable path outside personal state")
    if not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("Supply the exact reviewed code commit")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid private repository name")
    if type(gateway_port) is not int or not 1024 <= gateway_port <= 65535 or gateway_port in (18789, 18811):
        raise ValueError("Select a separate non-reserved gateway port; availability is not established here")
    destination = Path(destination)
    resolved = destination.resolve()
    protected = {form for base in ("/etc", "/usr", "/lib", "/bin", "/sbin", "/run",
                                  "/opt/company-pilot", "/var/lib/company-pilot",
                                  "/private/etc", "/private/var/lib/company-pilot")
                 for form in (Path(base), Path(base).resolve())}
    candidates = (destination.absolute(), resolved)
    if any(candidate == base or base in candidate.parents
           for candidate in candidates for base in protected) or any(
            part.startswith(".openclaw") for candidate in candidates for part in candidate.parts):
        raise ValueError("Render into a staging directory, never installed units/config/runtime state")
    if destination.exists():
        raise FileExistsError("Use a new staging directory; never overwrite an existing deployment")
    destination.mkdir(mode=0o700, parents=True)
    replacements = {"@OPENCLAW_EXECUTABLE@": str(executable), "@GATEWAY_PORT@": str(gateway_port)}
    for name in ("company-pilot-gateway", "company-pilot-nora"):
        body = (ROOT / "templates" / f"{name}.service.in").read_text()
        for key, value in replacements.items():
            body = body.replace(key, value)
        (destination / f"{name}.service").write_text(body)
    config = {
        "schema_version": 1, "mode": "company",
        "store": {"kind": "github", "repo": repository, "branch": "company-state",
                  "mirror": "/var/lib/company-pilot/mirror"},
        "operator": {"health_file": "/var/lib/company-pilot/health/analysis-not-on-this-host.json"},
        "nora": {"enabled": False, "route_verified": False,
            "verification_id": "@FUTURE_VERIFIED_ROUTE_RECEIPT@", "route_id": "@VERIFIED_ROUTE_UUID@",
            "route_revision": 1, "openclaw_executable": str(executable),
            "inbox_file": "/var/lib/company-pilot/inbound/messages.jsonl",
            "cursor_file": "/var/lib/company-pilot/cursor/nora-inbox.json",
            "health_file": "/var/lib/company-pilot/health/nora.json", "poll_seconds": 60},
    }
    fragment = {"plugins": {
        "allow": ["company-nora-bridge", "whatsapp"],
        "load": {"paths": ["/opt/company-pilot/source/company_pilot/nora/bridge"]},
        "entries": {"company-nora-bridge": {"enabled": False,
            "hooks": {"allowConversationAccess": True},
            "config": {"accountId": "@VERIFIED_ACCOUNT_ID@", "conversationId": "@VERIFIED_GROUP_JID@",
                "founderSenderId": "@VERIFIED_FOUNDER_NATIVE_SENDER_ID@",
                "spoolPath": "/var/lib/company-pilot/inbound/messages.jsonl"}}},
    }}
    manifest = {"schema_version": 1, "state": "prepared_inactive",
        "reviewed_code_commit": code_commit, "repository": repository,
        "openclaw_version_required": "2026.7.1-2", "gateway_port": gateway_port,
        "port_availability": "not_checked", "profile": "company-pilot",
        "linux_user": "company-pilot", "activation_approved": False,
        "gateway_config_complete": False, "account_binding_verified": False,
        "supervision_installed": False, "real_channel_tested": False,
        "approval_files_created": False}
    for name, value in (("config.json", config), ("openclaw-plugin-fragment.json", fragment),
                        ("deployment.json", manifest)):
        (destination / name).write_text(json.dumps(value, indent=2) + "\n")
    (destination / "gateway.env.example").write_text("# Template only; populate via the chosen private secret store after authorization.\nOPENCLAW_GATEWAY_TOKEN=\n")
    (destination / "github.env.example").write_text("# Template only; use scoped company-state access, not copied personal auth.\nGH_TOKEN=\n")
    for path in destination.iterdir():
        os.chmod(path, 0o600)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private staging directory")
    parser.add_argument("--openclaw-executable", required=True, help="Absolute path verified on the intended Linux host")
    parser.add_argument("--code-commit", required=True, help="Exact reviewed main commit, forty hex characters")
    parser.add_argument("--repository", default="lucaslgaio/company-work-map")
    parser.add_argument("--gateway-port", type=int, default=18849)
    args = parser.parse_args()
    result = render_bundle(args.output, openclaw_executable=args.openclaw_executable,
                           code_commit=args.code_commit, repository=args.repository,
                           gateway_port=args.gateway_port)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
