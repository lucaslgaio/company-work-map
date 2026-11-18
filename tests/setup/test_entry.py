"""NEW report-derived F4/F15/F17 actual callers; never a full Grok replay."""
from io import StringIO
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from company_pilot.cli import main, enroll_founder, validate_config, default_interpreter, CliError
from company_pilot.operator import write_private_json
from company_pilot.desktop.preflight import local_tools

class SetupEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.config = self.root/'config.json'
        write_private_json(self.config, {'schema_version':1,'mode':'synthetic','store':{'kind':'local','root':str(self.root/'brain')}})
    def tearDown(self): self.tmp.cleanup()
    def call(self, *args, brain=None):
        out, err = StringIO(), StringIO()
        code = main(['--config',str(self.config),*args], brain_factory=lambda _: brain, out=out, err=err)
        return code, json.loads(out.getvalue() or err.getvalue())
    def test_plain_text_answer_unknown_result_replays_same_event(self):
        calls=[]
        class Brain:
            def interview_answer(self,text,event):
                calls.append((text,event))
                if len(calls)==1: raise OSError('unknown acknowledgement')
                return {'state':'pending_analysis'}
        self.assertEqual(self.call('interview','answer','--text','A empresa entrega relatórios.',brain=Brain())[0],2)
        self.assertEqual(self.call('interview','answer','--text','A empresa entrega relatórios.',brain=Brain())[0],0)
        self.assertEqual(calls[0],calls[1]); self.assertEqual(len(calls),2)
    def test_confirmed_participant_without_login_gets_unsent_note(self):
        code, value=self.call('setup','participant','--name','Ana','--confirmed')
        self.assertEqual(code,0); self.assertEqual(value['state'],'convite_pendente')
        self.assertFalse(value['sent']); self.assertIn('Ana',Path(value['note']).read_text())
        self.assertIn('convite pendente',Path(value['note']).read_text().lower())
    def test_available_tools_do_not_ask_installation(self):
        with patch('company_pilot.desktop.preflight.shutil.which',return_value='/fake/gh'):
            checks=local_tools({'transport':{'kind':'github'}},'Windows',injected_native=True)
        self.assertFalse(checks['github_cli'].get('next_step'))
    def test_verify_release_parser_exists_and_names_operator_on_failure(self):
        with patch('company_pilot.access.GitHubAPI.__call__',side_effect=OSError('private dependency detail')):
            code,value=self.call('verify-release','--company-url','https://github.com/fixture/company')
        self.assertEqual(code,2); self.assertIn('operador',value['acao'])
        self.assertNotIn('private dependency',json.dumps(value))
    def test_configured_execution_identity_reaches_canonical_interpreter(self):
        config=json.loads(self.config.read_text())
        identity={'provider':'fixture-provider','model':'fixture-model','origin':'queue'}
        config['operator']={'execution_provenance':identity}
        validate_config(config)
        with patch('company_pilot.inference.claude.ClaudeInterpreter') as constructor:
            default_interpreter(config)
            constructor.assert_called_once_with(executable=None,execution_provenance=identity)
        config['operator']['execution_provenance']['origin']='guessed'
        with self.assertRaises(CliError): validate_config(config)
    def test_delegation_passes_literal_person_to_canonical_seam(self):
        calls=[]
        class Brain:
            def interview_defer(self,topic,text,event,*,delegate_to=None):
                calls.append((topic,text,event,delegate_to))
                return {'state':'awaiting_person','delegate_to':delegate_to}
        code,value=self.call('interview','defer','--topic','tracking','--text','Ainda não sei. Ana sabe.',
                             '--delegate-to','Ana',brain=Brain())
        self.assertEqual(code,0,value); self.assertEqual(value['state'],'awaiting_person')
        self.assertEqual(calls[0][3],'Ana')
    def test_concurrent_live_cli_cannot_steal_operation_lock(self):
        from company_pilot.brain import Brain, LocalStore
        brain=Brain(LocalStore(self.root/'brain',actor='fixture-owner'))
        brain.initialize('Empresa fictícia',synthetic=True)
        actual=brain.update_policy; entered=Event(); release=Event(); results=[]
        def hold(changes,event):
            result=actual(changes,event); entered.set()
            if not release.wait(5): raise AssertionError('test did not release its child thread')
            return result
        args=('setup','screenshots','--founder','yes','--team','no')
        with patch.object(brain,'update_policy',side_effect=hold):
            thread=Thread(target=lambda:results.append(self.call(*args,brain=brain)))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                code,value=self.call(*args,brain=brain)
                self.assertEqual(code,2); self.assertIn('operação',value['mensagem'])
            finally:
                release.set(); thread.join(5)
        self.assertEqual(results[0][0],0)
        version=brain.snapshot()['company']['policy_version']
        self.assertEqual(self.call(*args,brain=brain)[0],0)
        self.assertEqual(brain.snapshot()['company']['policy_version'],version)

if __name__=='__main__': unittest.main()
