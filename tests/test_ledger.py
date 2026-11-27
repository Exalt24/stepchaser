"""The onboarding ledger on a fake clock: dedupe, checklist, chase schedule, stall, release, log."""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from stepchaser.clock import FakeClock
from stepchaser.ledger import REQUIRED_ITEMS, Ledger, LedgerError

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


def make(**kw):
    clock = FakeClock(T0)
    return Ledger(clock, remind_every=timedelta(days=2), max_reminders=3, **kw), clock


def test_signup_is_stored_once_per_key_and_per_email():
    led, _ = make()
    p, created = led.register("k1", "Sam Sample", "sam@example.test")
    assert created and p["id"] == 1 and p["missing"] == list(REQUIRED_ITEMS)
    p2, created2 = led.register("k1", "Sam Sample", "sam@example.test")           # a retried webhook
    assert not created2 and p2["id"] == 1
    p3, created3 = led.register("k2", "Sam S", "SAM@Example.test")                # a double click, new key, same email
    assert not created3 and p3["id"] == 1
    assert len(led.partners()) == 1
    assert [e["kind"] for e in led.events()].count("signup") == 1


def test_concurrent_identical_signups_create_one_row():
    led, _ = make()
    results = []

    def go():
        results.append(led.register("same-key", "Sam", "sam@example.test")[1])

    threads = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1 and len(led.partners()) == 1


@pytest.mark.parametrize("key,name,email", [("", "A", "a@example.test"), ("k", "", "a@example.test"),
                                             ("k", "A", "nope"), ("k", "A", "a@b")])
def test_bad_signups_are_refused(key, name, email):
    led, _ = make()
    with pytest.raises(LedgerError):
        led.register(key, name, email)
    assert led.partners() == []


def test_marking_items_and_completion_once():
    led, _ = make()
    led.register("k1", "Sam", "sam@example.test")
    for item in REQUIRED_ITEMS[:-1]:
        p, done = led.mark_item("sam@example.test", item)
        assert not done
    p, done = led.mark_item(1, REQUIRED_ITEMS[-1])
    assert done and p["complete"] and p["completed_at"]
    p, done = led.mark_item(1, REQUIRED_ITEMS[-1])                                 # repeat: no second completion
    assert not done
    assert [e["kind"] for e in led.events()].count("completed") == 1


def test_unknown_item_and_partner():
    led, _ = make()
    led.register("k1", "Sam", "sam@example.test")
    with pytest.raises(LedgerError):
        led.mark_item(1, "Tax return copy")
    with pytest.raises(KeyError):
        led.mark_item("ghost@example.test", REQUIRED_ITEMS[0])


def test_chase_schedule_two_days_apart_then_stalled_once():
    led, clock = make()
    led.register("k1", "Sam", "sam@example.test")
    assert led.due_chases() == []                                                  # nothing at signup
    clock.advance(days=1, hours=23)
    assert led.due_chases() == []
    clock.advance(hours=1)
    first = led.due_chases()
    assert [c["kind"] for c in first] == ["reminder"] and first[0]["n"] == 1 and first[0]["missing"] == list(REQUIRED_ITEMS)
    assert led.due_chases() == []                                                  # claimed: a second run finds nothing
    clock.advance(days=2)
    assert led.due_chases()[0]["n"] == 2
    clock.advance(days=2)
    third = led.due_chases()
    assert third[0]["n"] == 3 and third[0]["last"] is True
    clock.advance(days=2)
    stalled = led.due_chases()
    assert [c["kind"] for c in stalled] == ["stalled"]
    clock.advance(days=30)
    assert led.due_chases() == []                                                  # flagged once, then quiet
    assert led.summary()["stalled"] == 1


def test_reminders_list_only_what_is_still_missing_and_stop_when_complete():
    led, clock = make()
    led.register("k1", "Sam", "sam@example.test")
    led.mark_item(1, "Signed partner agreement")
    led.mark_item(1, "Profile photo")
    clock.advance(days=2)
    due = led.due_chases()[0]
    assert due["missing"] == ["Identity check", "Payout details form", "Training module"]
    for item in due["missing"]:
        led.mark_item(1, item)
    clock.advance(days=10)
    assert led.due_chases() == []


def test_release_lets_the_next_run_retry_a_failed_send():
    led, clock = make()
    led.register("k1", "Sam", "sam@example.test")
    clock.advance(days=2)
    claim = led.due_chases()[0]
    led.release(claim["partner"]["id"])
    assert led.due_chases()[0]["n"] == 1                                           # the same reminder number, again


def test_each_partner_chased_on_their_own_clock():
    led, clock = make()
    led.register("k1", "A", "a@example.test")
    clock.advance(days=1)
    led.register("k2", "B", "b@example.test")
    clock.advance(days=1)
    assert [c["partner"]["name"] for c in led.due_chases()] == ["A"]
    clock.advance(days=1)
    assert [c["partner"]["name"] for c in led.due_chases()] == ["B"]


def test_event_log_is_written_to_a_json_lines_file(tmp_path):
    log = tmp_path / "ledger.jsonl"
    led, clock = make(log_path=log)
    led.register("k1", "Sam", "sam@example.test")
    led.register("k1", "Sam", "sam@example.test")
    led.mark_item(1, REQUIRED_ITEMS[0])
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [x["kind"] for x in lines] == ["signup", "duplicate-signup", "item-done"]


def test_kickoff_calendar_file_is_written_in_the_partner_zone(tmp_path):
    led, _ = make(calendar_dir=tmp_path)
    led.register("k1", "Sam", "sam@example.test", tz="Asia/Manila", kickoff="2026-03-05T10:00")
    text = (tmp_path / "kickoff-1.ics").read_text(encoding="utf-8")
    assert "DTSTART:20260305T020000Z" in text and "SUMMARY:Partner kickoff: Sam" in text
