"""Synthetic design probe; not the pilot implementation or a channel test."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from zoneinfo import ZoneInfo


BRASILIA = ZoneInfo("America/Sao_Paulo")
HOURS = (6, 10, 14, 18, 22)


def next_slot(after: datetime) -> datetime:
    if after.tzinfo is None:
        raise ValueError("Clock must be timezone aware")
    local = after.astimezone(BRASILIA)
    for day_offset in (0, 1):
        day = local.date() + timedelta(days=day_offset)
        for hour in HOURS:
            slot = datetime(day.year, day.month, day.day, hour, tzinfo=BRASILIA)
            if slot >= local:
                return slot.astimezone(timezone.utc)
    raise AssertionError("No slot found")


def due_slot(now: datetime) -> str | None:
    if now.tzinfo is None:
        raise ValueError("Clock must be timezone aware")
    local = now.astimezone(BRASILIA)
    if local.hour in HOURS and local.minute < 5:
        return local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    return None


def semantic_digest(gap: dict) -> str:
    # No mutable timestamp/model-generated prose: these do not justify a resend.
    meaningful = {key: gap[key] for key in ("missing", "revisit_evidence", "answers")}
    return sha256(json.dumps(meaningful, sort_keys=True).encode()).hexdigest()


def run() -> None:
    cases = {
        "2026-11-16T23:59:59-03:00": "2026-11-17T09:00:00+00:00",
        "2026-11-17T00:00:00-03:00": "2026-11-17T09:00:00+00:00",
        "2026-11-17T05:59:59-03:00": "2026-11-17T09:00:00+00:00",
        "2026-11-17T06:00:00-03:00": "2026-11-17T09:00:00+00:00",
        "2026-11-17T06:00:01-03:00": "2026-11-17T13:00:00+00:00",
        "2026-11-17T22:00:00-03:00": "2026-11-18T01:00:00+00:00",
        "2026-11-17T22:00:01-03:00": "2026-11-18T09:00:00+00:00",
        # Same instant in another zone must select the same slot.
        "2026-11-17T01:00:00-08:00": "2026-11-17T09:00:00+00:00",
    }
    for raw, expected in cases.items():
        assert next_slot(datetime.fromisoformat(raw)).isoformat() == expected, raw

    # Every minute across two days: quiet hours and missed-slot catch-up are forbidden.
    start = datetime.fromisoformat("2026-11-16T00:00:00-03:00")
    due = []
    for minute in range(48 * 60):
        now = start + timedelta(minutes=minute)
        slot = due_slot(now)
        if slot:
            local = now.astimezone(BRASILIA)
            assert local.hour >= 6 and local.minute < 5
            due.append(slot)
    assert len(set(due)) == 10
    assert due_slot(datetime.fromisoformat("2026-11-17T10:05:00-03:00")) is None
    assert due_slot(datetime.fromisoformat("2026-11-17T15:10:00-03:00")) is None

    gap = {"missing": ["purpose"], "evidence": ["synthetic-observation-1"], "revisit_evidence": [], "answers": []}
    last_sent = semantic_digest(gap)
    gap["updated_at"] = "later"
    gap["prompt"] = "Reworded unchanged question"
    assert semantic_digest(gap) == last_sent
    pending = {"status": "pending", "last_sent_digest": last_sent}
    assert pending["status"] == "pending"  # Silence never resolves a question.
    gap["evidence"].append("synthetic-observation-2")
    assert semantic_digest(gap) == last_sent  # Recurrence alone cannot justify a repeat.
    gap["revisit_evidence"].append("synthetic-relevant-new-context")
    assert semantic_digest(gap) != last_sent  # New context permits useful revisit.
    assert next_slot(datetime.fromisoformat("2026-11-17T22:01:00-03:00")).hour == 9
    print(json.dumps({
        "evidence_level": "E1_synthetic_design_probe",
        "next_slot_cases": len(cases),
        "minute_checks": 48 * 60,
        "unique_due_slots": len(set(due)),
        "unchanged_pending_suppressed": True,
        "recurring_observation_suppressed": True,
        "relevant_new_context_changes_digest": True,
        "network_or_real_company_data_used": False,
        "not_proved": ["durable_transactions", "answer_interpretation", "remote_brain", "real_Nora_delivery"],
    }, indent=2))


if __name__ == "__main__":
    run()
