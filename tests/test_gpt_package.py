"""The custom GPT package: limits, loading, prompt assembly and the case scorer. Both directions."""
import json
import shutil
from pathlib import Path

import pytest

from stepchaser import gpt
from stepchaser.evalkit import score_case
from stepchaser.llm import ScriptedLLM

ROOT = Path(__file__).resolve().parents[1]


def test_the_shipped_package_loads_and_is_inside_the_builder_limits():
    pkg = gpt.load(ROOT / "gpt")
    assert len(pkg.instructions) <= gpt.MAX_INSTRUCTIONS_CHARS and 1 <= len(pkg.starters) <= 4 and len(pkg.knowledge) <= gpt.MAX_FILES
    assert "services.md" in pkg.knowledge and "partner_onboarding_sop.md" in pkg.knowledge


def test_system_prompt_carries_instructions_and_every_knowledge_file():
    pkg = gpt.load(ROOT / "gpt")
    prompt = pkg.system_prompt()
    assert pkg.instructions.strip() in prompt
    assert all(f'<knowledge file="{name}">' in prompt for name in pkg.knowledge)


def copy_package(tmp_path):
    dst = tmp_path / "pkg"
    shutil.copytree(ROOT / "gpt", dst, ignore=shutil.ignore_patterns("eval"))
    shutil.copytree(ROOT / "kb", tmp_path / "kb")
    return dst


@pytest.mark.parametrize("mutate,message", [
    (lambda d: (d / "instructions.md").write_text("x" * 8001, encoding="utf-8"), "8001 characters"),
    (lambda d: (d / "instructions.md").write_text("  ", encoding="utf-8"), "instructions are empty"),
    (lambda d: (d / "starters.json").write_text(json.dumps(["a", "b", "c", "d", "e"]), encoding="utf-8"), "5 conversation starters"),
    (lambda d: (d / "starters.json").write_text("[]", encoding="utf-8"), "0 conversation starters"),
])
def test_a_package_outside_the_limits_is_refused(tmp_path, mutate, message):
    d = copy_package(tmp_path)
    mutate(d)
    with pytest.raises(gpt.PackageError, match=message):
        gpt.load(d)


def test_a_missing_knowledge_file_is_refused(tmp_path):
    d = copy_package(tmp_path)
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    cfg["knowledge"].append("knowledge/nope.md")
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(gpt.PackageError, match="nope.md"):
        gpt.load(d)


def test_too_many_files_and_long_description_are_refused(tmp_path):
    d = copy_package(tmp_path)
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    cfg["description"] = "d" * 301
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(gpt.PackageError, match="description"):
        gpt.load(d)
    pkg = gpt.load(ROOT / "gpt")
    pkg.knowledge = {f"f{i}.md": "x" for i in range(21)}
    with pytest.raises(gpt.PackageError, match="21 knowledge files"):
        gpt.validate(pkg)


def test_assistant_sends_instructions_and_knowledge_as_the_system_prompt():
    pkg = gpt.load(ROOT / "gpt")
    llm = ScriptedLLM(["ok"])
    out = gpt.Assistant(pkg, llm).ask("hello", history=[("user", "hi"), ("assistant", "hey")])
    system, user = llm.calls[0]
    assert out == "ok" and "Cedar Lane Team Assistant" in system and "KB-GUARD-7F3A" in system
    assert user.endswith("user: hello") and "assistant: hey" in user


CASE = {"all": ["welcome", "5|five"], "any": [["email|message"], ["checklist"]], "none": ["guarantee"]}


@pytest.mark.parametrize("answer,passed", [
    ("A welcome email lists the five items on the checklist.", True),
    ("A welcome message and a 5 item checklist.", True),
    ("A welcome email lists the items.", False),                              # no checklist group hit
    ("We guarantee a welcome email, five items, checklist.", False),          # forbidden word
    ("Hello there.", False),
])
def test_score_case_both_directions(answer, passed):
    assert score_case(answer, CASE)["passed"] is passed


def test_every_shipped_eval_case_is_well_formed_and_scoreable():
    cases = [json.loads(x) for x in (ROOT / "gpt" / "eval" / "cases.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(cases) >= 30 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        score_case("anything", c)                                             # every regex compiles
        assert c["ask"] and c["kind"]


def test_score_case_reads_through_typographic_variants():
    case = {"all": ["2 hours", "can't"], "none": []}
    assert score_case("I can’t, only the 2 hours one", case)["passed"]


def test_forbidden_patterns_judge_the_draft_not_the_left_out_line():
    case = {"all": ["subject"], "none": ["within one hour"]}
    draft = "Subject: Agreement\n\nPlease send it.\n\nLeft out: the promise of within one hour, not in the guides."
    assert score_case(draft, case)["passed"]
    assert not score_case("Subject: Agreement\n\nWe will do it within one hour.", case)["passed"]
