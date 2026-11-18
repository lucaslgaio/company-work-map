"""Selected correction authority is narrower than the supplied candidate map."""
import copy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, BrainError, LocalStore
from company_pilot.brain.messages import uncertainty_basis
import test_engine as fixtures


def uid(): return str(uuid4())
def empty(): return dict(tasks=[], nodes=[], context_updates=[], next_question=None)
def evidence(source): return [dict(source_id=source['id'], quote=source['text'])]


class SelectedScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.brain=Brain(LocalStore(Path(self.temp.name)));self.brain.initialize('Oficina sintética',synthetic=True)
        self.tasks=[]
        for name in ('Joana','Mira'):
            raw=self.brain.ingest_observation(fixtures.observation(name+' conferiu uma fatura.'))
            source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['observation_id'])
            self.brain.apply_analysis(raw['job_id'],fixtures.analysis(source,performer=name))
            self.tasks.append(next(t for t in self.brain.snapshot()['tasks'] if raw['observation_id'] in t['evidence_ids']))

    def message(self):
        raw=self.brain.record_message('Corrija somente a primeira atividade.',uid(),
            candidate_task_versions=[dict(id=t['id'],version=t['version']) for t in self.brain.snapshot()['tasks']])
        source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['answer_id'])
        state=self.brain.snapshot()
        scope={group:[dict(id=record['id'],version=record['version']) for record in state[group]] for group in ('tasks','nodes','edges')}
        scope['source_ids']=[s['id'] for s in state['sources']]
        intent=dict(kind='correction',task_ids=[self.tasks[0]['id']],question_ids=[],delegate_to=None,disposition=None,evidence=evidence(source))
        return raw,source,state,scope,intent

    def test_unselected_task_node_and_edge_writes_are_rejected_atomically(self):
        second=self.tasks[1]
        edge=next(e for e in self.brain.snapshot()['edges'] if e.get('task_id')==second['id'])
        node=edge['to']
        operations=[
            dict(op='rename',id=second['id'],name='Fora da seleção'),
            dict(op='correct',id=second['id'],fields={}),
            dict(op='reassign',id=second['id'],performer=None),
            dict(op='rename',id=node,name='Pessoa fora da seleção'),
            dict(op='disconnect',id=edge['id']),
            dict(op='connect',kind='tracks',**{'from':second['id'],'to':node}),
            dict(op='merge',ids=[self.tasks[0]['id'],second['id']],successors=[]),
        ]
        for operation in operations:
            with self.subTest(operation=operation['op']):
                raw,source,before,scope,intent=self.message()
                operation=copy.deepcopy(operation)
                operation.update(evidence=evidence(source),expected_versions=[])
                if operation['op']=='reassign':
                    operation['performer']=dict(value='Outra pessoa',basis='reported',evidence=evidence(source))
                result=dict(empty(),message_intent=intent,map_changes=[operation])
                head=self.brain.store.read().head
                with self.assertRaisesRegex(BrainError,'outside_selected'):
                    self.brain.apply_analysis(raw['job_id'],result,map_scope=scope)
                self.assertEqual(self.brain.store.read().head,head)
                after=self.brain.snapshot()
                for group in ('tasks','nodes','edges','questions','context','sources'):
                    self.assertEqual(after[group],before[group])

    def test_selected_task_can_connect_to_read_only_candidate_without_mutating_it(self):
        raw,source,before,scope,intent=self.message()
        first,second=self.tasks
        operation=dict(op='connect',kind='depends_on',**{'from':first['id'],'to':second['id']},
            evidence=evidence(source),expected_versions=[dict(id=t['id'],version=t['version']) for t in (first,second)])
        receipt=self.brain.apply_analysis(raw['job_id'],dict(empty(),message_intent=intent,map_changes=[operation]),map_scope=scope)
        self.assertEqual(receipt['selected_task_ids'],[first['id']])
        after=self.brain.snapshot()
        self.assertEqual(next(t for t in after['tasks'] if t['id']==second['id']),second)
        self.assertTrue(any(e['from']==first['id'] and e['to']==second['id'] and e['active'] for e in after['edges']))

    def test_delegation_basis_is_canonical_and_survives_conflict_before_projection(self):
        task=self.tasks[0]
        self.brain.change_map([dict(op='correct',id=task['id'],fields={'purpose':'Finalidade inicial'})],uid(),'Finalidade inicial confirmada pelo fundador.')
        def answer(value):
            raw=self.brain.record_message(value,uid())
            source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['answer_id'])
            current=next(t for t in self.brain.snapshot()['tasks'] if t['id']==task['id'])
            fields=copy.deepcopy(current['fields']);fields['purpose']=dict(value=value,basis='reported',evidence=evidence(source))
            result=dict(empty(),tasks=[dict(task_id=task['id'],name=task['name'],fields=fields,links=[])],message_intent=dict(kind='answer',task_ids=[task['id']],question_ids=[],delegate_to=None,disposition=None,evidence=evidence(source)))
            self.brain.apply_analysis(raw['job_id'],result)
        answer('Outra finalidade A')
        question=next(q for q in self.brain.snapshot()['questions'] if q['task_id']==task['id'] and q['missing_links']==['purpose'])
        before=uncertainty_basis(question)
        raw=self.brain.record_message('Renan deve responder a esta dúvida.',uid(),question_ids=[question['id']])
        source=next(s for s in self.brain.snapshot()['sources'] if s['id']==raw['answer_id'])
        result=dict(empty(),message_intent=dict(kind='delegate',task_ids=[],question_ids=[question['id']],delegate_to='Renan',disposition=None,evidence=evidence(source)))
        self.brain.apply_analysis(raw['job_id'],result)
        answer('Outra finalidade B')  # No Nora projection has happened yet.
        changed=next(q for q in self.brain.snapshot()['questions'] if q['id']==question['id'])
        frozen=changed['response_disposition']['uncertainty_basis']
        self.assertEqual(frozen,before)
        self.assertNotEqual(frozen,uncertainty_basis(changed))
        self.assertEqual(changed['response_disposition']['source_id'],source['id'])


if __name__=='__main__':unittest.main()
