"""Appointment reminders: bookings in, reminders out at set offsets, reschedule and cancel handled.

Rules, each one pinned by a test with a fake clock:
- A booking is stored as UTC plus the customer's IANA zone, and every message shows the customer's own local time.
- A local time that does not exist (spring forward) or happens twice (fall back) is refused, never guessed.
- A reminder goes out once per (booking, start time, offset). A reschedule changes the start, so its reminders re-arm.
- A cancelled booking sends no reminder. Cancelling twice sends one notice.
- A reminder whose moment passed before the booking was made is skipped (a booking made 3 hours ahead gets no 24 hour reminder).
- If the clock jumps past several offsets at once, only the closest one is sent and the earlier ones are recorded as skipped.
- Nothing is sent for an appointment that has already started.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ics

DEFAULT_OFFSETS_MIN = (24 * 60, 2 * 60)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL, tz TEXT NOT NULL,
  title TEXT NOT NULL, start_utc TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, cancel_notified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders(
  booking_id INTEGER NOT NULL, start_utc TEXT NOT NULL, offset_min INTEGER NOT NULL,
  outcome TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(booking_id, start_utc, offset_min));
CREATE TABLE IF NOT EXISTS outbox_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
  booking_id INTEGER NOT NULL, to_addr TEXT NOT NULL, detail TEXT NOT NULL);
"""


class BookingError(ValueError):
    pass


def parse_local(local: str, tz: str) -> datetime:
    """Turn '2026-03-08T09:30' in an IANA zone into an aware UTC time, refusing gap and fold times."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise BookingError(f"unknown time zone {tz!r}")
    try:
        naive = datetime.fromisoformat(local)
    except ValueError:
        raise BookingError(f"unreadable time {local!r}")
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        # fold=0 and fold=1 differ only for a repeated hour (fall back) or a skipped hour (spring forward)
        as_utc = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if as_utc != naive:
            raise BookingError(f"{local} does not exist in {tz} (clocks skip it)")
        raise BookingError(f"{local} happens twice in {tz} (clocks repeat it), send the time with its UTC offset")
    return first.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local_label(start_utc: datetime, tz: str) -> str:
    local = start_utc.astimezone(ZoneInfo(tz))
    return local.strftime("%A %d %B %Y, %H:%M ") + (local.tzname() or tz) + f" ({tz})"


def _human_lead(minutes: int) -> str:
    if minutes % (24 * 60) == 0:
        d = minutes // (24 * 60)
        return f"{d} day" + ("s" if d != 1 else "")
    if minutes % 60 == 0:
        h = minutes // 60
        return f"{h} hour" + ("s" if h != 1 else "")
    return f"{minutes} minutes"


class Reminders:
    def __init__(self, mailer, clock, db: str = ":memory:", offsets_min=DEFAULT_OFFSETS_MIN, sender_name: str = "Cedar Lane Services"):
        if not offsets_min or any(o <= 0 for o in offsets_min):
            raise BookingError("reminder offsets must be positive minutes")
        self.mailer, self.clock = mailer, clock
        self.offsets = tuple(sorted(set(offsets_min), reverse=True))
        self.sender_name = sender_name
        self._lock = threading.RLock()
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- bookings -------------------------------------------------------------------------------------------
    def book(self, name: str, email: str, local_time: str, tz: str, title: str = "Consultation") -> dict:
        name, email = (name or "").strip(), (email or "").strip()
        if not name or "@" not in email:
            raise BookingError("a booking needs a name and an email")
        start = parse_local(local_time, tz)
        now = self.clock.now()
        if start <= now:
            raise BookingError("that time is already in the past")
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO bookings(name,email,tz,title,start_utc,created_at) VALUES(?,?,?,?,?,?)",
                (name, email, tz, title, _iso(start), _iso(now)))
            self.db.commit()
            booking = self.get(cur.lastrowid)
        self._send(booking, "confirmation", f"Booked: {title} on {local_label(start, tz)}",
                   f"Hi {name},\n\nYour {title.lower()} is booked for {local_label(start, tz)}.\n"
                   f"A calendar invite is attached. You will get a reminder {self._lead_text()} before it.\n\n{self.sender_name}",
                   method="REQUEST")
        return booking

    def reschedule(self, booking_id: int, local_time: str) -> dict:
        with self._lock:
            b = self.get(booking_id)
            if b["status"] == "cancelled":
                raise BookingError("a cancelled booking cannot be moved, book a new time")
            start = parse_local(local_time, b["tz"])
            if start <= self.clock.now():
                raise BookingError("that time is already in the past")
            if _iso(start) == b["start_utc"]:
                return b  # same time again: nothing changes and nothing is sent
            self.db.execute("UPDATE bookings SET start_utc=?, version=version+1, created_at=? WHERE id=?",
                            (_iso(start), _iso(self.clock.now()), booking_id))
            self.db.commit()
            b = self.get(booking_id)
        self._send(b, "rescheduled", f"Moved: {b['title']} is now {local_label(start, b['tz'])}",
                   f"Hi {b['name']},\n\nYour {b['title'].lower()} moved to {local_label(start, b['tz'])}.\n"
                   f"The calendar invite is updated and your reminders follow the new time.\n\n{self.sender_name}",
                   method="REQUEST")
        return b

    def cancel(self, booking_id: int) -> dict:
        with self._lock:
            b = self.get(booking_id)
            if b["status"] == "cancelled":
                return b  # a second cancel is a no-op
            self.db.execute("UPDATE bookings SET status='cancelled', version=version+1 WHERE id=?", (booking_id,))
            self.db.commit()
            b = self.get(booking_id)
        self._send(b, "cancelled", f"Cancelled: {b['title']} on {local_label(_from_iso(b['start_utc']), b['tz'])}",
                   f"Hi {b['name']},\n\nYour {b['title'].lower()} is cancelled. No reminder will follow.\n\n{self.sender_name}",
                   method="CANCEL")
        return b

    def get(self, booking_id: int) -> dict:
        row = self.db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if row is None:
            raise BookingError(f"no booking {booking_id}")
        return dict(row)

    def list(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM bookings ORDER BY start_utc")]

    # ---- the tick ------------------------------------------------------------------------------------------
    def tick(self) -> list[dict]:
        """Send every reminder that is due now. Safe to call as often as you like; a reminder never goes twice."""
        now = self.clock.now()
        sent: list[dict] = []
        with self._lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM bookings WHERE status='active'")]
            for b in rows:
                start = _from_iso(b["start_utc"])
                if start <= now:
                    continue
                created = _from_iso(b["created_at"])
                due = []
                for off in self.offsets:
                    moment = start - timedelta(minutes=off)
                    if moment > now:
                        continue
                    if self.db.execute("SELECT 1 FROM reminders WHERE booking_id=? AND start_utc=? AND offset_min=?",
                                       (b["id"], b["start_utc"], off)).fetchone():
                        continue
                    due.append((off, moment))
                if not due:
                    continue
                # offsets are sorted largest first, so the last entry is the closest to the start
                for off, moment in due[:-1]:
                    self._record(b, off, "skipped-superseded", now)
                off, moment = due[-1]
                if moment < created:
                    self._record(b, off, "skipped-booked-after", now)
                    continue
                self._record(b, off, "sent", now)
                lead = _human_lead(off)
                ok = self._send(b, "reminder", f"Reminder: {b['title']} in {lead}",
                           f"Hi {b['name']},\n\nA reminder that your {b['title'].lower()} is on {local_label(start, b['tz'])}, "
                           f"about {lead} from now.\nNeed to move it? Reply to this message.\n\n{self.sender_name}",
                           method="REQUEST")
                if not ok:  # the mail server refused it: forget the mark so the next tick tries again
                    self.db.execute("DELETE FROM reminders WHERE booking_id=? AND start_utc=? AND offset_min=?",
                                    (b["id"], b["start_utc"], off))
                    continue
                sent.append({"booking_id": b["id"], "offset_min": off, "to": b["email"]})
            self.db.commit()
        return sent

    # ---- internals -----------------------------------------------------------------------------------------
    def _lead_text(self) -> str:
        return " and ".join(_human_lead(o) for o in self.offsets)

    def _record(self, b: dict, off: int, outcome: str, now: datetime) -> None:
        self.db.execute("INSERT OR IGNORE INTO reminders(booking_id,start_utc,offset_min,outcome,at) VALUES(?,?,?,?,?)",
                        (b["id"], b["start_utc"], off, outcome, _iso(now)))

    def reminder_log(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM reminders ORDER BY at, booking_id, offset_min")]

    def _send(self, b: dict, kind: str, subject: str, body: str, method: str) -> bool:
        start = _from_iso(b["start_utc"])
        invite = ics.make_event(f"booking-{b['id']}@stepchaser.local", start, b["title"], sequence=b["version"],
                                method=method, stamp=self.clock.now())
        status = "ok"
        try:
            self.mailer.send(b["email"], subject, body, ics=invite, ics_method=method)
        except Exception as exc:  # a mail failure is logged and surfaced by the log, never swallowed silently
            status = f"failed: {exc}"
        self.db.execute("INSERT INTO outbox_log(at,kind,booking_id,to_addr,detail) VALUES(?,?,?,?,?)",
                        (_iso(self.clock.now()), kind, b["id"], b["email"], json.dumps({"subject": subject, "status": status})))
        self.db.commit()
        return status == "ok"

    def outbox(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox_log ORDER BY id")]
