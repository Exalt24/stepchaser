"""Mask sensitive client fields before text reaches a model, a log or a handoff transcript.

Order matters: the specific patterns run first so the label says what was found, and the catch-all for any long
run of digits runs last so an unlabelled number is still hidden. Masking happens in code, before the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SSN_CTX = re.compile(r"(?i)\b(ssn|social security(?: number)?|ss#|itin)\b([^\d]{0,15})(\d[\d\- ]{7,12}\d)")
SSN = re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")
EIN = re.compile(r"(?<!\d)\d{2}-\d{7}(?!\d)")
BANK_CTX = re.compile(r"(?i)\b(account|acct|routing|aba|iban|bank)\b(?:\s*(?:number|no\.?|#))?([^\d\n]{0,15})(\d[\d\- ]{5,22}\d)")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
DOB_CTX = re.compile(r"(?i)\b(dob|date of birth|born(?: on)?)\b([^\d\n]{0,10})(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4})")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")
LONG_DIGITS = re.compile(r"(?<![\d])(?:\d[ \-]?){9,}(?![\d])")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass
class Masked:
    text: str
    kinds: dict[str, int] = field(default_factory=dict)

    @property
    def sensitive(self) -> bool:
        return bool(self.kinds)


def mask(text: str) -> Masked:
    kinds: dict[str, int] = {}

    def bump(kind: str) -> str:
        kinds[kind] = kinds.get(kind, 0) + 1
        return f"[{kind}]"

    out = text or ""
    out = SSN_CTX.sub(lambda m: f"{m.group(1)}{m.group(2)}{bump('SSN')}", out)
    out = SSN.sub(lambda m: bump("SSN"), out)
    out = EIN.sub(lambda m: bump("EIN"), out)
    out = BANK_CTX.sub(lambda m: f"{m.group(1)}{m.group(2)}{bump('BANK')}", out)
    out = IBAN.sub(lambda m: bump("BANK"), out)
    out = DOB_CTX.sub(lambda m: f"{m.group(1)}{m.group(2)}{bump('DOB')}", out)
    out = CARD.sub(lambda m: bump("CARD") if _luhn(re.sub(r"\D", "", m.group(0))) and len(re.sub(r"\D", "", m.group(0))) >= 13 else m.group(0), out)
    out = EMAIL.sub(lambda m: bump("EMAIL"), out)
    out = PHONE.sub(lambda m: bump("PHONE"), out)
    out = LONG_DIGITS.sub(lambda m: bump("NUMBER"), out)
    return Masked(out, kinds)
