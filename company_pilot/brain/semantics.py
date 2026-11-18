"""Evidence-scoped field meaning and event chronology, not text similarity guesses."""
import copy
import re
import unicodedata
from .stores import BrainError,canonical


def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c)).split())


def _numbers(value):
    values=[]
    for token in re.findall(r'\d+(?:[.,]\d+)*',value):
        if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?',token):token=token.replace('.','')
        token=token.replace(',','.')
        try:token=str(float(token))
        except ValueError:pass
        values.append(token)
    return values


def _constraints(value):
    words=set(re.findall(r'\w+',normalized(value)))
    # Preserve polarity/state facts even if an interpreter labels them wording.
    states = {'aprovad', 'reprovad', 'autorizad', 'recusad', 'rejeitad', 'negad',
              'liberad', 'bloquead', 'pag', 'quitad', 'abert', 'fechad', 'concluid',
              'cancelad', 'suspendid', 'executad', 'entregu', 'aceit'}
    result = words & {'nao', 'sem', 'nunca', 'antes', 'depois', 'pendente', 'pendentes', 'andamento', 'entregue', 'entregues'}
    for word in words:
        stem = re.sub(r'(?:os|as|o|a)$', '', word)
        if stem in states:
            result.add(stem)
    return result


def _cites_prior_record(source, context):
    tokens = set()
    for key in context.get('record_keys') or []:
        for piece in re.split(r'[#/?=&._\-\s]+', ' '.join(str(part) for part in key)):
            token = normalized(piece)
            if len(token) >= 8 and token not in {'observation', 'company', 'record', 'https', 'drive', 'google', 'sheets', 'docs'}:
                tokens.add(token)
    if not tokens:
        return False
    observed = source.get('observation', {}).get('source', {}) or {}
    haystack = normalized(' '.join(str(observed.get(key, '')) for key in ('title', 'record_ref', 'app')) + ' ' + source.get('text', ''))
    return any(token in haystack for token in tokens)


def claim_time(state,source):
    from .evolution import event_time
    at=event_time(source)
    if at:return at
    if source.get('speaker',source.get('actor')) in state.company['founders'] and source['kind'] in ('answer','correction','interview_correction'):
        return source.get('channel',{}).get('received_at') or source.get('received_at')
    return None


def field_context(state,task,previous):
    from .evolution import record_keys,time_value
    contexts=copy.deepcopy(task.get('field_context',{}))
    for field,value in task['fields'].items():
        if not value.get('value') or value.get('basis')=='inferred' or not value.get('evidence'):continue
        old=(previous or {}).get('fields',{}).get(field)
        if old==value and field in contexts:continue
        sources=[state.sources[r['source_id']] for r in value['evidence']]
        keys={key for s in sources for key in record_keys(s)}
        prior=(previous or {}).get('field_context',{}).get(field,{})
        if not keys:keys={tuple(key) for key in prior.get('record_keys',[])}
        if not keys and previous:
            keys={key for sid in previous['evidence_ids'] for key in record_keys(state.sources[sid])}
        times=[time_value(claim_time(state,s)) for s in sources if claim_time(state,s)]
        if not times:continue
        latest=max(times);comparable=True
        if prior.get('effective_at') and latest<time_value(prior['effective_at']):
            if old and old.get('value')!=value['value']:comparable=False
            latest=max(latest,time_value(prior['effective_at']))
        authority='founder_report' if any(s.get('speaker',s.get('actor')) in state.company['founders'] and s['kind'] in ('answer','correction','interview_correction') for s in sources) else 'business_record' if keys else 'unverified_context'
        contexts[field]=dict(record_keys=[list(k) for k in sorted(keys)],effective_at=latest.isoformat(),authority=authority,rank=2 if authority in ('founder_report','business_record') else 1,clock_comparable=comparable,source_ids=sorted({s['id'] for s in sources}))
    task['field_context']=contexts


def _meaning_guard(state,field,old,new):
    if _numbers(old)!=_numbers(new):return 'different_quantities'
    if _constraints(old)!=_constraints(new):return 'different_constraint_or_state'
    if field=='performer':
        from .people import person_identity
        params=dict(company_id=state.company['id'],nodes=state.groups['nodes'].values(),context_claims=state.context_claims)
        left,right=person_identity(old,**params),person_identity(new,**params)
        if not left['node_id'] or left['node_id']!=right['node_id']:return 'different_or_unknown_people'
    return None


def field_update(brain,state,task,field,old,claim,update,source,allowed):
    from .evolution import record_keys,time_value,event_time
    required={'mode','effective_at','evidence'}
    if not isinstance(update,dict) or not required<=set(update) or set(update)-required-{'prior_evidence'} or update['mode'] not in ('progress','conflict','equivalent'):
        raise BrainError('typed_field_update_required')
    brain._claim(state,dict(value=update['mode'],basis='reported',evidence=update['evidence']),allowed)
    if update['mode']=='conflict':return 'conflict'
    if not any(r['source_id']==source['id'] for r in update['evidence']) or not any(r['source_id']==source['id'] for r in claim['evidence']):
        raise BrainError('current_field_update_evidence_required')
    if update['mode']=='equivalent':
        if not old or not old.get('value') or not claim.get('value') or claim['basis']=='inferred':raise BrainError('supported_equivalence_required')
        conflict=task.get('conflicts',{}).get(field,{})
        claims=[old]+conflict.get('alternatives',[])+([conflict['reported_candidate']] if conflict.get('reported_candidate') else [])
        prior={canonical(r) for value in claims for r in value['evidence']}
        supplied={canonical(r) for r in update.get('prior_evidence',[])}
        if not prior or supplied!=prior:raise BrainError('complete_prior_field_evidence_required')
        brain._claim(state,dict(value='prior field',basis='reported',evidence=update['prior_evidence']),allowed)
        reason=next((reason for value in claims if (reason:=_meaning_guard(state,field,value['value'],claim['value']))),None)
        task.setdefault('equivalence_history',[]).append(dict(field=field,previous=copy.deepcopy(claims),candidate=copy.deepcopy(claim),source_id=source['id'],decision='conflict' if reason else 'equivalent',reason=reason))
        return 'conflict' if reason else 'equivalent'
    if field not in ('result','handoff','tracking_record') or source['kind'] not in ('observation','company_record'):
        raise BrainError('mutable_observation_progress_required')
    now=time_value(event_time(source));effective=time_value(update['effective_at']) if update['effective_at'] else now
    if effective>now:raise BrainError('progress_effective_time_after_observation')
    if not old or not old.get('value'):return 'replace'
    keys=record_keys(source);context=task.get('field_context',{}).get(field,{})
    if not context:
        field_context(state,task,state.tasks.get(task['id']));context=task.get('field_context',{}).get(field,{})
    same_record = bool(keys) and len(context.get('record_keys', [])) == 1 and bool(keys.intersection({tuple(key) for key in context['record_keys']}))
    # A later window can cite the same spreadsheet or row under another address,
    # as when the diary quotes Cronograma_Realizado. A different record that
    # does not cite the earlier one stays a conflict.
    cited_same_work = (not same_record) and _cites_prior_record(source, context)
    if not (same_record or cited_same_work) or not context.get('clock_comparable', True) or context.get('rank', 99) > 2:
        return 'conflict'
    # Existing later conflicting evidence is part of chronology, not erased.
    conflict=task.get('conflicts',{}).get(field,{})
    competing=conflict.get('alternatives',[])+([conflict['reported_candidate']] if conflict.get('reported_candidate') else [])
    dates=[time_value(context['effective_at'])]
    for value in competing:
        for ref in value['evidence']:
            s=state.sources[ref['source_id']];at=claim_time(state,s)
            if not at:return 'conflict'
            if s['kind'] in ('answer','correction') and time_value(at)<time_value(context['effective_at']):
                return 'conflict'  # A mixed/simulated clock cannot grant precedence.
            dates.append(time_value(at))
    prior=max(dates)
    task.setdefault('progress_history',[]).append(dict(field=field,claim=copy.deepcopy(claim),previous=copy.deepcopy(old),effective_at=effective.isoformat(),source_id=source['id'],prior_authority=context['authority']))
    if effective<prior:return 'historic'
    if effective==prior:return 'same' if old['value']==claim['value'] else 'conflict'
    task.setdefault('field_effective_at',{})[field]=effective.isoformat()
    return 'replace'


def apply_attention(brain,state,values,job,source,allowed):
    if not isinstance(values,list) or len(values)>10:raise BrainError('bounded_attention_requests_required')
    if not values:return
    if job['kind']!='message' or source.get('speaker') not in state.company['founders']:
        raise BrainError('founder_attention_scope_required')
    candidates=set(job.get('candidate_question_ids',[]))|set(job.get('question_ids',[]))
    seen=set()
    for value in values:
        if not isinstance(value,dict) or set(value)!={'question_id','reason'}:raise BrainError('typed_attention_request_required')
        qid=value['question_id']
        if qid not in candidates or qid in seen:raise BrainError('attention_outside_supplied_question_scope')
        seen.add(qid);reason=brain._claim(state,value['reason'],allowed)
        if not reason['value'] or reason['basis']!='reported' or not any(r['source_id']==source['id'] for r in reason['evidence']):
            raise BrainError('current_founder_attention_evidence_required')
        question=state.questions[qid]
        state.put('questions',{**question,'priority_hint':dict(reason=reason,source_id=source['id']),'resend_request_id':'attention:'+source['id']})
