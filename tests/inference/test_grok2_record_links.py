"""E11 actual company-record relation regression from the root's probe.

Synthetic records; actual parser/worker/Brain, fake Claude subprocess only.
"""
import copy
import hashlib
import unittest
from uuid import uuid4
from tests.inference import test_grok2_composition as fixtures
from company_pilot.brain import BrainError


class RecordLinkTests(unittest.TestCase):
    setUp=fixtures.Grok2CompositionTests.setUp
    source=fixtures.Grok2CompositionTests.source
    run_model=fixtures.Grok2CompositionTests.run_model
    text='Produzir a lista entrega a lista aprovada para Cotar materiais, que recebe a lista aprovada e entrega o comparativo.'

    def queue(self,revision):
        digest=hashlib.sha256(self.text.encode()).hexdigest()
        raw=self.brain.ingest_company_record(dict(schema_version=1,event_id=str(uuid4()),occurred_at='2026-11-23T12:00:00+00:00',synthetic=True,source=dict(system='fixture-files',record_id='purchase-flow',url='https://example.invalid/purchase-flow',revision=revision),operation='upsert',content=dict(text=self.text,sha256=digest,complete=False),object=None))
        return raw,self.source(raw['source_id'])

    def output(self,source,name,values):
        return dict(tasks=[fixtures.candidate(source,name,values)],nodes=[],context_updates=[],next_question=None,record_extraction=dict(complete=False,limitations=['Registro sintético parcial.']))

    def scenario(self):
        self.brain.update_policy(dict(watchdog_actors=['fixture-founder']),str(uuid4()))
        raw,source=self.queue('r1')
        self.brain.apply_analysis(raw['job_id'],self.output(source,'Cotar materiais',dict(input='lista aprovada',action='Cotar materiais',result='comparativo')))
        consumer=self.brain.snapshot()['tasks'][0]
        raw,source=self.queue('r2')
        output=self.output(source,'Produzir a lista',dict(action='Produzir a lista',result='lista aprovada'))
        output['tasks'][0]['links']=[dict(kind='handoff',target_id=consumer['id'],basis='reported',evidence=[dict(source_id=source['id'],quote=self.text)])]
        snapshot=self.brain.snapshot()
        scope=dict(tasks=[dict(id=consumer['id'],version=consumer['version'])],nodes=[],edges=[],source_ids=[s['id'] for s in snapshot['sources'] if s['kind']=='company_record'])
        return raw,source,consumer,output,scope

    def test_supplied_record_consumer_links_through_actual_parser_worker_and_brain(self):
        raw,source,consumer,output,scope=self.scenario()
        response,payload=self.run_model(output)
        self.assertIn(consumer['id'],{t['id'] for t in payload['base_context']['tasks']})
        self.assertEqual(payload['base_context']['map_scope'],scope)
        self.assertTrue(any(e['to']==consumer['id'] and e['kind']=='handoff' and e['active'] for e in self.brain.snapshot()['edges']))

    def test_unsupplied_stale_and_unavailable_record_targets_reject_atomically(self):
        for variant in ('unsupplied','stale','unavailable'):
            with self.subTest(variant=variant):
                # Each transaction remains pending, so all variants can share the
                # same current immutable raw source and before head.
                if variant=='unsupplied':raw,source,consumer,output,scope=self.scenario()
                bad=copy.deepcopy(scope);proposal=copy.deepcopy(output)
                if variant=='unsupplied':bad['tasks']=[]
                elif variant=='stale':bad['tasks'][0]['version']+=1
                else:proposal['tasks'][0]['links'][0]['target_id']=str(uuid4())
                frozen=self.brain.store.read()
                with self.assertRaises(BrainError):self.brain.apply_analysis(raw['job_id'],proposal,map_scope=bad)
                self.assertEqual(self.brain.store.read(),frozen)

if __name__=='__main__':unittest.main()
