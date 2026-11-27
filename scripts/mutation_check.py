"""Break one safeguard at a time in the real source and require the tests to fail.

Each mutant is an exact text replacement in one file. The file is restored byte for byte afterwards, even if the run is
interrupted. A mutant that the tests do not catch is a missing test, and the script exits 1.

  python scripts/mutation_check.py          # writes docs/proof/mutation_check_last_run.txt
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ["tests/test_ledger.py", "tests/test_properties.py"]
REM = ["tests/test_reminders.py", "tests/test_properties.py"]
ROUTER = ["tests/test_router.py", "tests/test_mask.py"]
DESK = ["tests/test_desk.py"]

# (name, file, old text, new text, tests that must fail)
MUTANTS = [
    ("ledger: duplicate signup check removed (dedupe)", "stepchaser/ledger.py", "            if row is not None:\n                self._event(\"duplicate-signup\"", "            if False:\n                self._event(\"duplicate-signup\"", LEDGER),
    ("ledger: a repeated checklist item counts again", "stepchaser/ledger.py", "if cur.rowcount == 0:", "if False:", LEDGER),
    ("ledger: finished partners are still chased", "stepchaser/ledger.py", "WHERE completed_at IS NULL AND stalled_at IS NULL", "WHERE stalled_at IS NULL", LEDGER),
    ("ledger: stalled partners are chased again", "stepchaser/ledger.py", "WHERE completed_at IS NULL AND stalled_at IS NULL", "WHERE completed_at IS NULL", LEDGER),
    ("ledger: reminder cap not enforced", "stepchaser/ledger.py", "if row[\"reminder_count\"] >= self.max_reminders:", "if False:", LEDGER),
    ("ledger: reminder interval ignored", "stepchaser/ledger.py", "if last + self.remind_every > now:", "if False:", LEDGER),
    ("ledger: a failed send does not release its claim", "stepchaser/ledger.py", "reminder_count=reminder_count-1", "reminder_count=reminder_count", LEDGER),
    ("ledger: reminder lists every item, not what is missing", "stepchaser/ledger.py", "                missing = self._missing(pid)\n                if row[\"reminder_count\"]", "                missing = list(REQUIRED_ITEMS)\n                if row[\"reminder_count\"]", LEDGER),
    ("reminders: cancelled bookings are reminded", "stepchaser/reminders.py", "WHERE status='active'", "WHERE 1=1", REM),
    ("reminders: sent reminders are not remembered", "stepchaser/reminders.py", "                self._record(b, off, \"sent\", now)", "                pass", REM),
    ("reminders: reminders for a started appointment", "stepchaser/reminders.py", "if start <= now:\n                    continue", "if False:\n                    continue", REM),
    ("reminders: a reminder that passed before booking is sent", "stepchaser/reminders.py", "if moment < created:", "if False:", REM),
    ("reminders: earlier offsets not superseded", "stepchaser/reminders.py", "for off, moment in due[:-1]:", "for off, moment in []:", REM),
    ("reminders: time zone ignored", "stepchaser/reminders.py", "first = naive.replace(tzinfo=zone, fold=0)", "first = naive.replace(tzinfo=timezone.utc, fold=0)", REM),
    ("reminders: reschedule keeps the old start", "stepchaser/reminders.py", "UPDATE bookings SET start_utc=?, version=version+1", "UPDATE bookings SET start_utc=start_utc, version=version+1", REM),
    ("reminders: a second cancel sends a second notice", "stepchaser/reminders.py", "if b[\"status\"] == \"cancelled\":\n                return b  # a second cancel", "if False:\n                return b  # a second cancel", REM),
    ("reminders: a failed send is marked as sent", "stepchaser/reminders.py", "                if not ok:  # the mail server refused it", "                if False:  # the mail server refused it", REM),
    ("router: masking removed", "stepchaser/router.py", "        masked = mask(message)\n        turns =", "        masked = type('M', (), {'text': message, 'kinds': {}, 'sensitive': False})()\n        turns =", ROUTER),
    ("router: the floor before the model removed", "stepchaser/router.py", "floor = self._floor(masked)", "floor = None", ROUTER),
    ("router: grounding guard off", "stepchaser/router.py", "elif not answer or not self._grounded(answer, text):", "elif not answer:", ROUTER),
    ("router: a price or percentage may come from the visitor", "stepchaser/router.py", "(money_or_percent or n not in said)", "(n not in said)", ROUTER),
    ("router: promise guard off", "stepchaser/router.py", "elif PROMISE.search(answer):", "elif False:", ROUTER),
    ("router: confidence threshold ignored", "stepchaser/router.py", "elif confidence < self.threshold:", "elif False:", ROUTER),
    ("router: the model's needs_human ignored", "stepchaser/router.py", "elif needs_human:", "elif False:", ROUTER),
    ("router: handoff is never delivered", "stepchaser/router.py", "                sink.deliver(reply.handoff)", "                pass", ROUTER),
    ("router: unmasked text joins a handoff after it", "stepchaser/router.py", "self.handoff[\"transcript\"].append({\"role\": \"visitor\", \"text\": masked.text})", "self.handoff[\"transcript\"].append({\"role\": \"visitor\", \"text\": message})", ROUTER),
    ("mask: social security pattern removed", "stepchaser/mask.py", "    out = SSN.sub(lambda m: bump(\"SSN\"), out)\n", "", ROUTER),
    ("mask: card check ignores Luhn", "stepchaser/mask.py", "if _luhn(re.sub(r\"\\D\", \"\", m.group(0))) and len", "if True and len", ROUTER),
    ("desk: ledger key check removed", "stepchaser/desk.py", "if self.headers.get(\"X-Desk-Key\") != desk.key:", "if False:", DESK),
]


def main() -> int:
    caught, lines = 0, []
    for name, rel, old, new, tests in MUTANTS:
        path = ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if "\r\n" in text:  # a file written on Windows keeps CRLF, and the snippets above use LF
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        if text.count(old) != 1:
            lines.append(f"ERROR   {name}: the text to break was found {text.count(old)} times")
            continue
        try:
            path.write_bytes(text.replace(old, new).encode("utf-8"))
            res = subprocess.run([sys.executable, "-m", "pytest", *tests, "-x", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, timeout=300)
        finally:
            path.write_bytes(original)
        died = res.returncode != 0
        caught += died
        lines.append(f"{'caught ' if died else 'MISSED '} {name}")
        print(lines[-1], flush=True)
    summary = f"{caught} of {len(MUTANTS)} mutants caught"
    lines.append(summary)
    out = ROOT / "docs" / "proof" / "mutation_check_last_run.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary)
    return 0 if caught == len(MUTANTS) and not any(x.startswith("ERROR") for x in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
