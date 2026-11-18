"""F4 owner-only generated IDs; every attempt performs fresh fake native GETs."""
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from company_pilot.brain import Brain, LocalStore
from company_pilot.cli import main
from company_pilot.operator import write_private_json
from tests.access.test_policy import API, request


class OwnerVerificationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.brain=Brain(LocalStore(self.root/'brain',actor='owner'))
        self.brain.initialize('Owner verification fixture',synthetic=True)
        self.brain.set_access_policy([request('owner'),request('member')],str(uuid4()),'Explicit complete requested access')
        self.config=self.root/'operator/config.json'
        write_private_json(self.config,{'schema_version':1,'mode':'synthetic','store':{'kind':'github','repo':'owner/pilot',
            'branch':'company-state-development','mirror':str(self.root/'mirror')}})
        self.api=API()
    def tearDown(self): self.tmp.cleanup()
    def invoke(self,*args):
        out,err=StringIO(),StringIO()
        with patch('company_pilot.access.GitHubAPI',return_value=self.api):
            code=main(['--config',str(self.config),'owner','verify-access',*args],brain_factory=lambda _:self.brain,out=out,err=err)
        return code,json.loads(out.getvalue() or err.getvalue())
    def test_unknown_result_uses_same_generated_id_and_still_checks_native_access(self):
        actual=self.brain.record_access_verification; ids=[]
        def lose_once(receipt,event):
            result=actual(receipt,event); ids.append(event)
            if len(ids)==1: raise OSError('lost verification acknowledgement')
            return result
        with patch.object(self.brain,'record_access_verification',side_effect=lose_once):
            self.assertEqual(self.invoke()[0],2)
            first_calls=len(self.api.calls); before=self.brain.store.read().head
            code,value=self.invoke()
        self.assertEqual(code,0,value); self.assertEqual(ids[0],ids[1]); self.assertEqual(before,self.brain.store.read().head)
        self.assertGreater(len(self.api.calls),first_calls); self.assertTrue(all(method=='GET' for _,method,_ in self.api.calls))
        self.assertEqual(len([s for s in self.brain.snapshot()['sources'] if s['kind']=='access_verification']),1)
    def test_changed_native_outcome_and_return_to_previous_outcome_record_fresh_state(self):
        self.assertEqual(self.invoke()[0],0)
        self.api.roles.pop('member'); code,pending=self.invoke()
        self.assertEqual(code,0,pending); self.assertEqual(pending['state'],'pending')
        self.api.roles['member']='write'; code,restored=self.invoke()
        self.assertEqual(code,0,restored); self.assertEqual(self.brain.access_policy()['state'],'verified')
        self.assertEqual(len([s for s in self.brain.snapshot()['sources'] if s['kind']=='access_verification']),3)
        # A current owner failure cannot return a previously completed success.
        before=self.brain.store.read().head; self.api.operator='member'
        self.assertEqual(self.invoke()[0],2); self.assertEqual(before,self.brain.store.read().head)
    def test_explicit_id_remains_supported(self):
        event=str(uuid4()); actual=self.brain.record_access_verification; ids=[]
        def record(receipt,event_id): ids.append(event_id); return actual(receipt,event_id)
        with patch.object(self.brain,'record_access_verification',side_effect=record):
            code,value=self.invoke('--event-id',event)
        self.assertEqual(code,0,value); self.assertEqual(ids,[event])

if __name__=='__main__': unittest.main()
