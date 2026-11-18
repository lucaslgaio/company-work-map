"""Exact recorded R1/R2 words in NEW restricted fixtures, never raw-run replay.

Usage: python3 THIS_FILE PUBLIC_FOUNDER_REPLIES_JSON NEW_OUTPUT_DIRECTORY
Real LocalStore/Brain/parser/worker; fake external CLI decisions, no network.
"""
import copy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from company_pilot.brain import Brain,FIELDS,LocalStore
from company_pilot.inference import ClaudeInterpreter,SemanticWorker
from tests.inference.test_claude import FakeRunner,stream

replies_path=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=False)
replies=json.loads(replies_path.read_text());recorded={r['id']:r for r in replies}
r1=next(r for k,r in recorded.items() if k.endswith('sim-caio-1'))
r2=next(r for k,r in recorded.items() if k.endswith('sim-caio-2'))
checks={};calls=[]
def uid():return str(uuid4())
def empty():return dict(tasks=[],nodes=[],context_updates=[],next_question=None)
def ref(s):return [dict(source_id=s['id'],quote=s['text'])]
def claim(s,value):return dict(value=value,basis='observed' if s['kind']=='observation' else 'reported',evidence=ref(s))
def source(b,sid):return next(s for s in b.snapshot()['sources'] if s['id']==sid)
def run(b,result,label):
 runner=FakeRunner(output=stream(result));answer=SemanticWorker(b,ClaudeInterpreter('/never-run',runner=runner)).run_once()
 assert answer['status']=='applied',answer
 calls.append(dict(label=label,decision_origin='NEW explicit fixture; exact recorded founder words, not reused model response',decision=result,response=answer))
 return answer

def export(b,name):
 target=out/name;target.mkdir();(target/'snapshot.json').write_text(json.dumps(b.snapshot(),ensure_ascii=False,indent=2)+'\n')
 for path,text in b.store.read().files.items():
  if path in ('views/empresa.md','views/perguntas.md','views/pessoas.md') or path.startswith(('views/tasks/','views/questions/')):
   p=target/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

with tempfile.TemporaryDirectory(prefix='grok2-original-words-') as tmp:
 b=Brain(LocalStore(Path(tmp)/'context'));b.initialize('Empresa sintética — palavras originais R1',synthetic=True)
 gap=b.ingest_context_gap(dict(schema_version=1,event_id=uid(),device_id=uid(),occurred_at='2026-11-23T12:00:00+00:00',app='Claude',surface_kind='desktop_window',synthetic=True))
 export(b,'r1-before');raw=b.record_message(r1['text'],uid(),question_ids=[gap['question_id']]);s=source(b,raw['answer_id'])
 intent=dict(kind='answer',task_ids=[],question_ids=[gap['question_id']],delegate_to=None,disposition=None,evidence=ref(s))
 result=dict(empty(),message_intent=intent,question_answers=[dict(question_id=gap['question_id'],claim=claim(s,'Transcrição do áudio do Valdir da Obra Um 301 para atualizar o cronograma e apoiar o relatório semanal ao cliente.'))])
 run(b,result,'R1-context');export(b,'r1-after')
 q=next(q for q in b.snapshot()['questions'] if q['id']==gap['question_id'])
 checks['R1_context_closed_with_supported_claim']=q['status']=='resolved' and q['resolution']['evidence']==ref(s)
 checks['R1_exact_recorded_words_preserved']=source(b,raw['answer_id'])['text']==r1['text']
 b=Brain(LocalStore(Path(tmp)/'task'));b.initialize('Empresa sintética — palavras originais R2',synthetic=True)
 text='Lucas marcou conduítes de todos os ambientes como Concluída, 100%, com base no áudio. A vistoria aponta que o quarto ainda está incompleto.'
 raw=b.ingest_observation(dict(schema_version=1,event_id=uid(),device_id=uid(),occurred_at='2026-11-23T12:00:00+00:00',policy_version=1,source=dict(kind='active_window',app='Cronograma sintético',record_ref='Cronograma_Realizado'),classification=dict(label='company',reason='fixture sintética permitida'),content=dict(text=text,sha256=sha256(text.encode()).hexdigest()),synthetic=True))
 s=source(b,raw['observation_id']);fields={f:dict(value=None,basis='inferred',evidence=[]) for f in FIELDS}
 for field,value in dict(performer='Lucas',action='Atualizar o cronograma',result='Concluída, 100%, todos os ambientes').items():fields[field]=claim(s,value)
 result=dict(empty(),tasks=[dict(task_id=None,name='Atualizar o status dos conduítes',fields=fields,links=[],consistency_issues=[dict(description='Conclusão integral sem confirmação em todos os ambientes',evidence=ref(s))])])
 run(b,result,'R2-before-task');task=b.snapshot()['tasks'][0];q=next(q for q in b.snapshot()['questions'] if q.get('gap_kind')=='business_consistency')
 old_files=copy.deepcopy(b.store.read().files);export(b,'r2-before')
 raw=b.record_message(r2['text'],uid(),question_ids=[q['id']]);s=source(b,raw['answer_id']);fields=copy.deepcopy(task['fields'])
 fields['performer']=claim(s,'Bia');fields['result']=claim(s,'Somente sala e cozinha; a conclusão total depende da confirmação de Bia na vistoria.')
 intent=dict(kind='answer',task_ids=[task['id']],question_ids=[q['id']],delegate_to=None,disposition=None,evidence=ref(s))
 result=dict(empty(),message_intent=intent,tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[],issue_resolutions=[dict(issue_ids=[task['consistency_issues'][0]['id']],resolution=claim(s,'Era só sala e cozinha; Bia confirma a conclusão na vistoria.'))])])
 run(b,result,'R2-named-resolution');export(b,'r2-after');current=b.snapshot()['tasks'][0]
 checks['R2_named_issue_closed']=not current['consistency_issues'] and next(x for x in b.snapshot()['questions'] if x['id']==q['id'])['status']=='resolved'
 checks['R2_current_performer']=current['fields']['performer']['value']=='Bia'
 checks['R2_no_known_stale_100_percent_current']=current['fields']['result']['value']!='Concluída, 100%, todos os ambientes'
 checks['R2_only_supported_state_no_invented_80_percent']='80%' not in current['fields']['result']['value']
 checks['R2_old_source_and_task_versions_unchanged']=all(b.store.read().files[path]==text for path,text in old_files.items() if path.startswith(('sources/','records/','ledger/')))
 checks['R2_remaining_genuine_gaps_visible']=current['status']=='provisional' and any(x['status']=='pending' for x in b.snapshot()['questions'])
 checks['R2_exact_recorded_words_preserved']=source(b,raw['answer_id'])['text']==r2['text']
receipt=dict(fixture='Exact public R1/R2 words in NEW reduced synthetic states + NEW explicit semantic decisions; NOT original request or fullDB replay',public_founder_replies_sha256=sha256(replies_path.read_bytes()).hexdigest(),original_reply_ids=[r1['id'],r2['id']],checks=checks,real_model_network_native_provider_channel_calls=0,calls=calls)
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'checks':checks,'receipt':str(out/'receipt.json')},ensure_ascii=False));assert all(checks.values())
