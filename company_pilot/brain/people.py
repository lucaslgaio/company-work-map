"""Pure person identity resolution for E12/G2.

Names come from existing people, grounded people claims or explicitly declared
actor display names. Login IDs and shared surname tokens are never expansions.
The original performer claim remains the caller's immutable evidence.
"""
from __future__ import annotations

import copy
import re
import unicodedata
import uuid


_PARTICLES = {"da", "das", "de", "do", "dos", "del", "di", "du", "van", "von"}
_ROLES = {"equipe", "time", "cliente", "clientes", "fornecedor", "fornecedores", "responsável", "responsavel",
          "fundador", "fundadora", "ninguém", "ninguem", "nenhum", "nenhuma", "desconhecido", "desconhecida",
          "pessoa", "pessoas", "colaborador", "colaboradores", "operador", "eu", "ele", "ela", "nós", "nos"}


def _key(value):
    folded = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W\d_]+", folded, re.UNICODE))


def _identifier(company_id, value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(company_id) + ":" + value))


def _parts(value):
    value = value.strip()
    match = re.fullmatch(r"([^()]*)\s*\(([^()]*)\)\s*", value)
    if match:
        return match[1].strip(), match[2].strip() or None
    return value, None


def _compound(name):
    return bool(re.search(r"\s+e\s+|[,;&/]", name, re.IGNORECASE))


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or _compound(value) or any(c in value for c in "()[]{}0123456789"):
        return False
    words = value.split()
    if not words or len(words) > 8 or _key(words[0]) in _ROLES or _key(value) in _ROLES:
        return False
    if any(not re.fullmatch(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*\.?", word, re.UNICODE) for word in words):
        return False
    if len(words) == 1:
        return True
    return all(word.casefold().rstrip(".") in _PARTICLES or word[0].isupper() for word in words)


def _refs(values):
    result = []
    for ref in values or []:
        if isinstance(ref, dict) and isinstance(ref.get("source_id"), str) and isinstance(ref.get("quote"), str) and ref not in result:
            result.append(copy.deepcopy(ref))
    return result


def _listed(value):
    """Only explicit name lists, never names guessed from a prose description."""
    if not isinstance(value, str):
        return []
    pieces, current, depth = [], [], 0
    for character in value:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character in ",;" and depth == 0:
            pieces.append("".join(current))
            current = []
        else:
            current.append(character)
    pieces.append("".join(current))
    names = []
    for piece in pieces:
        base, _ = _parts(piece)
        for member in re.split(r"\s+e\s+", base, flags=re.IGNORECASE):
            if not _name(member.strip()):
                return []
            names.append(member.strip())
    return names


def _catalog(company_id, nodes, context_claims, actor_names):
    entries = []
    for node in nodes or []:
        if not isinstance(node, dict) or node.get("kind") != "person" or node.get("superseded_by") or not isinstance(node.get("name"), str):
            continue
        name, qualifier = _parts(node["name"])
        if not _name(name) or not isinstance(node.get("id"), str):
            continue
        anchor = node.get("actor_id")
        entries.append({"name": name, "raw_name": node["name"], "node_id": node["id"], "qualifier": qualifier,
                        "anchor": anchor if isinstance(anchor, str) and anchor else None,
                        "evidence": _refs(node.get("evidence")), "origin": "node"})
    if isinstance(context_claims, dict):
        context_claims = [{"topic": "people", "claim": context_claims["people"]}] if "people" in context_claims else [context_claims]
    for record in context_claims or []:
        if not isinstance(record, dict) or record.get("topic") != "people" or record.get("status", "active") != "active":
            continue
        claim = record.get("claim", record)
        if not isinstance(claim, dict):
            continue
        if claim.get("basis") == "inferred":
            continue
        for name in _listed(claim.get("value")):
            evidence = [ref for ref in _refs(claim.get("evidence")) if (" " + _key(name) + " ") in (" " + _key(ref["quote"]) + " ")]
            if evidence:
                entries.append({"name": name, "raw_name": name, "node_id": _identifier(company_id, "person:" + name.casefold()),
                                "qualifier": None, "anchor": None, "evidence": evidence, "origin": "context"})
    actors = []
    for actor in actor_names or []:
        actor = {"name": actor} if isinstance(actor, str) else actor
        if not isinstance(actor, dict):
            continue
        name = actor.get("name", actor.get("display_name"))
        if isinstance(name, str) and _name(name):
            anchor = actor.get("actor_id")
            actors.append({**actor, "name": name, "actor_id": anchor if isinstance(anchor, str) and anchor else None})
    for actor in actors:
        name, anchor = actor["name"], actor.get("actor_id")
        anchors = {item.get("actor_id") for item in actors if _key(item["name"]) == _key(name) and item.get("actor_id")}
        existing = [entry for entry in entries if _key(entry["name"]) == _key(name)]
        node_id = actor.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            node_id = (existing[0]["node_id"] if len({item["node_id"] for item in existing}) == 1 and len(anchors) <= 1
                       else _identifier(company_id, "person-actor:" + anchor) if anchor and len(anchors) > 1
                       else _identifier(company_id, "person:" + name.casefold()))
        entries.append({"name": name, "raw_name": name, "node_id": node_id, "qualifier": None,
                        "anchor": anchor, "evidence": _refs(actor.get("evidence")), "origin": "actor"})
    groups = {}
    for entry in entries:
        groups.setdefault(_key(entry["name"]), []).append(entry)
    people = []
    for same_name in groups.values():
        anchors = {entry["anchor"] for entry in same_name if entry["anchor"]}
        partitions = [[entry for entry in same_name if entry["anchor"] == anchor] for anchor in sorted(anchors)] if len(anchors) > 1 else [same_name]
        for partition in partitions:
            chosen = min(partition, key=lambda entry: (entry["qualifier"] is not None, entry["origin"] != "node", entry["node_id"]))
            people.append({**chosen, "aliases": sorted({entry["raw_name"] for entry in partition} | {entry["name"] for entry in partition}),
                           "alias_node_ids": sorted({entry["node_id"] for entry in partition} - {chosen["node_id"]}),
                           "evidence": _refs([ref for entry in partition for ref in entry["evidence"]])})
    # A previously derived short/qualified duplicate can be an alias of one full
    # identity. Explicitly anchored homonyms prevent this automatic association.
    retired = set()
    for person in people:
        if len(_key(person["name"]).split()) != 1 or person["anchor"]:
            continue
        first = _key(person["name"])
        alternatives = [entry for entry in people if entry is not person and _key(entry["name"]).split()[0] == first]
        if len(alternatives) == 1 and len(_key(alternatives[0]["name"]).split()) > 1:
            canonical = alternatives[0]
            canonical["aliases"] = sorted(set(canonical["aliases"] + person["aliases"]))
            canonical["alias_node_ids"] = sorted(set(canonical["alias_node_ids"] + person["alias_node_ids"] + [person["node_id"]]))
            canonical["evidence"] = _refs(canonical["evidence"] + person["evidence"])
            retired.add(id(person))
    return [person for person in people if id(person) not in retired]


def person_identity(value, *, company_id, nodes=(), context_claims=(), actor_names=()):
    """Resolve exact full names or one unambiguous first-name alias.

    Qualifiers belong on task metadata. The caller must preserve the original
    claim and cite its own evidence when creating a derived relationship.
    """
    result = {"status": "unresolved", "raw_value": value, "name": None, "node_id": None,
              "qualifier": None, "candidate_ids": [], "alias_node_ids": [], "evidence": []}
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return result
    name, qualifier = _parts(value)
    result["qualifier"] = qualifier
    if _compound(name):
        result["status"] = "compound"
        return result
    key = _key(name)
    people = _catalog(company_id, nodes, context_claims, actor_names)
    matches = ([person for person in people if _key(person["name"]).split()[0] == key] if len(key.split()) == 1
               else [person for person in people if _key(person["name"]) == key])
    if len(matches) > 1:
        result.update(status="ambiguous", candidate_ids=sorted({person["node_id"] for person in matches}))
    elif matches:
        person = matches[0]
        result.update(status="known", name=person["name"], node_id=person["node_id"], candidate_ids=[person["node_id"]],
                      alias_node_ids=person["alias_node_ids"], evidence=copy.deepcopy(person["evidence"]))
    elif _name(name):
        result.update(status="new", name=name, node_id=_identifier(company_id, "person:" + name.casefold()))
    return result
