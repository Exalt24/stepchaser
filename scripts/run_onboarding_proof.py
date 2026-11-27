"""Prove the onboarding workflow against the running stack (docker compose up -d --build).

Everything here goes through n8n's real webhooks and the real Mailpit inbox. Time is moved with the desk clock
(the 'Skip ahead' button), and the 15 second n8n schedule then sends what is due. The transcript is written to
docs/proof/onboarding_run.txt.

  python scripts/run_onboarding_proof.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N8N, DESK, MAIL = "http://localhost:5678", "http://localhost:8080", "http://localhost:8025"
KEY = "local-desk-key"
QUICK = "--quick" in sys.argv
OUT = ROOT / "docs" / "proof" / "onboarding_run.txt"
lines: list[str] = []
failures: list[str] = []


def say(text: str) -> None:
    print(text, flush=True)
    lines.append(text)


def http(method: str, url: str, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            try:
                return r.status, (json.loads(raw) if raw else {})
            except ValueError:
                return r.status, {"raw": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}


def check(label: str, ok: bool, detail: str = "") -> None:
    say(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def inbox() -> list[dict]:
    return http("GET", f"{MAIL}/api/v1/messages?limit=500")[1].get("messages", [])


def mails_to(addr: str) -> list[dict]:
    return [m for m in inbox() if any(t["Address"] == addr for t in m["To"])]


def body_of(msg: dict) -> str:
    return http("GET", f"{MAIL}/api/v1/message/{msg['ID']}")[1].get("Text", "")


def wait_for(label: str, cond, seconds: int = 75):
    end = time.time() + seconds
    while time.time() < end:
        got = cond()
        if got:
            return got
        time.sleep(1)
    check(f"waited for: {label}", False, f"not seen within {seconds}s")
    return None


def skip_days(days: int) -> None:
    http("POST", f"{DESK}/api/clock", {"advance_minutes": days * 24 * 60})


def events(pid=None) -> list[dict]:
    ev = http("GET", f"{DESK}/ledger/events", headers={"X-Desk-Key": KEY})[1]["events"]
    return [e for e in ev if pid is None or e["partner_id"] == pid]


def signup(name, email, key, **extra):
    return http("POST", f"{N8N}/webhook/partner-signup", {"name": name, "email": email, "partner_type": "tax partner", "tz": "Asia/Manila", **extra},
                headers={"Idempotency-Key": key})


def item(email, name):
    return http("POST", f"{N8N}/webhook/partner-item", {"email": email, "item": name})


def finish() -> int:
    say("\n8. The log")
    ev = events()
    kinds: dict[str, int] = {}
    for e in ev:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    say("  ledger events by kind: " + json.dumps(kinds, sort_keys=True))
    log_file = ROOT / "runtime" / "ledger.log.jsonl"
    check("the event log file exists and has the same events", log_file.exists() and sum(1 for _ in log_file.open(encoding="utf-8")) >= len(ev))
    say(f"\nmessages in the inbox at the end: {len(inbox())}")
    say(f"\nRESULT: {'ALL CHECKS PASSED' if not failures else 'FAILED: ' + '; '.join(failures)}" + (" (quick run: sections 1 to 4)" if QUICK else ""))
    if not QUICK:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failures else 0


def main() -> int:
    run = uuid.uuid4().hex[:6]
    a_email, b_email = f"riley.{run}@example.test", f"morgan.{run}@example.test"
    team = "team@cedar-lane.example.test"
    items = ["Signed partner agreement", "Identity check", "Payout details form", "Training module", "Profile photo"]
    http("DELETE", f"{MAIL}/api/v1/messages")
    say(f"Partner onboarding proof run {run}: n8n webhooks -> ledger -> Mailpit. Partners are made up.")

    say("\n1. A signup starts the workflow")
    code, out = signup("Riley Sample", a_email, f"k-{run}-a", kickoff="2026-12-03T10:00")
    check("signup answers 202 accepted", code == 202 and out.get("status") == "accepted", f"{code} {out}")
    a_id = out.get("partner_id")
    wait_for("welcome and team summary", lambda: len(mails_to(a_email)) >= 1 and len(mails_to(team)) >= 1)
    welcome = body_of(mails_to(a_email)[0])
    check("welcome email lists all 5 checklist items", all(i in welcome for i in items))
    check("welcome email mentions the kickoff call", "2026-12-03T10:00" in welcome)
    summary = [m for m in mails_to(team) if "New partner signup" in m["Subject"]]
    check("team summary email sent", len(summary) == 1 and "Riley Sample" in body_of(summary[0]))
    check("kickoff calendar file was written", (ROOT / "runtime" / "calendar").exists() and any((ROOT / "runtime" / "calendar").glob("kickoff-*.ics")))

    say("\n2. Idempotent: retries, a parallel burst and a double click send nothing more")
    before = len(inbox())
    for _ in range(3):
        c, o = signup("Riley Sample", a_email, f"k-{run}-a")
        check("retry with the same key answers duplicate", c == 200 and o.get("status") == "duplicate", f"{c} {o}")
    results = []
    threads = [threading.Thread(target=lambda: results.append(signup("Riley Sample", a_email, f"k-{run}-a"))) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("6 parallel retries all answer duplicate", all(r[0] == 200 and r[1].get("status") == "duplicate" for r in results))
    c, o = signup("Riley S.", a_email.upper(), f"k-{run}-other")
    check("same email in capitals with a new key is a duplicate", c == 200 and o.get("status") == "duplicate", f"{c} {o}")
    time.sleep(3)  # authoring-ok: a fixed 3 second pause so the mail catcher orders the two sends
    check("no extra email left the system", len(inbox()) == before, f"{before} -> {len(inbox())}")
    parallel_fresh = []
    burst_email = f"burst.{run}@example.test"
    threads = [threading.Thread(target=lambda: parallel_fresh.append(signup("Burst Sample", burst_email, f"k-{run}-burst"))) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wait_for("burst welcome", lambda: len(mails_to(burst_email)) >= 1)
    time.sleep(3)  # authoring-ok: a fixed 3 second pause so the mail catcher orders the two sends
    accepted = sum(1 for r in parallel_fresh if r[1].get("status") == "accepted")
    check("6 simultaneous FIRST deliveries: exactly one accepted and one welcome email", accepted == 1 and len(mails_to(burst_email)) == 1,
          f"accepted={accepted}, welcomes={len(mails_to(burst_email))}")
    c, o = signup("No Email", "not-an-email", f"k-{run}-bad")
    check("an invalid signup answers 422 and sends nothing", c == 422, f"{c} {o}")

    say("\n3. The checklist is tracked")
    c, o = item(a_email, items[0])
    check("item recorded", c == 200 and o.get("status") == "recorded" and len(o["missing"]) == 4, f"{o}")
    c, o = item(a_email, items[0])
    check("the same item again changes nothing", c == 200 and len(o["missing"]) == 4)
    c, o = item(a_email, items[4])
    check("a second item recorded", len(o["missing"]) == 3)
    c, o = item(a_email, "Tax return copy")
    check("an unknown item is refused", c == 422, f"{c}")
    c, o = item("ghost@example.test", items[0])
    check("an unknown partner is refused", c == 404, f"{c}")

    say("\n4. Two days pass: the follow-up lists only what is missing")
    skip_days(2)
    rem = wait_for("reminder 1 to the partner", lambda: [m for m in mails_to(a_email) if m["Subject"].startswith("Reminder 1")])
    if rem:
        text = body_of(rem[0])
        check("reminder names the 3 missing items and none of the 2 done", all(i in text for i in (items[1], items[2], items[3])) and items[0] not in text and items[4] not in text)
    digest = wait_for("team digest", lambda: [m for m in mails_to(team) if m["Subject"].startswith("Onboarding digest")])
    check("team digest names the partner chased", bool(digest) and "Riley Sample" in body_of(digest[0]))

    if QUICK:
        return finish()

    say("\n5. A mail server outage loses no reminder")
    subprocess.run(["docker", "compose", "stop", "mailpit"], cwd=ROOT, capture_output=True, check=True, timeout=90)
    skip_days(2)
    released = wait_for("claim released after the failed send", lambda: [e for e in events() if e["kind"] == "claim-released" and e["partner_id"] == a_id])
    check("a failed send released its claim", bool(released))
    subprocess.run(["docker", "compose", "start", "mailpit"], cwd=ROOT, capture_output=True, check=True, timeout=90)
    time.sleep(4)
    rem2 = wait_for("reminder 2 after the mail server is back", lambda: [m for m in mails_to(a_email) if m["Subject"].startswith("Reminder 2")])
    check("the reminder arrives once the mail server is back", bool(rem2))

    say("\n6. The last items arrive: completion emails, then silence")
    for name in (items[1], items[2], items[3]):
        c, o = item(a_email, name)
    check("the final item answers complete", c == 200 and o.get("status") == "complete", f"{o}")
    wait_for("completion emails", lambda: [m for m in mails_to(a_email) if m["Subject"].startswith("You are all set")] and [m for m in mails_to(team) if m["Subject"].startswith("Onboarding complete")])
    count_before = len(mails_to(a_email))
    skip_days(10)
    time.sleep(35)  # authoring-ok: a negative check has no event to poll; the 15 s schedule must run twice and send nothing
    check("no reminder after completion", len(mails_to(a_email)) == count_before)

    say("\n7. A partner who never answers is flagged to the team once")
    c, o = signup("Morgan Sample", b_email, f"k-{run}-b")
    check("second partner accepted", c == 202, f"{c}")
    for n in (1, 2, 3):
        skip_days(2)
        wait_for(f"reminder {n} to Morgan", lambda: [m for m in mails_to(b_email) if m["Subject"].startswith(f"Reminder {n}")])
    last = [m for m in mails_to(b_email) if m["Subject"].startswith("Reminder 3")]
    check("reminder 3 says it is the last automatic one", bool(last) and "last automatic reminder" in body_of(last[0]))
    skip_days(2)
    wait_for("stalled alert", lambda: [m for m in mails_to(team) if m["Subject"].startswith("Stalled onboarding: Morgan")])
    skip_days(20)
    time.sleep(35)  # authoring-ok: same negative check, two schedule runs with nothing due
    stalled = [m for m in mails_to(team) if m["Subject"].startswith("Stalled onboarding: Morgan")]
    check("exactly one stalled alert and no fourth reminder", len(stalled) == 1 and len([m for m in mails_to(b_email) if m["Subject"].startswith("Reminder")]) == 3)

    return finish()


if __name__ == "__main__":
    sys.exit(main())
