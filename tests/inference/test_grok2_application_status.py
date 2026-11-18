"""E2 actual worker result must expose canonical unresolved/partial application."""
import copy
import unittest
from tests.inference import test_grok2_composition as fixtures


class ApplicationStatusTests(unittest.TestCase):
    setUp=fixtures.Grok2CompositionTests.setUp
    source=fixtures.Grok2CompositionTests.source
    run_model=fixtures.Grok2CompositionTests.run_model
    observe=fixtures.Grok2CompositionTests.observe

    def message(self,text,questions,tasks=()):
        raw=self.brain.record_message(text,fixtures.uid(),question_ids=questions)
        source=self.source(raw['answer_id'])
        intent=dict(kind='answer',task_ids=list(tasks),question_ids=list(questions),delegate_to=None,disposition=None,evidence=fixtures.claim(source,text,'reported')['evidence'])
        return raw,source,intent

    def test_unaddressed_context_answer_returns_awaiting_clarification_at_actual_worker_boundary(self):
        raw=self.brain.ingest_context_gap(dict(schema_version=1,event_id=fixtures.uid(),device_id=fixtures.uid(),occurred_at='2026-11-23T12:00:00+00:00',app='Editor sintético',surface_kind='desktop_window',synthetic=True))
        qid=raw['question_id'];raw,source,intent=self.message('Ainda não expliquei esse contexto.',[qid])
        response,payload=self.run_model(dict(fixtures.empty(),message_intent=intent),require_applied=False)
        self.assertEqual(response['status'],'awaiting_clarification')
        self.assertEqual(response['application_status'],'awaiting_clarification')
        self.assertEqual(response['receipt']['question_outcomes'][0]['status'],'unresolved')
        self.assertEqual(self.source(raw['answer_id'])['text'],source['text'])
        self.assertFalse(any(j['id']==raw['job_id'] for j in self.brain.snapshot()['pending_jobs']))

    def test_partial_scoped_reply_exposes_partial_state_while_applying_supported_field(self):
        raw,source=self.observe('Registrar solicitação; finalidade e repasse ainda desconhecidos.')
        self.run_model(dict(fixtures.empty(),tasks=[fixtures.candidate(source,'Registrar solicitação',dict(action='Registrar solicitação'))]))
        task=self.brain.snapshot()['tasks'][0]
        questions=[q['id'] for q in self.brain.snapshot()['questions'] if q['task_id']==task['id'] and q['missing_links'] in (['purpose'],['handoff'])]
        raw,source,intent=self.message('A finalidade é preparar o relatório. Não sei o próximo responsável.',questions,[task['id']])
        fields=copy.deepcopy(task['fields']);fields['purpose']=fixtures.claim(source,'Preparar o relatório','reported')
        response,payload=self.run_model(dict(fixtures.empty(),tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[])],message_intent=intent),require_applied=False)
        self.assertEqual(response['status'],'awaiting_clarification')
        self.assertEqual(response['application_status'],'partially_applied')
        self.assertEqual({o['status'] for o in response['receipt']['question_outcomes']},{'resolved','unresolved'})
        self.assertEqual(self.brain.snapshot()['tasks'][0]['fields']['purpose']['value'],'Preparar o relatório')

if __name__=='__main__':unittest.main()
