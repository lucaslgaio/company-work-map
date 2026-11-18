"""E4/G3/G5 actual Brain replay guard, derived from root's bounded probe."""
import copy
import unittest
from uuid import uuid4
import test_engine as brain_fixtures
import test_grok2_guards as guards
from test_grok2_guards import claim,refs


class IssueIdentityTests(unittest.TestCase):
    setUp=guards.PublicCoreGuards.setUp
    source=guards.PublicCoreGuards.source
    task=guards.PublicCoreGuards.task
    question=guards.PublicCoreGuards.question
    observe=guards.PublicCoreGuards.observe
    message=guards.PublicCoreGuards.message
    apply_message=guards.PublicCoreGuards.apply_message

    def seed_resolution(self):
        price='A medição diverge do contrato em R$ 1.500'
        approval='A aprovação do envio continua pendente'
        task_id,source=self.observe('A medição tem R$ 18.400; o contrato tem R$ 16.900. A aprovação está pendente.',issues=[price,approval])
        self.assertEqual(len(self.task(task_id)['consistency_issues']),2)
        qid=self.question(task_id,gap_kind='business_consistency')['id']
        issue=next(i for i in self.task(task_id)['consistency_issues'] if i['description']==price)
        raw,founder=self.message('Vale o contrato de R$ 16.900. Só resolvo o valor; a aprovação segue pendente.',[qid])
        self.apply_message(raw,founder,task_id=task_id,questions=[qid],resolutions=[dict(issue_ids=[issue['id']],resolution=claim(founder,'Vale o contrato de R$ 16.900'))])
        return task_id,source,approval

    def test_old_same_quote_paraphrase_does_not_reopen_resolved_issue_or_erase_approval(self):
        task_id,old,approval=self.seed_resolution()
        raw,source=self.message('Só relembro o registro antigo, sem nova divergência.')
        self.apply_message(raw,source,task_id=task_id,issues=[dict(description='Diferença de valor entre contrato e medição: R$ 16.900 versus R$ 18.400',evidence=refs(old))])
        current=self.task(task_id)
        self.assertEqual([i['description'] for i in current['consistency_issues']],[approval])
        self.assertEqual(len(current['resolved_issues']),1)
        self.assertTrue(current.get('issue_replays'))
        self.assertEqual(current['issue_replays'][-1]['evidence'],refs(old))
        self.assertEqual(self.question(task_id,gap_kind='business_consistency')['status'],'pending')

    def test_new_contradictory_source_can_create_material_issue_after_resolution(self):
        task_id,old,approval=self.seed_resolution()
        self.observe('Nova cobrança insiste em R$ 18.400 após a correção do contrato.',task_id=task_id,changes={},when='2026-11-27T12:00:00+00:00',issues=['Diferença nova entre contrato e cobrança'])
        current=self.task(task_id)
        self.assertEqual(len(current['consistency_issues']),2)
        self.assertIn(approval,[i['description'] for i in current['consistency_issues']])
        self.assertEqual(self.question(task_id,gap_kind='business_consistency')['status'],'pending')


    def test_distinct_later_point_from_same_passage_is_pending_and_not_established(self):
        text='A medição tem R$ 18.400; o contrato tem R$ 16.900. A aprovação está pendente.'
        raw=self.brain.ingest_observation(brain_fixtures.observation(text));old=self.source(raw['observation_id'])
        output=brain_fixtures.analysis(old,complete=True)
        output['tasks'][0]['consistency_issues']=[dict(description='Diferença de valor',evidence=refs(old))]
        self.brain.apply_analysis(raw['job_id'],output)
        task=self.brain.snapshot()['tasks'][0];qid=self.question(task['id'],gap_kind='business_consistency')['id']
        raw,founder=self.message('Vale o contrato de R$ 16.900; só esclareço o preço.',[qid])
        self.apply_message(raw,founder,task_id=task['id'],questions=[qid],resolutions=[dict(issue_ids=[task['consistency_issues'][0]['id']],resolution=claim(founder,'Vale R$ 16.900'))])
        self.assertEqual(self.task(task['id'])['status'],'established')
        raw,source=self.message('Revisão da passagem já arquivada.')
        self.apply_message(raw,source,task_id=task['id'],issues=[dict(description='A aprovação do envio continua pendente',evidence=refs(old))])
        current=self.task(task['id'])
        self.assertEqual(current['status'],'provisional')
        self.assertEqual(current['consistency_issues'],[])
        self.assertEqual(current['issue_replays'][-1]['status'],'awaiting_clarification')
        page=self.brain.store.read().files['views/tasks/'+task['id']+'.md']
        self.assertIn('aguarda interpretação',page)
        self.assertIn('A aprovação do envio continua pendente',page)
        raw,founder=self.message('Esclareço o ponto da aprovação: Lucas aprovou por escrito; não era a divergência de preço.')
        self.apply_message(raw,founder,task_id=task['id'],resolutions=[dict(issue_ids=[current['issue_replays'][-1]['id']],resolution=claim(founder,'Lucas aprovou por escrito'))])
        final=self.task(task['id'])
        self.assertEqual(final['issue_replays'][-1]['status'],'resolved')
        self.assertEqual(final['status'],'established')

if __name__=='__main__':unittest.main()
