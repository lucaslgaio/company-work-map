"""Scope v2: three proactive opportunities at 10/14/18 Brasília, no catch-up."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import aware


@dataclass(frozen=True)
class Schedule:
    timezone_name: str = "America/Sao_Paulo"
    hours: tuple[int, ...] = (10, 14, 18)
    grace: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone_name)
        if self.timezone_name != "America/Sao_Paulo" or self.hours != (10, 14, 18):
            raise ValueError("This pilot uses the agreed Brasília schedule")
        if not timedelta(0) < self.grace <= timedelta(minutes=5):
            raise ValueError("Grace must be positive and at most five minutes")

    def next_at(self, now: datetime, *, strictly_after: bool = False) -> datetime:
        local = aware(now).astimezone(ZoneInfo(self.timezone_name))
        for offset in (0, 1):
            day = local.date() + timedelta(days=offset)
            for hour in self.hours:
                slot = datetime(day.year, day.month, day.day, hour,
                                tzinfo=ZoneInfo(self.timezone_name))
                if slot > local or (slot == local and not strictly_after):
                    return slot.astimezone(timezone.utc)
        raise AssertionError("No next scheduled round")

    def due_at(self, now: datetime) -> datetime | None:
        local = aware(now).astimezone(ZoneInfo(self.timezone_name))
        if local.hour not in self.hours:
            return None
        slot = local.replace(minute=0, second=0, microsecond=0)
        if timedelta(0) <= local - slot < self.grace:
            return slot.astimezone(timezone.utc)
        return None

    def closes_at(self, slot: datetime) -> datetime:
        return aware(slot) + self.grace
