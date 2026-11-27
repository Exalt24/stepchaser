"""Seeded random worlds for the two stateful parts, checked against their rules at every step.

The worlds vary everything together: duplicate and re-cased signups, retried checklist items, clock jumps small and
large, failed sends and releases, reschedules, cancels and bookings made inside a reminder window, across zones
with and without daylight saving time. A rule that holds in 200 worlds of 80 steps is stronger than one example.
"""
import random
import re
from datetime import datetime, timedelta, timezone

import pytest

from stepchaser.clock import FakeClock
from stepchaser.ledger import REQUIRED_ITEMS, Ledger, LedgerError
from stepchaser.mail import MemoryMailer
from stepchaser.reminders import BookingError, Reminders

UTC = timezone.utc
T0 = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("seed", range(200))
def test_ledger_world(seed):
    rng = random.Random(seed)
    clock = FakeClock(T0)
    every, cap = timedelta(hours=rng.choice([6, 24, 48])), rng.choice([1, 2, 3])
    led = Ledger(clock, remind_every=every, max_reminders=cap)
    emails: dict[str, int] = {}
    claims: dict[int, list[datetime]] = {}
    stalled: dict[int, int] = {}
    completed_at: dict[int, datetime] = {}
    for _ in range(80):
        step = rng.random()
        if step < 0.25:
            n = rng.randrange(6)
            email = f"p{n}@example.test" if rng.random() < .5 else f"P{n}@Example.test"
            key = f"k{rng.randrange(8)}"
            try:
                p, created = led.register(key, f"Partner {n}", email)
            except LedgerError:
                continue
            emails.setdefault(email.lower(), p["id"])
        elif step < 0.5 and led.partners():
            p = rng.choice(led.partners())
            _, just = led.mark_item(p["id"], rng.choice(REQUIRED_ITEMS))
            if just:
                assert p["id"] not in completed_at
                completed_at[p["id"]] = clock.now()
        elif step < 0.9:
            clock.advance(minutes=rng.choice([1, 30, 600, 3000, 9000]))
            for c in led.due_chases():
                pid = c["partner"]["id"]
                assert pid not in completed_at, "a finished partner was chased"
                if c["kind"] == "stalled":
                    stalled[pid] = stalled.get(pid, 0) + 1
                    assert len(claims.get(pid, [])) == cap, "stalled before the last reminder"
                    continue
                if rng.random() < 0.2:                       # the send failed
                    led.release(pid)
                    continue
                times = claims.setdefault(pid, [])
                times.append(clock.now())
                assert len(times) <= cap
                assert all(b - a >= every for a, b in zip(times, times[1:])), "two reminders closer than the interval"
                assert c["missing"], "a reminder with nothing missing"
    assert all(v == 1 for v in stalled.values()), "a partner was flagged as stalled more than once"
    assert len(led.partners()) <= len(emails)      # a reused key or a re-cased email never adds a partner
    assert len({p["email"].lower() for p in led.partners()}) == len(led.partners())


class Recording(MemoryMailer):
    def __init__(self, clock, **kw):
        super().__init__(**kw)
        self.clock, self.log = clock, []

    def send(self, to, subject, body, ics=None, ics_method=None):
        super().send(to, subject, body, ics, ics_method)
        uid = int(re.search(r"UID:booking-(\d+)@", ics).group(1))
        start = datetime.strptime(re.search(r"DTSTART:(\S+)", ics).group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        self.log.append((self.clock.now(), uid, subject.split(":")[0], start))


ZONES = ["UTC", "Asia/Manila", "America/New_York", "Europe/London", "Australia/Sydney"]


@pytest.mark.parametrize("seed", range(200))
def test_reminder_world(seed):
    rng = random.Random(1000 + seed)
    clock = FakeClock(T0)
    mail = Recording(clock)
    rem = Reminders(mail, clock)
    cancelled_at: dict[int, datetime] = {}
    for _ in range(80):
        step = rng.random()
        ids = [b["id"] for b in rem.list()]
        try:
            if step < 0.2:
                when = (clock.now() + timedelta(minutes=rng.choice([30, 200, 1500, 3000, 9000, 20000]))).replace(tzinfo=None)
                rem.book(f"C{len(ids)}", f"c{len(ids)}@example.test", when.replace(second=0, microsecond=0).isoformat(),
                         rng.choice(ZONES))
            elif step < 0.3 and ids:
                when = (clock.now() + timedelta(minutes=rng.choice([90, 700, 4000, 12000]))).replace(tzinfo=None)
                rem.reschedule(rng.choice(ids), when.replace(second=0, microsecond=0).isoformat())
            elif step < 0.4 and ids:
                bid = rng.choice(ids)
                rem.cancel(bid)
                cancelled_at.setdefault(bid, clock.now())
            else:
                if rng.random() < 0.1:
                    mail.fail_for = {f"c{i}@example.test" for i in range(3)}
                else:
                    mail.fail_for = set()
                clock.advance(minutes=rng.choice([1, 15, 59, 180, 1500]))
                rem.tick()
        except BookingError:
            pass  # a refused input (past time, a gap or repeated local time, a cancelled booking) must not change state
    seen = set()
    for at, bid, kind, start in mail.log:
        if kind != "Reminder":
            continue
        assert at < start, "a reminder went out after the appointment started"
        assert bid not in cancelled_at or at <= cancelled_at[bid], "a reminder went out for a cancelled booking"
        key = (bid, start, at)
        assert key not in seen
        seen.add(key)
    per_start: dict[tuple, int] = {}
    for _, bid, kind, start in mail.log:
        if kind == "Reminder":
            per_start[(bid, start)] = per_start.get((bid, start), 0) + 1
    assert all(v <= len(rem.offsets) for v in per_start.values()), "more reminders than offsets for one start time"
