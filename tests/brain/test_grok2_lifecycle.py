"""Grok-2 E2/E3/E4 report-derived public Brain regressions, not original replay."""
import copy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from company_pilot.brain import Brain, BrainError, LocalStore
import test_engine as fixtures


def uid():return str(uuid4())
def empty():return dict(tasks=[],nodes=[],context_updates=[],next_question=None)
def ref(source):return [dict(source_id=source['id'],quote=source['text'])]
def claim(source,value):return dict(value=value,basis='reported' if source['kind']!='observation' else 'observed',evidence=ref(source))
def intent(source,tasks=(),questions=(),kind='answer'):
    return dict(kind=kind,task_ids=list(tasks),question_ids=list(questions),delegate_to=None,disposition=None,evidence=ref(source))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.brain=Brain(LocalStore(Path(self.tmp.name)));self.brain.initialize('Empresa sintética Grok-2',synthetic=True)
    def source(self,sid):return next(s for s in self.brain.snapshot()['sources'] if s['id']==sid)
    def task(self):return self.brain.snapshot()['tasks'][0]
    def observe(self,text,when='2026-11-23T12:00:00+00:00',value=None,task_id=None,issues=None,progress=False):
        payload=fixtures.observation(text);payload['occurred_at']=when
        raw=self.brain.ingest_observation(payload);source=self.source(raw['observation_id'])
        result=fixtures.analysis(source,task_id=task_id)
        if task_id:result['tasks'][0]['fields']=copy.deepcopy(self.task()['fields'])
        result['tasks'][0]['fields']['result']=claim(source,value or text)
        if issues is not None:result['tasks'][0]['consistency_issues']=[dict(description=issues,evidence=ref(source))]
        if progress:result['tasks'][0]['field_updates']={'result':dict(mode='progress',effective_at=when,evidence=ref(source))}
        self.brain.apply_analysis(raw['job_id'],result)
        return source
    def message(self,text,questions):
        # This fixture explicitly supplies the task being discussed; retrieval has separate guards.
        raw=self.brain.record_message(text,uid(),question_ids=questions,
            candidate_task_versions=[dict(id=t['id'],version=t['version']) for t in self.brain.snapshot()['tasks']])
        return raw,self.source(raw['answer_id'])

    def test_E2_explicit_context_answer_is_saved_and_closes_only_its_question(self):
        raw=self.brain.ingest_context_gap(dict(schema_version=1,event_id=uid(),device_id=uid(),occurred_at='2026-11-23T12:00:00+00:00',app='Claude',surface_kind='desktop_window',synthetic=True))
        qid=raw['question_id'];message,source=self.message('O Claude transcreve as reuniões da obra.',[qid])
        result=dict(empty(),message_intent=intent(source,questions=[qid]),question_answers=[dict(question_id=qid,claim=claim(source,'Transcrição das reuniões da obra'))])
        self.brain.apply_analysis(message['job_id'],result)
        question=next(q for q in self.brain.snapshot()['questions'] if q['id']==qid)
        self.assertEqual(question['status'],'resolved')
        self.assertEqual(question['resolution']['evidence'],ref(source))
        self.assertTrue(any('Transcrição' in c['value'] for c in self.brain.snapshot()['context'].values()))

    def test_E2_scoped_answer_replaces_only_named_field_and_keeps_old_claim_history(self):
        self.observe('Lucas marcou o cronograma.');task=self.task()
        self.observe('A autoria do status ficou em dúvida.',when='2026-11-24T12:00:00+00:00',value='Outro estado',task_id=task['id'])
        qid=next(q['id'] for q in self.brain.snapshot()['questions'] if q['task_id']==task['id'] and q['missing_links']==['result'])
        raw,source=self.message('O estado correto é Em andamento, 80%.',[qid])
        fields=copy.deepcopy(self.task()['fields']);fields['result']=claim(source,'Em andamento, 80%')
        output=dict(empty(),tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[])],message_intent=intent(source,[task['id']],[qid]))
        self.brain.apply_analysis(raw['job_id'],output)
        current=self.task();self.assertEqual(current['fields']['result']['value'],'Em andamento, 80%')
        self.assertNotIn('result',current['conflicts'])
        self.assertEqual(next(q for q in self.brain.snapshot()['questions'] if q['id']==qid)['status'],'resolved')
        history=self.brain.store.read().files
        self.assertIn('Lucas marcou',history[f"records/tasks/{task['id']}/v000001.json"])

    def test_E3_explicit_same_record_progress_updates_current_value(self):
        self.observe('Caçamba agendada para quarta.',value='Agendada para quarta');task=self.task()
        self.observe('O caminhão bateu; caçamba remarcada para quinta 7h30.',when='2026-11-25T12:00:00+00:00',value='Remarcada para quinta 7h30',task_id=task['id'],progress=True)
        self.assertEqual(self.task()['fields']['result']['value'],'Remarcada para quinta 7h30')
        self.assertNotIn('result',self.task()['conflicts'])

    def test_E4_named_founder_resolution_closes_only_named_issue(self):
        self.observe('Conduítes marcados concluídos, mas vistoria aponta quarto incompleto.',issues='Status concluído sem conferência em todos os ambientes.');task=self.task()
        qid=next(q['id'] for q in self.brain.snapshot()['questions'] if q.get('gap_kind')=='business_consistency')
        raw,source=self.message('Foi só sala e cozinha; Bia confirma na vistoria e corrigiu para 80%.',[qid])
        fields=copy.deepcopy(task['fields']);fields['result']=claim(source,'Em andamento, 80%')
        issue_ids=[i.get('id','missing-issue-id') for i in task['consistency_issues']]
        output=dict(empty(),tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[],issue_resolutions=[dict(issue_ids=issue_ids,resolution=claim(source,'Era só sala e cozinha; Bia confirma na vistoria.'))])],message_intent=intent(source,[task['id']],[qid]))
        self.brain.apply_analysis(raw['job_id'],output)
        self.assertEqual(self.task().get('consistency_issues',[]),[])
        self.assertTrue(self.task().get('resolved_issues'))
        self.assertEqual(next(q for q in self.brain.snapshot()['questions'] if q['id']==qid)['status'],'resolved')


    def test_E3_progress_cannot_erase_a_founder_disagreement(self):
        self.observe('Vistoria marca 80%.',value='80%');task=self.task()
        raw,source=self.message('O estado é 60%, segundo minha conferência.',[])
        fields=copy.deepcopy(task['fields']);fields['result']=claim(source,'60%')
        self.brain.apply_analysis(raw['job_id'],dict(empty(),tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[])],message_intent=intent(source,[task['id']])))
        self.observe('Vistoria posterior marca 100%.',when='2026-11-26T12:00:00+00:00',value='100%',task_id=task['id'],progress=True)
        self.assertIn('result',self.task()['conflicts'])
        self.assertEqual(self.task()['fields']['result']['value'],'80%')

    def test_E3_delayed_progress_cannot_erase_a_newer_observed_conflict(self):
        self.observe('Vistoria marca 80%.',value='80%');task=self.task()
        self.observe('Vistoria de quarta informa 40%.',when='2026-11-25T12:00:00+00:00',value='40%',task_id=task['id'])
        self.observe('Registro histórico de terça: avanço para 60%.',when='2026-11-24T12:00:00+00:00',value='60%',task_id=task['id'],progress=True)
        self.assertIn('result',self.task()['conflicts'])
        self.assertEqual(self.task()['fields']['result']['value'],'80%')


    def test_E12_later_known_full_name_consolidates_alias_without_rewriting_old_task(self):
        raw=self.brain.ingest_observation(fixtures.observation('Lia registrou a solicitação.'))
        self.brain.apply_analysis(raw['job_id'],fixtures.analysis(self.source(raw['observation_id']),performer='Lia'))
        old=copy.deepcopy(self.task())
        interview=self.brain.interview_answer('Lia Costa',uid());source=self.source(interview['source_id'])
        self.brain.apply_analysis(interview['job_id'],dict(empty(),context_updates=[dict(topic='people',**claim(source,'Lia Costa'))]))
        raw=self.brain.ingest_observation(fixtures.observation('Lia conferiu outra solicitação.'))
        self.brain.apply_analysis(raw['job_id'],fixtures.analysis(self.source(raw['observation_id']),performer='Lia'))
        people=[n for n in self.brain.snapshot()['nodes'] if n['kind']=='person' and not n.get('superseded_by')]
        self.assertEqual([n['name'] for n in people],['Lia Costa'])
        self.assertEqual(next(t for t in self.brain.snapshot()['tasks'] if t['id']==old['id']),old)
        page=self.brain.store.read().files['views/pessoas.md']
        self.assertNotIn('- Lia:',page)
        self.assertEqual(page.count('Lia Costa:'),2)

    def test_E12_ambiguous_first_name_stays_a_gap_without_collapsing_homonyms(self):
        raw=self.brain.interview_answer('João Silva e João Souza',uid());source=self.source(raw['source_id'])
        self.brain.apply_analysis(raw['job_id'],dict(empty(),nodes=[dict(kind='person',name=name,evidence=ref(source)) for name in ('João Silva','João Souza')]))
        raw=self.brain.ingest_observation(fixtures.observation('João conferiu a solicitação.'))
        self.brain.apply_analysis(raw['job_id'],fixtures.analysis(self.source(raw['observation_id']),performer='João'))
        self.assertEqual(self.task()['performer_identity']['status'],'ambiguous')
        self.assertIn('performer',self.task()['missing_fields'])
        self.assertEqual(len([n for n in self.brain.snapshot()['nodes'] if n['kind']=='person']),2)

if __name__=='__main__':unittest.main()
