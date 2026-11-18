"""E9/G4: actual stored selection metadata remains visible and honest."""
import copy
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from company_pilot.brain import Brain,LocalStore
from company_pilot.brain.messages import uncertainty_basis
import test_engine as fixtures


class QuestionViewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.brain=Brain(LocalStore(Path(self.tmp.name)));self.brain.initialize('Empresa sintética',synthetic=True)
        raw=self.brain.ingest_observation(fixtures.observation());self.source=self.brain.snapshot()['sources'][0]
        self.brain.apply_analysis(raw['job_id'],fixtures.analysis(self.source));self.task=self.brain.snapshot()['tasks'][0]
        self.q=next(q for q in self.brain.snapshot()['questions'] if q['missing_links']==['purpose'])

    def test_selection_view_uses_exact_stored_reason_evidence_and_actual_send_status(self):
        for status,label in [('prepared','Preparada; envio ainda não confirmado'),('unknown','Resultado do envio desconhecido'),('accepted','Envio aceito pelo canal')]:
            item=dict(question=copy.deepcopy(self.q),evidence=[dict(source_id=self.source['id'],quote=self.source['text'])],why_chosen=dict(reason='Divergência material de valor; confirmação ainda necessária',focus_description='Valor do serviço'))
            record=dict(id=status,slot='2026-11-23T10:00:00-03:00',items=[item],status=status,text='Mensagem efetivamente preparada para o caso sintético.')
            self.brain.transact_nora(lambda state:state.update(rounds={status:record}))
            page=self.brain.store.read().files['views/questions/'+self.q['id']+'.md']
            self.assertIn(label,page);self.assertIn(item['why_chosen']['reason'],page);self.assertIn(self.source['text'],page)
            self.assertIn('Mensagem efetivamente preparada',page)
            if status!='accepted':self.assertNotIn('Envio aceito pelo canal',page)

    def test_partial_named_resolution_retains_disposition_and_changes_issue_basis(self):
        raw=self.brain.ingest_observation(fixtures.observation('Conclusão e preço ainda divergem.'))
        source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['observation_id'])
        result=fixtures.analysis(source,task_id=self.task['id']);result['tasks'][0]['fields']=copy.deepcopy(self.task['fields'])
        evidence=[dict(source_id=source['id'],quote=source['text'])]
        result['tasks'][0]['consistency_issues']=[dict(description=name,evidence=evidence) for name in ('Conclusão divergente','Preço divergente')]
        self.brain.apply_analysis(raw['job_id'],result)
        q=next(q for q in self.brain.snapshot()['questions'] if q.get('gap_kind')=='business_consistency')
        def answer(text,kind,task_ids,**extras):
            raw=self.brain.record_message(text,str(uuid4()),question_ids=[q['id']]);source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['answer_id'])
            refs=[dict(source_id=source['id'],quote=source['text'])]
            intent=dict(kind=kind,task_ids=task_ids,question_ids=[q['id']],delegate_to='Renan' if kind=='delegate' else None,disposition=None,evidence=refs)
            output=dict(tasks=[],nodes=[],context_updates=[],next_question=None,message_intent=intent)
            if extras:
                task=next(t for t in self.brain.snapshot()['tasks'] if t['id']==self.task['id'])
                output['tasks']=[dict(task_id=task['id'],name=task['name'],fields=copy.deepcopy(task['fields']),links=[],issue_resolutions=[dict(issue_ids=[q['issue_ids'][0]],resolution=dict(value='Conclusão esclarecida; preço segue pendente',basis='reported',evidence=refs))])]
            self.brain.apply_analysis(raw['job_id'],output)
        answer('Peça a Renan para esclarecer as duas divergências.','delegate',[])
        frozen=next(x for x in self.brain.snapshot()['questions'] if x['id']==q['id'])['response_disposition']
        self.assertEqual(frozen['uncertainty_basis']['issue_ids'],sorted(q['issue_ids']))
        answer('Esclareço somente a conclusão. O preço continua sem resposta.','answer',[self.task['id']],resolve=True)
        current=next(x for x in self.brain.snapshot()['questions'] if x['id']==q['id'])
        self.assertEqual(current['status'],'awaiting_person');self.assertEqual(current['response_disposition'],frozen)
        self.assertEqual(len(current['issue_ids']),1)
        self.assertNotEqual(uncertainty_basis(current)['issue_ids'],frozen['uncertainty_basis']['issue_ids'])
        application=current['application'];resend=current['resend_request_id']
        raw=self.brain.ingest_observation(fixtures.observation('Atualização independente sem nova divergência.'))
        source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['observation_id'])
        task=next(t for t in self.brain.snapshot()['tasks'] if t['id']==self.task['id'])
        result=fixtures.analysis(source,task_id=task['id']);result['tasks'][0]['fields']=copy.deepcopy(task['fields'])
        self.brain.apply_analysis(raw['job_id'],result)
        current=next(x for x in self.brain.snapshot()['questions'] if x['id']==q['id'])
        self.assertEqual(current['application'],application);self.assertEqual(current['resend_request_id'],resend)

if __name__=='__main__':unittest.main()
