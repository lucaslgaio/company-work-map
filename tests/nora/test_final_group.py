"""Group answers and messages that keep the decisive fact."""
import unittest
from dataclasses import replace
from pathlib import Path

from company_pilot.nora.models import InboundEvent, Route, UnauthorizedInbound
from company_pilot.nora.presentation import compose_message
import test_nora as fixtures


class GroupAndCopyTests(unittest.TestCase):
    def test_two_authorized_responders_are_recorded_and_a_disagreement_is_asked_once(self):
        case = fixtures.NoraTests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        route = replace(case.route, group_name="Clarifying Questions", responders=(
            ("pessoa-b", "pessoa-b", "Bia"),
            ("pessoa-c", "pessoa-c", "Caio"),
        ))
        with case.repo.transaction(case.company) as state:
            state.route = route
        case.nora.put_question(case.question(activity_summary="Aprovar a compra do material"))
        sent = case.nora.tick()
        self.assertEqual(sent.outcome, "accepted")
        with self.assertRaises(UnauthorizedInbound):
            case.nora.receive(InboundEvent(route.account, route.conversation, "operador", "op-1",
                                           case.clock.value, "Eu não respondo."))
        first = case.nora.receive(InboundEvent(route.account, route.conversation, "pessoa-b", "lp-1",
                                               case.clock.value, "A Caio aprova.", "synthetic-channel-1"))
        second = case.nora.receive(InboundEvent(route.account, route.conversation, "pessoa-c", "aq-1",
                                                case.clock.value, "O cliente aprova.", "synthetic-channel-1"))
        self.assertEqual(first.responder_name, "Bia")
        self.assertEqual(second.responder_name, "Caio")
        case.clock.value = fixtures.dt("2026-11-17T14:00:00-03:00")
        follow = case.nora.tick()
        text = case.sender.calls[-1][1]
        self.assertEqual(follow.outcome, "accepted")
        self.assertIn("Bia", text)
        self.assertIn("Caio", text)
        self.assertIn("Caio aprova", text)
        self.assertIn("cliente aprova", text)
        self.assertEqual(text.count("?"), 1)
        self.assertNotIn("…", text)
        with self.assertRaises(UnauthorizedInbound):
            case.nora.receive(InboundEvent(route.account, route.conversation, route.founder_sender, "founder-1",
                                           case.clock.value, "O operador não responde."))
        source = Path(__file__).resolve().parents[2].joinpath("company_pilot/nora/engine.py").read_text(encoding="utf-8")
        self.assertNotIn("Clarifying Questions", source)
        self.assertNotIn("Bia", source)

    def test_the_same_answer_from_either_person_does_not_ask_again(self):
        case = fixtures.NoraTests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        route = replace(case.route, responders=(("pessoa-b", "pessoa-b", "Bia"),
                                                ("pessoa-c", "pessoa-c", "Caio")))
        with case.repo.transaction(case.company) as state:
            state.route = route
        case.nora.put_question(case.question())
        case.nora.tick()
        case.nora.receive(InboundEvent(route.account, route.conversation, "pessoa-b", "lp-2",
                                       case.clock.value, "O Ana aprova até 2 mil.", "synthetic-channel-1"))
        case.nora.receive(InboundEvent(route.account, route.conversation, "pessoa-c", "aq-2",
                                       case.clock.value, "O Ana aprova até 2 mil.", "synthetic-channel-1"))
        self.assertIsNone(case.state().pending_followup)

    def test_decisive_fact_is_not_cut_with_an_ellipsis(self):
        prefix = "Lucas, sobre o cronograma: no registro de 26/11 às 15:00. "
        description = "Os relatos não combinam: os conduítes estavam em 80% e agora está Concluída, conferido Bia 26/11, 100%."
        text = compose_message(prefix * 4, description, "O que deve ser considerado correto nesse caso?", "\nPode responder por aqui.")
        self.assertIn("conferido Bia 26/11, 100%", text)
        self.assertNotIn("…", text)
        self.assertEqual(text.count("?"), 1)
        self.assertIn(description, text)

    def test_cross_apply_waits_when_a_real_question_is_also_open(self):
        case = fixtures.NoraTests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        cross = case.question(activity_summary="Pedir a caçamba", known_facts=({
            "task_id": "other", "field": "purpose",
            "claim": {"value": "Permitir pagamento após liberação escrita", "basis": "observed",
                      "evidence": [{"source_id": "accepted-record", "quote": "Pagamento somente após liberação escrita"}]}},))
        business = case.question(2, gap_kind="business_consistency", issue_ids=("purchase",),
                                 activity_summary="Aprovar a compra",
                                 issues=({"id": "purchase", "description": "Falta definir quem aprova a compra", "evidence": cross.evidence},))
        case.nora.put_question(cross)
        case.nora.put_question(business)
        case.nora.tick()
        text = case.sender.calls[0][1]
        self.assertIn("quem aprova a compra", text.casefold())
        self.assertNotIn("também se aplica", text)


if __name__ == "__main__":
    unittest.main()
