"""Participant-owned local terms. Only their digest/choices may enter the company brain."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from .config import replace_config
from .outbox import canonical

DEFAULT_EXCLUSIONS = ("cartão pessoal", "conta pessoal", "e-mail pessoal", "conteúdo pessoal", "uso pessoal", "perfil pessoal")


def configure_private_policy(config_path, config, choices, policy_version):
    if not isinstance(choices, dict) or any(type(choices.get(k)) is not bool for k in ("participating", "screenshots", "reviewed_defaults")):
        raise ValueError("private_choices_required")
    if not choices["reviewed_defaults"]:
        raise ValueError("private_defaults_review_required")
    terms = choices.get("personal_exclusions", [])
    if not isinstance(terms, list) or len(terms) > 100 or any(not isinstance(t, str) or not 3 <= len(t.strip()) <= 200 for t in terms):
        raise ValueError("invalid_private_exclusions")
    local = {"schema_version": 1, "policy_version": policy_version, "participating": choices["participating"],
             "screenshots": choices["screenshots"], "reviewed_defaults": True,
             "personal_exclusions": list(dict.fromkeys([*DEFAULT_EXCLUSIONS, *(t.strip() for t in terms)]))}
    digest = sha256(canonical(local)).hexdigest()
    local_path = Path(config_path).resolve().parent / "personal-policy.json"
    choices_path = local_path.with_name("participation-choices.json")
    receipt = {"participating": local["participating"], "screenshots": local["screenshots"],
               "acknowledged_policy_version": policy_version, "local_exclusions_configured": True, "local_policy_sha256": digest}
    replace_config(local_path, local)
    replace_config(choices_path, receipt)
    config["private_policy_file"] = str(local_path)
    replace_config(Path(config_path), config)
    return {"status": "local_choices_saved_pending_company_ack", "private_policy_file": str(local_path),
            "participation_choices_file": str(choices_path), "local_policy_sha256": digest,
            "effective_local_exclusions": local["personal_exclusions"], "participation": receipt,
            "message": "Confirme estas escolhas na política da própria conta com participation-choices.json. Os termos ficam apenas neste computador. Uma janela pessoal sem estes sinais ainda pode ficar sem identificação; revise seus contextos pessoais."}


def bind_private_policy(policy, config):
    if not policy.requires_local_exclusions:
        return policy  # explicit local development fixture; never real consent evidence
    try:
        raw = json.loads(Path(config["private_policy_file"]).read_text(encoding="utf-8"))
        ack = policy.participation or {}
        terms = raw["personal_exclusions"]
        if (raw.get("schema_version") != 1 or raw.get("reviewed_defaults") is not True
                or type(raw.get("participating")) is not bool or type(raw.get("screenshots")) is not bool
                or not isinstance(terms, list) or any(not isinstance(t, str) or not 3 <= len(t) <= 200 for t in terms)
                or raw.get("policy_version") != policy.version
                or ack.get("acknowledged_policy_version") != policy.version
                or ack.get("local_exclusions_configured") is not True
                or ack.get("local_policy_sha256") != sha256(canonical(raw)).hexdigest()
                or ack.get("participating") != raw["participating"] or ack.get("screenshots") != raw["screenshots"]):
            raise ValueError("private_policy_ack_mismatch")
        return replace(policy, local_exclusions_bound=True, capture_allowed=policy.capture_allowed and raw["participating"],
                       control_reason=policy.control_reason if raw["participating"] else "participant_declined",
                       screenshot_allowed=policy.screenshot_allowed and raw["screenshots"],
                       personal_markers=tuple(dict.fromkeys([*policy.personal_markers, *terms])))
    except (ValueError, KeyError, OSError, TypeError):
        return replace(policy, capture_allowed=False, screenshot_allowed=False, control_reason="local_choices_missing_or_changed")


def control_status(policy, now):
    return {"capture_allowed": policy.valid(now), "reason": policy.control_reason,
            "policy_version": policy.version, "screenshots_allowed": policy.permits_image(now),
            "local_exclusions_required": policy.requires_local_exclusions,
            "local_exclusions_bound": policy.local_exclusions_bound,
            "exclusion_count": len(policy.personal_markers),
            "compatibility": "brain_controls" if policy.requires_local_exclusions else "local_development_fixture_not_real_consent"}
