"""Scoped answers, temporal work facts and inspectable question support.

These helpers operate only inside the Brain transaction. Semantic proposals do
not grant source authority or erase an unresolved issue by omission.
"""
import copy
from datetime import datetime
from .stores import BrainError, canonical, digest


def record_keys(source):
    if source.get('kind')=='observation':
        ref=source.get('observation',{}).get('source',{}).get('record_ref')
        return {('observation',ref)} if isinstance(ref,str) and ref.strip() else set()
    record=source.get('source_record',{})
    if source.get('kind')=='company_record' and record.get('record_id'):
        return {('company_record',record.get('system'),record['record_id'])}
    return set()


def event_time(source):
    return source.get('observation',{}).get('occurred_at',source.get('safe_metadata',{}).get('occurred_at',source.get('occurred_at')))


def time_value(value):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError()
        return stamp
    except (ValueError,TypeError,AttributeError):
        raise BrainError('effective_time_with_timezone_required') from None


def scoped_field_answer(state,job,source,task_id,field):
    if source.get('speaker') not in state.company['founders']:return False
    return any(qid in state.questions and state.questions[qid].get('task_id')==task_id
               and state.questions[qid].get('gap_kind')!='business_consistency'
               and state.questions[qid].get('missing_links')==[field]
               and state.questions[qid]['status'] in ('pending','awaiting_person')
               and fresh_question(state,source,qid)
               for qid in job.get('question_ids',[]))


def fresh_question(state,source,qid):
    from .messages import uncertainty_basis
    if qid in source.get('question_bases',{}):
        return source['question_bases'][qid]==uncertainty_basis(state.questions[qid])
    task=state.tasks.get(state.questions[qid].get('task_id'))
    return bool(task and source.get('base_versions',{}).get(task['id'])==task['version'])


def progress_decision(brain,state,task,field,old,claim,update,source,allowed):
    from .semantics import field_update
    return field_update(brain,state,task,field,old,claim,update,source,allowed)


def issue_id(task_id,issue):
    from .engine import identifier
    return identifier(task_id,'consistency:'+digest(issue['description']))


def normalize_issues(task):
    for issue in task.get('consistency_issues',[]):issue['id']=issue_id(task['id'],issue)


def resolve_issues(brain,state,task,values,job,source,allowed,message):
    normalize_issues(task)
    if not values:return False
    if not isinstance(values,list) or len(values)>20 or not message or message['kind'] not in ('answer','correction') or source.get('speaker') not in state.company['founders']:
        raise BrainError('scoped_founder_issue_resolution_required')
    questions=[qid for qid in job.get('question_ids',[]) if state.questions[qid].get('task_id')==task['id'] and state.questions[qid].get('gap_kind')=='business_consistency']
    if not all(fresh_question(state,source,qid) for qid in questions) or (not questions and source.get('base_versions',{}).get(task['id'])!=state.tasks.get(task['id'],{}).get('version')):
        raise BrainError('stale_issue_resolution')
    current={i['id']:i for i in task.get('consistency_issues',[])}
    current.update({item['id']:item for item in task.get('issue_replays',[]) if item.get('status')=='awaiting_clarification'})
    selected=set()
    for value in values:
        if not isinstance(value,dict) or set(value)!={'issue_ids','resolution'} or not value['issue_ids'] or not set(value['issue_ids'])<=set(current):
            raise BrainError('explicit_current_issue_ids_required')
        resolved=brain._claim(state,value['resolution'],allowed)
        if not resolved['value'] or resolved['basis']=='inferred' or not any(r['source_id']==source['id'] for r in resolved['evidence']):
            raise BrainError('founder_resolution_evidence_required')
        for iid in value['issue_ids']:
            selected.add(iid)
            task.setdefault('resolved_issues',[]).append(dict(issue=copy.deepcopy(current[iid]),resolution=resolved,source_id=source['id']))
    task['consistency_issues']=[i for i in task['consistency_issues'] if i['id'] not in selected]
    for replay in task.get('issue_replays',[]):
        if replay['id'] in selected:
            replay.update(status='resolved',resolved_by=source['id'])
    return True


def merge_issues(task,issues):
    normalize_issues(task)
    current={i['id']:i for i in task.get('consistency_issues',[])}
    for issue in issues:
        value=copy.deepcopy(issue);value['id']=issue_id(task['id'],value)
        resolved=[r for r in task.get('resolved_issues',[]) if r['issue']['id']==value['id']]
        if resolved and all(canonical(ref) in {canonical(r) for done in resolved for r in done['issue']['evidence']} for ref in value['evidence']):
            continue  # Same named issue and support: the explicit resolution wins.
        settled=task.get('resolved_issues',[])
        old_support=[ref for item in settled for ref in item['issue']['evidence']]
        old_only=bool(value['evidence']) and all(any(ref['source_id']==old['source_id'] and ref['quote'] in old['quote'] for old in old_support) for ref in value['evidence'])
        if value['id'] not in current and old_only:
            # Shared support cannot identify the point. Keep an explicit open
            # interpretation rather than equating it to a settled issue or
            # reopening that issue from wording alone.
            replay={**copy.deepcopy(value),'reason':'issue_identity_requires_clarification','status':'awaiting_clarification'}
            history=task.setdefault('issue_replays',[])
            if replay not in history:history.append(replay)
            task['status']='provisional'
            continue
        if value['id'] in current:
            refs={canonical(r):r for r in current[value['id']]['evidence']+value['evidence']}
            value['evidence']=list(refs.values())
        current[value['id']]=value
        for replay in task.get('issue_replays',[]):
            if replay['id']==value['id'] and replay.get('status')=='awaiting_clarification':
                replay.update(status='promoted',issue_id=value['id'])
    task['consistency_issues']=list(current.values())


def question_answers(brain,state,values,job,source,allowed,scope=None):
    if not isinstance(values,list) or len(values)>20:raise BrainError('bounded_question_answers_required')
    if values and job['kind'] not in ('message','answer','observation'):
        raise BrainError('question_answer_job_scope_required')
    seen=set()
    for item in values:
        if not isinstance(item,dict) or set(item)!={'question_id','claim'}:raise BrainError('typed_question_answer_required')
        qid=item['question_id']
        if qid in seen or qid not in state.questions:raise BrainError('unique_current_question_answer_required')
        seen.add(qid);q=state.questions[qid]
        scoped=(qid in job.get('question_ids',[]) and (q.get('target_kind')=='context' or q.get('task_id') in job.get('task_ids',[]))) or (job['kind']=='observation' and q.get('task_id') in {r['id'] for r in (scope or {}).get('tasks',[])})
        if not scoped:raise BrainError('question_answer_outside_selected_scope')
        value=brain._claim(state,item['claim'],allowed)
        if not value['value'] or value['basis']=='inferred':continue
        if q.get('target_kind')=='context':
            if source.get('speaker') not in state.company['founders'] or not any(r['source_id']==source['id'] for r in value['evidence']):raise BrainError('founder_context_answer_required')
            state.context['work-signal:'+source['id']+':'+qid]=value
            state.company['context_version']+=1
            state.put('questions',{**q,'status':'resolved','resolved_by':source['id'],'resolution':value})
        elif q.get('task_id') in state.tasks and not q.get('gap_kind') and len(q.get('missing_links',[]))==1:
            field=q['missing_links'][0];task=copy.deepcopy(state.tasks[q['task_id']]);old=task['fields'].get(field)
            value=brain._field_claim(state,value,allowed,field)
            if old and old.get('value') and not scoped_field_answer(state,job,source,task['id'],field):continue
            task['fields'][field]=value;task['conflicts'].pop(field,None)
            task['evidence_ids']=sorted(set(task['evidence_ids'])|{r['source_id'] for r in value['evidence']})
            brain._refresh_task(state,task)
        else:raise BrainError('named_question_requires_typed_resolution')


def answer_outcomes(state,job,source,message):
    if not message or message['kind'] not in ('answer','correction'):return []
    outcomes=[]
    for qid in message['question_ids']:
        q=state.questions[qid];resolved=q['status'] in ('resolved','superseded')
        outcome=dict(question_id=qid,status='resolved' if resolved else 'unresolved',reason='supported_answer_applied' if resolved else 'selected_question_not_substantively_resolved',source_id=source['id'])
        outcomes.append(outcome)
        state.put('questions',{**q,'application':outcome,
            **({'resend_request_id':'unresolved-application:'+source['id']} if not resolved else {})})
    return outcomes


def decorate_question(state,question):
    """Evidence is real support, never harness-selected first-N questions."""
    q=copy.deepcopy(question);task=state.tasks.get(q.get('task_id'));refs=copy.deepcopy(q.get('link_evidence',[]))
    if task:
        if refs:pass
        elif q.get('gap_kind')=='business_consistency':refs=[r for i in task.get('consistency_issues',[]) for r in i['evidence']]
        elif q.get('conflict'):
            refs=[r for value in [q['conflict'].get(key,{}) for key in ('previous','reported_candidate')]+q['conflict'].get('alternatives',[]) for r in value.get('evidence',[])]
        else:refs=[r for field in q.get('missing_links',[]) for r in task['fields'].get(field,{}).get('evidence',[])]
        if not refs:refs=[r for field in ('action','input','result') for r in task['fields'].get(field,{}).get('evidence',[])]
    if not refs:refs=[dict(source_id=sid,quote=state.sources[sid]['text'][:500]) for sid in q.get('evidence_ids',[]) if sid in state.sources]
    unique={canonical(r):r for r in refs if r.get('quote') and r.get('source_id') in state.sources and r['quote'] in state.sources[r['source_id']]['text']}
    q['evidence']=[]
    for r in unique.values():
        s=state.sources[r['source_id']]
        q['evidence'].append({**copy.deepcopy(r),'locator':'sources/'+s['id']+'.json','actor':s.get('actor'),'occurred_at':event_time(s)})
    if q['evidence']:
        s=state.sources[q['evidence'][-1]['source_id']];meta=s.get('observation',{}).get('source',{}) or s.get('safe_metadata',{})
        q['observed_context']=dict(actor=s.get('actor'),occurred_at=event_time(s),app=meta.get('app'),record_ref=meta.get('record_ref'))
    q['known_facts']=[]
    if task:
        keys={key for sid in task['evidence_ids'] for key in record_keys(state.sources[sid])}
        for other in state.tasks.values():
            if other.get('superseded_by') or not keys.intersection({key for sid in other['evidence_ids'] for key in record_keys(state.sources[sid])}):continue
            for field,value in other['fields'].items():
                if value.get('value') and value['basis']!='inferred' and value['evidence'] and field not in other.get('conflicts',{}):q['known_facts'].append(dict(task_id=other['id'],field=field,claim=copy.deepcopy(value)))
        q['known_facts']=q['known_facts'][:100]
    return q


def observation_scope(state,job,scope,result):
    tasks=result.get('tasks',[])
    if not isinstance(tasks,list) or not 1<=len(tasks)<=3:raise BrainError('observation_task_bound_required')
    ids=[t.get('task_id') for t in tasks if t.get('task_id')]
    if len(ids)!=len(set(ids)):raise BrainError('duplicate_observation_task_refinement')
    for task in tasks:
        if not task.get('task_id') and not any(r.get('source_id')==job['source_ids'][0] for value in task.get('fields',{}).values() for r in value.get('evidence',[])):
            raise BrainError('new_task_requires_current_observation')
    candidate_scope(state,job,scope,result)


def candidate_scope(state,job,scope,result):
    ids=[t.get('task_id') for t in result.get('tasks',[]) if t.get('task_id')]
    if len(ids)!=len(set(ids)):raise BrainError('duplicate_task_refinement')
    if not scope:return
    if set(scope)!={'tasks','nodes','edges','source_ids'}:raise BrainError('trusted_candidate_scope_required')
    supplied={t['id']:t['version'] for t in scope['tasks']}
    if not set(ids)<=set(supplied):raise BrainError('observation_task_outside_supplied_scope')
    for tid,version in supplied.items():
        if tid not in state.tasks or state.tasks[tid]['version']!=version:raise BrainError('stale_observation_task_scope')
    accepted={sid for s in state.sources.values() if s.get('kind')=='analysis' and s.get('source_scope')!='historic' for sid in s.get('input_source_ids',[])}
    required=set(job['source_ids'])|{r['source_id'] for c in state.context.values() for r in c.get('evidence',[])}
    required.update(sid for tid in supplied for sid in state.tasks[tid]['evidence_ids'])
    supplied_sources=set(scope['source_ids'])
    if not set(job['source_ids'])<=supplied_sources or not supplied_sources<=accepted|required or len(supplied_sources-required)>50:
        raise BrainError('source_not_previously_accepted_or_outside_bound')


def carry_prior_statements(state, task):
    """A founder answer given before its task exists still fills the empty card.

    Only a shared numbered object (for example «aditivo 01») and an explicit
    «a pessoa monta» / «eu mando» sentence are copied. Other fields stay put.
    """
    import re
    from .semantics import normalized
    blob = ' '.join(part for part in (task.get('name'), (task.get('fields') or {}).get('result', {}).get('value')) if part)
    objects = set(re.findall(r'\b[\wÀ-ÿ]{4,}\s+\d+\b', normalized(blob)))
    if not objects:
        return
    role = re.compile(r'\b(?:o|a)\s+([A-Za-zÀ-ÿ]{2,})\s+(monta|faz|executa|redige|envia|manda|assina|revisa|confere|cota)\b')
    founders = set(state.company.get('founders', []))

    def blank(field):
        claim = task['fields'].get(field) or {}
        return not claim.get('value') or claim.get('basis') == 'inferred'

    for source in state.sources.values():
        if source.get('kind') not in ('answer', 'correction', 'message'):
            continue
        if source.get('speaker') not in founders:
            continue
        if not objects.intersection(set(re.findall(r'\b[\wÀ-ÿ]{4,}\s+\d+\b', normalized(source.get('text', ''))))):
            continue
        quote = source['text']
        found = role.search(quote)
        if found and blank('performer'):
            task['fields']['performer'] = {'value': found.group(1), 'basis': 'reported', 'evidence': [{'source_id': source['id'], 'quote': quote}]}
        if re.search(r'\beu\s+(?:mando|envio|enviou)\b', quote, re.I) and blank('handoff'):
            task['fields']['handoff'] = {'value': 'Quem respondeu manda o resultado', 'basis': 'reported', 'evidence': [{'source_id': source['id'], 'quote': quote}]}
        if not blank('performer') and not blank('handoff'):
            return


def track_task_link(brain,state,task_id,edge_id,link,source):
    """An inferred handoff remains an explicit, sourced confirmation gap."""
    from .engine import identifier,now
    qid=identifier(edge_id,'confirm-handoff');old=state.questions.get(qid)
    task=copy.deepcopy(state.tasks[task_id]);pending=set(task.get('unconfirmed_links',[]))
    if link.get('basis')=='inferred':pending.add(edge_id)
    else:pending.discard(edge_id)
    if pending!=set(task.get('unconfirmed_links',[])):
        task['unconfirmed_links']=sorted(pending);task=brain._refresh_task(state,task)
    if edge_id in pending:
        state.put('questions',dict(id=qid,company_id=state.company['id'],task_id=task_id,base_version=task['version'],task_version=task['version'],gap_kind='task_handoff',missing_links=['handoff'],missing_fields=['handoff'],uncertainty_ids=[qid],context_key=task_id,activity_summary=task['name'],prompt='O resultado de «'+task['name']+'» é usado para iniciar «'+state.tasks[link['target_id']]['name']+'»?',status='pending',created_at=(old or {}).get('created_at',now()),evidence_ids=task['evidence_ids'],observation_ids=[sid for sid in task['evidence_ids'] if state.sources[sid]['kind'] in ('observation','company_record')],link_evidence=copy.deepcopy(link['evidence']),link_id=edge_id))
    elif old:
        state.put('questions',{**old,'status':'resolved','resolved_by':source['id']})
