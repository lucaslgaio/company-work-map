"""Round4 F3/F6/F8/F11/F13 report-derived actual Brain regressions, not raw replay."""
import copy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from company_pilot.brain import Brain,FIELDS,LocalStore
import test_engine as fixtures


def uid():return str(uuid4())
def refs(source):return [dict(source_id=source['id'],quote=source['text'])]
def claim(source,value,basis='observed'):return dict(value=value,basis=basis,evidence=refs(source))
def empty():return dict(tasks=[],nodes=[],context_updates=[],next_question=None)


class Round4CoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.brain=Brain(LocalStore(Path(self.tmp.name)));self.brain.initialize('Empresa sintética round4',synthetic=True)
 def source(self,sid):return next(s for s in self.brain.snapshot()['sources'] if s['id']==sid)
 def task(self):return self.brain.snapshot()['tasks'][0]
 def observe(self,text,when='2026-11-23T12:00:00+00:00',updates=None,field='result',value=None):
  payload=fixtures.observation(text);payload['occurred_at']=when
  raw=self.brain.ingest_observation(payload);source=self.source(raw['observation_id'])
  previous=self.brain.snapshot()['tasks'];candidate=fixtures.analysis(source,task_id=previous[0]['id'] if previous else None)
  if previous:candidate['tasks'][0]['fields']=copy.deepcopy(previous[0]['fields'])
  candidate['tasks'][0]['fields'][field]=claim(source,value or text)
  if updates:candidate['tasks'][0]['field_updates']=updates(source)
  self.brain.apply_analysis(raw['job_id'],candidate)
  return source
 def test_F6_later_verified_same_record_state_advances_founder_report(self):
  self.observe('Cronograma CT-42: conduítes 80%.',value='Conduítes 80%');task=self.task()
  with patch('company_pilot.brain.engine.now',return_value='2026-11-24T12:00:00+00:00'):
   self.brain.change_map([dict(op='correct',id=task['id'],fields={'result':'Conduítes 80%, conforme vistoria de terça'})],uid(),'Bia confirmou na vistoria de terça: conduítes 80%.')
  self.observe('Cronograma CT-42 quinta: Concluída, 100%, conferido Bia.',when='2026-11-26T12:00:00+00:00',value='Conduítes 100%, conferido Bia',updates=lambda s:{'result':dict(mode='progress',effective_at=None,evidence=refs(s))})
  self.assertEqual(self.task()['fields']['result']['value'],'Conduítes 100%, conferido Bia')
  self.assertNotIn('result',self.task()['conflicts'])
 def test_F3_evidence_backed_equivalence_does_not_become_material_conflict(self):
  old=self.observe('A finalidade é preparar o relatório semanal para o cliente.',field='purpose',value='Preparar o relatório semanal para o cliente')
  prior=copy.deepcopy(self.task()['fields']['purpose']['evidence'])
  self.observe('Este trabalho serve para elaborar o relatório da semana destinado ao cliente.',field='purpose',value='Elaborar o relatório da semana destinado ao cliente',updates=lambda s:{'purpose':dict(mode='equivalent',effective_at=None,evidence=refs(s),prior_evidence=prior)})
  self.assertNotIn('purpose',self.task()['conflicts'])
  self.assertTrue(self.task().get('equivalence_history'))
 def test_F8_interview_delegate_is_awaiting_named_person_not_founder(self):
  receipt=self.brain.interview_defer('tracking','Ana sabe informar onde o andamento é acompanhado.',uid(),delegate_to='Ana')
  question=next(q for q in self.brain.snapshot()['questions'] if q['id']==receipt['question_id'])
  self.assertEqual(question['status'],'awaiting_person')
  self.assertEqual(question['response_disposition']['delegate_to'],'Ana')
  self.assertNotIn(question['id'],self.brain.message_targets()['open_question_ids'])
 def test_F11_withheld_is_visible_coverage_without_founder_question(self):
  result=self.brain.ingest_context_gap(dict(schema_version=1,event_id=uid(),device_id=uid(),occurred_at='2026-11-23T12:00:00+00:00',app='PRIVATE title',surface_kind='desktop_window',synthetic=True,privacy_disposition='withheld'))
  self.assertEqual(result['state'],'coverage_only')
  self.assertEqual(self.brain.snapshot()['questions'],[])
  self.assertNotIn('PRIVATE title',str(self.brain.snapshot()))
  self.assertIn('withheld',str(self.brain.snapshot()['sources']))
 def test_F13_report_correction_does_not_offer_unrelated_tracking_gap(self):
  self.brain.interview_defer('tracking','Não sei onde pagamentos e saldos são acompanhados.',uid())
  targets=self.brain.message_targets('Correção do relatório semanal: o Bia revisa as fotos antes de enviar ao cliente.')
  self.assertEqual(targets['open_question_ids'],[])


 def test_F3_opposed_payment_states_cannot_clear_conflict_as_equivalent(self):
  self.observe('Pagamento autorizado.',value='Pagamento autorizado')
  self.observe('Pagamento recusado.',value='Pagamento recusado')
  task=self.task();prior=copy.deepcopy(task['fields']['result']['evidence'])
  for c in task['conflicts']['result'].get('alternatives',[])+[task['conflicts']['result']['reported_candidate']]:
   for r in c['evidence']:
    if r not in prior:prior.append(r)
  self.observe('Pagamento recusado.',value='Pagamento recusado',updates=lambda source:{'result':dict(mode='equivalent',effective_at=None,evidence=refs(source),prior_evidence=prior)})
  self.assertIn('result',self.task()['conflicts'])
  self.assertEqual(self.task()['fields']['result']['value'],'Pagamento autorizado')
  self.assertTrue(any(q['status']=='pending' and q['missing_links']==['result'] for q in self.brain.snapshot()['questions']))

if __name__=='__main__':unittest.main()
