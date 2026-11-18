"""F3/F8/F10/F11, G3/G4/G5/G6: NEW report-derived actual-caller probe.

Usage: python3 THIS PRODUCT OUTPUT_NEW_DIR. No original Grok3 export replay;
semantic proposals and channel are explicit fixtures, Brain/Nora/operator real.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

product,out=map(lambda x:Path(x).resolve(),sys.argv[1:3]);sys.path.insert(0,str(product))
from company_pilot.brain import Brain,FIELDS,LocalStore
from company_pilot.nora import BrainRepository,Nora,Route,SendReceipt,InboundEvent
from company_pilot.nora.worker import health
from company_pilot.nora.presentation import display_text
from company_pilot.operator import project_questions,drain_nora_inbox

out.mkdir(parents=True,exist_ok=False)
class Fixture:
 def __init__(self,path):
  self.now=datetime.fromisoformat('2026-11-23T10:00:00-03:00')
  self.clock=patch('company_pilot.brain.engine.now',side_effect=lambda:self.now.isoformat());self.clock.start()
  self.brain=Brain(LocalStore(Path(path)/'brain',actor='Lucas'));self.brain.initialize('Empresa sintética round4',synthetic=True)
  self.company=self.brain.snapshot()['company']['id'];self.repo=BrainRepository(self.company,self.brain.transact_nora)
  self.route=Route(self.company,str(uuid4()),1,'fixture-account','100000000000@g.us','fixture-native','Lucas',True)
  self.repo.transact(self.company,lambda s:setattr(s,'route',self.route));self.messages=[];owner=self
  class Sender:
   def send(_,route,text):
    owner.messages.append(text);return SendReceipt(route.account,route.conversation,'fixture-message-'+str(len(owner.messages)))
  self.nora=Nora(self.company,self.repo,lambda:self.now,Sender(),founder_name='Lucas')
 def close(self):self.clock.stop()
 def source(self,text,record='Pedido de compra'):
  raw=self.brain.ingest_observation({'schema_version':1,'event_id':str(uuid4()),'device_id':str(uuid4()),'occurred_at':self.now.isoformat(),'policy_version':1,
    'source':{'kind':'active_window','app':'Planilha','record_ref':record},'classification':{'label':'company','reason':'synthetic report-derived'},
    'content':{'text':text,'sha256':sha256(text.encode()).hexdigest()},'synthetic':True})
  return raw,next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['observation_id'])
 def task(self,name,missing=(),issue=None,record='Pedido de compra'):
  values={'trigger':'Chegada do pedido','performer':'Ana','input':'Pedido recebido','action':name,'result':'Pedido conferido','purpose':'Evitar compra sem aprovação','placement':'Compras','handoff':'Lucas','tracking_record':record}
  text='; '.join(k+': '+v for k,v in values.items())+('. '+issue if issue else '')
  raw,source=self.source(text,record);refs=[{'source_id':source['id'],'quote':text}]
  fields={k:{'value':None if k in missing else v,'basis':'inferred' if k in missing else 'observed','evidence':[] if k in missing else refs} for k,v in values.items()}
  item={'task_id':None,'name':name,'fields':fields,'links':[]}
  if issue:item['consistency_issues']=[{'description':issue,'evidence':refs}]
  self.brain.apply_analysis(raw['job_id'],{'tasks':[item],'nodes':[],'context_updates':[],'next_question':None})
  return next(t for t in self.brain.snapshot()['tasks'] if t['name']==name)
 def tick(self):
  project_questions(self.brain,self.nora);tick=self.nora.tick()
  return self.repo.transact(self.company,lambda s:s.rounds[tick.round_id])
 def save(self,name,**data):
  (out/(name+'.json')).write_text(json.dumps({'fixture':'NEW report-derived; no original export replay','messages':self.messages,'snapshot':self.brain.snapshot(),**data},ensure_ascii=False,indent=2,default=str)+'\n')

checks={}
with TemporaryDirectory() as root:
 f=Fixture(root)
 try:
  task=f.task('Conferir cronograma')
  f.now+=timedelta(hours=1)
  raw,source=f.source('Ana revisa o cronograma.','Cronograma de obra')
  fields=deepcopy(task['fields']);fields['action']={'value':'Revisar cronograma','basis':'observed','evidence':[{'source_id':source['id'],'quote':source['text']}]}
  f.brain.apply_analysis(raw['job_id'],{'tasks':[{'task_id':task['id'],'name':task['name'],'fields':fields,'links':[]}],'nodes':[],'context_updates':[],'next_question':None})
  f.now=datetime.fromisoformat('2026-11-24T10:00:00-03:00')
  business=f.task('Confirmar aprovação da compra',issue='O pedido foi enviado sem definir quem aprova a compra.',record='cronograma-obra um301#Financeiro')
  expected=next(q for q in f.brain.snapshot()['questions'] if q['task_id']==business['id'] and q.get('gap_kind')=='business_consistency')
  first=f.tick();f.now+=timedelta(hours=4);second=f.tick()
  checks['F3_business_before_field_wording']=first.items[0].question.id==expected['id']
  checks['F10_no_slug_hash_in_human_message']=all('#' not in text and 'cronograma-obra um301' not in text for text in f.messages)
  checks['F10_specific_distinct_reasons']=first.items[0].why_chosen['reason']!=second.items[0].why_chosen['reason']
  checks['F10_word_boundary']=display_text('abcdefghijklmnop',5) not in ('abcde…','abcdefghijklmnop…')
  f.save('priority-copy',rounds=[asdict(first),asdict(second)],checks=deepcopy(checks))
 finally:f.close()
with TemporaryDirectory() as root:
 f=Fixture(root)
 try:
  f.task('Conferir medição',missing=('purpose',));round=f.tick();qid=round.items[0].question.id
  answer=f.nora.receive(InboundEvent(f.route.account,f.route.conversation,f.route.founder_sender,'fixture-delegate',f.now,'Pergunta ao Ana; isso ele sabe melhor.'))
  drain_nora_inbox(f.brain);source=next(s for s in f.brain.snapshot()['sources'] if s['text']==answer.event.text)
  job=next(j for j in f.brain.snapshot()['pending_jobs'] if source['id'] in j['source_ids'])
  f.brain.apply_analysis(job['id'],{'tasks':[],'nodes':[],'context_updates':[],'next_question':None,'message_intent':{'kind':'delegate','task_ids':[],'question_ids':[qid],'delegate_to':'Ana','disposition':None,'evidence':[{'source_id':source['id'],'quote':source['text']}]}})
  project_questions(f.brain,f.nora);status=health(f.nora)
  checks['F8_explicit_awaiting_delegate']=status.get('awaiting_delegate_questions')==1 and status.get('founder_pending_questions')==0
  checks['F8_no_false_resolution']=next(q for q in f.brain.snapshot()['questions'] if q['id']==qid)['status'] not in ('resolved','superseded')
  f.now+=timedelta(days=1);f.nora.tick();checks['F8_no_founder_resend']=len(f.messages)==1
  f.save('delegate',health=status,checks={k:v for k,v in checks.items() if k.startswith('F8')})
 finally:f.close()
with TemporaryDirectory() as root:
 f=Fixture(root)
 try:
  raw=f.brain.ingest_context_gap({'schema_version':1,'event_id':str(uuid4()),'device_id':str(uuid4()),'occurred_at':f.now.isoformat(),'app':'PrivateApp','surface_kind':'desktop_window','synthetic':True,'privacy_disposition':'withheld'})
  project_questions(f.brain,f.nora);status=health(f.nora)
  qs=[q for q in f.brain.snapshot()['questions'] if q['status']=='pending']
  checks['F11_withheld_not_founder_pending']=not qs and status.get('coverage_constraint_count',0)>=1
  checks['F11_no_private_send']=not f.messages
  f.save('withheld',health=status,checks={k:v for k,v in checks.items() if k.startswith('F11')})
 finally:f.close()
receipt={'checks':checks,'failures':[k for k,v in checks.items() if not v],'source_sha256':{name:sha256((product/name).read_bytes()).hexdigest() for name in ('company_pilot/nora/presentation.py','company_pilot/operator.py','company_pilot/nora/worker.py')},'original_exports_available':False,'real_external_actions':0}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2));raise SystemExit(bool(receipt['failures']))
