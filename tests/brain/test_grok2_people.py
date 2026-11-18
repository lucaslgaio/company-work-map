"""E12/G2 report-derived people fixture; not an original-run replay."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from company_pilot.brain import Brain, FIELDS, LocalStore
from company_pilot.brain.people import person_identity


def uid():
    return str(uuid4())


def claim(source, value, basis="reported"):
    return {"value": value, "basis": basis, "evidence": [{"source_id": source["id"], "quote": source["text"]}]}


def report_fixture(root):
    brain = Brain(LocalStore(root))
    brain.initialize("Empresa sintética E12 — fixture derivada do relatório", synthetic=True)
    known = brain.interview_answer("Lia Costa coordena a empresa. Pedro Nunes faz a conferência técnica. Camila Souza atua no PMO.", uid())
    source = next(s for s in brain.snapshot()["sources"] if s["id"] == known["source_id"])
    brain.apply_analysis(known["job_id"], {"tasks": [], "context_updates": [], "next_question": None,
        "nodes": [{"kind": "person", "name": name, "evidence": [{"source_id": source["id"], "quote": name}]}
                  for name in ("Lia Costa", "Pedro Nunes", "Camila Souza")]})
    raw_values = ("Lia", "Lia Costa", "Pedro Nunes", "Pedro Nunes (confirma o status na vistoria)")
    for index, performer in enumerate(raw_values):
        text = performer + " executa a tarefa sintética " + str(index) + "."
        observed = brain.ingest_observation({"schema_version": 1, "event_id": uid(), "device_id": uid(),
            "occurred_at": "2026-11-23T12:00:00+00:00", "policy_version": brain.snapshot()["company"]["policy_version"],
            "source": {"kind": "active_window", "app": "Synthetic E12"},
            "classification": {"label": "company", "reason": "fixture derivada do relatório E12"},
            "content": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}, "synthetic": True})
        source = next(s for s in brain.snapshot()["sources"] if s["id"] == observed["observation_id"])
        fields = {field: {"value": None, "basis": "inferred", "evidence": []} for field in FIELDS}
        fields["performer"] = claim(source, performer, "observed")
        fields["action"] = claim(source, "Executar tarefa sintética " + str(index), "observed")
        brain.apply_analysis(observed["job_id"], {"tasks": [{"task_id": None, "name": "Tarefa sintética " + str(index), "fields": fields, "links": []}],
            "context_updates": [], "nodes": [], "next_question": None})
    return brain, raw_values


class ActualPeopleFixtureTests(unittest.TestCase):
    def test_known_people_are_not_duplicated_by_alias_or_qualifier(self):
        with tempfile.TemporaryDirectory() as root:
            brain, raw_values = report_fixture(root)
            snapshot = brain.snapshot()
            people = [node for node in snapshot["nodes"] if node["kind"] == "person" and not node.get("superseded_by")]
            print(json.dumps({"fixture": "report-derived E12/G2, not replay", "people": [{"id": n["id"], "name": n["name"]} for n in people],
                "raw_performer_values": [task["fields"]["performer"]["value"] for task in snapshot["tasks"]]}, ensure_ascii=False))
            self.assertEqual(sorted(node["name"] for node in people), sorted(("Lia Costa", "Pedro Nunes", "Camila Souza")))
            self.assertEqual(sorted(task["fields"]["performer"]["value"] for task in snapshot["tasks"]), sorted(raw_values))
            qualified = next(task for task in snapshot["tasks"] if task["fields"]["performer"]["value"].startswith("Pedro Nunes ("))
            self.assertEqual(qualified["performer_identity"]["qualifier"], "confirma o status na vistoria")
            self.assertEqual(qualified["performer_identity"]["name"], "Pedro Nunes")
            for task in snapshot["tasks"]:
                for ref in task["fields"]["performer"]["evidence"]:
                    source = next(source for source in snapshot["sources"] if source["id"] == ref["source_id"])
                    self.assertIn(ref["quote"], source["text"])


def person(name, node_id, **extra):
    return {"id": node_id, "kind": "person", "name": name, "superseded_by": [],
            "evidence": [{"source_id": "known-source", "quote": name}], **extra}


class PersonIdentityTests(unittest.TestCase):
    company = "report-derived-company"

    def resolve(self, value, **inputs):
        return person_identity(value, company_id=self.company, **inputs)

    def test_full_name_first_alias_and_qualifier_reuse_known_ids(self):
        nodes = [person("Lia Costa", "lia-id"), person("Pedro Nunes", "pedro-id"), person("Camila Souza", "camila-id")]
        retained = copy.deepcopy(nodes)
        for alias in ("Lia", "Lia Costa", "lia costa", "LIA"):
            resolved = self.resolve(alias, nodes=nodes)
            self.assertEqual((resolved["status"], resolved["name"], resolved["node_id"]), ("known", "Lia Costa", "lia-id"))
            self.assertEqual(resolved["raw_value"], alias)
        resolved = self.resolve("Pedro Nunes (confirma o status na vistoria)", nodes=nodes)
        self.assertEqual((resolved["node_id"], resolved["qualifier"]), ("pedro-id", "confirma o status na vistoria"))
        resolved["evidence"][0]["quote"] = "modified output only"
        self.assertEqual(nodes, retained)

    def test_homonyms_do_not_merge_or_choose_a_recent_candidate(self):
        nodes = [person("Lia Costa", "costa-id"), person("Lia Alves", "alves-id")]
        result = self.resolve("Lia", nodes=nodes)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["node_id"])
        self.assertEqual(result["candidate_ids"], ["alves-id", "costa-id"])
        self.assertEqual(self.resolve("Lia Alves", nodes=nodes)["node_id"], "alves-id")
        actors = [{"actor_id": "actor-1", "name": "Ana Silva"}, {"actor_id": "actor-2", "name": "Ana Silva"}]
        result = self.resolve("Ana Silva", actor_names=actors)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidate_ids"]), 2)

    def test_compound_couple_is_explicitly_unresolved_without_name_expansion(self):
        raw = "Camila e Marcos Prado (clientes)"
        result = self.resolve(raw, nodes=[person(raw, "bad-compound-id")])
        self.assertEqual(result["status"], "compound")
        self.assertEqual(result["raw_value"], raw)
        self.assertEqual(result["qualifier"], "clientes")
        self.assertIsNone(result["node_id"])
        self.assertIsNone(result["name"])
        self.assertEqual(result["candidate_ids"], [])
        people = [{"topic": "people", "status": "active", "claim": {"value": "Camila e Marcos Prado", "basis": "reported",
            "evidence": [{"source_id": "couple-source", "quote": "Camila e Marcos Prado"}]}}]
        # Both literal names may be known; Camila's surname is never invented.
        self.assertEqual(self.resolve("Camila", context_claims=people)["name"], "Camila")
        self.assertEqual(self.resolve("Camila Prado", context_claims=people)["status"], "new")

    def test_unknown_person_is_new_but_roles_and_login_ids_are_not_people(self):
        result = self.resolve("Valdir (empreiteiro, externo)")
        self.assertEqual((result["status"], result["name"], result["qualifier"]), ("new", "Valdir", "empreiteiro, externo"))
        for value in (None, "", "equipe técnica", "Equipe técnica", "ninguém", "Eu"):
            self.assertEqual(self.resolve(value)["status"], "unresolved")
        result = self.resolve("Lia", actor_names=[{"actor_id": "lia-costa-sim"}])
        self.assertEqual(result["status"], "new")
        self.assertEqual(result["name"], "Lia")

    def test_context_names_require_specific_source_support_not_summary_or_shared_word(self):
        claims = [{"topic": "people", "status": "active", "claim": {"value": "Lia Costa, Pedro Nunes e Camila Souza", "basis": "reported",
            "evidence": [{"source_id": "interview", "quote": "Lia Costa, Pedro Nunes e Camila Souza"}]}}]
        self.assertEqual(self.resolve("Lia", context_claims=claims)["name"], "Lia Costa")
        claims[0]["claim"]["evidence"][0]["quote"] = "Lia coordena a equipe"
        self.assertEqual(self.resolve("Lia", context_claims=claims)["name"], "Lia")
        self.assertEqual(self.resolve("Lia", context_claims=claims)["status"], "new")
        nodes = [person("Camila Prado", "camila"), person("Marcos Prado", "marcos")]
        self.assertEqual(self.resolve("Prado", nodes=nodes)["status"], "new")
        self.assertNotIn(self.resolve("Prado", nodes=nodes)["node_id"], ("camila", "marcos"))

    def test_old_duplicate_ids_are_advisory_aliases_without_mutation(self):
        nodes = [person("Lia Costa", "canonical-lia"), person("Lia", "old-short"),
                 person("Pedro Nunes", "canonical-pedro"), person("Pedro Nunes (confirma o status)", "old-qualified")]
        retained = copy.deepcopy(nodes)
        self.assertEqual(self.resolve("Lia", nodes=nodes)["alias_node_ids"], ["old-short"])
        result = self.resolve("Pedro Nunes", nodes=nodes)
        self.assertEqual(result["node_id"], "canonical-pedro")
        self.assertEqual(result["alias_node_ids"], ["old-qualified"])
        self.assertEqual(nodes, retained)


if __name__ == "__main__":
    unittest.main()
