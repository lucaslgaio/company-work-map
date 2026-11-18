"""Bounded F3/F8/F10/F11 controls; actual-caller red/green lives in round4_probe."""
from dataclasses import replace
from datetime import timedelta
import unittest
import test_nora as fixtures
from company_pilot.nora.engine import question_digest
from company_pilot.nora.presentation import display_text
from company_pilot.nora.worker import health

class Round4Controls(unittest.TestCase):
 def fixture(self):
  case=fixtures.NoraTests('runTest');case.setUp();self.addCleanup(case.doCleanups);return case
 def test_business_consistency_precedes_older_field_conflict_and_reminder(self):
  f=self.fixture();old=f.question(conflict_ids=('field',),missing_links=('action',));f.nora.put_question(old);f.nora.tick()
  f.clock.value+=timedelta(days=2)
  material=f.question(2,gap_kind='business_consistency',issue_ids=('purchase-approval',),issues=({'id':'purchase-approval','description':'Falta definir quem aprova a compra','evidence':old.evidence},))
  f.nora.put_question(material);sent=f.nora.tick();item=f.state().rounds[sent.round_id].items[0]
  self.assertEqual(item.question.id,material.id);self.assertFalse(item.reminder)
  self.assertEqual(item.why_chosen['tier'],'business_consistency');self.assertIn('aprova a compra',item.why_chosen['reason'])
 def test_human_record_or_activity_replaces_no_slug_with_invented_name(self):
  f=self.fixture();q=f.question(activity_summary='Confirmar aprovação da compra',observed_context={'record_ref':'cronograma-obra um301#Financeiro'})
  f.nora.put_question(q);f.nora.tick();text=f.sender.calls[0][1]
  self.assertIn('Confirmar aprovação da compra',text);self.assertNotIn('#',text);self.assertNotIn('cronograma-obra um301',text)
 def test_opaque_tab_reference_in_issue_body_is_not_copied_to_message(self):
  f=self.fixture();q=f.question(gap_kind='business_consistency',issues=({'id':'approval','description':'No cronograma-obra um301#Financeiro falta definir a aprovação da compra.','evidence':f.question().evidence},))
  f.nora.put_question(q);f.nora.tick();text=f.sender.calls[0][1]
  self.assertNotIn('#',text);self.assertNotIn('cronograma-obra um301',text);self.assertIn('aprovação da compra',text)

 def test_founder_text_is_not_cut_with_an_ellipsis(self):
  phrase='Uma frase com palavras completas para verificar o corte.'
  self.assertEqual(display_text('abcdefghijklmnop',5),'abcdefghijklmnop')
  self.assertEqual(display_text(phrase,10),phrase)
  self.assertNotIn('…',display_text(phrase,24))
  self.assertEqual(display_text('curto',20),'curto')
 def test_awaiting_delegate_is_separate_from_founder_queue_without_resolution(self):
  f=self.fixture();q=f.question();disposition={'kind':'delegate','delegate_to':'Ana','source_id':'fixture-founder-delegation'}
  q=replace(q,response_disposition=disposition,disposition_digest=question_digest(q));f.nora.put_question(q)
  status=health(f.nora)
  self.assertEqual(status['awaiting_delegate_questions'],1);self.assertEqual(status['founder_pending_questions'],0)
  self.assertEqual(status['awaiting_delegate'][0]['delegate_to'],'Ana')
  self.assertEqual(f.state().questions[q.id].status.value,'pending');self.assertEqual(f.nora.tick().outcome,'empty')
  f.nora.put_question(replace(q,revision=2,conflict_ids=('new-evidence-conflict',)))
  self.assertEqual(health(f.nora)['awaiting_delegate_questions'],0);self.assertEqual(health(f.nora)['founder_pending_questions'],1)
 def test_canonical_awaiting_person_preserves_code_free_reply_and_codec(self):
  from company_pilot.nora.models import QuestionStatus
  from company_pilot.nora.repository import encode_state,decode_state
  f=self.fixture();q=f.question();f.nora.put_question(q);f.nora.tick()
  q=replace(q,revision=2,status=QuestionStatus.AWAITING_PERSON,response_disposition={'kind':'delegate','delegate_to':'Ana'})
  f.nora.put_question(q);f.clock.value+=timedelta(hours=4)
  self.assertEqual(f.nora.tick().outcome,'empty')
  self.assertEqual(health(f.nora)['pending_questions'],1)
  self.assertEqual(health(f.nora)['founder_pending_questions'],0)
  answer=f.nora.receive(f.event(message_id='delegated-followup',reply_to=None,text='Ana explicou que a revisão evita compra duplicada.'))
  self.assertEqual(answer.question_ids,(q.id,))
  raw=encode_state(f.state(),f.company);restored=decode_state(raw,f.company)
  self.assertEqual(restored.questions[q.id].status,QuestionStatus.AWAITING_PERSON)
  self.assertEqual(encode_state(restored,f.company),raw)

 def test_supported_founder_priority_precedes_other_business_issue(self):
  f=self.fixture();older=f.question(gap_kind='business_consistency');f.nora.put_question(older)
  hint={'reason':{'value':'Precisamos esclarecer quem aprova compra primeiro','basis':'reported','evidence':[{'source_id':'founder-pointer','quote':'tem coisa mais importante: quem aprova compra'}]}}
  target=f.question(2,gap_kind='business_consistency',priority_hint=hint,activity_summary='Confirmar aprovação da compra');f.nora.put_question(target)
  sent=f.nora.tick();item=f.state().rounds[sent.round_id].items[0]
  self.assertEqual(item.question.id,target.id);self.assertEqual(item.why_chosen['tier'],'founder_priority')
  self.assertIn('aprova compra',item.why_chosen['reason']);self.assertIn('founder-pointer',item.why_chosen['source_ids'])
 def test_priority_hint_requires_actual_accepted_quote(self):
  from company_pilot.nora.projection import priority_support
  raw={'priority_hint':{'reason':{'value':'Importante','basis':'reported','evidence':[{'source_id':'unknown','quote':'inexistente'}]}}}
  self.assertIsNone(priority_support(raw,{}))

 def test_distinct_real_questions_have_specific_frozen_reasons(self):
  f=self.fixture();a=f.question(activity_summary='Conferir medição',missing_links=('purpose',));b=f.question(1,activity_summary='Enviar orçamento',missing_links=('handoff',))
  f.nora.put_question(a);f.nora.put_question(b)
  first=f.nora.tick();f.clock.value+=timedelta(hours=4);second=f.nora.tick()
  items=[f.state().rounds[x.round_id].items[0] for x in (first,second)]
  self.assertNotEqual(items[0].why_chosen['reason'],items[1].why_chosen['reason'])
  for item in items:self.assertIn(item.question.activity_summary,item.why_chosen['reason'])

if __name__=='__main__':unittest.main()
