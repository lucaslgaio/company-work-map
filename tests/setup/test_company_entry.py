"""Company-only preparation and actual CLI/Brain, with all GitHub calls fake.

NEW fixtures, no original Grok exports, permissions/native/model/channel proof.
"""
import base64
import copy
from hashlib import sha256
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from company_pilot.access import Response
from company_pilot.brain import Brain
from company_pilot.cli import enroll_founder, main, validate_config
from company_pilot.operator import write_private_json
from company_pilot.setup import ARTIFACT, HANDOFF, CompanyEntry, SetupError, archive_entries, prepare
from company_pilot.setup_bootstrap import download, extract

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('setup_fake_github', ROOT/'tests/brain/test_github_store.py')
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
REPO='synthetic-owner/synthetic-company'; SOFTWARE='synthetic-owner/synthetic-software'; ENTRY='e'*40; BLOB='b'*40

class CompanyOnlyAPI:
    def __init__(self, files): self.files=files; self.calls=[]; self.code_root=False; self.private=True
    def __call__(self, route, method='GET', body=None):
        self.calls.append((route,method))
        if method!='GET' or not route.startswith('/repos/'+REPO): raise AssertionError('second authority or mutation')
        if route=='/repos/'+REPO: return Response(200,{'full_name':REPO,'private':self.private,'default_branch':'entry','size':10})
        if '/contents/company_pilot/__init__.py?' in route: return Response(200 if self.code_root else 404,{'type':'file'})
        if route.endswith('/commits/entry'): return Response(200,{'sha':ENTRY})
        if '/contents?ref=' in route: return Response(200,[{'path':'company_pilot' if self.code_root else '.company-pilot'}])
        for path,data in self.files.items():
            if route=='/repos/'+REPO+'/contents/'+path+'?ref='+ENTRY:
                if path==ARTIFACT: return Response(200,{'type':'file','size':len(data),'sha':BLOB,'encoding':'none'})
                return Response(200,{'type':'file','size':len(data),'encoding':'base64','content':base64.b64encode(data).decode()})
        if route.endswith('/git/blobs/'+BLOB): return Response(200,{'encoding':'base64','content':base64.b64encode(self.files[ARTIFACT]).decode()})
        return Response(404)

class EntryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_tmp=tempfile.TemporaryDirectory(); cls.source=Path(cls.source_tmp.name)
        for name in ('company_pilot','install','contracts/v1'):
            shutil.copytree(ROOT/name,cls.source/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (cls.source/'founder').mkdir()
        for name in ('configuracao-fundador.md','configuracao-equipe.md','fontes-empresa.md','proposta.md'):
            shutil.copy2(ROOT/'founder'/name,cls.source/'founder'/name)
        for name in ('docs/operator-setup.md','START-HERE.md','AGENTS.md',
                     'deploy/nora-vps/README.md','deploy/nora-vps/render_bundle.py'):
            path=cls.source/name; path.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ROOT/name,path)
        shutil.copytree(ROOT/'deploy/nora-vps/templates',cls.source/'deploy/nora-vps/templates')
        write_private_json(cls.source/'founder/pilot-release.json',{'repository':SOFTWARE,'code_commit':'a'*40,'release_status':'review_candidate'})
        for name in ('README.md','pyproject.toml'): shutil.copy2(ROOT/name,cls.source/name)
        # Excluded publication corpora and private local config, even if tracked.
        (cls.source/'docs/extra').mkdir(parents=True,exist_ok=True); (cls.source/'docs/extra/should-not-distribute.txt').write_text('review corpus')
        (cls.source/'private-config.json').write_text('private local sentinel')
        env=dict(os.environ,GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_SYSTEM=os.devnull)
        def git(*args): return subprocess.run(['git','-C',str(cls.source),*args],env=env,check=True,capture_output=True).stdout
        git('init','--quiet'); git('add','.')
        git('-c','user.name=Setup fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Explicit synthetic distribution fixture')
        cls.commit=git('rev-parse','HEAD').decode().strip()
    @classmethod
    def tearDownClass(cls): cls.source_tmp.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.store=fixture.FakeGitHub(self.root/'mirror'); self.store.development=False; self.store.login='synthetic-owner'
        self.brain=Brain(self.store); self.brain.initialize('Empresa fictícia de setup',synthetic=False)
        class Access:
            def __init__(self, repo): pass
            def plan(self, requests, request_reference):
                return {'operator':'synthetic-owner','owner':'synthetic-owner','repository_id':123,
                        'requests':[{'login':'nina','actual':'write'}]}
        enroll_founder(self.brain,{'store':{'kind':'github','repo':REPO}},'nina','Pedido explícito fictício',
                       'f1a84245-2745-4b70-b944-6530436bc1e3',access_factory=Access)
        self.operator_config={'schema_version':1,'mode':'company','store':{'kind':'github','repo':REPO,'branch':'company-state','mirror':str(self.root/'operator-mirror')}}
        self.prepared=prepare(self.brain,self.operator_config,'https://github.com/'+REPO,'nina',self.root/'prepared',SOFTWARE,self.commit,source=self.source)
        self.files={path:(self.root/'prepared'/path).read_bytes() for path in (HANDOFF,ARTIFACT)}
        self.api=CompanyOnlyAPI(self.files); self.config=self.root/'founder/config.json'
        self.store.login='nina'; self.store.permissions={'pull':True,'push':True,'admin':False}
    def tearDown(self): self.tmp.cleanup()
    def cli(self,*args):
        out,err=StringIO(),StringIO()
        with patch('company_pilot.setup.GitHubAPI',return_value=self.api):
            code=main(['--config',str(self.config),*args],brain_factory=lambda _:self.brain,out=out,err=err)
        return code,json.loads(out.getvalue() or err.getvalue())
    def connect(self):
        code,value=self.cli('setup','from-url','https://github.com/'+REPO+'/tree/'+ENTRY)
        self.assertEqual(code,0,value); return value
    def test_fresh_prepared_company_only_flow_and_minimal_artifact(self):
        result=self.connect(); cfg=validate_config(json.loads(self.config.read_text()))
        self.assertEqual(cfg['store']['repo'],REPO); self.assertEqual(cfg['software_repository'],SOFTWARE)
        self.assertEqual(cfg['store']['branch'],'company-state'); self.assertFalse(cfg['nora']['enabled'])
        self.assertFalse(result['capture_enabled']); self.assertEqual(result['release_status'],'review_candidate')
        self.assertTrue(any('/git/blobs/' in route for route,_ in self.api.calls))
        self.assertTrue(all(route.startswith('/repos/'+REPO) for route,_ in self.api.calls))
        with archive_entries(self.files[ARTIFACT]) as archive:
            self.assertEqual(archive.comment.decode(),self.commit)
            self.assertFalse(any(name.startswith('tests/') or 'private-config' in name or 'should-not-distribute' in name for name in archive.namelist()))
    def test_distribution_includes_conditional_sources_operator_and_proposal_assets(self):
        with archive_entries(self.files[ARTIFACT]) as archive:
            names=set(archive.namelist())
        required={'founder/fontes-empresa.md','founder/proposta.md','docs/operator-setup.md','START-HERE.md','AGENTS.md',
                  'deploy/nora-vps/README.md','deploy/nora-vps/render_bundle.py'}
        self.assertTrue(required<=names,required-names)
        self.assertFalse(any(name.startswith('docs/extra/') for name in names))
    def test_hash_mismatch_fails_before_config_or_canonical_write(self):
        before=self.store.head; self.files[ARTIFACT]+=b'changed'
        code,value=self.cli('setup','from-url','https://github.com/'+REPO+'/tree/'+ENTRY)
        self.assertEqual(code,2); self.assertIn('hash',value['mensagem']); self.assertIn('operador',value['acao'])
        self.assertFalse(self.config.exists()); self.assertEqual(before,self.store.head)
    def test_code_root_rejected_but_nested_software_allowed(self):
        self.api.code_root=True
        code,value=self.cli('verify-release','--company-url','https://github.com/'+REPO+'/tree/'+ENTRY)
        self.assertEqual(code,2); self.assertIn('código',value['mensagem'])
        self.api.code_root=False; self.assertEqual(self.cli('verify-release','--company-url','https://github.com/'+REPO+'/tree/'+ENTRY)[0],0)
    def test_wrong_company_id_founder_or_public_destination_refused(self):
        for key,value in (('company_id','wrong-company'),('founder_login','other-founder')):
            original=self.files[HANDOFF]; handoff=json.loads(original); handoff[key]=value
            self.files[HANDOFF]=json.dumps(handoff).encode()
            self.assertEqual(self.cli('setup','from-url','https://github.com/'+REPO+'/tree/'+ENTRY)[0],2)
            self.assertFalse(self.config.exists()); self.files[HANDOFF]=original
        self.api.private=False; self.assertEqual(self.cli('verify-release','--company-url','https://github.com/'+REPO+'/tree/'+ENTRY)[0],2)
    def test_access_read_only_preserved_and_intervening_choices_get_new_identity(self):
        self.connect()
        for modify in ('all','all','none','all'):
            code,value=self.cli('setup','access','--person','nina','--read','all','--modify',modify,'--request-reference','Pedido fictício')
            self.assertEqual(code,0,value)
            self.assertEqual(self.brain.access_policy()['requests'][0]['modify_scope'],modify)
            if modify=='none':
                self.assertEqual(self.brain.access_policy()['state'],'unsupported')
                self.assertFalse(self.brain.device_policy()['controls']['capture_allowed'])
        sources=[s for s in self.brain.snapshot()['sources'] if s['kind']=='access_policy_request']
        self.assertEqual(len(sources),3)
    def test_unknown_ack_after_actual_brain_commit_has_one_source_job_and_event(self):
        self.connect(); self.cli('interview','next'); actual=self.brain.interview_answer; calls=[]
        def unknown(text,event):
            result=actual(text,event); calls.append(event)
            if len(calls)==1: raise OSError('lost result')
            return result
        with patch.object(self.brain,'interview_answer',side_effect=unknown):
            self.assertEqual(self.cli('interview','answer','--text','A empresa entrega relatórios semanais.')[0],2)
            self.assertEqual(self.cli('interview','answer','--text','A empresa entrega relatórios semanais.')[0],0)
        self.assertEqual(calls[0],calls[1]); snapshot=self.brain.snapshot()
        self.assertEqual(len([s for s in snapshot['sources'] if s['kind']=='interview']),1)
        self.assertEqual(len(snapshot['pending_jobs']),1)
    def test_same_text_new_prompt_is_distinct_and_revoked_write_never_cached(self):
        self.connect(); events=[]
        with patch.object(self.brain,'interview_next',return_value={'state':'question','topic':'activities','question':'Atividades?'}), patch.object(self.brain,'interview_answer',side_effect=lambda text,event:events.append(event) or {'state':'pending_analysis'}):
            self.cli('interview','next'); self.cli('interview','answer','--text','Sim.'); self.cli('interview','answer','--text','Sim.')
            with patch.object(self.brain,'interview_next',return_value={'state':'question','topic':'people','question':'Pessoas?'}):
                self.cli('interview','next'); self.cli('interview','answer','--text','Sim.')
        self.assertEqual(events[0],events[1]); self.assertNotEqual(events[1],events[2])
        self.assertEqual(self.cli('setup','screenshots','--founder','yes','--team','no')[0],0)
        self.store.permissions['push']=False
        self.assertEqual(self.cli('setup','screenshots','--founder','yes','--team','no')[0],2)
    def test_bootstrap_real_blob_decoding_and_safe_extraction(self):
        def run(command,**kwargs):
            response=self.api(command[-1]); return type('Result',(),{'returncode':0 if response.status==200 else 1,'stdout':json.dumps(response.data)})()
        with patch('company_pilot.setup_bootstrap.subprocess.run',side_effect=run):
            handoff,data,url=download('https://github.com/'+REPO+'/tree/'+ENTRY)
        target=self.root/'extracted'; extract(data,target,handoff['code_commit'])
        self.assertTrue((target/'company_pilot/cli.py').is_file()); self.assertTrue(url.endswith(ENTRY))
        unsafe=BytesIO()
        with zipfile.ZipFile(unsafe,'w') as archive:
            archive.comment=self.commit.encode(); archive.writestr('../escape','no')
        with self.assertRaises(ValueError): extract(unsafe.getvalue(),self.root/'unsafe',self.commit)
        self.assertFalse((self.root/'unsafe').exists()); self.assertFalse((self.root/'escape').exists())

    def test_installer_device_and_consent_share_actual_path_and_keep_terms_private(self):
        from company_pilot.desktop.github_transport import BrainTransport
        self.connect(); actual_path=self.root/'installed/desktop.json'
        receipt=self.root/'installation.json'
        write_private_json(receipt,{'schema_version':1,'startup':'files_generated_not_registered','config':str(actual_path)})
        with patch('company_pilot.desktop.__main__.make_transport',return_value=BrainTransport(self.brain)):
            code,value=self.cli('setup','device','--installation-receipt',str(receipt))
        self.assertEqual(code,0,value); self.assertTrue(actual_path.is_file())
        self.assertEqual(Path(json.loads((self.config.parent/'device-receipt.json').read_text())['config']),actual_path)
        for choice in ('yes','yes','no','yes'):
            code,value=self.cli('setup','consent','--participate',choice,'--screenshots','no','--reviewed-defaults',
                                '--personal-exclusion','FRASE PESSOAL DO TESTE')
            self.assertEqual(code,0,value)
            self.assertEqual(self.brain.snapshot()['company']['participation']['nina']['participating'],choice=='yes')
        self.assertFalse(json.loads(actual_path.read_text())['capture_enabled'])
        self.assertIn('FRASE PESSOAL DO TESTE',(actual_path.parent/'personal-policy.json').read_text())
        snapshot=self.store.read()
        self.assertNotIn('FRASE PESSOAL DO TESTE',''.join(snapshot.files.values()))
        self.assertEqual(len([s for s in self.brain.snapshot()['sources'] if s['kind']=='participation_choice']),3)

    def test_team_entry_uses_own_identity_without_founder_or_consent_grant(self):
        self.store.login='pessoa a'
        code,value=self.cli('setup','from-url','https://github.com/'+REPO+'/tree/'+ENTRY,'--role','team')
        self.assertEqual(code,0,value); self.assertEqual(value['role'],'team')
        self.assertNotIn('pessoa a',self.brain.snapshot()['company']['founders'])
        self.assertNotIn('pessoa a',self.brain.snapshot()['company'].get('participation',{}))

    def test_commit_provenance_not_only_content_hash(self):
        handoff=json.loads(self.files[HANDOFF]); handoff['code_commit']='0'*40
        self.files[HANDOFF]=json.dumps(handoff).encode()
        self.assertEqual(self.cli('verify-release','--company-url','https://github.com/'+REPO+'/tree/'+ENTRY)[0],2)

    def test_legacy_desktop_configure_rejects_code_root_before_transport_and_accepts_nested(self):
        from contextlib import redirect_stdout, redirect_stderr
        from company_pilot.desktop.__main__ import main as desktop_main
        from company_pilot.desktop.github_transport import BrainTransport
        path=self.root/'legacy/desktop.json'; out,err=StringIO(),StringIO()
        self.api.code_root=True
        with patch('company_pilot.setup.GitHubAPI',return_value=self.api), patch('company_pilot.desktop.__main__.make_transport') as factory, redirect_stdout(out), redirect_stderr(err):
            code=desktop_main(['configure','--config',str(path),'--repo',REPO])
        self.assertEqual(code,2); self.assertFalse(path.exists()); factory.assert_not_called()
        self.assertEqual(json.loads(err.getvalue())['code'],'software_repository_is_not_company_store')
        self.api.code_root=False
        with patch('company_pilot.setup.GitHubAPI',return_value=self.api), patch('company_pilot.desktop.__main__.make_transport',return_value=BrainTransport(self.brain)), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code=desktop_main(['configure','--config',str(path),'--repo',REPO])
        self.assertEqual(code,0); self.assertEqual(json.loads(path.read_text())['transport']['repo'],REPO)
        self.assertFalse(json.loads(path.read_text())['capture_enabled'])

    def test_participant_note_preserves_entry_and_marks_missing_or_mismatched_receipt_pending(self):
        self.connect(); before=self.store.head
        expected='https://github.com/'+REPO+'/tree/'+ENTRY
        code,value=self.cli('setup','participant','--name','Ana','--confirmed')
        self.assertEqual(code,0,value)
        text=Path(value['note']).read_text()
        self.assertIn(expected,text); self.assertIn('--role team',text)
        self.assertEqual(value['company_url'],expected); self.assertEqual(value['entry_state'],'verified_setup_receipt')
        self.assertFalse(value['sent']); self.assertEqual(before,self.store.head)
        receipt=self.config.with_name('setup-receipt.json'); original=json.loads(receipt.read_text())
        for field,wrong in (('company_repository','other/company'),('entry_commit','f'*40),('company_url','https://github.com/'+REPO),('code_commit','0'*40)):
            record=copy.deepcopy(original); record[field]=wrong; write_private_json(receipt,record)
            code,value=self.cli('setup','participant','--name','Ana','--confirmed')
            self.assertEqual(code,0,value); self.assertEqual(value['entry_state'],'pending'); self.assertIsNone(value['company_url'])
            text=Path(value['note']).read_text(); self.assertIn('Entrada pendente',text); self.assertNotIn('https://github.com/',text)
        receipt.unlink()
        code,value=self.cli('setup','participant','--name','Ana','--confirmed')
        self.assertEqual(code,0,value); self.assertEqual(value['entry_state'],'pending'); self.assertIsNone(value['company_url'])

    def test_independent_config_access_cas_rebase_keeps_both_restrictive_choices(self):
        from company_pilot.access import policy_request_hash
        self.connect(); other=self.root/'other-device/config.json'
        write_private_json(other,json.loads(self.config.read_text()))
        actual_commit=self.store.commit; interleaved=[]
        def competing_commit(expected,files,message):
            if not interleaved and message.endswith('access_policy_person_request'):
                interleaved.append(True)
                out,err=StringIO(),StringIO()
                code=main(['--config',str(other),'setup','access','--person','alice','--read','all','--modify','none',
                           '--request-reference','Alice restrictive choice'],brain_factory=lambda _:self.brain,out=out,err=err)
                self.assertEqual(code,0,out.getvalue()+err.getvalue())
            return actual_commit(expected,files,message)
        with patch.object(self.store,'commit',side_effect=competing_commit):
            code,value=self.cli('setup','access','--person','bob','--read','all','--modify','none','--request-reference','Bob restrictive choice')
        self.assertEqual(code,0,value); self.assertTrue(interleaved)
        current=self.brain.access_policy(); requests={row['actor_id']:row for row in current['requests']}
        self.assertEqual(set(requests),{'alice','bob'})
        self.assertTrue(all(row['modify_scope']=='none' for row in requests.values()))
        self.assertEqual(current['state'],'unsupported'); self.assertFalse(self.brain.device_policy()['controls']['capture_allowed'])
        self.assertEqual(current['request_hash'],policy_request_hash(current['requests'],current['request_reference']))
        self.assertEqual(len([s for s in self.brain.snapshot()['sources'] if s['kind']=='access_policy_request']),2)

    def test_unknown_person_result_reuses_id_after_other_person_choice_and_reads_current_policy(self):
        self.connect(); actual=self.brain.set_person_access_policy; bob_ids=[]
        def lose_once(request,event,reference):
            result=actual(request,event,reference)
            if request['actor_id']=='bob':
                bob_ids.append(event)
                if len(bob_ids)==1: raise OSError('unknown canonical acknowledgement')
            return result
        bob=('setup','access','--person','bob','--read','all','--modify','none','--request-reference','Bob restrictive choice')
        with patch.object(self.brain,'set_person_access_policy',side_effect=lose_once):
            self.assertEqual(self.cli(*bob)[0],2)
            self.assertEqual(self.cli('setup','access','--person','alice','--read','all','--modify','none','--request-reference','Alice restrictive choice')[0],0)
            before=self.store.head; code,value=self.cli(*bob)
            self.assertEqual(code,0,value); self.assertEqual(before,self.store.head)
            self.assertEqual(bob_ids[0],bob_ids[1])
            self.assertEqual({row['actor_id'] for row in value['requested']},{'alice','bob'})
            self.assertGreater(value['revision'],value['operation_receipt']['revision'])
            # An unrelated policy refresh cannot turn the next known retry into a new Bob intent.
            self.assertEqual(self.cli(*bob)[0],0); self.assertEqual(bob_ids[-1],bob_ids[0]); self.assertEqual(before,self.store.head)
        self.assertEqual(len([s for s in self.brain.snapshot()['sources'] if s['kind']=='access_policy_request']),2)

    def test_full_matrix_api_stays_replacement_and_person_merge_keeps_authority_validation(self):
        from uuid import uuid4
        from company_pilot.brain import AccessDenied, BrainError
        self.connect()
        alice={'actor_id':'alice','read_scope':'all','modify_scope':'none','record_categories':[]}
        bob={**alice,'actor_id':'bob'}
        self.brain.set_person_access_policy(alice,str(uuid4()),'Alice explicit choice')
        self.brain.set_access_policy([bob],str(uuid4()),'Explicit complete replacement')
        self.assertEqual([row['actor_id'] for row in self.brain.access_policy()['requests']],['bob'])
        before=self.store.head; self.store.login='not-founder'
        with self.assertRaises(AccessDenied): self.brain.set_person_access_policy(alice,str(uuid4()),'Not founder')
        self.assertEqual(before,self.store.head); self.store.login='nina'
        with self.assertRaises(BrainError): self.brain.set_person_access_policy({**alice,'read_scope':'selected'},str(uuid4()),'Missing category')
        self.assertEqual(before,self.store.head)

if __name__=='__main__': unittest.main()
