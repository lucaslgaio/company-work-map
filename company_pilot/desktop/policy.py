"""Versioned capture policy. A work computer shares by default.

A capture is company work unless it is clearly personal: the person's own
exclusion phrases, a built-in denylist, a protected field, or an explicit
personal/excluded disposition. A confirmed name adds a linking signal. It
does not decide whether the capture leaves the computer.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import unicodedata
from typing import Any

from .personal import personal_reason


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Decision:
    state: str
    signals: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Policy:
    policy_id: str
    version: int
    expires_at: datetime
    role: str
    screenshot_allowed: bool
    work_markers: tuple[str, ...]
    record_refs: tuple[str, ...]
    tool_hints: tuple[str, ...]
    personal_markers: tuple[str, ...]
    revoked: bool = False
    context_version: int = 1
    capture_allowed: bool = True
    control_reason: str = "legacy_local_fixture"
    participation: dict | None = None
    requires_local_exclusions: bool = False
    local_exclusions_bound: bool = False
    weak_markers: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Policy":
        if not isinstance(raw, dict):
            raise ValueError("invalid_policy")
        if raw.get("schema_version") != 1 or type(raw.get("version")) is not int or raw["version"] < 1:
            raise ValueError("invalid_policy_version")
        role = raw.get("participant_kind")
        if role not in {"founder", "team"} or not raw.get("policy_id"):
            raise ValueError("invalid_policy_identity")
        context = raw.get("context", {})
        if not isinstance(context, dict) or not isinstance(raw.get("screenshots", {}), dict):
            raise ValueError("invalid_policy_context")

        def terms(name: str) -> tuple[str, ...]:
            values = context.get(name, [])
            if not isinstance(values, list) or len(values) > 500:
                raise ValueError("invalid_context_terms")
            if any(not isinstance(v, str) or not 3 <= len(v) <= 500 for v in values):
                raise ValueError("invalid_context_term")
            return tuple(values)

        return cls(str(raw["policy_id"]), raw["version"], instant(raw["expires_at"]), role,
                   raw.get("screenshots", {}).get(role) is True,
                   terms("work_markers"), terms("record_refs"), terms("tool_hints"),
                   terms("personal_markers"), raw.get("revoked") is not False,
                   int(raw.get("context_version", raw["version"])),
                   raw.get("capture_allowed", True) is True,
                   str(raw.get("control_reason", "legacy_local_fixture")),
                   raw.get("participation"), raw.get("requires_local_exclusions") is True,
                   False, terms("weak_markers"))

    @classmethod
    def from_brain(cls, raw):
        role = raw["role"]
        controls = raw.get("controls", {})
        participation = controls.get("participation") or {}
        markers = raw.get("recognition_markers")
        # The brain validates grounding; do not turn a descriptive sentence into a matcher.
        work = ([m["value"] for m in markers if isinstance(m, dict) and m.get("operation", "add") == "add"
                 and m.get("kind") in {"record_name", "record_ref", "company_alias", "business_account"}
                 and m.get("status", "active") == "active" and isinstance(m.get("value"), str) and m.get("evidence")]
                if isinstance(markers, list) else raw.get("work_signals", []))
        weak = [m["value"] for m in (markers or []) if isinstance(m, dict) and isinstance(m.get("value"), str)
                and m.get("kind") in {"record_name", "business_account"} and m["value"] in work
                and len(_normalized(m["value"]).split()) == 1 and _normalized(m["value"]).isalpha()]
        return cls.parse({"schema_version": 1, "policy_id": raw["company_id"], "version": raw["policy_version"],
                          "context_version": raw["context_version"], "expires_at": raw["expires_at"],
                          "participant_kind": role, "revoked": False,
                          "capture_allowed": controls.get("capture_allowed") is True,
                          "control_reason": controls.get("reason", "participation_or_access_pending"),
                          "participation": participation, "requires_local_exclusions": True,
                          "screenshots": {role: raw.get("screenshots_allowed") is True and raw.get("screenshots", {}).get(role) is True
                                          and participation.get("screenshots") is True},
                          "context": {"work_markers": [s[:500] for s in work if isinstance(s, str) and len(s) >= 3],
                                      "weak_markers": weak,
                                      "record_refs": raw.get("record_refs", []), "tool_hints": raw.get("tool_hints", []),
                                      "personal_markers": raw.get("personal_exclusions", [])}})

    def valid(self, now: datetime) -> bool:
        return (self.current(now) and self.capture_allowed
                and (not self.requires_local_exclusions or self.local_exclusions_bound))

    def current(self, now: datetime) -> bool:
        return not self.revoked and now < self.expires_at

    def as_mapping(self):
        return {"schema_version": 1, "policy_id": self.policy_id, "version": self.version,
                "context_version": self.context_version, "expires_at": self.expires_at.isoformat(),
                "participant_kind": self.role, "revoked": self.revoked, "screenshots": {self.role: self.screenshot_allowed},
                "capture_allowed": self.capture_allowed, "control_reason": self.control_reason,
                "participation": self.participation, "requires_local_exclusions": self.requires_local_exclusions,
                "context": {"work_markers": list(self.work_markers), "record_refs": list(self.record_refs),
                            "tool_hints": list(self.tool_hints), "personal_markers": list(self.personal_markers), "weak_markers": list(self.weak_markers)}}

    def permits_image(self, now: datetime) -> bool:
        return self.valid(now) and self.screenshot_allowed

    def classify(self, surface: dict[str, Any], now: datetime) -> Decision:
        if not self.valid(now):
            return Decision("excluded", (), "policy_unavailable")
        if surface.get("secure_context") or surface.get("origin") == "company-pilot-semantic-worker":
            return Decision("excluded", (), "protected_or_internal_surface")
        disposition = surface.get("privacy_disposition")
        if disposition is not None and (not isinstance(disposition, str) or disposition not in {"", "personal", "excluded", "withheld"}):
            return Decision("excluded", (), "invalid_privacy_disposition")
        if disposition in {"personal", "excluded"}:
            return Decision("excluded", (), "participant_private_context")
        # Do not inspect body text to decide whether unknown private content is work.
        title = str(surface.get("title", ""))
        ref = str(surface.get("record_ref", ""))
        metadata = title + "\n" + ref
        if any(_contains(metadata, marker) for marker in self.personal_markers):
            return Decision("excluded", (), "personal_context")
        blocked = personal_reason(surface)
        if blocked:
            return Decision("excluded", (), blocked)
        if disposition == "withheld":
            return Decision("uncertain", (), "participant_context_withheld")
        weak = {_normalized(m) for m in self.weak_markers}
        matches = tuple("work_marker:" + m for m in self.work_markers if _normalized(m) not in weak and _contains(metadata, m))
        refs = tuple("record_ref:" + r for r in self.record_refs if ref == r or ref.startswith(r.rstrip("/") + "/") or ref.startswith(r + "#") or ref.startswith(r + "?"))
        partial = tuple("ambiguous_marker:" + m for m in self.weak_markers if _contains(metadata, m))
        app = str(surface.get("app_id", ""))
        tools = tuple("tool_hint:" + tool for tool in self.tool_hints if _contains(app, tool))
        signals = refs + matches + partial + tools
        if matches or refs:
            return Decision("company", signals, "learned_company_context")
        return Decision("company", signals, "work_computer_default")


def _contains(value: str, term: str) -> bool:
    # Compare complete normalized phrases, never a shared token or arbitrary
    # person/project prefix. Source-backed aliases remain explicit markers.
    needle = _normalized(term)
    return bool(needle and " " + needle + " " in " " + _normalized(value) + " ")


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in letters).split())
