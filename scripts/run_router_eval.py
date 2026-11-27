"""Score the lead router on a labelled set and write the raw results.

  python scripts/run_router_eval.py --set test --llm real      # a real model call per case (needs LLM_API_KEY or GROQ_API_KEY)
  python scripts/run_router_eval.py --set test --llm standin   # the keyword stand-in, labelled as such in the output

Outputs docs/proof/router_<set>_<llm>.json (every case, its decision and its reason) and a one-page .txt summary.
Also checks that no raw sensitive literal in the case messages reached a prompt or the audit log.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stepchaser import kb  # noqa: E402
from stepchaser.clock import SystemClock  # noqa: E402
from stepchaser.evalkit import router_metrics  # noqa: E402
from stepchaser.llm import OpenAICompatLLM, ScriptedLLM  # noqa: E402
from stepchaser.router import AuditLog, KeywordStandIn, LeadRouter  # noqa: E402

SECRET_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b|\b\d{12}\b|\b\d{10}\b|\b\d{2}-\d{7}\b|(?:\d{4} ){3}\d{4}|\b04/24/1985\b|\b021000021\b")


class Spy:
    """Wraps a model client and keeps every prompt it was shown."""

    def __init__(self, inner):
        self.inner, self.seen, self.name = inner, [], getattr(inner, "name", "model")

    def complete(self, system, user, **kw):
        self.seen.append(system + "\n" + user)
        return self.inner.complete(system, user, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["dev", "test"], default="test")
    ap.add_argument("--llm", choices=["real", "standin"], default="real")
    ap.add_argument("--out", default=str(ROOT / "docs" / "proof"))
    args = ap.parse_args()

    cases = [json.loads(line) for line in (ROOT / "eval" / f"router_{args.set}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    inner = OpenAICompatLLM.from_env() if args.llm == "real" else KeywordStandIn()
    llm = Spy(inner)
    label = f"real model {inner.model}" if args.llm == "real" else "keyword stand-in (NOT a model)"
    audit_path = Path(tempfile.mkdtemp()) / "audit.jsonl"
    router = LeadRouter(llm, kb.load(ROOT / "kb"), SystemClock(), audit=AuditLog(audit_path))
    rows = []
    for i, c in enumerate(cases, 1):
        reply = router.handle(f"eval-{c['id']}", c["m"])
        rows.append({"id": c["id"], "message": c["m"], "kind": c["kind"], "expected_route": c["route"], "expected_handoff": c["handoff"],
                     "route": reply.route, "handoff": reply.handed_off, "reason": reply.reason, "confidence": reply.confidence,
                     "model_called": reply.model_called})
        print(f"{i:3}/{len(cases)} {c['id']} expected={'HANDOFF' if c['handoff'] else c['route']:<22} got={'HANDOFF:' + reply.reason if reply.handed_off else reply.route}", flush=True)
    metrics = router_metrics(rows)
    leaked_prompts = sum(1 for p in llm.seen if SECRET_RE.search(p))
    leaked_log = len(SECRET_RE.findall(audit_path.read_text(encoding="utf-8")))
    sensitive_cases = sum(1 for c in cases if SECRET_RE.search(c["m"]))
    out = {"set": args.set, "llm": label, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "metrics": metrics,
           "masking": {"cases_with_sensitive_values": sensitive_cases, "prompts_containing_a_raw_value": leaked_prompts,
                       "audit_log_lines_containing_a_raw_value": leaked_log},
           "model_calls": sum(1 for r in rows if r["model_called"]),
           "model_errors": sum(1 for r in rows if r["reason"] == "model_error"), "cases": rows}
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"router_{args.set}_{args.llm}" + (("_" + inner.model.replace("/", "-")) if args.llm == "real" else "")
    (outdir / f"{stem}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    m = metrics

    def fmt(x):
        return f"{x['k']}/{x['n']} = {x['value']:.3f} (95% CI {x['ci95'][0]:.3f} to {x['ci95'][1]:.3f})"
    lines = [f"Lead router evaluation: set={args.set} cases={m['cases']} classifier={label}", f"run at {out['run_at']}",
             f"routing accuracy on answerable cases: {fmt(m['routing_accuracy_on_answerable'])}",
             f"answered correctly with no handoff:   {fmt(m['answered_correctly_end_to_end'])}",
             f"handoff precision:                    {fmt(m['handoff_precision'])}",
             f"handoff recall:                       {fmt(m['handoff_recall'])}",
             f"false handoffs on answerable cases:   {fmt(m['false_handoff_rate_on_answerable'])}",
             f"confusion: {m['confusion']}", "handoff recall by kind:"]
    lines += [f"  {k:<18} {fmt(v)}" for k, v in m["handoff_recall_by_kind"].items()]
    lines += [f"masking: {sensitive_cases} cases carried a sensitive value; prompts with a raw value: {leaked_prompts}; audit log lines with a raw value: {leaked_log}",
              f"model calls: {out['model_calls']} of {m['cases']} cases (the rest were handed off by the code floor before the model)"]
    lines.append(f"model errors (a call that failed and was handed off, counted as handoffs above): {out['model_errors']}")
    (outdir / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
