"""Company-only entry, local preparation and durable human-input operations.

This module owns no company lifecycle and never publishes or grants access.
Canonical writes go through Brain, native rights through the existing verifier.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile
from urllib.parse import quote, urlparse
from uuid import uuid4

from .access import GitHubAPI
from .operator import write_private_json

HANDOFF = '.company-pilot/handoff.json'
ARTIFACT = '.company-pilot/software.zip'
SLUG = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_ARCHIVE = 100 * 1024 * 1024
DISTRIBUTION_PATHS = (
    'company_pilot', 'install', 'contracts/v1',
    'founder/configuracao-fundador.md', 'founder/configuracao-equipe.md', 'founder/fontes-empresa.md',
    'founder/proposta.md', 'founder/pilot-release.json',
    'docs/operator-setup.md', 'START-HERE.md', 'AGENTS.md',
    'deploy/nora-vps/README.md', 'deploy/nora-vps/render_bundle.py', 'deploy/nora-vps/templates',
    'README.md', 'pyproject.toml',
)


class SetupError(ValueError):
    pass


def company_url(value):
    parsed = urlparse(value)
    parts = parsed.path.strip('/').split('/')
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or len(parts) not in (2, 4):
        raise SetupError('company_url_invalid')
    repo = '/'.join(parts[:2])
    if not SLUG.fullmatch(repo) or any(p in ('.', '..') for p in parts):
        raise SetupError('company_url_invalid')
    ref = None
    if len(parts) == 4:
        if parts[2] != 'tree' or not SHA.fullmatch(parts[3]):
            raise SetupError('immutable_company_url_required')
        ref = parts[3]
    return repo, ref


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise SetupError('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def archive_entries(data):
    if len(data) > MAX_ARCHIVE: raise SetupError('software_too_large')
    archive = zipfile.ZipFile(io.BytesIO(data))
    names, total = set(), 0
    for entry in archive.infolist():
        name = PurePosixPath(entry.filename)
        mode = entry.external_attr >> 16
        if (not entry.filename or entry.filename.startswith('/') or '\\' in entry.filename or ':' in entry.filename
                or '..' in name.parts or name.as_posix() in names or mode & 0o170000 == 0o120000):
            raise SetupError('unsafe_software_archive')
        names.add(name.as_posix()); total += entry.file_size
        if len(names) > 20000 or total > MAX_ARCHIVE * 5: raise SetupError('software_too_large')
    required = {'company_pilot/__main__.py', 'company_pilot/cli.py', 'company_pilot/setup.py',
                'founder/pilot-release.json', 'founder/configuracao-fundador.md', 'founder/configuracao-equipe.md',
                'founder/fontes-empresa.md', 'founder/proposta.md',
                'docs/operator-setup.md', 'START-HERE.md', 'AGENTS.md', 'deploy/nora-vps/README.md',
                'install/distribution.md'}
    if not required <= names: raise SetupError('software_incomplete')
    return archive


class CompanyEntry:
    def __init__(self, api=None): self.api = api or GitHubAPI()

    def get(self, route, *, absent=False):
        response = self.api(route)
        if absent and response.status == 404: return None
        if response.status != 200: raise SetupError('company_release_unavailable')
        return response.data

    def guard(self, repo, ref=None):
        metadata = self.get('/repos/' + repo)
        if metadata.get('private') is not True or metadata.get('full_name', '').casefold() != repo.casefold():
            raise SetupError('private_company_repository_required')
        # Only the root signature counts. A nested, prepared software archive is legitimate.
        branch = ref or metadata.get('default_branch', 'main')
        marker = self.api('/repos/' + repo + '/contents/company_pilot/__init__.py?ref=' + quote(branch, safe=''))
        if marker.status == 200: raise SetupError('software_repository_is_not_company_store')
        if marker.status != 404 and not (marker.status == 409 and metadata.get('size') == 0):
            raise SetupError('company_release_unavailable')
        return metadata

    def content(self, repo, path, ref):
        record = self.get('/repos/' + repo + '/contents/' + path + '?ref=' + ref)
        if record.get('type') != 'file': raise SetupError('company_release_invalid')
        if record.get('size', 0) > MAX_ARCHIVE: raise SetupError('software_too_large')
        if record.get('encoding') == 'base64' and record.get('content') is not None:
            data = base64.b64decode(record['content'].replace('\n', ''), validate=True)
        else:
            blob_sha = record.get('sha', '')
            if not SHA.fullmatch(blob_sha): raise SetupError('company_release_invalid')
            blob = self.get('/repos/' + repo + '/git/blobs/' + blob_sha)
            if blob.get('encoding') != 'base64': raise SetupError('company_release_invalid')
            data = base64.b64decode(blob['content'].replace('\n', ''), validate=True)
        return data

    def verify(self, url):
        repo, ref = company_url(url)
        metadata = self.guard(repo, ref)
        if not ref:
            commit = self.get('/repos/' + repo + '/commits/' + quote(metadata.get('default_branch', 'main'), safe=''))
            ref = commit.get('sha', '')
        if not SHA.fullmatch(ref): raise SetupError('company_release_invalid')
        value = _json(self.content(repo, HANDOFF, ref))
        fields = {'schema_version', 'company_repository', 'state_branch', 'company_id', 'founder_login', 'operator_login',
                  'software_repository', 'code_commit', 'software_sha256', 'release_status'}
        if set(value) != fields or value['schema_version'] != 1: raise SetupError('handoff_invalid')
        if (value['company_repository'].casefold() != repo.casefold() or value['state_branch'] != 'company-state'
                or not SLUG.fullmatch(value['software_repository'])
                or value['software_repository'].casefold() == repo.casefold()
                or not SHA.fullmatch(value['code_commit'])
                or not re.fullmatch(r'[0-9a-f]{64}', value['software_sha256'])
                or value['release_status'] not in ('review_candidate', 'pilot_ready')
                or not value['company_id']
                or any(not re.fullmatch(r'[A-Za-z0-9-]{1,39}', value[key]) for key in ('founder_login','operator_login'))):
            raise SetupError('handoff_invalid')
        # Also reject a code-root default branch when the entry lives on another branch.
        if metadata.get('default_branch') != ref: self.guard(repo)
        data = self.content(repo, ARTIFACT, ref)
        if hashlib.sha256(data).hexdigest() != value['software_sha256']: raise SetupError('software_hash_mismatch')
        with archive_entries(data) as archive:
            if archive.comment.decode('ascii') != value['code_commit']:
                raise SetupError('software_commit_mismatch')
            embedded = _json(archive.read('founder/pilot-release.json'))
            if embedded.get('repository') != value['software_repository'] or embedded.get('release_status') != value['release_status']:
                raise SetupError('software_provenance_invalid')
        return value, ref, data


def write_source_config(path, company_config, catalog, archive_root, source_root, company_id):
    """Write the watchdog file. No account is connected and capture stays off."""
    value = {
        "schema_version": 1,
        "company_id": company_id,
        "company_config": str(company_config),
        "catalog": str(catalog),
        "enabled": False,
        "source_scope_reference": "preparado-localmente",
        "interval_seconds": 300,
        "archive": {"kind": "local", "root": str(archive_root)},
        "sources": [{"id": "fonte-local", "kind": "directory", "root": str(source_root)}],
    }
    write_private_json(Path(path), value)
    return {"state": "sources_prepared", "path": str(path), "enabled": False, "connected_accounts": False,
            "message": "A configuração foi gerada. Nenhuma conta real foi conectada e a leitura continua desligada."}


def prepare(brain, config, url, founder_login, directory, software_repository, code_commit, *, source=None):
    """Generate reviewed publication files locally, from one exact committed tree."""
    repo, _ = company_url(url)
    if (config['store']['kind'] != 'github' or config['mode'] != 'company' or config['store']['repo'].casefold() != repo.casefold()
            or config['store']['branch'] != 'company-state' or not SLUG.fullmatch(software_repository)
            or repo.casefold() == software_repository.casefold() or not SHA.fullmatch(code_commit)):
        raise SetupError('company_destination_mismatch')
    source = Path(source or Path(__file__).resolve().parents[1])
    directory = Path(directory).expanduser().resolve()
    if directory == source.resolve() or source.resolve() in directory.parents:
        raise SetupError('private_output_outside_software_required')
    def git(*args):
        return subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True).stdout
    if git('rev-parse', 'HEAD').decode().strip() != code_commit or git('status', '--porcelain').strip():
        raise SetupError('clean_exact_source_required')
    state = brain.snapshot(); company = state['company']; actor = brain.store.authenticated_actor()
    if 'admin' not in actor.get('permissions', []) or actor['id'].casefold() != repo.split('/')[0].casefold():
        raise SetupError('owner_preparation_required')
    if company.get('synthetic') is not False or founder_login.casefold() not in {x.casefold() for x in company.get('founders', [])}:
        raise SetupError('prepared_founder_required')
    manifest = _json(git('show', code_commit + ':founder/pilot-release.json'))
    if manifest.get('repository') != software_repository or manifest.get('release_status') not in ('review_candidate','pilot_ready'):
        raise SetupError('software_provenance_invalid')
    data = git('archive', '--format=zip', code_commit, *DISTRIBUTION_PATHS)
    archive_entries(data).close()
    if directory.exists():
        if any(directory.iterdir()):
            raise SetupError('output_exists')
    else:
        directory.mkdir(parents=True, mode=0o700)
    (directory / '.company-pilot').mkdir(mode=0o700)
    value = dict(schema_version=1, company_repository=repo, state_branch='company-state', company_id=company['id'],
                 founder_login=founder_login, operator_login=actor['id'], software_repository=software_repository,
                 code_commit=code_commit, software_sha256=hashlib.sha256(data).hexdigest(), release_status=manifest['release_status'])
    write_private_json(directory/HANDOFF, value)
    (directory/ARTIFACT).write_bytes(data)
    (directory/'bootstrap.py').write_bytes(git('show', code_commit + ':company_pilot/setup_bootstrap.py'))
    (directory/'START-HERE.md').write_text(
        '# Começar a configuração\n\n**' + ('Versão em revisão; aguarde a liberação do operador para instalar ou operar.' if value['release_status'] != 'pilot_ready' else 'Confira a liberação desta versão com o operador.') + '**\n\n'
        'Entregue este único link privado a seu assistente com acesso a comandos. Entre na sua própria conta GitHub e aceite o acesso a este repositório.\n\n'
        'Assistente: resolva o commit deste link pela API GitHub e baixe bootstrap.py dessa mesma revisão. Confira Python 3.12+ e GitHub CLI. '
        'Execute `python bootstrap.py URL_DESTE_REPOSITORIO --destination PASTA_PRIVADA_NOVA`. No PowerShell use o Python detectado. '
        'O comando verifica o pacote preparado, mantém captura/canal inativos e gera a configuração; não pede acesso ao repositório de software. '
        'Com estado review_candidate, limite-se à revisão autorizada; instalação na empresa aguarda o operador.\n\n'
        'Concluído quando o recibo identifica esta empresa, versão, sua conta e pendências. Leia então o arquivo founder/configuracao-fundador.md do pacote verificado. '
        'Falha de pacote, cadastro ou acesso: preserve o recibo e encaminhe ao operador.\n', encoding='utf-8')
    return {'state':'prepared_locally', 'directory':str(directory), **value, 'published':False, 'capture_enabled':False}


@contextmanager
def _operation_lock(path):
    """Kernel-owned lock: process death releases it without deleting an inode.

    Keep the file in place: unlinking it can split concurrent callers across
    different lock inodes. No age/PID heuristic may steal a live operation.
    """
    if path.is_symlink(): raise SetupError('private_operation_receipt_required')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and (info.st_mode & 0o077 or info.st_uid != os.getuid())):
            raise SetupError('private_operation_receipt_required')
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SetupError('operation_in_progress_preserve_receipt') from error
        yield
    finally:
        os.close(fd)


def operation(config_path, kind, payload, call, explicit_id=None, *, state_reader=None, receipt_state=None):
    """Persist identity before an effect; successful and unknown retries share ID.

    Immediate identical input replays. A different intervening intent or changed
    target state starts a fresh operation. Unknown outcomes retain their ID.
    The journal stores hashes/receipts only, no raw private founder response.
    """
    if explicit_id: return call(explicit_id)
    path = Path(config_path).with_name('operation-receipts.json')
    config = _json(Path(config_path).read_bytes())
    store = config['store']
    target = [config['mode'], store.get('kind'), store.get('repo'), store.get('branch'), store.get('root')]
    config_digest = hashlib.sha256(json.dumps(target).encode()).hexdigest()
    channel = hashlib.sha256(json.dumps([config_digest, kind]).encode()).hexdigest()
    key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Exclusive lock prevents two assistant processes generating distinct IDs.
    lock = path.with_suffix('.lock')
    with _operation_lock(lock):
        if path.is_symlink() or (path.exists() and os.name != 'nt' and path.stat().st_mode & 0o077):
            raise SetupError('private_operation_receipt_required')
        journal = _json(path.read_bytes()) if path.exists() else {'schema_version':1,'latest':{},'operations':{}}
        if journal.get('schema_version') != 1: raise SetupError('private_operation_receipt_required')
        record = journal['operations'].get(journal['latest'].get(channel))
        # A pending operation may already have committed remotely. Replay its
        # stable intent before considering state changes; they cannot redefine
        # an unknown-result retry. New intents need no speculative state read.
        current = state_reader() if state_reader and record and record['state'] == 'complete' else None
        if (record is None or record.get('input_hash') != key
                or (record['state'] == 'complete' and state_reader and record.get('post_state') != current)):
            record = {'event_id':str(uuid4()), 'state':'awaiting_result','input_hash':key}
            journal['operations'][record['event_id']] = record
            journal['latest'][channel] = record['event_id']
            write_private_json(path, journal)
        # Always revisit the canonical caller: never cache authentication or
        # return an old success after current repository authority was revoked.
        result = call(record['event_id'])
        post_state = receipt_state(result) if receipt_state else state_reader() if state_reader else None
        record.update(state='complete', result=result, post_state=post_state)
        write_private_json(path, journal)
        return result


def generated_config(path, handoff):
    root = Path(path).parent
    return {'schema_version':1, 'mode':'company', 'company_repository':handoff['company_repository'],
            'software_repository':handoff['software_repository'], 'code_commit':handoff['code_commit'],
            'store':{'kind':'github','repo':handoff['company_repository'],'branch':handoff['state_branch'],'mirror':str(root/'mirror')},
            'operator':{'health_file':str(root/'operator-health.json')}, 'nora':{'enabled':False,'route_verified':False}}


def _verified_setup_entry(config_path, config):
    """Reuse only a private from-url receipt bound to this exact destination.

    A bare repository URL may open company-state, so it is never a fallback.
    This is a retained verification receipt, not a new remote-access claim.
    """
    path = Path(config_path).with_name('setup-receipt.json')
    try:
        if path.is_symlink(): return None
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024
                    or (os.name != 'nt' and (info.st_mode & 0o077 or info.st_uid != os.getuid()))):
                return None
            receipt = _json(stream.read())
        repo, ref = company_url(receipt['company_url'])
        if (receipt.get('schema_version') != 1 or receipt.get('state') != 'configured'
                or receipt.get('release_status') not in ('review_candidate', 'pilot_ready')
                or ref is None or ref != receipt.get('entry_commit')
                or config['store'].get('kind') != 'github' or config['mode'] != 'company'
                or repo.casefold() != config['store']['repo'].casefold()
                or receipt['company_repository'] != config.get('company_repository')
                or receipt['company_repository'].casefold() != repo.casefold()
                or receipt.get('state_branch') != config['store'].get('branch')
                or receipt.get('software_repository') != config.get('software_repository')
                or receipt.get('code_commit') != config.get('code_commit')
                or receipt.get('config') != str(Path(config_path).expanduser().absolute())):
            return None
        return receipt['company_url']
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def participant_note(config_path, name, login=None):
    if not isinstance(name,str) or not name.strip() or len(name)>160 or any(ord(c)<32 for c in name): raise SetupError('participant_invalid')
    if login is not None and not re.fullmatch(r'[A-Za-z0-9-]{1,39}',login): raise SetupError('participant_invalid')
    config = _json(Path(config_path).read_bytes())
    url = _verified_setup_entry(config_path, config)
    state = 'aceite_pendente' if login else 'convite_pendente'
    key = hashlib.sha256((name+'\0'+str(login)).encode()).hexdigest()[:16]
    directory = Path(config_path).parent/'participant-notes'; directory.mkdir(exist_ok=True, mode=0o700)
    path = directory/(key+'.md')
    safe_name = name.replace('`','').replace('<','').replace('>','')
    entry = (f'Abra {url} com sua própria conta após receber e aceitar o acesso. '
             'Peça ao assistente para seguir o START-HERE dessa revisão e usar `--role team` no bootstrap, '
             'depois o roteiro founder/configuracao-equipe.md do pacote verificado. '
             if url else '**Entrada pendente:** aguarde o link imutável da empresa com START-HERE conferido pelo operador. ')
    text = (f'# Configuração individual — {safe_name}\n\n'
            + ('**Convite pendente:** falta confirmar sua conta GitHub e o proprietário conferir o acesso escolhido pelo fundador.\n\n' if not login else f'Conta confirmada pelo fundador: {login}. Acesso efetivo e aceite pessoal ainda precisam ser verificados.\n\n')
            + 'O fundador indicou você para participar. ' + entry +
            'Você decide se participa e se permite imagens; prepare suas exclusões pessoais apenas no seu computador. '
            'Nenhum aceite foi dado por você e a captura continua bloqueada até os controles serem verificados.\n\n'
            'Este texto está pronto para encaminhamento pelo fundador. Nenhum convite ou mensagem foi enviado pelo comando.\n')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(text)
    return {'state':state,'name':name,'github_login':login,'note':str(path),'company_url':url,
            'entry_state':'verified_setup_receipt' if url else 'pending',
            'sent':False,'participation':'pending','capture_enabled':False}


def status(brain, health):
    try:
        from .brain.diagnostics import REJECTIONS
    except ImportError:  # Older prepared cores have only generic failure codes.
        REJECTIONS = {}
    snapshot = brain.snapshot()
    jobs = [{key:job[key] for key in ('id','status','kind','created_at','age_seconds','attempts','next_attempt_at','error_code') if key in job}
            for job in snapshot.get('pending_jobs',[])]
    for row, job in zip(jobs, snapshot.get('pending_jobs', [])):
        code = job.get('rejection_reason')
        if code in REJECTIONS:
            row.update(rejection_reason=code, reason=REJECTIONS[code])
        elif row.get('error_code'):
            row['reason'] = 'A análise não foi aplicada. O operador precisa conferir o código e a próxima tentativa, preservando a resposta.'
    interview = brain.interview_next()
    result = {'state':'setup_status','interview':interview,'jobs':jobs,'access':brain.access_policy(),
            'operator':health,'process_liveness':'not_verified','wait_estimate':None,
            'message':'O armazenamento online não mantém a análise funcionando. O operador deve manter operator watch ativo, acordado e online. Pendências e falhas acima permanecem abertas.'}
    if interview.get('recognition'):
        result['recognition'] = interview['recognition']
    return result


def device_path(config_path):
    receipt = Path(config_path).with_name('device-receipt.json')
    path = Path(_json(receipt.read_bytes())['config']) if receipt.exists() else Path(config_path).with_name('desktop.json')
    if not path.is_absolute(): raise SetupError('device_receipt_invalid')
    return path


def device(config_path, config, installation_receipt=None):
    from contextlib import redirect_stdout, redirect_stderr
    from .desktop.__main__ import main
    from .desktop.config import read_config
    if installation_receipt:
        installation = _json(Path(installation_receipt).read_bytes())
        if installation.get('schema_version') != 1 or installation.get('startup') != 'files_generated_not_registered':
            raise SetupError('installation_receipt_invalid')
        path = Path(installation['config'])
        if not path.is_absolute(): raise SetupError('installation_receipt_invalid')
    else:
        path = device_path(config_path)
    if config['mode'] != 'company' or config['store']['kind'] != 'github': raise SetupError('company_device_required')
    if path.exists():
        current = read_config(path); target = current.get('transport', {})
        if (target.get('repo') != config['store']['repo'] or target.get('branch') != config['store']['branch']
                or target.get('synthetic') is not False): raise SetupError('existing_device_destination_mismatch')
        result = {'state':'device_already_configured','config':str(path),'capture_enabled':current.get('capture_enabled',False)}
        write_private_json(Path(config_path).with_name('device-receipt.json'), result)
        return result
    output, error = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(error):
        code = main(['configure','--config',str(path),'--repo',config['store']['repo'],'--branch',config['store']['branch']])
    if code: raise SetupError('device_setup_pending')
    result = _json(output.getvalue())
    write_private_json(Path(config_path).with_name('device-receipt.json'), {'config':str(path),'capture_enabled':False})
    return result


def consent(config_path, config, brain, participating, screenshots, exclusions):
    from .desktop.config import read_config
    from .desktop.privacy import configure_private_policy
    path = device_path(config_path); current = read_config(path)
    target = current.get('transport', {})
    if (target.get('repo') != config['store'].get('repo') or target.get('branch') != config['store'].get('branch')
            or target.get('synthetic') is not False or config['mode'] != 'company'):
        raise SetupError('consent_destination_mismatch')
    policy_version = brain.snapshot()['company']['policy_version']
    current['capture_enabled'] = False
    result = configure_private_policy(path, current, {'participating':participating, 'screenshots':screenshots,
                                      'reviewed_defaults':True, 'personal_exclusions':exclusions}, policy_version)
    receipt = result['participation']
    recorded = operation(config_path, 'personal_consent', receipt, lambda event: brain.set_participation(receipt, event))
    return {'state':'own_choices_recorded','receipt':recorded,'private_policy_file':result['private_policy_file'],
            'capture_enabled':False,'message':'Escolhas da própria conta registradas. Os termos pessoais ficaram neste computador; confira os controles e o diagnóstico antes de ativar.'}
