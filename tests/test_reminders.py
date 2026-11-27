"""Appointment reminders, run on a fake clock. Both directions: what must be sent and what must not."""
from datetime import datetime, timezone

import pytest

from stepchaser.clock import FakeClock
from stepchaser.mail import MemoryMailer
from stepchaser.reminders import BookingError, Reminders, parse_local

UTC = timezone.utc


def make(start=datetime(2026, 3, 1, 12, 0, tzinfo=UTC), **kw):
    clock = FakeClock(start)
    mailer = MemoryMailer(**kw)
    return Reminders(mailer, clock), clock, mailer


def subjects(mailer, prefix):
    return [m for m in mailer.outbox if m.subject.startswith(prefix)]


def test_booking_sends_confirmation_with_invite():
    r, clock, mail = make()
    b = r.book("Sample Partner", "p1@example.test", "2026-03-05T10:00", "Asia/Manila")
    assert b["start_utc"] == "2026-03-05T02:00:00Z"
    assert len(mail.outbox) == 1
    assert mail.outbox[0].ics_method == "REQUEST" and "SEQUENCE:0" in mail.outbox[0].ics
    assert "Thursday 05 March 2026, 10:00 PST" in mail.outbox[0].body or "10:00" in mail.outbox[0].body


def test_reminders_fire_at_both_offsets_once():
    r, clock, mail = make()
    r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")   # start 2026-03-05 10:00Z
    mail.outbox.clear()
    clock.set(datetime(2026, 3, 4, 9, 59, tzinfo=UTC))
    assert r.tick() == [] and not mail.outbox                  # one minute before the 24h mark
    clock.set(datetime(2026, 3, 4, 10, 0, tzinfo=UTC))
    assert len(r.tick()) == 1 and "in 1 day" in mail.outbox[-1].subject
    assert r.tick() == []                                      # ticking again sends nothing
    clock.set(datetime(2026, 3, 5, 7, 59, tzinfo=UTC))
    assert r.tick() == []
    clock.set(datetime(2026, 3, 5, 8, 0, tzinfo=UTC))
    assert len(r.tick()) == 1 and "in 2 hours" in mail.outbox[-1].subject
    clock.set(datetime(2026, 3, 5, 9, 30, tzinfo=UTC))
    assert r.tick() == [] and len(subjects(mail, "Reminder")) == 2


def test_cancelled_booking_gets_no_reminder_and_one_notice():
    r, clock, mail = make()
    b = r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    r.cancel(b["id"])
    r.cancel(b["id"])                                          # second cancel changes nothing
    clock.set(datetime(2026, 3, 5, 8, 0, tzinfo=UTC))
    assert r.tick() == []
    assert len(subjects(mail, "Reminder")) == 0
    cancels = subjects(mail, "Cancelled")
    assert len(cancels) == 1 and cancels[0].ics_method == "CANCEL" and "STATUS:CANCELLED" in cancels[0].ics


def test_cancel_after_first_reminder_stops_the_second():
    r, clock, mail = make()
    b = r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    clock.set(datetime(2026, 3, 4, 10, 0, tzinfo=UTC))
    r.tick()
    r.cancel(b["id"])
    clock.set(datetime(2026, 3, 5, 8, 0, tzinfo=UTC))
    assert r.tick() == []
    assert len(subjects(mail, "Reminder")) == 1


def test_reschedule_rearms_reminders_for_the_new_time():
    r, clock, mail = make()
    b = r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    clock.set(datetime(2026, 3, 4, 10, 0, tzinfo=UTC))
    r.tick()                                                   # 24h reminder for the old time
    r.reschedule(b["id"], "2026-03-07T10:00")
    assert subjects(mail, "Moved")[0].ics.count("SEQUENCE:1") == 1
    clock.set(datetime(2026, 3, 5, 8, 0, tzinfo=UTC))
    assert r.tick() == []                                      # the old 2h mark no longer applies
    clock.set(datetime(2026, 3, 6, 10, 0, tzinfo=UTC))
    assert len(r.tick()) == 1                                  # 24h before the NEW time
    clock.set(datetime(2026, 3, 7, 8, 0, tzinfo=UTC))
    assert len(r.tick()) == 1
    assert len(subjects(mail, "Reminder")) == 3


def test_reschedule_to_same_time_sends_nothing():
    r, clock, mail = make()
    b = r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    n = len(mail.outbox)
    r.reschedule(b["id"], "2026-03-05T10:00")
    assert len(mail.outbox) == n


def test_cannot_move_a_cancelled_booking_or_into_the_past():
    r, clock, mail = make()
    b = r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    with pytest.raises(BookingError):
        r.reschedule(b["id"], "2026-02-01T10:00")
    r.cancel(b["id"])
    with pytest.raises(BookingError):
        r.reschedule(b["id"], "2026-03-09T10:00")


def test_booking_made_inside_the_window_skips_the_earlier_reminder():
    r, clock, mail = make()
    r.book("A", "a@example.test", "2026-03-01T15:00", "UTC")   # starts in 3 hours
    mail.outbox.clear()
    assert r.tick() == []                                      # the 24h moment was before the booking existed
    clock.set(datetime(2026, 3, 1, 13, 0, tzinfo=UTC))
    assert len(r.tick()) == 1 and "in 2 hours" in mail.outbox[-1].subject


def test_a_clock_jump_sends_only_the_closest_reminder():
    r, clock, mail = make()
    r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    mail.outbox.clear()
    clock.set(datetime(2026, 3, 5, 9, 0, tzinfo=UTC))        # both offsets are past
    assert len(r.tick()) == 1 and "in 2 hours" in mail.outbox[-1].subject
    outcomes = sorted(x["outcome"] for x in r.reminder_log())
    assert outcomes == ["sent", "skipped-superseded"]


def test_nothing_is_sent_for_an_appointment_that_started():
    r, clock, mail = make()
    r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    mail.outbox.clear()
    clock.set(datetime(2026, 3, 5, 10, 0, tzinfo=UTC))
    assert r.tick() == [] and not mail.outbox


def test_failed_send_is_retried_on_the_next_tick():
    r, clock, mail = make()
    r.book("A", "a@example.test", "2026-03-05T10:00", "UTC")
    mail.fail_for.add("a@example.test")
    clock.set(datetime(2026, 3, 4, 10, 0, tzinfo=UTC))
    assert r.tick() == []                                      # refused, not marked as sent
    assert any("failed" in o["detail"] for o in r.outbox())
    mail.fail_for.clear()
    assert len(r.tick()) == 1


def test_timezone_conversion_across_zones_and_dst():
    assert parse_local("2026-07-01T09:00", "America/New_York").hour == 13       # EDT, UTC-4
    assert parse_local("2026-01-01T09:00", "America/New_York").hour == 14       # EST, UTC-5
    assert parse_local("2026-03-05T09:00", "Asia/Manila").hour == 1             # no DST, UTC+8
    assert parse_local("2026-03-05T09:00+02:00", "Asia/Manila").hour == 7       # an explicit offset wins


def test_nonexistent_and_repeated_local_times_are_refused():
    with pytest.raises(BookingError, match="does not exist"):
        parse_local("2026-03-08T02:30", "America/New_York")       # spring forward gap
    with pytest.raises(BookingError, match="twice"):
        parse_local("2026-11-01T01:30", "America/New_York")       # fall back repeat
    with pytest.raises(BookingError, match="time zone"):
        parse_local("2026-03-08T10:00", "Mars/Olympus")


def test_reminder_follows_wall_clock_across_a_dst_change():
    # 10:00 New York on 9 March is EDT (14:00Z); the 24h reminder is 14:00Z on 8 March, after the clocks moved
    r, clock, mail = make(start=datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
    r.book("A", "a@example.test", "2026-03-09T10:00", "America/New_York")
    mail.outbox.clear()
    clock.set(datetime(2026, 3, 8, 13, 59, tzinfo=UTC))
    assert r.tick() == []
    clock.set(datetime(2026, 3, 8, 14, 0, tzinfo=UTC))
    assert len(r.tick()) == 1
    assert "10:00 EDT" in mail.outbox[-1].body


def test_bad_input_is_refused():
    r, clock, mail = make()
    with pytest.raises(BookingError):
        r.book("", "a@example.test", "2026-03-05T10:00", "UTC")
    with pytest.raises(BookingError):
        r.book("A", "not-an-email", "2026-03-05T10:00", "UTC")
    with pytest.raises(BookingError):
        r.book("A", "a@example.test", "tomorrow at ten", "UTC")
    with pytest.raises(BookingError):
        Reminders(mail, clock, offsets_min=(0,))
    assert not mail.outbox
