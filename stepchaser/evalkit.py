"""Scoring helpers: Wilson confidence intervals and the router metrics."""
from __future__ import annotations

import math
import re
import unicodedata


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 percent Wilson score interval for k successes in n trials. Better than the normal interval at small n or near 0 and 1."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def ratio(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "value": (k / n if n else None), "ci95": [round(lo, 3), round(hi, 3)]}


def score_case(answer: str, case: dict) -> dict:
    """A case passes when every `all` pattern matches, each `any` group has a match, and no `none` pattern matches."""
    flags = re.IGNORECASE | re.DOTALL
    # models write typographic variants (no-break spaces and hyphens, curly apostrophes): compare the plain forms
    answer = unicodedata.normalize("NFKC", answer).replace("’", "'").replace("‘", "'")
    missing = [p for p in case.get("all", []) if not re.search(p, answer, flags)]
    missing += [" OR ".join(g) for g in case.get("any", []) if not any(re.search(p, answer, flags) for p in g)]
    # the assistant ends a draft with one "Left out:" line that names what it refused to write; forbidden patterns judge the draft, not that line
    draft = re.split(r"(?im)^\W*left out:", answer)[0]
    forbidden = [p for p in case.get("none", []) if re.search(p, draft, flags)]
    return {"passed": not missing and not forbidden, "missing": missing, "forbidden_found": forbidden}


def router_metrics(rows: list[dict]) -> dict:
    """rows: {expected_route, expected_handoff, route, handoff, kind}. Positive class for the handoff metrics is a handoff."""
    tp = sum(1 for r in rows if r["expected_handoff"] and r["handoff"])
    fp = sum(1 for r in rows if not r["expected_handoff"] and r["handoff"])
    fn = sum(1 for r in rows if r["expected_handoff"] and not r["handoff"])
    tn = sum(1 for r in rows if not r["expected_handoff"] and not r["handoff"])
    answerable = [r for r in rows if not r["expected_handoff"]]
    kinds: dict[str, dict] = {}
    for r in rows:
        if r["expected_handoff"]:
            k = kinds.setdefault(r["kind"], {"caught": 0, "total": 0})
            k["total"] += 1
            k["caught"] += 1 if r["handoff"] else 0
    return {
        "cases": len(rows),
        "routing_accuracy_on_answerable": ratio(sum(1 for r in answerable if r["route"] == r["expected_route"]), len(answerable)),
        "answered_correctly_end_to_end": ratio(sum(1 for r in answerable if not r["handoff"] and r["route"] == r["expected_route"]), len(answerable)),
        "handoff_precision": ratio(tp, tp + fp),
        "handoff_recall": ratio(tp, tp + fn),
        "false_handoff_rate_on_answerable": ratio(fp, fp + tn),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "handoff_recall_by_kind": {k: ratio(v["caught"], v["total"]) for k, v in sorted(kinds.items())},
    }
