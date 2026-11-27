"""Minimal iCalendar (RFC 5545) events: a request, an update with a higher SEQUENCE, or a cancel."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\n", "\\n"))


def make_event(uid: str, start: datetime, summary: str, *, minutes: int = 30, sequence: int = 0,
               method: str = "REQUEST", stamp: datetime | None = None, description: str = "") -> str:
    if method not in ("REQUEST", "CANCEL", "PUBLISH"):
        raise ValueError(f"unsupported ICS method {method!r}")
    if start.tzinfo is None:
        raise ValueError("an event start needs a timezone")
    stamp = stamp or datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//stepchaser//reminders//EN", f"METHOD:{method}",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_fmt(stamp)}", f"DTSTART:{_fmt(start)}",
        f"DTEND:{_fmt(start + timedelta(minutes=minutes))}", f"SEQUENCE:{sequence}",
        f"SUMMARY:{_esc(summary)}", f"STATUS:{'CANCELLED' if method == 'CANCEL' else 'CONFIRMED'}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
