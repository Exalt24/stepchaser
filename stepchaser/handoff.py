"""Where a handoff goes: an email to the team (summary first, then the masked transcript) and a queue file."""
from __future__ import annotations

import json
import threading
from pathlib import Path


def render_email(package: dict) -> tuple[str, str]:
    contact = package.get("contact") or {}
    who = contact.get("name") or "a visitor"
    subject = f"Handoff #{package['id']}: {who} ({package['reason'].replace('_', ' ')})"
    lines = [package["summary"], "", f"Reply to: {contact.get('email') or 'no email given in the form'}", "", "Transcript (sensitive fields hidden):"]
    lines += [f"  {t['role']}: {t['text']}" for t in package["transcript"]]
    return subject, "\n".join(lines)


class MailHandoffSink:
    def __init__(self, mailer, to: str):
        self.mailer, self.to = mailer, to

    def deliver(self, package: dict) -> None:
        subject, body = render_email(package)
        self.mailer.send(self.to, subject, body)


class QueueHandoffSink:
    """Appends each handoff to a JSON lines queue the team page reads. Also keeps the live list in memory."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.items: list[dict] = []
        self._lock = threading.Lock()

    def deliver(self, package: dict) -> None:
        with self._lock:
            self.items.append(package)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(package) + "\n")
