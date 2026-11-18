"""F3/G3/G4: primary-founder attention uses real sent reply and canonical scope."""
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
import os
from pathlib import Path
import unittest
import test_grok2_integration as fixtures
from company_pilot.brain import FIELDS
from company_pilot.operator import drain_nora_inbox,Operator,project_questions

class Round4FounderAttention(unittest.TestCase):
 def test_primary_founder_pointer_promotes_exact_supplied_question_with_quotes(self):
  f=fixtures.Grok2ActualSetupAndReply('runTest');f.setUp();self.addCleanup(f.doCleanups)
  task,target=f.task()
  values={'trigger':'Recebimento do pedido','performer':'Ana','input':'Pedido e contrato','action':'Conferir preço do pedido','result':'Preços conferidos','purpose':'Evitar cobrança divergente','placement':'Compras','handoff':'Lucas','tracking_record':'Pedido OS-17'}
  text='; '.join(key+': '+value for key,value in values.items())+'. Preço do pedido R$ 18.400 e preço do contrato R$ 16.900.'
  raw,source=f.observe(text);refs=[{'source_id':source['id'],'quote':source['text']}]
  fields={field:{'value':value,'basis':'observed','evidence':refs} for field,value in values.items()}
  other={'task_id':None,'name':'Conferir preço do pedido','fields':fields,'links':[],
    'consistency_issues':[{'description':'O pedido registra R$ 18.400 e o contrato R$ 16.900','evidence':refs}]}
  f.brain.apply_analysis(raw['job_id'],{'tasks':[other],'nodes':[],'context_updates':[],'next_question':None})
  project_questions(f.brain,f.nora);first=f.nora.tick()
  first_round=f.nora.repository.transact(f.nora.company_id,lambda s:s.rounds[first.round_id])
  self.assertNotEqual(first_round.items[0].question.id,target['id'])
  answer=f.reply('Priorize a finalidade da revisão do Pedido OS-17; o preço pode esperar.')
  self.assertEqual(drain_nora_inbox(f.brain)['queued_answers'],1)
  primary=next(s for s in f.brain.snapshot()['sources'] if s['text']==answer.event.text)
  self.assertIn(target['id'],primary['candidate_question_ids'])
  evidence=[{'source_id':primary['id'],'quote':primary['text']}]
  decision={'tasks':[],'nodes':[],'context_updates':[],'next_question':None,
   'message_intent':{'kind':'context','task_ids':[],'question_ids':[],'delegate_to':None,'disposition':None,'evidence':evidence},
   'attention_requests':[{'question_id':target['id'],'reason':{'value':'O fundador priorizou a finalidade da revisão do Pedido OS-17','basis':'reported','evidence':evidence}}]}
  worker,runner=f.worker(decision)
  result=Operator(worker,brain=f.brain,nora=f.nora,clock=lambda:f.now).once()
  destination=os.environ.get('NORA_ROUND4_PROOF')
  def emit(**extra):
   if destination:
    out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    (out/'actual-founder-priority.json').write_text(json.dumps({'fixture':'NEW report-derived, external process/channel faked','operator':result,'messages':f.sent,'snapshot':f.brain.snapshot(),**extra},ensure_ascii=False,indent=2,default=str)+'\n')
  if result['status']!='applied':emit(actual_request=json.loads(runner.calls[-1][1]['input']))
  self.assertEqual(result['status'],'applied',result)
  current=next(q for q in f.brain.snapshot()['questions'] if q['id']==target['id'])
  self.assertEqual(current['status'],'pending');self.assertTrue(current['priority_hint'])
  f.now+=timedelta(hours=4);next_tick=f.nora.tick()
  round=f.nora.repository.transact(f.nora.company_id,lambda s:s.rounds[next_tick.round_id]);item=round.items[0]
  self.assertEqual(item.question.id,target['id']);self.assertEqual(item.why_chosen['tier'],'founder_priority')
  self.assertIn(primary['id'],item.why_chosen['source_ids'])
  self.assertTrue(any(ref['quote']==answer.event.text for ref in item.evidence))
  self.assertEqual(round.text.count('?'),1);self.assertLessEqual(len(round.text),400)
  page=f.brain.store.read().files['views/questions/'+target['id']+'.md']
  self.assertIn(answer.event.text,page)
  emit(round=asdict(round),view=page,actual_request=json.loads(runner.calls[-1][1]['input']))

if __name__=='__main__':unittest.main()
