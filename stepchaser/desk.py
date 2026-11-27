"""The desk: one small HTTP service that holds the ledger, the bookings, the lead chat and the demo page.

  python -m stepchaser.desk            (reads its settings from environment variables, listed in docker-compose.yml)

n8n calls the /ledger/* routes with a shared key. The browser page calls /api/*. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import kb
from .clock import OffsetClock
from .handoff import MailHandoffSink, QueueHandoffSink
from .ledger import REQUIRED_ITEMS, Ledger, LedgerError
from .llm import LLMError, OpenAICompatLLM
from .mail import SmtpMailer
from .reminders import BookingError, Reminders
from .router import AuditLog, ChatSession, KeywordStandIn, LeadRouter

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "desk" / "web"


class Desk:
    def __init__(self, env=os.environ, mailer=None):
        self.key = env.get("DESK_KEY", "local-desk-key")
        data = Path(env.get("DATA_DIR", str(ROOT / "runtime")))
        data.mkdir(parents=True, exist_ok=True)
        self.clock = OffsetClock()
        self.mailer = mailer or SmtpMailer(env.get("SMTP_HOST", "localhost"), int(env.get("SMTP_PORT", "1025")), sender="desk@cedar-lane.example.test")
        self.team = env.get("TEAM_EMAIL", "team@cedar-lane.example.test")
        self.n8n = env.get("N8N_BASE", "http://localhost:5678").rstrip("/")
        self.ledger = Ledger(self.clock, str(data / "ledger.sqlite"),
                             remind_every=timedelta(seconds=int(env.get("REMIND_EVERY_SECONDS", str(2 * 86400)))),
                             max_reminders=int(env.get("MAX_REMINDERS", "3")), log_path=data / "ledger.log.jsonl",
                             calendar_dir=data / "calendar")
        self.reminders = Reminders(self.mailer, self.clock, str(data / "bookings.sqlite"))
        self.queue = QueueHandoffSink(data / "handoffs.jsonl")
        try:
            self.llm = OpenAICompatLLM.from_env(env=env)
            self.classifier = f"real model {self.llm.model}"
        except LLMError:
            self.llm = KeywordStandIn()
            self.classifier = "keyword stand-in (set GROQ_API_KEY or LLM_API_KEY for a real model)"
        self.router = LeadRouter(self.llm, kb.load(ROOT / "kb"), self.clock, audit=AuditLog(data / "chat_audit.jsonl"),
                                 sinks=[self.queue, MailHandoffSink(self.mailer, self.team)])
        self.sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def tick_loop(self):
        while True:
            try:
                self.reminders.tick()
            except Exception as exc:  # the loop must outlive one bad tick
                print("tick failed:", type(exc).__name__, flush=True)
            time.sleep(3)

    # ---- helpers ----------------------------------------------------------------------------------------------
    def forward(self, path: str, body: dict, headers: dict) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.n8n}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            except ValueError:
                return exc.code, {"error": "the workflow answered with an error"}
        except (urllib.error.URLError, OSError):
            return 502, {"error": "the workflow engine (n8n) is not reachable"}

    def state(self) -> dict:
        return {"now": self.clock.now().isoformat(timespec="seconds"), "classifier": self.classifier,
                "required_items": list(REQUIRED_ITEMS), "partners": self.ledger.partners(), "summary": self.ledger.summary(),
                "events": self.ledger.events(12), "bookings": self.reminders.list(), "outbox": self.reminders.outbox()[-12:],
                "handoffs": self.queue.items[-10:]}


def make_handler(desk: Desk):
    class Handler(BaseHTTPRequestHandler):
        server_version = "stepchaser-desk"

        def log_message(self, fmt, *args):  # keep the console quiet; the ledger log is the record
            pass

        def _send(self, code: int, payload, ctype="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                raise ValueError("body too large")
            return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

        def _authed(self) -> bool:
            if self.headers.get("X-Desk-Key") != desk.key:
                self._send(401, {"error": "bad or missing key"})
                return False
            return True

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, desk.state())
            if path == "/health":
                return self._send(200, {"ok": True})
            if path in ("/ledger/partners", "/ledger/events"):
                if not self._authed():
                    return
                return self._send(200, {"partners": desk.ledger.partners()} if path.endswith("partners") else {"events": desk.ledger.events(500)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self._body()
            except ValueError:
                return self._send(400, {"error": "unreadable body"})
            try:
                if path.startswith("/ledger/"):
                    if not self._authed():
                        return
                    return self._ledger(path, body)
                return self._api(path, body)
            except (LedgerError, BookingError) as exc:
                self._send(422, {"ok": False, "error": str(exc)})
            except KeyError as exc:
                self._send(404, {"ok": False, "error": str(exc.args[0])})

        def _ledger(self, path: str, b: dict):
            if path == "/ledger/register":
                p, created = desk.ledger.register(b.get("idempotency_key", ""), b.get("name", ""), b.get("email", ""),
                                                  b.get("partner_type") or "tax partner", b.get("tz") or "UTC", b.get("kickoff") or None)
                return self._send(200, {"ok": True, "created": created, "duplicate": not created, "partner": p})
            if path == "/ledger/item":
                p, just = desk.ledger.mark_item(b.get("email") or b.get("partner_id"), b.get("item", ""))
                return self._send(200, {"ok": True, "just_completed": just, "partner": p})
            if path == "/ledger/chase":
                return self._send(200, {"ok": True, "chases": desk.ledger.due_chases()})
            if path == "/ledger/release":
                desk.ledger.release(int(b["partner_id"]), b.get("kind", "reminder"))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def _api(self, path: str, b: dict):
            if path == "/api/signup":      # the page's form: forward to the workflow engine like any website would
                key = b.get("signup_id") or str(uuid.uuid4())
                code, out = desk.forward("/webhook/partner-signup", b, {"Idempotency-Key": key})
                return self._send(code, out)
            if path == "/api/item":
                code, out = desk.forward("/webhook/partner-item", b, {})
                return self._send(code, out)
            if path == "/api/chat":
                sid = (b.get("session") or "")[:64] or str(uuid.uuid4())
                with desk._lock:
                    sess = desk.sessions.setdefault(sid, ChatSession(desk.router, sid, {"name": b.get("name", ""), "email": b.get("email", "")}))
                reply = sess.say(str(b.get("message", ""))[:2000])
                return self._send(200, {"session": sid, "text": reply.text, "route": reply.route, "handed_off": reply.handed_off,
                                        "reason": reply.reason, "kinds": reply.kinds, "masked_input": reply.masked_input,
                                        "summary": (reply.handoff or {}).get("summary") if reply.handed_off else None})
            if path == "/api/bookings":
                return self._send(200, desk.reminders.book(b.get("name", ""), b.get("email", ""), b.get("local_time", ""), b.get("tz", "UTC"), b.get("title") or "Consultation"))
            m = re.fullmatch(r"/api/bookings/(\d+)/(reschedule|cancel)", path)
            if m:
                bid = int(m.group(1))
                return self._send(200, desk.reminders.reschedule(bid, b.get("local_time", "")) if m.group(2) == "reschedule" else desk.reminders.cancel(bid))
            if path == "/api/clock":
                desk.clock.offset += timedelta(minutes=float(b.get("advance_minutes", 0)))
                return self._send(200, {"now": desk.clock.now().isoformat(timespec="seconds")})
            if path == "/api/tick":
                return self._send(200, {"sent": desk.reminders.tick()})
            self._send(404, {"error": "not found"})

    return Handler


def main() -> None:
    desk = Desk()
    threading.Thread(target=desk.tick_loop, daemon=True).start()
    port = int(os.environ.get("DESK_PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(desk))
    print(f"desk listening on :{port}; classifier: {desk.classifier}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
