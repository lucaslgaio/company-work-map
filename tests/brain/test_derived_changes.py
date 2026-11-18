"""P34 reviewer regressions: implicit writes and intentional disconnection."""
import copy
import unittest

from company_pilot.inference import SemanticWorker
import test_structural_answers as fixtures
from test_structural_answers import FixtureInterpreter, claim, evidence, expected, result, uid


class DerivedRelationTests(unittest.TestCase):
    setUp = fixtures.StructuralAnswerTests.setUp
    answer = fixtures.StructuralAnswerTests.answer

    def edge(self):
        return next(e for e in self.brain.snapshot()["edges"] if e.get("derived_field") == "performer")

    def test_task_rename_keeps_concurrent_shared_person_rename(self):
        person = self.brain.snapshot()["nodes"][0]
        def rename(output, job, context):
            self.brain.change_map([{"op": "rename", "id": person["id"], "name": "Joana Souza"}], uid(), "Nome completo confirmado.")
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            return result([{"op": "rename", "id": self.task["id"], "name": "Conferir transferência", "evidence": evidence(source),
                            "expected_versions": expected(context, [self.task["id"]])}])
        self.answer()
        self.assertEqual(SemanticWorker(self.brain, FixtureInterpreter(rename)).run_once()["status"], "applied")
        current = next(n for n in self.brain.snapshot()["nodes"] if n["id"] == person["id"])
        self.assertEqual(current["name"], "Joana Souza")
        self.assertEqual(current["version"], person["version"] + 1)

    def test_new_relation_checks_existing_target_node_version(self):
        person = self.brain.snapshot()["nodes"][0]
        self.answer()
        edge = self.edge()
        def reassign(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            versions = expected(context, [self.task["id"], person["id"], edge["id"]])
            self.brain.change_map([{"op": "rename", "id": person["id"], "name": "Joana Souza"}], uid(), "Nome completo confirmado.")
            return result([{"op": "reassign", "id": self.task["id"], "performer": claim(source, "Joana"),
                            "evidence": evidence(source), "expected_versions": versions}])
        response = SemanticWorker(self.brain, FixtureInterpreter(reassign)).run_once()
        self.assertEqual(response["status"], "pending")
        current = next(n for n in self.brain.snapshot()["nodes"] if n["id"] == person["id"])
        self.assertEqual(current["name"], "Joana Souza")
        self.assertEqual(self.brain.snapshot()["tasks"][0], self.task)

    def disconnect(self):
        edge = self.edge()
        self.answer()
        def operation(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            return result([{"op": "disconnect", "id": edge["id"], "evidence": evidence(source), "expected_versions": expected(context, [edge["id"]])}])
        self.assertEqual(SemanticWorker(self.brain, FixtureInterpreter(operation)).run_once()["status"], "applied")
        self.assertFalse(self.edge()["active"])

    def ordinary(self, changes):
        self.answer()
        def update(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            task = next(t for t in context["tasks"] if t["id"] == self.task["id"])
            fields = copy.deepcopy(task["fields"])
            for field, value in changes.items():
                fields[field] = claim(source, value)
            normal = result()
            normal["tasks"] = [{"task_id": task["id"], "name": task["name"], "fields": fields, "links": []}]
            return normal
        return SemanticWorker(self.brain, FixtureInterpreter(update)).run_once()

    def test_disconnect_survives_unrelated_answer_then_new_relation_evidence_reconnects(self):
        self.disconnect()
        removed = self.edge()
        self.assertEqual(self.ordinary({"purpose": "Quitar serviço aprovado"})["status"], "applied")
        self.assertEqual(self.edge(), removed)
        self.assertEqual(self.ordinary({"performer": "Joana"})["status"], "applied")
        self.assertTrue(self.edge()["active"])
        self.assertGreater(self.edge()["version"], removed["version"])

    def test_explicit_connect_can_restore_a_disconnected_relation(self):
        self.disconnect()
        edge = self.edge()
        self.answer()
        def reconnect(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            return result([{"op": "connect", "from": edge["from"], "to": edge["to"], "kind": edge["kind"],
                            "evidence": evidence(source), "expected_versions": expected(context, [edge["from"], edge["to"], edge["id"]])}])
        self.assertEqual(SemanticWorker(self.brain, FixtureInterpreter(reconnect)).run_once()["status"], "applied")
        restored = next(e for e in self.brain.snapshot()["edges"] if e["id"] == edge["id"])
        self.assertTrue(restored["active"])

    def test_queued_older_relation_evidence_cannot_undo_later_disconnect(self):
        self.answer()
        edge = self.edge()
        self.brain.change_map([{"op": "disconnect", "id": edge["id"]}], uid(), "Correção posterior: relação ainda não confirmada.")
        removed = self.edge()
        def older(output, job, context):
            source = next(s for s in context["sources"] if s["id"] == job["source_ids"][0])
            fields = copy.deepcopy(self.task["fields"])
            fields["performer"] = claim(source, "Joana")
            normal = result()
            normal["tasks"] = [{"task_id": self.task["id"], "name": self.task["name"], "fields": fields, "links": []}]
            return normal
        self.assertEqual(SemanticWorker(self.brain, FixtureInterpreter(older)).run_once()["status"], "applied")
        self.assertEqual(self.edge(), removed)


if __name__ == "__main__":
    unittest.main()
