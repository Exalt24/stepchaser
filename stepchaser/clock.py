"""One clock for everything that waits. Production uses the system clock; tests and the demo move a fake one."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("a fake clock needs a timezone aware start")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kw) -> datetime:
        self._now += timedelta(**kw)
        return self._now

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("a fake clock needs a timezone aware time")
        self._now = when.astimezone(timezone.utc)


class OffsetClock:
    """System clock plus an offset the demo page can move forward (never used by the library defaults)."""

    def __init__(self):
        self.offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self.offset
