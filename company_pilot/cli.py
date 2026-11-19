"""Comandos locais do piloto. Importar este módulo não inicia LLM nem canal."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

from .operator import Operator, diagnostic_code, drain_nora_inbox, project_questions, write_private_json
from .access import RepositoryAccess

DEFAULT_CONFIG = Path.home() / ".company-pilot" / "config.json"
MAX_INPUT = 1024 * 1024


class CliError(Exception):
    def __init__(self, code, message, action):
        self.code, self.message, self.action = code, message, action


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self.add_argument("-h", "--help", action="help", help="Mostrar esta ajuda e sair.")
        self._positionals.title = "comandos"
        self._optionals.title = "opções"

    def format_help(self):
        return super().format_help().replace("usage:", "uso:", 1)

    def error(self, _message):
        raise CliError("ARGUMENTOS_INVALIDOS", "O comando está incompleto ou contém uma opção desconhecida.", "Execute company-pilot --help ou o subcomando com --help.")


def _emit(value, stream):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True), file=stream, flush=True)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _read_file(path, *, json_value=False):
    try:
        path = Path(path).expanduser()
        if path.stat().st_size > MAX_INPUT:
            raise ValueError("input_size")
        text = path.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > MAX_INPUT:
            raise ValueError("input_size")
        if not json_value:
            if not text.strip():
                raise ValueError("empty_text")
            return text
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise CliError("ARQUIVO_INVALIDO", "Não foi possível ler o arquivo no formato esperado.", "Use um arquivo UTF-8 de até 1 MiB; JSON deve ter chaves únicas e valores válidos.") from None


def _reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?:^|_)(?:token|password|passwd|secret|credential|api_key|private_key)(?:$|_)", key, re.I):
                raise CliError("SEGREDO_NA_CONFIGURACAO", "A configuração não aceita senhas, tokens nem chaves.", "Autentique gh e o assistente pelos gerenciadores próprios; remova o segredo do JSON.")
            _reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secrets(child)


def _keys(value, allowed):
    if type(value) is not dict or set(value) - set(allowed):
        raise CliError("CONFIGURACAO_INVALIDA", "A configuração contém campos não reconhecidos.", "Use a configuração criada por company-pilot init e consulte --help.")


def validate_config(config):
    _reject_secrets(config)
    _keys(config, {"schema_version", "mode", "store", "operator", "nora", "company_repository", "software_repository", "code_commit"})
    if config.get("schema_version") != 1 or config.get("mode") not in ("synthetic", "company"):
        raise CliError("CONFIGURACAO_INVALIDA", "Versão ou modo da configuração inválido.", "Escolha synthetic para dados de teste ou company para a empresa.")
    store = config.get("store")
    _keys(store, {"kind", "root", "repo", "branch", "mirror"})
    if store.get("kind") == "local":
        if config["mode"] != "synthetic" or not isinstance(store.get("root"), str) or not store["root"]:
            raise CliError("LOCAL_APENAS_SINTETICO", "O armazenamento local aceita somente dados sintéticos.", "Para a empresa, configure o repositório GitHub privado e seu mirror retido.")
    elif store.get("kind") == "github":
        if not isinstance(store.get("repo"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", store["repo"]) or not isinstance(store.get("mirror"), str) or not store["mirror"]:
            raise CliError("REPOSITORIO_INVALIDO", "Informe um repositório e um mirror retido válidos.", "Use dono/repositorio e um diretório local de mirror.")
        branch = store.get("branch")
        expected = branch == "company-state" if config["mode"] == "company" else isinstance(branch, str) and re.fullmatch(r"company-state-development(?:-[A-Za-z0-9][A-Za-z0-9-]*)?", branch)
        if not expected:
            raise CliError("BRANCH_INCOMPATIVEL", "A branch não corresponde ao modo de dados escolhido.", "Dados da empresa usam company-state. Dados sintéticos usam company-state-development ou company-state-development-SUFIXO.")
    else:
        raise CliError("CONFIGURACAO_INVALIDA", "Selecione um armazenamento reconhecido.", "Use local para teste sintético ou github para o cérebro online.")
    if any(key in config for key in ("company_repository", "software_repository", "code_commit")):
        company, software, commit = (config.get(key) for key in ("company_repository", "software_repository", "code_commit"))
        if (store.get("kind") != "github" or company != store["repo"] or not isinstance(software, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", software)
                or company.casefold() == software.casefold() or not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-f]{40}", commit)):
            raise CliError("DESTINOS_DIVERGENTES", "O destino da empresa e a origem do software precisam estar separados e identificados.", "Peça ao operador um START-HERE preparado; não use o repositório do software como cérebro.")
    operator = config.get("operator", {})
    _keys(operator, {"health_file", "poll_seconds", "retry_initial_seconds", "retry_max_seconds", "max_failures", "claude_executable", "execution_provenance", "analysis"})
    if operator.get("analysis", "local") not in ("local", "external"):
        raise CliError("CONFIGURACAO_INVALIDA", "O modo de análise do operador não é reconhecido.", "Use analysis local ou external. external significa que este computador não chama modelo.")
    provenance = operator.get("execution_provenance")
    if provenance is not None:
        _keys(provenance, {"provider", "model", "origin"})
        if (set(provenance) != {"provider", "model", "origin"}
                or any(not isinstance(provenance[key], str) or provenance[key].strip() != provenance[key]
                       or not re.fullmatch(r"[\w .:/@()+-]{1,160}", provenance[key]) for key in ("provider", "model"))
                or provenance["origin"] not in ("cli", "queue", "shim", "injected")):
            raise CliError("PROVENIENCIA_INVALIDA", "A identidade de execução configurada está incompleta ou inválida.", "O operador deve conferir provedor, modelo e origem reais. Ausência fica desconhecida; não use a identidade do armazenamento ou o nome do executável como modelo.")
    nora = config.get("nora", {})
    _keys(nora, {"enabled", "route_verified", "verification_id", "route_id", "route_revision", "founder_name", "founder_name_verification_id", "openclaw_executable", "inbox_file", "cursor_file", "health_file", "poll_seconds", "retry_initial_seconds", "retry_max_seconds", "max_failures"})
    for name in ("enabled", "route_verified"):
        if name in nora and type(nora[name]) is not bool:
            raise CliError("BOOLEANO_INVALIDO", "As escolhas de ativação devem ser booleanos JSON.", "Use true ou false, sem aspas.")
    name = nora.get("founder_name")
    if name is not None and (not isinstance(name, str) or not name.strip()
                             or len(name) > 120 or not name.isprintable()):
        raise CliError("NOME_NORA_INVALIDO", "O nome para a conversa precisa ser um texto curto, sem quebras de linha.", "Use o nome confirmado na configuração verificada da rota ou deixe o campo ausente.")
    name_binding = nora.get("founder_name_verification_id")
    if name_binding is not None and (not isinstance(name_binding, str) or not name_binding.strip()):
        raise CliError("NOME_NORA_INVALIDO", "A referência de verificação do nome é inválida.", "Refaça a configuração verificada da rota, sem inventar uma referência.")
    return config


def load_config(path):
    path = Path(path).expanduser()
    try:
        if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
            raise CliError("CONFIGURACAO_NAO_PRIVADA", "O arquivo de configuração precisa ser privado e não pode ser um link.", "No macOS, use chmod 600 no arquivo. No Windows, mantenha-o no perfil privado do usuário.")
    except FileNotFoundError:
        raise CliError("CONFIGURACAO_AUSENTE", "O piloto ainda não tem configuração neste caminho.", "Execute company-pilot --config CAMINHO init --help e faça a configuração inicial.") from None
    return validate_config(_read_file(path, json_value=True))


def build_brain(config):
    from .brain import Brain, GitHubStore, LocalStore
    store = config["store"]
    if store["kind"] == "local":
        backend = LocalStore(store["root"])
    else:
        backend = GitHubStore(store["repo"], branch=store["branch"], mirror_dir=store["mirror"],
                              development=config["mode"] == "synthetic")
    return Brain(backend)


def _device_skips_model(config, brain):
    """The founder machine stores captures. It does not call the analyst model."""
    if config.get("operator", {}).get("analysis") == "external":
        return True
    try:
        actor = brain.store.authenticated_actor()["id"]
        workers = brain.snapshot()["company"].get("semantic_workers") or []
    except Exception:
        return False
    return actor not in workers


def default_interpreter(config):
    from .inference.claude import ClaudeInterpreter
    settings = config.get("operator", {})
    kwargs = {"executable": settings.get("claude_executable")}
    if settings.get("execution_provenance") is not None:
        kwargs["execution_provenance"] = settings["execution_provenance"]
    return ClaudeInterpreter(**kwargs)


def _screenshots(path):
    value = _read_file(path, json_value=True) if path else {"founder": True, "team": True}
    if type(value) is not dict or set(value) != {"founder", "team"} or any(type(item) is not bool for item in value.values()):
        raise CliError("POLITICA_INVALIDA", "Informe as duas escolhas de screenshot como booleanos JSON.", 'Use {"founder": true, "team": true}, com true ou false sem aspas.')
    return value


def _inactive_nora(brain):
    snapshot = brain.snapshot()
    return {"state": "inactive", "route_configured": False, "channel_connectivity": "not_probed",
            "pending_questions": sum(q.get("status") in ("pending", "partially_answered") for q in snapshot.get("questions", [])),
            "media_replies_awaiting_text": sum(a.get("status") == "awaiting_text" for a in (snapshot.get("nora") or {}).get("inbox", {}).values()),
            "availability": "requires_online_nora_host", "schedule_catchup": False,
            "action": "A rota permanece inativa até a configuração e verificação explícitas no ambiente autorizado."}


def build_nora(brain, config):
    setting = config.get("nora", {})
    if setting.get("enabled") is not True:
        return None
    snapshot = brain.snapshot()
    raw = (snapshot.get("nora") or {}).get("route") or {}
    if (setting.get("route_verified") is not True or not isinstance(setting.get("verification_id"), str)
            or not setting["verification_id"].strip() or raw.get("enabled") is not True
            or raw.get("company_id") != snapshot["company"]["id"]
            or raw.get("id") != setting.get("route_id")
            or type(setting.get("route_revision")) is not int or raw.get("revision") != setting["route_revision"]
            or not Path(setting.get("openclaw_executable", "")).is_absolute()):
        raise CliError("ROTA_NORA_NAO_VERIFICADA", "A configuração não corresponde a uma rota Nora habilitada e verificada.", "Mantenha enabled=false até verificar conta, grupo, fundador, versão da rota e executável no host autorizado.")
    from .nora import BrainRepository, Nora, Route
    from .nora.openclaw import OpenClawSender
    route = Route(**raw)
    recipient = {}
    if setting.get("founder_name") and setting.get("founder_name_verification_id") == setting["verification_id"]:
        recipient["founder_name"] = setting["founder_name"].strip()
    return Nora(snapshot["company"]["id"], BrainRepository(snapshot["company"]["id"], brain.transact_nora),
                lambda: datetime.now(timezone.utc), OpenClawSender(setting["openclaw_executable"], route), **recipient)


def _open_trusted_spool(path, setting):
    expected = setting.get("inbox_file")
    if not expected or Path(path).expanduser().absolute() != Path(expected).expanduser().absolute():
        raise CliError("ENTRADA_NAO_VERIFICADA", "O arquivo de entrada não é o arquivo privado vinculado à ponte Nora.", "Configure o caminho exato da entrada autenticada; não importe um JSON arbitrário como mensagem real.")
    descriptor = None
    try:
        if os.name == "nt":
            raise OSError()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError()
        return os.fdopen(descriptor, "rb"), info
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise CliError("ENTRADA_NAO_VERIFICADA", "Não foi possível comprovar a proteção do arquivo de entrada.", "Use o arquivo regular privado da ponte, pertencente ao operador no host autorizado. ACL de entrada Windows não foi validada.") from None


def consume_spool(brain, nora, path, setting, cursor_path, *, decoder=None):
    """Consume bounded JSONL; cursor advances only after durable intake/answer handoff."""
    if decoder is None:
        from .nora.openclaw import inbound_from_record
        decoder = inbound_from_record
    stream, info = _open_trusted_spool(path, setting)
    cursor_path = Path(cursor_path)
    with stream:
        cursor = _read_file(cursor_path, json_value=True) if cursor_path.exists() else {"offset": 0}
        offset = cursor.get("offset")
        if (type(offset) is not int or offset < 0 or offset > info.st_size
                or (offset and (cursor.get("device"), cursor.get("inode")) != (info.st_dev, info.st_ino))):
            raise CliError("ENTRADA_SUBSTITUIDA", "O arquivo da ponte mudou ou foi truncado desde o último recibo.", "Preserve o arquivo, o cursor e a quarentena; peça a reconciliação ao operador antes de retomar.")
        stream.seek(offset)
        received = 0
        for _ in range(50):
            start = stream.tell()
            line = stream.readline(MAX_INPUT + 1)
            if not line:
                break
            if not line.endswith(b"\n") and len(line) <= MAX_INPUT:
                break  # An append may be incomplete; wait for its complete line.
            try:
                if len(line) > MAX_INPUT:
                    raise ValueError()
                raw = json.loads(line.decode("utf-8"), object_pairs_hook=_pairs,
                                 parse_constant=lambda _v: (_ for _ in ()).throw(ValueError()))
                event = decoder(raw)
            except (ValueError, TypeError, UnicodeError, KeyError):
                write_private_json(cursor_path.with_name("nora-inbox-quarantine.json"), {
                    "schema_version": 1, "offset": start, "sha256": hashlib.sha256(line).hexdigest(),
                    "reason": "invalid_record", "record_base64": base64.b64encode(line).decode("ascii")})
                raise CliError("ENTRADA_EM_QUARENTENA", "Uma linha inválida da ponte foi preservada em quarentena privada; o cursor não avançou.", "Revise a ponte e a quarentena no host autorizado. Nenhuma linha foi descartada automaticamente.") from None
            answer = nora.receive(event)
            handoff = drain_nora_inbox(brain)
            if answer.status != "awaiting_text":
                current = brain.snapshot().get("nora", {}).get("inbox", {}).get(answer.source_key, {})
                if current.get("status") != "queued_for_analysis":
                    raise CliError("RESPOSTA_PENDENTE", "A resposta foi preservada, mas sua interpretação ainda não recebeu confirmação.", "Mantenha o operador ativo ou consulte pending. O cursor da ponte continua nesta linha para repetição segura.")
            write_private_json(cursor_path, {"schema_version": 1, "device": info.st_dev, "inode": info.st_ino,
                                            "offset": stream.tell()})
            received += 1
        return {"received_records": received}


def _processing_health(value):
    if not isinstance(value, dict):
        return {"state": "not_verified"}
    try:
        result = {}
        for key in ("pending_job_count", "queued_answer_count", "awaiting_clarification_count"):
            count = value[key]
            if type(count) is not int or count < 0:
                raise ValueError()
            result[key] = count
        for key in ("oldest_pending_at", "oldest_queued_answer_at", "last_analysis_at"):
            timestamp = datetime.fromisoformat(value[key]) if value.get(key) else None
            if timestamp is not None and timestamp.utcoffset() is None:
                raise ValueError()
            result[key] = timestamp.isoformat() if timestamp else None
        current = datetime.now(timezone.utc)
        for field, timestamp in (("oldest_pending_age_seconds", "oldest_pending_at"),
                                 ("oldest_queued_answer_age_seconds", "oldest_queued_answer_at")):
            result[field] = max(0, int((current - datetime.fromisoformat(result[timestamp])).total_seconds())) if result[timestamp] else None
        result.update(interpretation_availability="requires_awake_online_operator",
                      summary=f"{result['pending_job_count']} registro(s) aguardam processamento ou esclarecimento; {result['queued_answer_count']} são respostas já recebidas. Confira a disponibilidade do Mac do operador.")
        if value.get("recommended_route") == "manual":
            from .brain.quotes import MANUAL_INTERVIEW_MESSAGE
            result["recommended_route"] = "manual"
            result["summary"] = result["summary"] + " " + MANUAL_INTERVIEW_MESSAGE
        if value.get("recognition_names_confirmed") is False:
            from .brain.knowledge import RECOGNITION_HEALTH_MESSAGE
            result["recognition_names_confirmed"] = False
            result["summary"] = result["summary"] + " " + RECOGNITION_HEALTH_MESSAGE
        elif value.get("recognition_names_confirmed") is True:
            result["recognition_names_confirmed"] = True
        return result
    except (ValueError, TypeError, KeyError):
        return {"state": "not_verified"}


def _health(path):
    try:
        value = _read_file(path, json_value=True)
        if (type(value) is not dict or value.get("schema_version") != 1
                or value.get("state") not in ("not_started", "running", "attention_required", "stopped")
                or type(value.get("pid")) is not int or value["pid"] < 1
                or type(value.get("consecutive_failures")) is not int or value["consecutive_failures"] < 0):
            raise ValueError()
        result = {key: value[key] for key in ("schema_version", "pid", "state", "consecutive_failures")}
        for key in ("last_tick_at", "last_success_at"):
            timestamp = datetime.fromisoformat(value[key]) if value.get(key) else None
            if timestamp is not None and timestamp.utcoffset() is None:
                raise ValueError()
            result[key] = timestamp.isoformat() if timestamp else None
        result["error_code"] = diagnostic_code(value["error_code"]) if value.get("error_code") else None
        result["receipt_age_seconds"] = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(result["last_tick_at"])).total_seconds())) if result["last_tick_at"] else None
        role = value.get("role", "analysis")
        if role not in ("analysis", "nora"):
            raise ValueError()
        result.update(role=role, availability="requires_online_nora_host" if role == "nora" else "requires_awake_online_operator",
                      channel_connectivity="not_probed", schedule_catchup=False, process_liveness="not_verified")
        if "processing" in value:
            result["processing"] = _processing_health(value["processing"])
        return result
    except (CliError, ValueError, TypeError, KeyError):
        return {"state": "not_recorded", "availability": "not_established", "action": "Inicie operator watch no computador autorizado, acordado e online."}


def enroll_founder(brain, config, login, request_reference, event_id, *, access_factory=RepositoryAccess):
    """Owner-only company designation after existing GitHub access is verified.

    The explicit request reference is checked by the owner-side Agent Room. This
    command neither authenticates a free-text request nor sends an invitation.
    """
    if config["store"]["kind"] != "github":
        raise CliError("OPERADOR_GITHUB_NECESSARIO", "O cadastro do fundador exige o repositório privado e a conta do proprietário.", "Execute esta etapa no computador do operador depois de aceitar o convite de acesso.")
    plan = access_factory(config["store"]["repo"]).plan([{"login": login, "role": "write"}], request_reference=request_reference)
    member = plan["requests"][0]
    if plan["operator"].casefold() != plan["owner"].casefold():
        raise CliError("CONTA_DO_PROPRIETARIO_NECESSARIA", "O cadastro precisa ser aplicado pela conta proprietária já configurada.", "Encaminhe a identidade e o pedido ao operador; mantenha sua própria conta no seu computador.")
    actual = member["actual"]
    if actual != "write" and not (member["login"].casefold() == plan["owner"].casefold() and actual == "admin"):
        raise CliError("ACESSO_DO_FUNDADOR_PENDENTE", "O acesso de escrita do fundador ainda não foi confirmado.", "Confira o convite e as permissões desejadas com o proprietário. Este comando não envia convites nem altera permissões GitHub.")
    founders = list(brain.snapshot()["company"].get("founders", []))
    if member["login"].casefold() not in {value.casefold() for value in founders}:
        founders.append(member["login"])
        receipt = brain.update_policy({"founders": founders}, event_id)
    else:
        receipt = {"state": "unchanged"}
    return {"state": "founder_enrolled", "message": "Fundador já cadastrado." if receipt.get("state") == "unchanged" else "Fundador cadastrado.", "github_login": member["login"],
            "repository_id": plan["repository_id"], "request_reference": request_reference,
            "access_changed": False, "receipt": receipt}


def verify_access(brain, config, event_id, *, access_factory=RepositoryAccess, config_path=None):
    """Inspect native access against the stored request; never invite or apply."""
    if config["store"]["kind"] != "github":
        raise CliError("OPERADOR_GITHUB_NECESSARIO", "A verificação nativa exige o repositório privado.",
                       "Use a configuração GitHub do proprietário. Uma fixture local não comprova acesso real.")
    policy = brain.access_policy()
    if not policy or not policy.get("request_hash"):
        raise CliError("PEDIDO_DE_ACESSO_PENDENTE", "Ainda não há um pedido de acesso registrado pelo fundador.",
                       "Registre o pedido confirmado com policy request-access antes de verificar permissões.")
    receipt = access_factory(config["store"]["repo"]).verify_policy(
        policy["requests"], request_reference=policy["request_reference"], request_hash=policy["request_hash"])
    if event_id is not None:
        return brain.record_access_verification(receipt, event_id)
    if config_path is None:
        raise CliError("CONFIGURACAO_PRIVADA_NECESSARIA", "A identificação automática exige o caminho da configuração privada.",
                       "Use owner verify-access com sua configuração do operador ou forneça um identificador explícito.")
    from .setup import operation
    def receipt_hash(value):
        return ((value.get("verification") or {}).get("receipt") or {}).get("receipt_hash")
    # Inspect native authority BEFORE every attempt; journal only the exact
    # inspected outcome. Returning to an earlier outcome gets a new identity
    # instead of replaying an obsolete success.
    return operation(config_path, "owner-verify-access", receipt["receipt_hash"],
                     lambda event: brain.record_access_verification(receipt, event),
                     state_reader=lambda: receipt_hash(brain.access_policy()), receipt_state=receipt_hash)


def parser():
    p = Parser(prog="company-pilot", description="Instale e opere o piloto de mapa do trabalho da empresa.")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Arquivo privado de configuração (padrão: ~/.company-pilot/config.json).")
    commands = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    release = commands.add_parser("verify-release", help="Conferir o pacote preparado usando apenas acesso ao repositório da empresa.")
    release.add_argument("--company-url", required=True)
    setup = commands.add_parser("setup", help="Configuração guiada, sem JSON ou UUIDs manuais.")
    setup_actions = setup.add_subparsers(dest="action", required=True, parser_class=Parser)
    entry = setup_actions.add_parser("from-url", help="Conferir o START-HERE e gerar a configuração privada.")
    entry.add_argument("company_url")
    entry.add_argument("--role", choices=("founder", "team"), default="founder")
    prep = setup_actions.add_parser("prepare", help="Gerar arquivos locais para publicação revisada pelo operador; não publica.")
    prep.add_argument("--company-url", required=True)
    prep.add_argument("--founder-login", required=True)
    prep.add_argument("--software-repository", required=True)
    prep.add_argument("--code-commit", required=True)
    prep.add_argument("--output-dir", type=Path, required=True)
    setup_actions.add_parser("status", help="Mostrar análise, acesso e pendências atuais.")
    confirm = setup_actions.add_parser("confirm-names", help="Confirmar nomes literais já citados, para reconhecer janelas. Não confirma nada que não for passado.")
    confirm.add_argument("--name", action="append", required=True, help="Nome exato já presente numa fonte. Repita o par com --kind.")
    confirm.add_argument("--kind", action="append", required=True, choices=("record_name", "record_ref", "company_alias", "business_account"))
    confirm.add_argument("--event-id", help="UUID estável para repetir a mesma confirmação.")
    member = setup_actions.add_parser("participant", help="Gerar nota privada para um participante confirmado; não enviar convite.")
    member.add_argument("--name", required=True)
    member.add_argument("--github-login")
    member.add_argument("--confirmed", action="store_true", required=True)
    rights = setup_actions.add_parser("access", help="Registrar o acesso pedido para uma pessoa sem alterar permissão GitHub.")
    rights.add_argument("--person", required=True)
    rights.add_argument("--read", choices=("all", "none", "selected"), required=True)
    rights.add_argument("--modify", choices=("all", "none", "own", "selected"), required=True)
    rights.add_argument("--request-reference", required=True)
    rights.add_argument("--category", action="append", help="Categoria declarada pela pessoa quando pedir acesso selected; continua bloqueado se não suportado.")
    sources = setup_actions.add_parser("sources", help="Gerar a configuração local das fontes, sem conta real e sem identificador digitado.")
    sources.add_argument("--output", type=Path, required=True)
    sources.add_argument("--catalog", type=Path, required=True)
    sources.add_argument("--archive-root", type=Path, required=True)
    sources.add_argument("--source-root", type=Path, required=True)
    images = setup_actions.add_parser("screenshots", help="Registrar separadamente as escolhas da empresa para fundador e equipe.")
    images.add_argument("--founder", choices=("yes", "no"), required=True)
    images.add_argument("--team", choices=("yes", "no"), required=True)
    device_command = setup_actions.add_parser("device", help="Gerar configuração local de coleta desativada, com o destino já conferido.")
    device_command.add_argument("--installation-receipt", type=Path, help="installation.json gerado pelo instalador, para usar o mesmo caminho do agente.")
    consent = setup_actions.add_parser("consent", help="Registrar somente a escolha da própria pessoa e suas exclusões locais.")
    consent.add_argument("--participate", choices=("yes", "no"), required=True)
    consent.add_argument("--screenshots", choices=("yes", "no"), required=True)
    consent.add_argument("--personal-exclusion", action="append", default=[])
    consent.add_argument("--reviewed-defaults", action="store_true", required=True)
    analyst = setup_actions.add_parser("external-analyst", help="Entregar a análise a uma conta e parar o modelo neste computador.")
    analyst.add_argument("--login", required=True, help="Login GitHub do operador que grava a análise. O fundador continua gravando capturas e respostas.")
    analyst.add_argument("--event-id", help="UUID estável da entrega.")
    init = commands.add_parser("init", help="Inicializar o cérebro e gravar a configuração privada.")
    init.add_argument("--name", required=True, help="Nome informado pelo fundador.")
    init.add_argument("--mode", choices=("synthetic", "company"), required=True, help="Separar dados sintéticos dos dados reais da empresa.")
    init.add_argument("--store", choices=("local", "github"), required=True)
    init.add_argument("--root", type=Path, help="Pasta para os dados locais sintéticos.")
    init.add_argument("--repo", help="Repositório privado já existente: dono/repositorio.")
    init.add_argument("--branch", help="Branch de dados: company-state para a empresa; company-state-development[-SUFIXO] para testes sintéticos.")
    init.add_argument("--mirror", type=Path, help="Pasta de mirror retido para verificar o histórico remoto.")
    init.add_argument("--screenshots-file", type=Path, help="JSON com founder e team, cada qual true ou false; padrão: ambos true.")
    commands.add_parser("status", help="Mostrar contagens e disponibilidade, sem conteúdo capturado.")
    repair = commands.add_parser("repair", help="Reparos auditáveis do cérebro, sem reescrever o histórico.")
    repair_actions = repair.add_subparsers(dest="action", required=True, parser_class=Parser)
    conflicts = repair_actions.add_parser("context-conflicts", help="Resolver conflitos de contexto criados sem mode conflict ou por correção fora de ordem.")
    conflicts.add_argument("--dry-run", action="store_true", help="Mostrar o que seria resolvido, sem gravar.")
    conflicts.add_argument("--event-id", help="UUID estável da aplicação. Ignorado no dry-run.")
    snapshot = commands.add_parser("snapshot", help="Ler os registros completos; contém informação privada da empresa.")
    snapshot.add_argument("--output-file", type=Path, help="Gravar o snapshot em arquivo privado em vez de imprimir.")
    commands.add_parser("pending", help="Listar metadados dos trabalhos de interpretação pendentes.")
    interview = commands.add_parser("interview", help="Conversa adaptativa de contexto com o fundador.")
    steps = interview.add_subparsers(dest="step", required=True, parser_class=Parser)
    steps.add_parser("next", help="Ler a próxima pergunta ou o motivo da espera.")
    answer = steps.add_parser("answer", help="Guardar resposta UTF-8 e criar o trabalho de interpretação.")
    text = answer.add_mutually_exclusive_group(required=True)
    text.add_argument("--text-file", type=Path)
    text.add_argument("--text", help="Resposta literal, sem preparar arquivo.")
    answer.add_argument("--event-id", help="UUID estável para repetir o mesmo envio com segurança.")
    for verb, description in (("correct", "Corrigir um assunto do negócio, inclusive após a conversa inicial."),
                              ("defer", "Registrar um assunto desconhecido e manter seu esclarecimento pendente.")):
        update = steps.add_parser(verb, help=description)
        update.add_argument("--topic", required=True, choices=("activities", "people", "stages", "tracking", "documents"))
        text = update.add_mutually_exclusive_group(required=True)
        text.add_argument("--text-file", type=Path, help="Arquivo UTF-8 com o relato literal do fundador.")
        text.add_argument("--text")
        update.add_argument("--event-id", help="Identidade opcional para uma nova declaração idêntica; repetições automáticas mantêm o recibo privado.")
        if verb == "defer": update.add_argument("--delegate-to", help="Pessoa indicada pelo fundador; fica aguardando, sem envio externo.")
    message = commands.add_parser("message", help="Guardar esclarecimento, correção ou contexto do fundador, sem exigir uma pergunta aberta.")
    message_actions = message.add_subparsers(dest="action", required=True, parser_class=Parser)
    message_actions.add_parser("targets", help="Consultar perguntas atuais e tarefas que podem receber correção.")
    send = message_actions.add_parser("send", help="Guardar o relato literal para interpretação; não envia uma mensagem externa.")
    text = send.add_mutually_exclusive_group(required=True)
    text.add_argument("--text-file", type=Path)
    text.add_argument("--text")
    send.add_argument("--event-id", help="Identidade opcional; a retomada usa o recibo privado automaticamente.")
    send.add_argument("--question-id", action="append", dest="question_ids", help="Referência explícita, quando houver; pode ser repetida.")
    policy = commands.add_parser("policy", help="Consultar política, registrar acesso desejado ou a própria participação.")
    actions = policy.add_subparsers(dest="action", required=True, parser_class=Parser)
    actions.add_parser("show", help="Ler a política atual de captura.")
    update = actions.add_parser("set", help="Alterar as escolhas de screenshots do fundador e da equipe.")
    update.add_argument("--json-file", type=Path, required=True)
    update.add_argument("--event-id", help="UUID estável da alteração.")
    request = actions.add_parser("request-access", help="Registrar quem deve poder ler e modificar os registros, sem conceder permissões.")
    request.add_argument("--json-file", type=Path, required=True)
    request.add_argument("--request-reference", required=True)
    request.add_argument("--event-id", required=True)
    participate = actions.add_parser("participation", help="Registrar somente a escolha da própria conta; exclusões pessoais ficam no computador.")
    participate.add_argument("--json-file", type=Path, required=True)
    participate.add_argument("--event-id", required=True)
    watchdog = actions.add_parser("watchdog-actors", help="Autorizar as identidades do coletor central de fontes, por decisão do fundador.")
    watchdog.add_argument("--json-file", type=Path, required=True, help="Lista JSON de identidades autenticadas, sem credenciais.")
    watchdog.add_argument("--event-id", required=True)
    operator = commands.add_parser("operator", help="Interpretar a fila no computador autorizado, acordado e online.")
    operations = operator.add_subparsers(dest="action", required=True, parser_class=Parser)
    operations.add_parser("once", help="Processar no máximo um trabalho e uma verificação Nora.")
    apply = operations.add_parser("apply", help="Aplicar o JSON escrito pelo assistente do fundador, sem o programa claude.")
    apply.add_argument("--file", type=Path, required=True)
    apply.add_argument("--job-id")
    operations.add_parser("commit-literal", help="Guardar o texto da entrevista como foi escrito, quando não houver assistente.")
    prompt = operations.add_parser("prompt", help="Gravar o trabalho pendente e as instruções para o assistente do fundador.")
    prompt.add_argument("--output", type=Path, required=True)
    watch = operations.add_parser("watch", help="Manter análise ativa até Ctrl+C/SIGTERM; intervalo padrão de 60s, sem recuperação de horários perdidos.")
    watch.add_argument("--max-cycles", type=int, help="Limitar as rodadas desta execução.")
    operations.add_parser("health", help="Ler o último recibo local de saúde; não prova que o processo continua vivo.")
    pull = operations.add_parser("pull", help="Sincronizar o mirror dos registros. Com --output, também grava os trabalhos pendentes.")
    pull.add_argument("--output", type=Path, help="Pasta dos arquivos de trabalho, um JSON por análise.")
    export = operations.add_parser("export", help="Gravar os trabalhos pendentes para o analista externo, com texto, anexos, escopo e schema.")
    export.add_argument("--output", type=Path, required=True)
    submit = operations.add_parser("submit", help="Validar o JSON do analista, aplicar e gravar no repositório. Recusa devolve o motivo.")
    submit.add_argument("--job", required=True, help="Id do trabalho exportado.")
    submit.add_argument("--file", type=Path, required=True, dest="result_file")
    nora = commands.add_parser("nora", help="Consultar ou operar somente a rota explicitamente verificada.")
    nora_actions = nora.add_subparsers(dest="action", required=True, parser_class=Parser)
    nora_actions.add_parser("health", help="Ler metadados de pendências, rota e agenda.")
    nora_actions.add_parser("tick", help="Verificar uma rodada no horário normal, se a rota estiver habilitada.")
    nora_watch = nora_actions.add_parser("watch", help="Manter agenda e entrada Nora sem modelo; intervalo padrão de 60s, rota verificada e host online.")
    nora_watch.add_argument("--max-cycles", type=int, help="Limitar as rodadas desta execução.")
    receive = nora_actions.add_parser("receive", help="Receber um registro privado da ponte autenticada e enfileirar sua resposta.")
    receive.add_argument("--record-file", type=Path, required=True, help="Arquivo JSONL privado e autenticado da ponte; o cursor é durável.")
    owner = commands.add_parser("owner", help="Etapas de cadastro que pertencem ao operador proprietário.")
    owner_actions = owner.add_subparsers(dest="action", required=True, parser_class=Parser)
    founder = owner_actions.add_parser("enroll-founder", help="Cadastrar o fundador após conferir seu acesso GitHub existente.")
    founder.add_argument("--github-login", required=True)
    founder.add_argument("--request-reference", required=True, help="Referência ao pedido explícito verificado pelo proprietário.")
    founder.add_argument("--event-id", help="UUID estável do cadastro.")
    verify = owner_actions.add_parser("verify-access", help="Verificar somente por leitura o acesso nativo contra o pedido atual do fundador.")
    verify.add_argument("--event-id", help="UUID explícito opcional; omitido, o comando gera e preserva a identidade da verificação.")
    return p


def _safe_error(error):
    from .setup import SetupError
    if isinstance(error, SetupError):
        messages = {
            "company_url_invalid": "O link precisa apontar para o repositório privado da empresa no GitHub.",
            "immutable_company_url_required": "A entrada precisa identificar uma revisão imutável preparada pelo operador.",
            "company_release_unavailable": "O pacote preparado não está disponível para esta conta.",
            "software_hash_mismatch": "O pacote recebido diverge do hash preparado.",
            "software_commit_mismatch": "A revisão dentro do pacote diverge da revisão preparada.",
            "software_repository_is_not_company_store": "Este destino contém o código do software na raiz; não é o repositório de registros da empresa.",
            "company_destination_mismatch": "O destino dos registros diverge da empresa preparada.",
            "prepared_company_identity_mismatch": "A empresa, o cadastro ou a conta autenticada diverge da preparação.",
            "operation_in_progress_preserve_receipt": "Outra operação ainda usa este recibo, ou o sistema não confirmou a trava. Aguarde a operação e repita o mesmo comando, preservando os arquivos.",
            "clean_exact_source_required": "A preparação exige a árvore limpa na revisão exata testada.",
            "output_exists": "A pasta de saída já existe e foi preservada.",
            "private_company_repository_required": "O destino precisa ser um repositório privado da empresa.",
            "private_output_outside_software_required": "Os arquivos preparados da empresa precisam ficar fora da árvore do software.",
        }
        reason = error.args[0] if len(error.args) == 1 and isinstance(error.args[0], str) else None
        return {"ok": False, "codigo": "PREPARACAO_PENDENTE", "mensagem": messages.get(reason, "Não foi possível confirmar a preparação e o pacote desta empresa."),
                "acao": "Peça ao operador para conferir o START-HERE, a revisão, o cadastro e o recibo privado. Preserve os arquivos e mantenha captura e canal inativos."}
    if isinstance(error, CliError):
        return {"ok": False, "codigo": error.code, "mensagem": error.message, "acao": error.action}
    name = type(error).__name__
    # Only exact internal reason codes are public. Never interpolate exception
    # text: a dependency may include private source text or credentials in it.
    reasons = {
        "brain_not_initialized": (
            "DADOS_NAO_ACEITOS", "O destino consultado não tem um cérebro inicializado.",
            "Confira repositório, branch e modo da configuração com os valores entregues. Peça ao operador para confirmar o cérebro preparado; não execute init para contornar a falha."),
        "founder_authority_required": (
            "ACESSO_NAO_CONFIRMADO", "A conta autenticada não está cadastrada como fundador neste cérebro.",
            "Confira a própria identidade com gh api user. O operador deve verificar o pedido e usar owner enroll-founder; permissão de escrita no repositório não substitui esse cadastro."),
        "development_adapter_accepts_synthetic_only": (
            "ACESSO_NAO_CONFIRMADO", "O adaptador de desenvolvimento recusou dados reais.",
            "Use modo synthetic apenas para dados fictícios. Para a empresa, confirme modo company, armazenamento GitHub e branch company-state com o operador; não copie dados reais para a fixture local."),
        "synthetic_real_boundary_mismatch": (
            "ACESSO_NAO_CONFIRMADO", "O tipo do registro diverge do ambiente de destino.",
            "Confira o modo company ou synthetic e a branch de destino. Preserve a fila e o registro; não altere o rótulo synthetic para forçar o envio."),
        "repository_read_required": (
            "ACESSO_NAO_CONFIRMADO", "A conta autenticada não tem leitura confirmada no repositório.",
            "Confira gh auth status, identidade, repositório e aceite do próprio convite com o operador. Não forneça tokens ao comando."),
        "repository_write_required": (
            "ACESSO_NAO_CONFIRMADO", "A conta autenticada não tem escrita confirmada no repositório.",
            "Confira identidade, repositório e permissão efetiva com o operador. Preserve a fila; não troque de conta nem amplie a permissão para contornar a política do fundador."),
        "configured_semantic_worker_required": (
            "ACESSO_NAO_CONFIRMADO", "Esta conta não grava análise. Só o operador semântico configurado faz isso.",
            "No computador do fundador a análise fica desligada. No computador do operador, use operator export e operator submit."),
        "semantic_operator_login_required": (
            "DADOS_NAO_ACEITOS", "O login do operador semântico não é um login GitHub.",
            "Use o login da conta que vai gravar a análise, sem senha e sem token."),
        "unknown_analysis_job": (
            "DADOS_NAO_ACEITOS", "Esse trabalho não está pendente.",
            "Rode operator export de novo e use o id de um arquivo ainda pendente."),
        "configured_watchdog_actor_required": (
            "ACESSO_NAO_CONFIRMADO", "Esta conta não está autorizada a registrar fontes da empresa pelo watchdog.",
            "O fundador deve conferir a identidade do operador e registrar a decisão com policy watchdog-actors. Não copie credenciais de outra pessoa."),
        "company_human_access_not_verified": (
            "ACESSO_NAO_CONFIRMADO", "O acesso humano aos registros da empresa ainda não está verificado.",
            "Confira policy show e o pedido do fundador. O proprietário deve concluir owner verify-access contra o pedido atual; não amplie o acesso para forçar a sincronização."),
        "marker_name_must_be_literal_in_source": (
            "DADOS_NAO_ACEITOS", "Esse nome não aparece como frase inteira numa fonte já guardada.",
            "Use o nome exatamente como a entrevista ou o registro o citou. Não confirme um nome que ainda não está numa fonte."),
        "app_only_or_generic_marker_not_authorized": (
            "DADOS_NAO_ACEITOS", "Esse nome é genérico demais para reconhecer janelas.",
            "Confirme um nome específico de registro, projeto ou empresa, não o aplicativo sozinho."),
        "invalid_grounded_marker": (
            "DADOS_NAO_ACEITOS", "O nome ou o tipo não pode ser confirmado.",
            "Repita --name e --kind em pares. O tipo é record_name, record_ref, company_alias ou business_account."),
    }
    reason = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
    if name in ("BrainError", "AccessDenied") and reason in reasons:
        code, message, action = reasons[reason]
        return {"ok": False, "codigo": code, "subcodigo": reason, "mensagem": message, "acao": action}
    if name == "IntegrityError":
        code, message, action = "HISTORICO_NAO_VERIFICADO", "A verificação do histórico não foi concluída.", "Preserve o mirror e o alerta. Peça ao responsável para reconciliar a origem antes de retomar."
    elif name in ("AccessDenied", "GitHubError"):
        code, message, action = "ACESSO_NAO_CONFIRMADO", "Não foi possível confirmar o acesso necessário ao repositório.", "Confira gh auth status, a conexão e a permissão nativa da conta no repositório privado. Não forneça tokens ao comando."
    elif name == "ConcurrentWrite":
        code, message, action = "GRAVACAO_PENDENTE", "A alteração não recebeu confirmação após concorrência.", "Consulte status e repita com o mesmo event-id; não apague o mirror."
    elif name in ("ModuleNotFoundError", "ImportError"):
        code, message, action = "INSTALACAO_INCOMPLETA", "Um componente necessário não está instalado.", "Reinstale o pacote completo com Python 3.12 ou posterior; no Windows inclua tzdata."
    elif name in ("ValueError", "TypeError", "KeyError", "BrainError"):
        code, message, action = "DADOS_NAO_ACEITOS", "A operação não foi aceita no estado atual ou contém dados inválidos.", "Confira status, interview next e a configuração. Preserve o event-id para uma repetição idêntica."
    else:
        code, message, action = "OPERACAO_PENDENTE", "A operação não foi confirmada.", "Consulte status e operator health; preserve arquivos e recibos antes de pedir suporte."
    return {"ok": False, "codigo": code, "mensagem": message, "acao": action}


def main(argv=None, *, brain_factory=None, interpreter_factory=None, nora_factory=None, out=None, err=None, stop=None):
    out, err = out or sys.stdout, err or sys.stderr
    try:
        args = parser().parse_args(argv)
        config_path = args.config.expanduser().absolute()
        native_brain = brain_factory is None
        brain_factory, interpreter_factory = brain_factory or build_brain, interpreter_factory or default_interpreter
        nora_factory = nora_factory or build_nora
        from .setup import CompanyEntry, SetupError, generated_config, operation, participant_note, prepare, status as setup_status, device, consent
        if args.command == "verify-release" or (args.command == "setup" and args.action == "from-url"):
            entry = CompanyEntry()
            try:
                handoff, ref, _archive = entry.verify(args.company_url)
            except SetupError:
                raise
            except Exception as error:
                raise SetupError("company_release_unavailable") from error
            result = {"state": "release_verified", **handoff, "entry_commit": ref, "capture_enabled": False, "channel_enabled": False,
                      "message": "Pacote conferido. Versão em revisão: aguarde a liberação do operador para instalar ou operar." if handoff["release_status"] != "pilot_ready" else "Pacote conferido; os demais controles ainda precisam ser verificados."}
            if args.command == "setup":
                config = validate_config(generated_config(config_path, handoff))
                if config_path.exists() and load_config(config_path) != config:
                    raise CliError("CONFIGURACAO_EXISTENTE", "Existe uma configuração diferente neste caminho.", "Preserve a configuração e peça ao operador para conferir o destino; ela não foi substituída.")
                brain = brain_factory(config)
                company = brain.snapshot()["company"]
                actor = brain.store.authenticated_actor()
                if (company["id"] != handoff["company_id"] or company.get("synthetic") is not False
                        or handoff["founder_login"].casefold() not in {x.casefold() for x in company.get("founders", [])}
                        or (args.role == "founder" and actor["id"].casefold() != handoff["founder_login"].casefold())):
                    raise SetupError("prepared_company_identity_mismatch")
                write_private_json(config_path, config)
                result.update(state="configured", config=str(config_path), role=args.role,
                              company_url="https://github.com/" + handoff["company_repository"] + "/tree/" + ref,
                              access=brain.access_policy())
                write_private_json(config_path.with_name("setup-receipt.json"), result)
            _emit(result, out)
            return 0
        if args.command == "init":
            store = {"kind": args.store}
            if args.store == "local":
                store["root"] = str((args.root or config_path.parent / "synthetic-data").expanduser().absolute())
            else:
                branch = args.branch or ("company-state-development" if args.mode == "synthetic" else "company-state")
                store.update(repo=args.repo, branch=branch, mirror=str((args.mirror or config_path.parent / "mirror").expanduser().absolute()))
            config = validate_config({"schema_version": 1, "mode": args.mode, "store": store,
                                      "operator": {"health_file": str(config_path.parent / "operator-health.json")},
                                      "nora": {"enabled": False, "route_verified": False}})
            if config_path.exists() and load_config(config_path) != config:
                raise CliError("CONFIGURACAO_EXISTENTE", "Já existe outra configuração neste caminho.", "Use o arquivo atual ou escolha outro --config; a configuração anterior não foi substituída.")
            if native_brain and args.store == "github":
                CompanyEntry().guard(args.repo)
            brain = brain_factory(config)
            result = brain.initialize(args.name, synthetic=args.mode == "synthetic", screenshots=_screenshots(args.screenshots_file))
            write_private_json(config_path, config)
            _emit({"ok": True, "state": result.get("state"), "company_id": result.get("company_id"), "synthetic": args.mode == "synthetic", "nora": "inactive"}, out)
            return 0
        config = load_config(config_path)
        if args.command == "setup" and args.action == "participant":
            _emit(participant_note(config_path, args.name, args.github_login), out)
            return 0
        setting = config.get("operator", {})
        health_file = setting.get("health_file", str(config_path.parent / "operator-health.json"))
        if args.command == "operator" and args.action == "health":
            _emit(_health(health_file), out)
            return 0
        brain = brain_factory(config)
        if args.command == "setup":
            if args.action == "prepare":
                CompanyEntry().guard(config["store"].get("repo"))
                result = prepare(brain, config, args.company_url, args.founder_login, args.output_dir,
                                 args.software_repository, args.code_commit)
            elif args.action == "status":
                result = setup_status(brain, _health(health_file))
            elif args.action == "confirm-names":
                if len(args.name) != len(args.kind) or len(args.name) > 20:
                    raise CliError("ARGUMENTOS_INVALIDOS", "Cada nome precisa de um tipo, na mesma ordem.", "Repita --name e --kind em pares: record_name, record_ref, company_alias ou business_account.")
                items = [{"value": name, "kind": kind} for name, kind in zip(args.name, args.kind)]
                result = operation(config_path, "confirm-names", items, lambda event: brain.confirm_recognition_names(items, event), args.event_id)
            elif args.action == "sources":
                from .setup import write_source_config
                result = write_source_config(args.output, config_path, args.catalog, args.archive_root, args.source_root, brain.snapshot()["company"]["id"])
            elif args.action == "access":
                request = {"actor_id": args.person, "read_scope": args.read, "modify_scope": args.modify, "record_categories": args.category or []}
                def person_state(policy):
                    return next((item for item in policy.get("requests", [])
                                 if item["actor_id"].casefold() == args.person.casefold()), None)
                receipt = operation(config_path, "access:" + args.person.casefold(), [request, args.request_reference],
                                    lambda event: brain.set_person_access_policy(request, event, args.request_reference),
                                    state_reader=lambda: person_state(brain.access_policy()), receipt_state=person_state)
                current = brain.access_policy()
                if current.get("revision", 0) < receipt["revision"]:
                    current = brain.access_policy()
                if current.get("revision", 0) < receipt["revision"]:
                    raise CliError("POLITICA_ATUAL_PENDENTE", "O pedido foi recebido, mas a leitura atual ainda não confirma essa revisão.", "Preserve o recibo e consulte policy show com o operador; não repita a escolha como nova nem amplie direitos.")
                result = {**current, "operation_receipt": receipt, "requested": current.get("requests", []), "access_changed": False,
                          "message": "Pedido registrado. A permissão GitHub não foi alterada; um pedido incompatível mantém a coleta e a entrevista compartilhada bloqueadas."}
            elif args.action == "device":
                result = device(config_path, config, args.installation_receipt)
            elif args.action == "consent":
                result = consent(config_path, config, brain, args.participate == "yes", args.screenshots == "yes", args.personal_exclusion)
            elif args.action == "external-analyst":
                def analyst_state():
                    snapshot = brain.snapshot()
                    now = datetime.now(timezone.utc)
                    deferred = False
                    for job in snapshot.get("pending_jobs", []):
                        stamp = job.get("next_attempt_at")
                        if job.get("status") == "pending" and isinstance(stamp, str):
                            try:
                                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                            except ValueError:
                                continue
                            if when > now:
                                deferred = True
                    return {"workers": snapshot["company"].get("semantic_workers"), "deferred": deferred}
                result = operation(config_path, "external-analyst", [args.login],
                                   lambda event: brain.assign_external_analyst(args.login, event),
                                   args.event_id, state_reader=analyst_state)
                config.setdefault("operator", {})["analysis"] = "external"
                write_private_json(config_path, config)
                result = {**result, "analysis": "external", "local_model": False,
                          "message": "Este computador não chama mais o modelo. Capturas e respostas continuam sendo gravadas. A análise fica com o operador " + args.login + "."}
            else:
                choices = {"founder": args.founder == "yes", "team": args.team == "yes"}
                result = operation(config_path, "screenshots", choices, lambda event: brain.update_policy({"screenshots": choices}, event),
                                   state_reader=lambda: brain.snapshot()["company"].get("screenshots"))
            _emit(result, out)
            return 0
        if args.command == "owner":
            result = (verify_access(brain, config, args.event_id, config_path=config_path) if args.action == "verify-access" else
                      enroll_founder(brain, config, args.github_login, args.request_reference, args.event_id or str(uuid4())))
            _emit(result, out)
            return 0
        if args.command == "repair":
            if args.dry_run:
                result = brain.repair_context_conflicts(dry_run=True)
            else:
                def fingerprint():
                    return [item["key"] for item in brain.repair_context_conflicts(dry_run=True)["repairable"]]
                result = operation(config_path, "repair-context-conflicts", ["context-conflicts"],
                                   lambda event: brain.repair_context_conflicts(event), args.event_id, state_reader=fingerprint)
            _emit(result, out)
            return 0
        if args.command == "status":
            from . import __version__
            from .brain.quotes import manual_interview_route
            snapshot = brain.snapshot()
            report = {"state": "ready", "version": __version__, "synthetic": snapshot["company"]["synthetic"], "store": config["store"]["kind"],
                      "task_count": len(snapshot.get("tasks", [])), "pending_job_count": len(snapshot.get("pending_jobs", [])),
                      "pending_question_count": sum(q.get("status") in ("pending", "partially_answered") for q in snapshot.get("questions", [])),
                      "operator": _health(health_file), "nora_enabled_in_config": config.get("nora", {}).get("enabled", False),
                      "online_storage_is_not_operator_availability": True}
            route = manual_interview_route(snapshot.get("pending_jobs", []))
            if route:
                report.update(recommended_route="manual", founder_message=route["message"], manual_commands=route["commands"])
            _emit(report, out)
        elif args.command == "snapshot":
            result = brain.snapshot()
            if args.output_file:
                write_private_json(args.output_file, result)
                _emit({"ok": True, "state": "snapshot_saved"}, out)
            else:
                _emit(result, out)
        elif args.command == "pending":
            # Include jobs deferred by retry or awaiting assignment; do not hide them as 'no work'.
            jobs = brain.snapshot().get("pending_jobs", [])
            keys = {"id", "kind", "status", "created_at", "age_seconds", "attempts", "next_attempt_at"}
            _emit({"jobs": [{key: job[key] for key in keys if key in job} for job in jobs]}, out)
        elif args.command == "interview":
            if args.step == "next":
                result = brain.interview_next()
                if result.get("state") == "question":
                    write_private_json(config_path.with_name("interview-prompt.json"), result)
            else:
                text = args.text if args.text is not None else _read_file(args.text_file)
                if not text.strip() or len(text.encode()) > 1024 * 1024:
                    raise CliError("RESPOSTA_INVALIDA", "A resposta precisa conter texto e caber no limite de um MiB.", "Use seu relato literal ou um arquivo de texto menor.")
                def submit(event):
                    if args.step == "answer": return brain.interview_answer(text, event)
                    if args.step == "correct": return brain.interview_correct(args.topic, text, event)
                    if args.delegate_to: return brain.interview_defer(args.topic, text, event, delegate_to=args.delegate_to)
                    return brain.interview_defer(args.topic, text, event)
                prompt_path = config_path.with_name("interview-prompt.json")
                prompt = _read_file(prompt_path, json_value=True) if args.step == "answer" and prompt_path.exists() else None
                result = operation(config_path, "interview_" + args.step,
                                   [text, getattr(args, "topic", None), getattr(args, "delegate_to", None), prompt], submit, args.event_id)
            _emit(result, out)
        elif args.command == "message":
            if args.action == "targets": result = brain.message_targets()
            else:
                text = args.text if args.text is not None else _read_file(args.text_file)
                result = operation(config_path, "message", [text, args.question_ids],
                                   lambda event: brain.record_message(text, event, question_ids=args.question_ids), args.event_id)
            _emit(result, out)
        elif args.command == "policy":
            if args.action == "show":
                result = brain.device_policy()
                # Onboarding needs the actual requested/effective matrix and
                # blockers, not only the device's ready/pending summary.
                result["access_policy"] = brain.access_policy()
            elif args.action == "set":
                result = brain.update_policy({"screenshots": _screenshots(args.json_file)}, args.event_id or str(uuid4()))
            else:
                value = _read_file(args.json_file, json_value=True)
                _reject_secrets(value)
                if args.action == "request-access":
                    result = brain.set_access_policy(value, args.event_id, args.request_reference)
                elif args.action == "watchdog-actors":
                    result = brain.update_policy({"watchdog_actors": value}, args.event_id)
                else:
                    result = brain.set_participation(value, args.event_id)
            _emit(result, out)
        elif args.command == "operator":
            if args.action in ("pull", "export", "submit"):
                from .inference.external import export_jobs, load_result, pull_records, submit_analysis
                if args.action == "pull":
                    result = pull_records(brain)
                    if args.output:
                        result.update(export_jobs(brain, args.output))
                    _emit(result, out)
                    return 0
                if args.action == "export":
                    _emit(export_jobs(brain, args.output), out)
                    return 0
                try:
                    payload = load_result(args.result_file)
                except (OSError, UnicodeError, ValueError, TypeError):
                    raise CliError("ARQUIVO_INVALIDO", "Não foi possível ler o JSON da análise.", "Grave um objeto JSON UTF-8 de até 1 MiB e envie de novo o mesmo trabalho.") from None
                result = submit_analysis(brain, args.job, payload)
                _emit(result, out)
                return 0 if result.get("ok") else 2
            external_message = {"status": "external", "analysis": "not_run", "local_model": False,
                                "message": "Este computador não analisa. Ele só separa o pessoal do trabalho e envia o trabalho. O operador externo usa operator pull, operator export e operator submit."}
            if _device_skips_model(config, brain) and args.action in ("once", "watch", "apply", "commit-literal"):
                _emit(external_message, out)
                return 0
            if args.action == "prompt":
                jobs = brain.snapshot().get("pending_jobs", [])
                write_private_json(args.output, {"instrucao": "Leia as fontes citadas e escreva somente o que elas sustentam. Não invente fato, não envie mensagem e não ligue captura. O operador externo grava o JSON com operator submit --job ID --file. Este arquivo não chama modelo.", "jobs": jobs})
                _emit({"ok": True, "state": "prompt_written", "path": str(args.output), "pending": len(jobs)}, out)
                return 0
            if args.action == "commit-literal":
                from .brain import TOPICS
                jobs = [job for job in brain.snapshot().get("pending_jobs", []) if job["kind"] == "interview" and job["status"] == "pending"]
                if not jobs:
                    _emit({"ok": True, "state": "nothing_pending"}, out)
                    return 0
                job = jobs[0]
                source = next(item for item in brain.snapshot()["sources"] if item["id"] == job["source_ids"][0])
                topic = (source.get("question") or {}).get("topic") or source.get("context_topic")
                if topic not in TOPICS:
                    raise CliError("DADOS_NAO_ACEITOS", "A resposta guardada não está num assunto da entrevista.", "Rode interview next e responda de novo se a pergunta ainda estiver aberta.")
                result = {"tasks": [], "nodes": [], "context_updates": [{"topic": topic, "value": source["text"], "basis": "reported", "evidence": [{"source_id": source["id"], "quote": source["text"]}], "mode": "add"}], "next_question": None}
                receipt = brain.apply_analysis(job["id"], result, metadata={"execution_provider": "founder_assistant", "execution_model": "literal_fallback", "execution_origin": "commit_literal", "execution_identity_basis": "founder_text"})
                _emit({"ok": True, "state": receipt.get("state", "applied"), "topic": topic}, out)
                return 0
            if args.action == "apply":
                import json as _json
                payload = _json.loads(args.file.read_text(encoding="utf-8"))
                job_id = args.job_id or next(job["id"] for job in brain.snapshot().get("pending_jobs", []) if job["status"] == "pending")
                receipt = brain.apply_analysis(job_id, payload, metadata={"execution_provider": "founder_assistant", "execution_model": "assistant_file", "execution_origin": "operator_apply", "execution_identity_basis": "assistant_file"})
                _emit({"ok": True, "state": receipt.get("state", "applied"), "job_id": job_id}, out)
                return 0
            from .inference.worker import SemanticWorker
            worker = SemanticWorker(brain, interpreter_factory(config))
            nora = nora_factory(brain, config)
            nora_setting = config.get("nora", {})
            cursor_path = nora_setting.get("cursor_file", str(config_path.parent / "nora-inbox-cursor.json"))
            intake = None
            if nora is not None and nora_setting.get("inbox_file"):
                intake = lambda: consume_spool(brain, nora, nora_setting["inbox_file"], nora_setting, cursor_path)
            operator = Operator(worker, brain=brain, nora=nora, intake=intake, health_file=health_file,
                                poll_seconds=setting.get("poll_seconds", 60), retry_initial_seconds=setting.get("retry_initial_seconds", 5),
                                retry_max_seconds=setting.get("retry_max_seconds", 60), max_failures=setting.get("max_failures", 5), stop=stop)
            if args.action == "once":
                result = operator.once()
                operator._save("attention_required" if operator.failures else "stopped")
                _emit(result, out)
                return 3 if operator.failures or result["status"] == "pending" else 0
            return operator.watch(lambda result: _emit(result, out), max_cycles=args.max_cycles)
        elif args.command == "nora":
            nora_setting = config.get("nora", {})
            nora_health_file = nora_setting.get("health_file", str(config_path.parent / "nora-health.json"))
            if args.action == "watch":
                if nora_setting.get("enabled") is not True:
                    raise CliError("ROTA_NORA_INATIVA", "A agenda Nora não está habilitada.", "Mantenha o serviço parado até concluir a configuração e a verificação explícitas da rota.")
                if nora_setting.get("route_verified") is not True or not isinstance(nora_setting.get("verification_id"), str) or not nora_setting["verification_id"].strip():
                    raise CliError("ROTA_NORA_NAO_VERIFICADA", "A agenda exige a referência de uma rota verificada.", "Confira conta, conversa, fundador e processo no host autorizado antes de iniciar nora watch.")
                if Path(nora_health_file).expanduser().resolve() == Path(health_file).expanduser().resolve():
                    raise CliError("SAUDE_NORA_SEPARADA", "Nora e análise precisam de recibos de saúde separados.", "Escolha nora.health_file diferente de operator.health_file.")
            nora = nora_factory(brain, config)
            if nora is None:
                if args.action in ("receive", "watch"):
                    raise CliError("ROTA_NORA_INATIVA", "A entrada Nora não está habilitada.", "Conclua a verificação explícita da rota e da ponte antes de importar respostas.")
                _emit({**_inactive_nora(brain), "worker_health": _health(nora_health_file)}, out)
                return 0
            if args.action == "watch":
                cursor_path = nora_setting.get("cursor_file", str(config_path.parent / "nora-inbox-cursor.json"))
                intake = None
                if nora_setting.get("inbox_file"):
                    intake = lambda: consume_spool(brain, nora, nora_setting["inbox_file"], nora_setting, cursor_path)
                operator = Operator(None, brain=brain, nora=nora, intake=intake, health_file=nora_health_file,
                                    poll_seconds=nora_setting.get("poll_seconds", 60),
                                    retry_initial_seconds=nora_setting.get("retry_initial_seconds", 5),
                                    retry_max_seconds=nora_setting.get("retry_max_seconds", 60),
                                    max_failures=nora_setting.get("max_failures", 5), stop=stop)
                return operator.watch(lambda result: _emit(result, out), max_cycles=args.max_cycles)
            if args.action == "health":
                from .nora import health
                project_questions(brain, nora)
                _emit({**health(nora), "worker_health": _health(nora_health_file)}, out)
            elif args.action == "tick":
                project_questions(brain, nora)
                intake = drain_nora_inbox(brain)
                tick = nora.tick()
                _emit({"outcome": tick.outcome, "pending_count": tick.pending_count, "overflow_count": tick.overflow_count,
                       "next_scheduled_at": tick.next_scheduled_at.isoformat(), **intake}, out)
            else:
                setting = config.get("nora", {})
                cursor_path = setting.get("cursor_file", str(config_path.parent / "nora-inbox-cursor.json"))
                project_questions(brain, nora)
                result = consume_spool(brain, nora, args.record_file, setting, cursor_path)
                if result["received_records"]:
                    project_questions(brain, nora)
                _emit(result, out)
        return 0
    except SystemExit as outcome:
        return int(outcome.code or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        _emit(_safe_error(error), err)
        return 2
