"""F8/G3/G5: actual verified setup→Nora→ordinary reply→parser/worker→map."""
from datetime import timedelta
import json
import os
from pathlib import Path
import unittest
import test_grok2_integration as fixtures
from company_pilot.operator import drain_nora_inbox,Operator,project_questions
from company_pilot.nora.worker import health

class Round4DelegateComposition(unittest.TestCase):
 def test_plain_delegate_is_awaiting_person_not_resolved_and_does_not_resend(self):
  f=fixtures.Grok2ActualSetupAndReply('runTest');f.setUp();self.addCleanup(f.doCleanups)
  task,q=f.task();f.nora.tick();answer=f.reply('Pergunta ao Ana; isso ele sabe melhor.')
  self.assertEqual(answer.question_ids,(q['id'],));self.assertEqual(drain_nora_inbox(f.brain)['queued_answers'],1)
  source=next(s for s in f.brain.snapshot()['sources'] if s['text']==answer.event.text)
  output={'tasks':[],'nodes':[],'context_updates':[],'next_question':None,'message_intent':{'kind':'delegate','task_ids':[],'question_ids':[q['id']],'delegate_to':'Ana','disposition':None,'evidence':[{'source_id':source['id'],'quote':source['text']}]}}
  worker,runner=f.worker(output)
  result=Operator(worker,brain=f.brain,nora=f.nora,clock=lambda:f.now).once()
  self.assertEqual(result['status'],'applied',result)
  snapshot=f.brain.snapshot();current=next(item for item in snapshot['questions'] if item['id']==q['id'])
  self.assertEqual(current['status'],'awaiting_person')
  self.assertEqual(current['response_disposition']['delegate_to'],'Ana')
  status=health(f.nora);self.assertEqual(status['awaiting_delegate_questions'],1);self.assertEqual(status['founder_pending_questions'],0)
  f.now+=timedelta(days=1);self.assertEqual(f.nora.tick().outcome,'empty');self.assertEqual(len(f.sent),1)
  if os.environ.get('NORA_ROUND4_PROOF'):
   out=Path(os.environ['NORA_ROUND4_PROOF']);out.mkdir(parents=True,exist_ok=True)
   page=f.brain.store.read().files['views/questions/'+q['id']+'.md']
   (out/'actual-plain-delegate.json').write_text(json.dumps({'fixture':'NEW report-derived; external Claude process/channel faked','operator':result,'health':status,'messages':f.sent,'question':current,'view':page,'actual_request':json.loads(runner.calls[-1][1]['input'])},ensure_ascii=False,indent=2)+'\n')

 def test_interview_delegation_leaves_founder_queue_and_preserves_native_waiting(self):
  from uuid import uuid4
  f=fixtures.Grok2ActualSetupAndReply('runTest');f.setUp();self.addCleanup(f.doCleanups)
  receipt=f.brain.interview_defer('tracking','Ana sabe explicar pagamentos e saldos; pergunte ao Ana.',str(uuid4()),delegate_to='Ana')
  self.assertEqual(receipt['state'],'awaiting_person')
  project_questions(f.brain,f.nora);status=health(f.nora)
  self.assertEqual(status['founder_pending_questions'],0);self.assertEqual(status['awaiting_delegate_questions'],1)
  self.assertEqual(status['awaiting_delegate'][0]['delegate_to'],'Ana')
  self.assertEqual(f.nora.tick().outcome,'empty');self.assertEqual(f.sent,[])
  self.assertNotIn(receipt['question_id'],f.brain.message_targets('pagamentos e saldos')['open_question_ids'])
  question=next(q for q in f.brain.snapshot()['questions'] if q['id']==receipt['question_id'])
  self.assertEqual(question['status'],'awaiting_person')
  page=f.brain.store.read().files['views/perguntas.md'];self.assertIn('Ana',page)
  if os.environ.get('NORA_ROUND4_PROOF'):
   out=Path(os.environ['NORA_ROUND4_PROOF']);out.mkdir(parents=True,exist_ok=True)
   (out/'actual-interview-delegate.json').write_text(json.dumps({'fixture':'NEW local actual Brain interview→Nora→native view, no external action','receipt':receipt,'question':question,'health':status,'view':page,'messages':f.sent},ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':unittest.main()
