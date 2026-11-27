"""Check the custom GPT package against the builder's limits, then score it on gpt/eval/cases.jsonl.

  python scripts/run_gpt_eval.py                  # needs LLM_API_KEY or GROQ_API_KEY (any OpenAI compatible endpoint)
  python scripts/run_gpt_eval.py --validate-only

Each case has deterministic checks (patterns that must appear, groups of which one must appear, patterns that must
not appear), so the score does not depend on a second model judging the first. Writes docs/proof/gpt_eval.json and .txt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stepchaser import gpt  # noqa: E402
from stepchaser.evalkit import ratio, score_case  # noqa: E402
from stepchaser.llm import LLMError, OpenAICompatLLM  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "proof"))
    args = ap.parse_args()

    pkg = gpt.load(ROOT / "gpt")
    print(f"package ok: {pkg.name!r}, instructions {len(pkg.instructions)} of {gpt.MAX_INSTRUCTIONS_CHARS} characters, "
          f"{len(pkg.starters)} starters, {len(pkg.knowledge)} knowledge files")
    if args.validate_only:
        return 0
    llm = OpenAICompatLLM.from_env(json_mode=False)
    assistant = gpt.Assistant(pkg, llm)
    cases = [json.loads(x) for x in (ROOT / "gpt" / "eval" / "cases.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = []
    for i, c in enumerate(cases, 1):
        answer = ""
        for attempt in range(4):  # a rate limit is not a wrong answer: ask again, and report any case that never got one
            try:
                answer = assistant.ask(c["ask"])
                break
            except LLMError as exc:
                answer = f"[model error: {exc}]"
                time.sleep(12)
        time.sleep(1.5)  # fixed-wait-ok: pacing under the provider's tokens per minute limit
        res = score_case(answer, c)
        rows.append({"id": c["id"], "kind": c["kind"], "ask": c["ask"], "answer": answer, **res})
        print(f"{i:2}/{len(cases)} {c['id']} {c['kind']:<16} {'PASS' if res['passed'] else 'FAIL ' + str(res['missing'] + res['forbidden_found'])}", flush=True)
    by_kind: dict[str, list[bool]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r["passed"])
    splits = {c["id"]: c.get("split", "dev") for c in cases}
    for r in rows:
        r["split"] = splits[r["id"]]
    overall = ratio(sum(r["passed"] for r in rows), len(rows))
    by_split = {sp: ratio(sum(r["passed"] for r in rows if r["split"] == sp), sum(1 for r in rows if r["split"] == sp)) for sp in sorted(set(splits.values()))}
    out = {"model": llm.model, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "overall": overall, "by_split": by_split, "model_errors": sum(1 for r in rows if r["answer"].startswith("[model error")),
           "by_kind": {k: ratio(sum(v), len(v)) for k, v in sorted(by_kind.items())}, "cases": rows}
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "gpt_eval.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    def fmt(x):
        return f"{x['k']}/{x['n']} = {x['value']:.3f} (95% CI {x['ci95'][0]:.3f} to {x['ci95'][1]:.3f})"
    lines = [f"Custom GPT package evaluation: model={llm.model} cases={len(rows)} run at {out['run_at']}", f"overall: {fmt(overall)}"]
    lines += [f"  split {sp:<8} {fmt(v)}" for sp, v in by_split.items()]
    lines += [f"  {k:<16} {fmt(v)}" for k, v in out["by_kind"].items()]
    failed = [r for r in rows if not r["passed"]]
    lines.append(f"cases with no model answer (rate limit or outage, counted as failed): {out['model_errors']}")
    lines.append(f"failed cases: {', '.join(r['id'] for r in failed) or 'none'}")
    (outdir / "gpt_eval.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
