"""NEW author fixtures: actual cards/selection view, no original-run replay.

python3 THIS_FILE COMPOSED_PRODUCT NEW_OUTPUT_DIRECTORY
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

product=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(product));sys.path.insert(0,str(product/'tests/brain'))
from company_pilot.brain import Brain,FIELDS,LocalStore
from company_pilot.nora import BrainRepository,Nora,Route,SendReceipt
from company_pilot.operator import project_questions
from test_grok2_lifecycle import LifecycleTests
from test_grok2_issue_identity import IssueIdentityTests
from test_grok2_people import report_fixture

checks={}
def export(brain,name):
 target=out/name;target.mkdir();(target/'snapshot.json').write_text(json.dumps(brain.snapshot(),ensure_ascii=False,indent=2)+'\n')
 for path,text in brain.store.read().files.items():
  if path in ('views/perguntas.md','views/pessoas.md') or path.startswith(('views/tasks/','views/questions/')):
   p=target/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

# Read-only capture wrappers do not alter any semantic decision or assertion.
case=LifecycleTests('test_E3_explicit_same_record_progress_updates_current_value');case.setUp();original=case.observe;steps=[]
def observe(*args,**kwargs):
 value=original(*args,**kwargs);name='progress-'+str(len(steps)+1);export(case.brain,name);steps.append(name);return value
case.observe=observe
try:case.test_E3_explicit_same_record_progress_updates_current_value();checks['progress_current_card_updated']=True
finally:case.doCleanups()
case=IssueIdentityTests('test_distinct_later_point_from_same_passage_is_pending_and_not_established');case.setUp();original=case.apply_message;steps=[]
def apply(*args,**kwargs):
 value=original(*args,**kwargs);name='issue-identity-'+str(len(steps)+1);export(case.brain,name);steps.append(name);return value
case.apply_message=apply
try:case.test_distinct_later_point_from_same_passage_is_pending_and_not_established();checks['same_passage_identity_pending_then_named_resolution']=True
finally:case.doCleanups()
with tempfile.TemporaryDirectory(prefix='grok2-visible-') as tmp:
 brain,raw=report_fixture(str(Path(tmp)/'people'));export(brain,'people-and-qualifiers');checks['three_known_people_with_raw_claims_retained']=len([n for n in brain.snapshot()['nodes'] if n['kind']=='person' and not n.get('superseded_by')])==3
 brain=Brain(LocalStore(Path(tmp)/'question'));brain.initialize('Empresa sintética — seleção visível',synthetic=True)
 text='A medição recebida é R$ 18.400; o contrato da etapa prevê R$ 16.900. A diferença ainda não foi esclarecida.'
 raw=brain.ingest_observation(dict(schema_version=1,event_id=str(uuid4()),device_id=str(uuid4()),occurred_at='2026-11-23T12:00:00+00:00',policy_version=1,source=dict(kind='active_window',app='Planilha sintética',record_ref='MEDICAO-02'),classification=dict(label='company',reason='fixture sintética permitida'),content=dict(text=text,sha256=hashlib.sha256(text.encode()).hexdigest()),synthetic=True))
 src=brain.snapshot()['sources'][0];refs=[dict(source_id=src['id'],quote=text)];fields={f:dict(value=None,basis='inferred',evidence=[]) for f in FIELDS};fields['action']=dict(value='Conferir a medição',basis='observed',evidence=refs)
 brain.apply_analysis(raw['job_id'],dict(tasks=[dict(task_id=None,name='Conferir a medição',fields=fields,links=[],consistency_issues=[dict(description='Diferença entre os valores da medição e do contrato',evidence=refs)])],nodes=[],context_updates=[],next_question=None))
 company=brain.snapshot()['company']['id'];repo=BrainRepository(company,brain.transact_nora);route=Route(company,str(uuid4()),1,'synthetic-account','synthetic-company','synthetic-founder','fixture-founder',True);repo.transact(company,lambda state:setattr(state,'route',route))
 class Sender:
  def send(self,route,text):return SendReceipt(route.account,route.conversation,'synthetic-view-message')
 nora=Nora(company,repo,lambda:datetime.fromisoformat('2026-11-24T10:00:00-03:00'),Sender());project_questions(brain,nora);tick=nora.tick();assert tick.outcome=='accepted',tick
 snapshot=brain.snapshot();round=next(r for r in snapshot['nora']['rounds'].values() if r['status']=='accepted');item=round['items'][0]
 export(brain,'actual-nora-selection');(out/'actual-nora-message.txt').write_text(round['text']+'\n')
 page=brain.store.read().files['views/questions/'+item['question']['id']+'.md']
 checks['actual_nora_one_frozen_item']=len(round['items'])==1
 checks['actual_reason_visible_from_stored_item']=bool(item['why_chosen']) and item['why_chosen']['reason'] in page
 checks['actual_selected_evidence_visible']=bool(item['evidence']) and all(r['quote'] in page for r in item['evidence'])
 checks['accepted_status_not_invented']='Envio aceito pelo canal' in page
 checks['no_codes_or_record_paths_in_new_message']='Q-' not in round['text'] and 'views/' not in round['text'] and 'Registro:' not in round['text']
receipt=dict(fixture='NEW synthetic actual Brain/cards/Nora selection; only sender/clock/semantic proposals are fixtures',executed_product=str(product),checks=checks,real_model_network_native_channel_calls=0)
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps(receipt,ensure_ascii=False));assert all(checks.values())
