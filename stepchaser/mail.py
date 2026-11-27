"""Mail senders. SmtpMailer talks to any SMTP server (the demo uses a local mail catcher); MemoryMailer is for tests."""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass
class Sent:
    to: str
    subject: str
    body: str
    ics: str | None = None
    ics_method: str | None = None


class MemoryMailer:
    def __init__(self, fail_for: set[str] | None = None):
        self.outbox: list[Sent] = []
        self.fail_for = fail_for or set()

    def send(self, to: str, subject: str, body: str, ics: str | None = None, ics_method: str | None = None) -> None:
        if to in self.fail_for:
            raise OSError(f"mail server refused {to}")
        self.outbox.append(Sent(to, subject, body, ics, ics_method))


@dataclass
class SmtpMailer:
    host: str = "localhost"
    port: int = 1025
    sender: str = "desk@example.test"
    timeout: float = 10.0

    def send(self, to: str, subject: str, body: str, ics: str | None = None, ics_method: str | None = None) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        if ics:
            msg.add_attachment(ics.encode("utf-8"), maintype="text", subtype="calendar",
                               filename="invite.ics", params={"method": ics_method or "REQUEST"})
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.send_message(msg)
