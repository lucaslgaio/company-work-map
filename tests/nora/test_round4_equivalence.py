"""F3/G3/G5: consume canonical source-backed equivalence, not lexical guessing."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
import test_grok2_integration as fixtures
from company_pilot.operator import project_questions

class Round4EquivalenceComposition(unittest.TestCase):
 def test_supported_equivalence_stays_inspectable_without_founder_wording_question(self):
  f=fixtures.Grok2ActualSetupAndReply('runTest');f.setUp();self.addCleanup(f.doCleanups)
  values={'trigger':'Atualização do relato','performer':'Ana','input':'Cronograma de obra','action':'Conferir cronograma',
   'result':'Cronograma conferido','purpose':'Informar o cliente','placement':'Planejamento','handoff':'Lucas','tracking_record':'Pedido OS-17'}
  raw,source=f.observe('; '.join(values.values()))
  refs=[{'source_id':source['id'],'quote':source['text']}]
  fields={key:{'value':value,'basis':'observed','evidence':refs} for key,value in values.items()}
  f.brain.apply_analysis(raw['job_id'],{'tasks':[{'task_id':None,'name':'Conferir cronograma','fields':fields,'links':[]}],'nodes':[],'context_updates':[],'next_question':None})
  task=f.brain.snapshot()['tasks'][0]
  f.now=f.now.replace(hour=14);raw,current=f.observe('Ana revisa o cronograma de obra para informar o cliente.')
  newrefs=[{'source_id':current['id'],'quote':current['text']}]
  changed=deepcopy(task['fields']);changed['action']={'value':'Revisar cronograma','basis':'observed','evidence':newrefs}
  proposal={'tasks':[{'task_id':task['id'],'name':task['name'],'fields':changed,'links':[],
   'field_updates':{'action':{'mode':'equivalent','effective_at':f.now.isoformat(),'evidence':newrefs,'prior_evidence':task['fields']['action']['evidence']}}}],
   'nodes':[],'context_updates':[],'next_question':None}
  worker,runner=f.worker(proposal);result=worker.run_once()
  self.assertEqual(result['status'],'applied',result)
  updated=f.brain.snapshot()['tasks'][0]
  self.assertNotIn('action',updated['conflicts'])
  self.assertEqual(updated['equivalence_history'][-1]['decision'],'equivalent')
  self.assertFalse(any(q['status']=='pending' and q['missing_links']==['action'] for q in f.brain.snapshot()['questions']))
  project_questions(f.brain,f.nora);self.assertEqual(f.nora.tick().outcome,'empty');self.assertEqual(f.sent,[])
  if os.environ.get('NORA_ROUND4_PROOF'):
   out=Path(os.environ['NORA_ROUND4_PROOF']);out.mkdir(parents=True,exist_ok=True)
   (out/'actual-equivalent-wording.json').write_text(json.dumps({'fixture':'NEW report-derived; explicit source-grounded semantic decision, fake process/channel','result':result,'task':updated,'messages':f.sent,'actual_request':json.loads(runner.calls[-1][1]['input'])},ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':unittest.main()
