"""F9 independent-review counterexample through parser/worker/Brain, no model."""
import copy
import unittest
from tests.inference import test_grok2_composition as fixtures


class Round4RelationTests(unittest.TestCase):
    setUp = fixtures.Grok2CompositionTests.setUp
    source = fixtures.Grok2CompositionTests.source
    observe = fixtures.Grok2CompositionTests.observe
    run_model = fixtures.Grok2CompositionTests.run_model

    def relation(self, quote, *, result='Lista conferida V7', basis='observed', incoming='Lista conferida V7', kind='handoff', target_name='Consolidar relatório mensal', task_name='Preparar lista'):
        _, src = self.observe('Rafa usa '+incoming+' para '+target_name+'.', record_ref='CONSUMER')
        self.run_model({**fixtures.empty(), 'tasks': [fixtures.candidate(src, target_name,
            {'input': incoming, 'action': target_name, 'result': 'Relatório mensal consolidado'})]})
        consumer = self.brain.snapshot()['tasks'][0]
        raw, src = self.observe('Rafa produziu '+result+'. '+quote, record_ref='PRODUCER')
        decision = fixtures.candidate(src, task_name, {'action': task_name, 'result': result})
        decision['links'] = [{'kind': kind, 'target_id': consumer['id'], 'basis': basis,
            'evidence': [{'source_id': src['id'], 'quote': quote}]}]
        before = copy.deepcopy(self.brain.snapshot()['tasks'])
        response, _ = self.run_model({**fixtures.empty(), 'tasks': [decision]}, require_applied=False)
        return raw, src, before, response

    def test_true_literal_output_to_input_relation_applies(self):
        _, _, _, response = self.relation('A Lista conferida V7 é a entrada da tarefa Consolidar relatório mensal.')
        self.assertEqual(response['status'], 'applied', response)
        self.assertTrue(any(e['kind']=='handoff' and e['active'] for e in self.brain.snapshot()['edges']))

    def test_explicit_no_handoff_and_different_objects_reject_atomically(self):
        raw, src, before, response = self.relation('Rafa aparece em ambas as atividades. Não há indicação de repasse entre elas.', result='Comprovante arquivado')
        self.assert_rejected(raw, src, before, response)

    def test_matching_object_with_explicit_negated_relation_cannot_confirm(self):
        raw, src, before, response = self.relation('A Lista conferida V7 não é a entrada da tarefa Consolidar relatório mensal.')
        self.assert_rejected(raw, src, before, response)

    def test_shared_person_and_object_without_relation_cannot_confirm(self):
        raw, src, before, response = self.relation('Rafa, Lista conferida V7 e Consolidar relatório mensal aparecem na consulta.')
        self.assert_unlinked(response, before)

    def test_relation_words_cannot_bridge_different_supported_objects(self):
        raw, src, before, response = self.relation('O Comprovante arquivado inicia a tarefa Consolidar relatório mensal.', result='Comprovante arquivado')
        self.assert_inferred(response)

    def test_reversed_transfer_does_not_confirm_producer_to_consumer(self):
        raw, src, before, response = self.relation('Consolidar relatório mensal envia a Lista conferida V7 para Preparar lista.')
        self.assert_rejected(raw, src, before, response)

    def test_reverse_transfer_relative_clause_keeps_its_own_subject(self):
        raw, src, before, response = self.relation('Consolidar relatório mensal envia a Lista conferida V7 para Preparar lista, que usa a Lista conferida V7.')
        self.assert_rejected(raw, src, before, response)

    def test_unrelated_negative_approval_does_not_block_true_transfer(self):
        _, _, _, response = self.relation('Não há indicação de dependência de aprovação. Rafa envia a Lista conferida V7 para Consolidar relatório mensal.')
        self.assertEqual(response['status'], 'applied', response)
        self.assertTrue(any(e['kind']=='handoff' and e['active'] for e in self.brain.snapshot()['edges']))

    def test_shared_cited_object_links_without_verbatim_result(self):
        _, _, _, response = self.relation(
            'A lista revisada vai para consolidar o relatório mensal.',
            result='Lista revisada de material elétrico', incoming='Lista revisada')
        self.assertEqual(response['status'], 'applied', response)
        self.assertTrue(any(edge['kind'] == 'handoff' and edge['active'] for edge in self.brain.snapshot()['edges']))

    def test_unsupported_link_keeps_the_capture(self):
        raw, source, before, response = self.relation(
            'Rafa e o relatório aparecem na mesma tela.', result='Comprovante arquivado')
        self.assertEqual(response['status'], 'applied', response)
        self.assertGreater(len(self.brain.snapshot()['tasks']), len(before))
        self.assertFalse(any(edge['kind'] == 'handoff' and edge['active'] and edge.get('basis') == 'observed'
                             for edge in self.brain.snapshot()['edges']))
        self.assertEqual(self.source(source['id'])['text'], source['text'])
        self.assertTrue(any(task.get('omitted_relations') for task in self.brain.snapshot()['tasks']))
        self.assertFalse(any(job['id'] == raw['job_id'] and job['status'] == 'pending' for job in self.brain.snapshot()['pending_jobs']))

    def test_supported_object_hypothesis_stays_inferred_and_pending(self):
        _, _, before, response = self.relation('Lista conferida V7 e Consolidar relatório mensal aparecem na consulta.', basis='inferred')
        self.assert_unlinked(response, before)

    def test_paraphrased_supported_object_labels_remain_visible_for_confirmation(self):
        _, _, _, response = self.relation('Rafa envia a Lista conferida V7, também descrita como Relação de itens revisados, para Consolidar relatório mensal.', incoming='Relação de itens revisados')
        self.assert_inferred(response)

    def test_dependency_keeps_consumer_to_producer_direction_without_equal_labels(self):
        _, _, _, response = self.relation('Consolidar relatório depende de Preparar lista.',
            result='Relatório consolidado', incoming='Itens a revisar', kind='depends_on',
            task_name='Consolidar relatório', target_name='Preparar lista')
        self.assertEqual(response['status'], 'applied', response)
        edge = next(e for e in self.brain.snapshot()['edges'] if e['kind']=='depends_on')
        self.assertEqual(edge['basis'], 'observed')

    def test_payment_relation_keeps_distinct_payment_and_service_labels(self):
        _, _, _, response = self.relation('O pagamento registrado quita o serviço Conferir instalação.',
            result='Pagamento registrado', incoming='Instalação executada', kind='pays_for',
            task_name='Registrar pagamento', target_name='Conferir instalação')
        self.assertEqual(response['status'], 'applied', response)
        self.assertTrue(any(e['kind']=='pays_for' and e['basis']=='observed' for e in self.brain.snapshot()['edges']))

    def test_co_mention_without_a_transfer_stays_unlinked(self):
        """Seven co-mentions, plus two that were already left off the map."""
        negatives = (
            ("A planilha Cronograma_Realizado da Obra Um mostra a cotação do material elétrico e a medição 02.", "Lista de material"),
            ("Na reunião de alinhamento falamos da cotação do material e do aditivo 01.", "Lista de material"),
            ("O arquivo no Drive tem o rascunho do relatório e a lista de material.", "Lista de material"),
            ("A lista de material aparece na cotação e também na conferência da compra.", "Lista de material"),
            ("No grupo alguém citou a medição 02 e o cronograma atualizado.", "Lista de material"),
            ("Ana trabalha na cotação do material e também no aditivo 01.", "Lista de material"),
            ("Agenda de segunda: cotar o material elétrico; liberar o pagamento da medição.", "Lista de material"),
            ("O Ana vai à obra amanhã olhar a planilha de custos. O Bia também vai olhar a mesma planilha.", "planilha de custos"),
            ("A planilha de custos da Obra Um está na mesma planilha da medição. A Caio mandou um áudio à parte.", "planilha de custos"),
        )
        for quote, shared in negatives:
            _raw, _src, before, response = self.relation(quote, result=shared, incoming=shared)
            self.assert_unlinked(response, before)

    def assert_unlinked(self, response, before):
        self.assertEqual(response['status'], 'applied', response)
        self.assertFalse(any(edge['kind'] == 'handoff' and edge.get('active') for edge in self.brain.snapshot()['edges']))
        self.assertGreaterEqual(len(self.brain.snapshot()['tasks']), len(before))

    def assert_inferred(self, response):
        self.assertEqual(response['status'], 'applied', response)
        edge = next(e for e in self.brain.snapshot()['edges'] if e['kind']=='handoff')
        self.assertEqual(edge['basis'], 'inferred')
        self.assertEqual(edge['proposed_basis'], 'observed')
        self.assertTrue(edge['validation_reason'])
        self.assertTrue(any(q.get('gap_kind')=='task_handoff' and q['status']=='pending' for q in self.brain.snapshot()['questions']))
        producer = next(t for t in self.brain.snapshot()['tasks'] if t['id']==edge['from'])
        self.assertEqual(producer['status'], 'provisional')

    def assert_rejected(self, raw, src, before, response):
        self.assertEqual(response['status'], 'pending', response)
        self.assertEqual(self.brain.snapshot()['tasks'], before)
        self.assertFalse(any(e['kind']=='handoff' for e in self.brain.snapshot()['edges']))
        self.assertEqual(self.source(src['id'])['text'], src['text'])
        self.assertTrue(any(j['id']==raw['job_id'] and j['status']=='pending' for j in self.brain.snapshot()['pending_jobs']))


if __name__ == '__main__':
    unittest.main()
