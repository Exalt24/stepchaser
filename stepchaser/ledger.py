"""The onboarding ledger: the one place that knows who signed up, what is still missing and who was chased.

n8n runs the workflow (webhook in, emails out). This ledger holds the state, because a check-then-insert done
inside a workflow can race when two deliveries arrive together. Here the check is a UNIQUE constraint.

Rules, each pinned by a test:
- A signup is stored once. The same idempotency key, or the same email in another case, is a duplicate: no new row,
  no second welcome email.
- Marking a checklist item twice changes nothing and never reports a second completion.
- A partner is chased only while items are missing, at most `max_reminders` times, one `remind_every` apart.
  After the last reminder the partner is flagged to the team as stalled, once. A finished partner is never chased.
- A reminder is claimed before it is sent, so two chase runs cannot send the same one. If the send fails the claim
  is released and the next run tries again.
- Every state change is one line in the event log (SQLite and a JSON lines file).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ics

REQUIRED_ITEMS = (
    "Signed partner agreement",
    "Identity check",
    "Payout details form",
    "Training module",
    "Profile photo",
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners(
  id INTEGER PRIMARY KEY AUTOINCREMENT, idem_key TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL, partner_type TEXT NOT NULL, tz TEXT NOT NULL, created_at TEXT NOT NULL,
  completed_at TEXT, reminder_count INTEGER NOT NULL DEFAULT 0, last_reminder_at TEXT, stalled_at TEXT);
CREATE TABLE IF NOT EXISTS items(partner_id INTEGER NOT NULL, item TEXT NOT NULL, done_at TEXT NOT NULL,
  PRIMARY KEY(partner_id, item));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
  partner_id INTEGER, detail TEXT NOT NULL);
"""


class LedgerError(ValueError):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class Ledger:
    def __init__(self, clock, db: str = ":memory:", remind_every: timedelta = timedelta(days=2),
                 max_reminders: int = 3, log_path: str | Path | None = None, calendar_dir: str | Path | None = None):
        if max_reminders < 1 or remind_every <= timedelta(0):
            raise LedgerError("max_reminders must be 1 or more and remind_every positive")
        self.clock, self.remind_every, self.max_reminders = clock, remind_every, max_reminders
        self.log_path = Path(log_path) if log_path else None
        self.calendar_dir = Path(calendar_dir) if calendar_dir else None
        self._lock = threading.RLock()
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- signup ---------------------------------------------------------------------------------------------
    def register(self, idem_key: str, name: str, email: str, partner_type: str = "tax partner",
                 tz: str = "UTC", kickoff: str | None = None) -> tuple[dict, bool]:
        idem_key, name, email = (idem_key or "").strip(), (name or "").strip(), (email or "").strip()
        if not idem_key:
            raise LedgerError("a signup needs an idempotency key")
        if not name or not EMAIL_RE.match(email):
            raise LedgerError("a signup needs a name and a valid email")
        with self._lock:
            row = self.db.execute("SELECT * FROM partners WHERE idem_key=? OR email=? COLLATE NOCASE",
                                  (idem_key, email)).fetchone()
            if row is not None:
                self._event("duplicate-signup", row["id"], {"idem_key": idem_key})
                return self._partner(row["id"]), False
            now = self.clock.now()
            cur = self.db.execute(
                "INSERT INTO partners(idem_key,email,name,partner_type,tz,created_at) VALUES(?,?,?,?,?,?)",
                (idem_key, email, name, partner_type or "tax partner", tz or "UTC", _iso(now)))
            pid = cur.lastrowid
            self._event("signup", pid, {"email": email, "type": partner_type})
            self.db.commit()
            if kickoff and self.calendar_dir:
                self._write_kickoff(pid, name, kickoff, tz)
            return self._partner(pid), True

    def _write_kickoff(self, pid: int, name: str, kickoff: str, tz: str) -> None:
        from .reminders import parse_local  # same time zone rules as the reminders
        start = parse_local(kickoff, tz or "UTC")
        self.calendar_dir.mkdir(parents=True, exist_ok=True)
        body = ics.make_event(f"kickoff-{pid}@stepchaser.local", start, f"Partner kickoff: {name}",
                              minutes=30, stamp=self.clock.now())
        (self.calendar_dir / f"kickoff-{pid}.ics").write_text(body, encoding="utf-8")
        self._event("kickoff-calendar-file", pid, {"file": f"kickoff-{pid}.ics"})

    # ---- checklist ------------------------------------------------------------------------------------------
    def mark_item(self, ref, item: str) -> tuple[dict, bool]:
        """Mark one checklist item done. Returns the partner and whether THIS call finished the checklist."""
        if item not in REQUIRED_ITEMS:
            raise LedgerError(f"unknown checklist item {item!r}; expected one of {list(REQUIRED_ITEMS)}")
        with self._lock:
            pid = self._resolve(ref)
            cur = self.db.execute("INSERT OR IGNORE INTO items(partner_id,item,done_at) VALUES(?,?,?)",
                                  (pid, item, _iso(self.clock.now())))
            if cur.rowcount == 0:
                self._event("item-repeat", pid, {"item": item})
                self.db.commit()
                return self._partner(pid), False
            self._event("item-done", pid, {"item": item})
            just_done = False
            if not self._missing(pid):
                self.db.execute("UPDATE partners SET completed_at=? WHERE id=? AND completed_at IS NULL",
                                (_iso(self.clock.now()), pid))
                self._event("completed", pid, {})
                just_done = True
            self.db.commit()
            return self._partner(pid), just_done

    # ---- chasing --------------------------------------------------------------------------------------------
    def due_chases(self) -> list[dict]:
        """Claim every reminder due now and return what to send. Call release() for one whose send failed."""
        now = self.clock.now()
        out: list[dict] = []
        with self._lock:
            for row in self.db.execute("SELECT * FROM partners WHERE completed_at IS NULL AND stalled_at IS NULL").fetchall():
                last = _from_iso(row["last_reminder_at"]) if row["last_reminder_at"] else _from_iso(row["created_at"])
                if last + self.remind_every > now:
                    continue
                pid = row["id"]
                missing = self._missing(pid)
                if row["reminder_count"] >= self.max_reminders:
                    self.db.execute("UPDATE partners SET stalled_at=? WHERE id=? AND stalled_at IS NULL", (_iso(now), pid))
                    self._event("stalled", pid, {"missing": missing})
                    out.append({"kind": "stalled", "partner": self._partner(pid), "missing": missing,
                                "n": row["reminder_count"]})
                    continue
                n = row["reminder_count"] + 1
                self.db.execute("UPDATE partners SET reminder_count=?, last_reminder_at=? WHERE id=?", (n, _iso(now), pid))
                self._event("reminder-claimed", pid, {"n": n, "missing": missing})
                out.append({"kind": "reminder", "partner": self._partner(pid), "missing": missing, "n": n,
                            "last": n == self.max_reminders})
            self.db.commit()
        return out

    def release(self, partner_id: int, kind: str = "reminder") -> None:
        """Undo a claim whose email failed, so the next chase run tries that partner again."""
        with self._lock:
            row = self.db.execute("SELECT * FROM partners WHERE id=?", (partner_id,)).fetchone()
            if row is None:
                raise LedgerError(f"no partner {partner_id}")
            if kind == "stalled":
                self.db.execute("UPDATE partners SET stalled_at=NULL WHERE id=?", (partner_id,))
            elif row["reminder_count"] > 0:
                prev = None
                if row["reminder_count"] > 1:  # the previous claim's time is the best guess we still hold
                    prev = _iso(_from_iso(row["last_reminder_at"]) - self.remind_every)
                self.db.execute("UPDATE partners SET reminder_count=reminder_count-1, last_reminder_at=? WHERE id=?",
                                (prev, partner_id))
            self._event("claim-released", partner_id, {"kind": kind})
            self.db.commit()

    # ---- reads ----------------------------------------------------------------------------------------------
    def partners(self) -> list[dict]:
        return [self._partner(r["id"]) for r in self.db.execute("SELECT id FROM partners ORDER BY id")]

    def events(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in reversed(rows)]

    def summary(self) -> dict:
        ps = self.partners()
        return {"partners": len(ps), "complete": sum(1 for p in ps if p["complete"]),
                "stalled": sum(1 for p in ps if p["stalled_at"]),
                "waiting": sum(1 for p in ps if not p["complete"] and not p["stalled_at"])}

    # ---- internals ------------------------------------------------------------------------------------------
    def _resolve(self, ref) -> int:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            row = self.db.execute("SELECT id FROM partners WHERE id=?", (int(ref),)).fetchone()
        else:
            row = self.db.execute("SELECT id FROM partners WHERE email=? COLLATE NOCASE", ((ref or "").strip(),)).fetchone()
        if row is None:
            raise KeyError(f"no partner {ref!r}")
        return row["id"]

    def _missing(self, pid: int) -> list[str]:
        done = {r["item"] for r in self.db.execute("SELECT item FROM items WHERE partner_id=?", (pid,))}
        return [i for i in REQUIRED_ITEMS if i not in done]

    def _partner(self, pid: int) -> dict:
        row = dict(self.db.execute("SELECT * FROM partners WHERE id=?", (pid,)).fetchone())
        row.pop("idem_key", None)
        missing = self._missing(pid)
        row["missing"] = missing
        row["done"] = [i for i in REQUIRED_ITEMS if i not in missing]
        row["complete"] = not missing
        return row

    def _event(self, kind: str, pid: int | None, detail: dict) -> None:
        at = _iso(self.clock.now())
        self.db.execute("INSERT INTO events(at,kind,partner_id,detail) VALUES(?,?,?,?)", (at, kind, pid, json.dumps(detail)))
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": at, "kind": kind, "partner_id": pid, **detail}) + "\n")
