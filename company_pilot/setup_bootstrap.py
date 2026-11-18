"""Standalone company-only bootstrap. Download this file from the entry commit.

Uses Python 3.12+ and an existing authenticated gh CLI; installs no service and
never captures work, changes permissions, sends messages or reads the code repo.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlparse
import zipfile


def download(url):
    parsed=urlparse(url); parts=parsed.path.strip('/').split('/')
    if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment
            or len(parts) not in (2,4) or any(p in ('.','..') for p in parts)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+','/'.join(parts[:2]))):
        raise ValueError('invalid_company_url')
    repo='/'.join(parts[:2]); ref=parts[3] if len(parts)==4 and parts[2]=='tree' else None
    if len(parts)==4 and (ref is None or not re.fullmatch(r'[0-9a-f]{40}',ref)): raise ValueError('invalid_entry_commit')
    def get(path):
        # Every endpoint stays under the one company authority.
        result=subprocess.run(['gh','api','--hostname','github.com','/repos/'+repo+path],capture_output=True,text=True,
                              timeout=60,env=dict(os.environ,GH_PROMPT_DISABLED='1'))
        if result.returncode: raise ValueError('company_release_unavailable')
        return json.loads(result.stdout)
    metadata=get('')
    if metadata.get('private') is not True or metadata.get('full_name','').casefold()!=repo.casefold(): raise ValueError('private_company_required')
    if not ref: ref=get('/commits/'+quote(metadata.get('default_branch','main'),safe=''))['sha']
    if not re.fullmatch(r'[0-9a-f]{40}',ref): raise ValueError('invalid_entry_commit')
    for branch in {ref, metadata.get('default_branch','main')}:
        root=get('/contents?ref='+quote(branch,safe=''))
        if (not isinstance(root,list) or len(root)>=1000
                or any(item.get('path')=='company_pilot' for item in root)):
            raise ValueError('software_repository_is_not_company_store')
    def content(path):
        record=get('/contents/'+path+'?ref='+ref)
        if record.get('type')!='file': raise ValueError('invalid_release_file')
        if record.get('size',0)>100*1024*1024: raise ValueError('software_too_large')
        if record.get('encoding')!='base64':
            sha=record.get('sha','')
            if not re.fullmatch(r'[0-9a-f]{40}',sha): raise ValueError('invalid_blob')
            record=get('/git/blobs/'+sha)
        if record.get('encoding')!='base64': raise ValueError('invalid_blob')
        return base64.b64decode(record['content'].replace('\n',''),validate=True)
    handoff=json.loads(content('.company-pilot/handoff.json'))
    if (handoff.get('schema_version')!=1 or handoff.get('company_repository','').casefold()!=repo.casefold()
            or handoff.get('state_branch')!='company-state'
            or handoff.get('software_repository','').casefold()==repo.casefold()
            or handoff.get('release_status') not in ('review_candidate','pilot_ready')
            or not re.fullmatch(r'[0-9a-f]{40}',handoff.get('code_commit',''))): raise ValueError('invalid_handoff')
    data=content('.company-pilot/software.zip')
    if len(data)>100*1024*1024 or hashlib.sha256(data).hexdigest()!=handoff.get('software_sha256'): raise ValueError('software_hash_mismatch')
    return handoff,data,'https://github.com/'+repo+'/tree/'+ref


def extract(data,destination,code_commit):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names=set(); total=0
        if archive.comment.decode('ascii')!=code_commit: raise ValueError('software_commit_mismatch')
        for item in archive.infolist():
            path=PurePosixPath(item.filename); total+=item.file_size
            if (item.filename.startswith('/') or '\\' in item.filename or ':' in item.filename or '..' in path.parts
                    or path.as_posix() in names or item.external_attr>>16 & 0o170000==0o120000): raise ValueError('unsafe_archive')
            names.add(path.as_posix())
        if total>500*1024*1024 or len(names)>20000 or not {'company_pilot/setup.py','company_pilot/__main__.py'}<=names:
            raise ValueError('unsafe_archive')
        # Validate every member before writing anything, into a new private folder.
        destination.mkdir(mode=0o700)
        archive.extractall(destination)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Conferir o pacote do START-HERE usando só o acesso à empresa.')
    parser.add_argument('company_url'); parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path.home()/'.company-pilot/config.json')
    parser.add_argument('--role',choices=('founder','team'),default='founder')
    args=parser.parse_args(argv)
    try:
        if sys.version_info<(3,12): raise ValueError('python_312_required')
        destination=args.destination.expanduser().absolute()
        if destination.exists(): raise ValueError('destination_exists')
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        handoff,data,pinned_url=download(args.company_url)
        extract(data,destination,handoff['code_commit'])
        result=subprocess.run([sys.executable,'-m','company_pilot','--config',str(args.config.expanduser().absolute()),
                               'setup','from-url',pinned_url,'--role',args.role],cwd=destination,check=False)
        print(json.dumps({'source_directory':str(destination),'interpreter':sys.executable,'config':str(args.config.expanduser().absolute()),
                          'company_url':pinned_url,'capture_enabled':False,'channel_enabled':False,
                          'setup_completed':result.returncode==0,'release_status':handoff['release_status']},ensure_ascii=False))
        return result.returncode
    except Exception:
        print('Preparação não confirmada. Preserve os arquivos e peça ao operador para conferir o START-HERE, a versão e o acesso. Captura e canal continuam inativos.',file=sys.stderr)
        return 2


if __name__=='__main__': raise SystemExit(main())
