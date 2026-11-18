"""Owner-side typed Nora binding. No send, model, deployment or role invitation.

Configuration/Brain imports happen only when main executes an actual command.
Importing this module or requesting help never authenticates or loads a model.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import UUID

from .models import Route


class SetupError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _uuid(value):
    if not isinstance(value, str):
        raise SetupError("UUID_INVALIDO")
    try:
        return str(UUID(value))
    except ValueError:
        raise SetupError("UUID_INVALIDO") from None


def _reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:/_.#=?&%-]{0,255}", value):
        raise SetupError("REFERENCIA_VERIFICADA_NECESSARIA")
    return value


def _recipient_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 120 or not value.isprintable():
        raise SetupError("NOME_VERIFICADO_INVALIDO")
    return value.strip()


def read_private_json(path):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077) or info.st_size > 65536:
        raise SetupError("ARQUIVO_PRIVADO_NECESSARIO")
    try:
        result = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        raise SetupError("JSON_PRIVADO_INVALIDO") from None
    if type(result) is not dict:
        raise SetupError("JSON_PRIVADO_INVALIDO")
    return result


def _writer(path, value):
    from ..operator import write_private_json
    write_private_json(path, value)


def _authorize(brain):
    actor = brain.store.authenticated_actor()
    snapshot = brain.snapshot()
    company = snapshot["company"]
    founders = {item.casefold() for item in company.get("founders", [])}
    if ("write" not in actor.get("permissions", []) or not isinstance(actor.get("id"), str)
            or not ("admin" in actor.get("permissions", []) or actor["id"].casefold() in founders)):
        raise SetupError("AUTORIDADE_PROPRIETARIO_OU_FUNDADOR_NECESSARIA")
    if actor.get("provider") == "local_fixture" and company.get("synthetic") is not True:
        raise SetupError("IDENTIDADE_LOCAL_APENAS_SINTETICA")
    if actor["id"] not in company.get("nora_actors", []):
        raise SetupError("OPERADOR_NORA_CONFIGURADO_NECESSARIO")
    return actor, snapshot


def _route(raw, company, enable):
    required = {"company_id", "id", "revision", "account", "conversation", "founder_sender", "founder_actor_id"}
    optional = {"enabled", "founder_name", "group_name", "responders"}
    if type(raw) is not dict or not required <= set(raw) or set(raw) - required - optional:
        raise SetupError("ROTA_COMPLETA_TIPADA_NECESSARIA")
    responders = []
    if "responders" in raw:
        if not isinstance(raw["responders"], list) or len(raw["responders"]) > 8:
            raise SetupError("ROTA_INVALIDA")
        for person in raw["responders"]:
            if not isinstance(person, dict) or set(person) != {"sender", "actor_id", "name"}:
                raise SetupError("ROTA_INVALIDA")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,80}", person["sender"]) or not str(person["actor_id"]).strip():
                raise SetupError("ROTA_INVALIDA")
            responders.append((person["sender"], person["actor_id"].strip(), _recipient_name(person["name"])))
    if raw.get("group_name"):
        _recipient_name(raw["group_name"])
    if any(not isinstance(raw[key], str) or not raw[key].strip() or len(raw[key]) > 256
           or any(ord(char) < 32 for char in raw[key]) for key in required - {"revision"}):
        raise SetupError("ROTA_INVALIDA")
    if type(raw["revision"]) is not int or raw["revision"] < 1 or type(enable) is not bool:
        raise SetupError("REVISAO_INVALIDA")
    if "enabled" in raw and (type(raw["enabled"]) is not bool or raw["enabled"] != enable):
        raise SetupError("ATIVACAO_EXIGE_ESCOLHA_EXPLICITA")
    if "founder_name" in raw:
        _recipient_name(raw["founder_name"])
    if _uuid(raw["company_id"]) != company["id"]:
        raise SetupError("EMPRESA_DA_ROTA_DIVERGE")
    _uuid(raw["id"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", raw["account"]) or not re.fullmatch(r"[0-9]+@g\.us", raw["conversation"]):
        raise SetupError("CONTA_OU_GRUPO_INVALIDO")
    canonical = next((actor for actor in company.get("founders", [])
                      if actor.casefold() == raw["founder_actor_id"].casefold()), None)
    if canonical is None:
        raise SetupError("CADASTRE_FUNDADOR_COM_OWNER_ENROLL_FOUNDER")
    route = {key: value for key, value in raw.items() if key not in ("founder_name", "responders")}
    return Route(**{**route, "company_id": company["id"], "id": _uuid(raw["id"]),
                    "founder_actor_id": canonical, "enabled": enable, "responders": tuple(responders)})


def _journal_path(config_path, event_id):
    return Path(config_path).parent / "nora-bindings" / (event_id + ".json")


def _public(journal, state=None):
    return {"schema_version": 1, "state": state or journal["state"],
            "event_id": journal["event_id"], "request_sha256": journal["request_sha256"],
            "local_enabled": journal.get("local_enabled", False), "channel_connectivity": "not_probed",
            "service_started": False, "message_sent": False}


def _validate_journal(journal, event_id, company):
    if journal.get("schema_version") != 1 or journal.get("event_id") != event_id or type(journal.get("enable")) is not bool:
        raise SetupError("DIARIO_DE_VINCULO_INVALIDO")
    _route(journal["route"], company, journal["enable"])
    _reference(journal["request_reference"])
    if journal["enable"]:
        _reference(journal["verification_reference"])
    request = {key: journal[key] for key in ("route", "request_reference", "enable", "verification_reference")}
    if "founder_name" in journal:
        request["founder_name"] = _recipient_name(journal["founder_name"])
    if _hash(request) != journal["request_sha256"]:
        raise SetupError("DIARIO_DE_VINCULO_DIVERGE")


def _matching_ledger_receipt(brain, journal):
    suffix = "-" + journal["event_id"] + ".json"
    for name, content in brain.store.read().files.items():
        if name.startswith("ledger/") and name.endswith(suffix):
            event = json.loads(content)
            result = event.get("result", {})
            return (event.get("actor") == journal["actor_id"]
                    and result.get("kind") == "nora_route_binding"
                    and result.get("request_sha256") == journal["request_sha256"]
                    and result.get("route_sha256") == _hash(journal["route"]))
    return False


def _finish_local(config_path, config, journal, writer):
    configured = deepcopy(config)
    setting = configured.setdefault("nora", {})
    setting.update(enabled=journal["enable"], route_verified=journal["enable"],
                   route_id=journal["route"]["id"], route_revision=journal["route"]["revision"],
                   verification_id=journal.get("verification_reference") or journal["request_reference"])
    if journal["enable"] and journal.get("founder_name"):
        setting.update(founder_name=journal["founder_name"], founder_name_verification_id=setting["verification_id"])
    else:
        setting.pop("founder_name", None)
        setting.pop("founder_name_verification_id", None)
    if configured != config:
        writer(config_path, configured)
    journal.update(state="bound" if journal["enable"] else "prepared_inactive",
                   local_enabled=journal["enable"])
    writer(_journal_path(config_path, journal["event_id"]), journal)
    return _public(journal)


def reconcile_binding(brain, config_path, config, event_id, *, writer=_writer):
    config_path = Path(config_path).expanduser().absolute()
    event_id = _uuid(event_id)
    _actor, snapshot = _authorize(brain)
    path = _journal_path(config_path, event_id)
    journal = read_private_json(path)
    _validate_journal(journal, event_id, snapshot["company"])
    if journal["state"] in ("bound", "prepared_inactive"):
        # Replaying a completed setup must not reverse a later local pause or route.
        current = (snapshot.get("nora") or {}).get("route")
        journal["local_enabled"] = bool(current == journal["route"] and config.get("nora", {}).get("enabled") is True)
        return _public(journal, "already_bound" if current == journal["route"] else "superseded")
    if journal["state"] == "rejected":
        return _public(journal)
    if (snapshot.get("nora") or {}).get("route") != journal["route"]:
        return _public(journal, "unknown_requires_review")
    # Route equality can come from an earlier request, even when this request
    # stopped at prepared_local. Every unfinished request needs its own receipt.
    if not _matching_ledger_receipt(brain, journal):
        return _public(journal, "unknown_requires_review")
    return _finish_local(config_path, config, journal, writer)


def bind_route(brain, config_path, config, raw_route, request_reference, event_id,
               *, enable=False, verification_reference=None, writer=_writer):
    """Persist an exact route then its local gates. Default is inactive preparation.

    --enable only records an explicitly authorized, already verified binding; it
    does not start a worker/gateway or send. Reference verification is the trusted
    owner's responsibility, as in the existing owner enroll-founder command.
    """
    config_path = Path(config_path).expanduser().absolute()
    event_id, request_reference = _uuid(event_id), _reference(request_reference)
    if enable:
        verification_reference = _reference(verification_reference)
    actor, snapshot = _authorize(brain)
    route = asdict(_route(raw_route, snapshot["company"], enable))
    # JSON stores lists. Keep the hashed route identical to the copy that is read back.
    route["responders"] = [list(person) for person in route["responders"]]
    if not Path(config.get("nora", {}).get("openclaw_executable", "")).is_absolute():
        raise SetupError("EXECUTAVEL_NORA_ABSOLUTO_NECESSARIO")
    request = {"route": route, "request_reference": request_reference,
               "enable": enable, "verification_reference": verification_reference}
    if "founder_name" in raw_route:
        request["founder_name"] = _recipient_name(raw_route["founder_name"])
    request_hash = _hash(request)
    path = _journal_path(config_path, event_id)
    if path.exists() or path.is_symlink():
        previous = read_private_json(path)
        _validate_journal(previous, event_id, snapshot["company"])
        if previous["request_sha256"] != request_hash or previous["actor_id"] != actor["id"]:
            raise SetupError("EVENTO_REUTILIZADO_COM_OUTRO_PEDIDO")
        if previous["state"] in ("unknown", "applying"):
            return _public(previous, "unknown_requires_reconcile")
        return reconcile_binding(brain, config_path, config, event_id, writer=writer)
    journal = {"schema_version": 1, "state": "prepared_local", "event_id": event_id,
               "actor_id": actor["id"], "request_sha256": request_hash, **request,
               "local_enabled": False}
    writer(path, journal)
    disabled = deepcopy(config)
    disabled.setdefault("nora", {}).update(enabled=False, route_verified=False)
    writer(config_path, disabled)  # Fail safe before any remote mutation.
    journal["state"] = "applying"
    writer(path, journal)
    def apply(state):
        fresh_actor, fresh = _authorize(brain)
        if fresh_actor["id"] != actor["id"] or fresh["company"]["id"] != route["company_id"]:
            raise SetupError("IDENTIDADE_MUDOU_DURANTE_O_PEDIDO")
        # First owner binding must be readable by the actual worker codec.
        # Older setup wrote only route; there is no delivery history to infer.
        if set(state) <= {"route", "binding_request"}:
            from .repository import encode_state
            from .models import NoraState
            defaults = encode_state(NoraState(), route["company_id"])
            defaults.update(state)
            state.update(defaults)
        current = state.get("route")
        if current != route:
            if current and (current.get("id") != route["id"] or route["revision"] != current.get("revision", 0) + 1):
                raise SetupError("ROTA_OU_REVISAO_DIVERGE")
            if not current and route["revision"] != 1:
                raise SetupError("ROTA_INICIAL_EXIGE_REVISAO_UM")
            state["route"] = route
        # Every new verified setup request needs a real ledger receipt, even if
        # only the optional local recipient name changed on an identical route.
        state["binding_request"] = {"event_id": event_id, "actor_id": actor["id"],
                                    "request_sha256": request_hash, "route_sha256": _hash(route)}
        return {"kind": "nora_route_binding", "request_sha256": request_hash,
                "route_sha256": _hash(route), "request_reference": request_reference,
                "verification_reference": verification_reference, "state": "binding_recorded"}
    try:
        receipt = brain.transact_nora(apply, event_id=event_id)
        if (receipt.get("request_sha256") != request_hash or receipt.get("route_sha256") != _hash(route)
                or (brain.snapshot().get("nora") or {}).get("route") != route):
            raise RuntimeError("binding_receipt_not_confirmed")
    except SetupError:
        journal["state"] = "rejected"
        writer(path, journal)
        return _public(journal)
    except Exception:
        journal["state"] = "unknown"
        writer(path, journal)
        return _public(journal)
    journal["state"] = "remote_confirmed_local_pending"
    writer(path, journal)
    try:
        return _finish_local(config_path, disabled, journal, writer)
    except Exception:
        return _public(journal, "remote_confirmed_local_pending")


def parser():
    p = argparse.ArgumentParser(description="Preparar/vincular a rota Nora pelo operador autorizado, sem enviar ou iniciar serviços.")
    p.add_argument("--config", type=Path, required=True, help="Configuração privada existente do piloto")
    actions = p.add_subparsers(dest="action", required=True)
    bind = actions.add_parser("bind", help="Gravar rota tipada; padrão: preparação inativa")
    bind.add_argument("--route-file", type=Path, required=True)
    bind.add_argument("--request-reference", required=True, help="Referência ao pedido explícito verificado pelo operador")
    bind.add_argument("--event-id", required=True, help="UUID estável do pedido")
    bind.add_argument("--enable", action="store_true", help="Somente depois da autorização de ativação e verificação real da identidade")
    bind.add_argument("--verification-reference", help="Recibo de vínculo verificado, obrigatório com --enable")
    check = actions.add_parser("reconcile", help="Reconciliar recibo confirmado; nunca repetir a mutação indeterminada")
    check.add_argument("--event-id", required=True)
    return p


def main(argv=None, *, brain_factory=None, config_loader=None, writer=_writer, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    try:
        args = parser().parse_args(argv)
        args.config = args.config.expanduser().absolute()
        if config_loader is None or brain_factory is None:
            from ..cli import load_config, build_brain
            config_loader, brain_factory = config_loader or load_config, brain_factory or build_brain
        config = config_loader(args.config)
        brain = brain_factory(config)
        if args.action == "bind":
            result = bind_route(brain, args.config, config, read_private_json(args.route_file),
                args.request_reference, args.event_id, enable=args.enable,
                verification_reference=args.verification_reference, writer=writer)
        else:
            result = reconcile_binding(brain, args.config, config, args.event_id, writer=writer)
        print(json.dumps(result, ensure_ascii=False), file=out)
        if result["state"] == "rejected":
            return 2
        return 3 if result["state"] in ("unknown", "unknown_requires_review", "unknown_requires_reconcile", "remote_confirmed_local_pending") else 0
    except SystemExit as error:
        return int(error.code or 0)
    except Exception as error:
        print(json.dumps({"ok": False, "codigo": error.code if isinstance(error, SetupError) else "VINCULO_NAO_CONFIRMADO",
            "acao": "Mantenha a Nora inativa; preserve o event-id e os arquivos privados para revisão ou reconcile."}, ensure_ascii=False), file=err)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
