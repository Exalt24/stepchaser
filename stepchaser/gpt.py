"""A custom-GPT-style assistant package, runnable against any OpenAI-compatible endpoint.

A package is what you paste and upload into ChatGPT's GPT builder: an instructions file, up to four conversation
starters and knowledge files. This module loads the same files, checks them against the builder's limits, and runs
them as a system prompt so the package can be evaluated before it is uploaded. ChatGPT retrieves from uploaded files;
here the whole knowledge set is small enough to place in the prompt, so scores describe the files and the
instructions, not ChatGPT's retrieval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAX_INSTRUCTIONS_CHARS = 8000   # the GPT builder's instructions box limit
MAX_STARTERS = 4
MAX_FILES = 20
MAX_NAME = 100
MAX_DESCRIPTION = 300


class PackageError(ValueError):
    pass


@dataclass
class Package:
    name: str
    description: str
    instructions: str
    starters: list[str]
    knowledge: dict[str, str]

    def system_prompt(self) -> str:
        blocks = "\n\n".join(f"<knowledge file=\"{name}\">\n{text.strip()}\n</knowledge>" for name, text in self.knowledge.items())
        return f"{self.instructions.strip()}\n\nKNOWLEDGE FILES (answer from these only)\n\n{blocks}"


def load(directory: str | Path) -> Package:
    root = Path(directory)
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    instructions = (root / cfg["instructions"]).read_text(encoding="utf-8")
    starters = json.loads((root / cfg["conversation_starters"]).read_text(encoding="utf-8"))
    knowledge = {}
    for rel in cfg["knowledge"]:
        path = (root / rel).resolve()
        if not path.is_file():
            raise PackageError(f"knowledge file listed in config.json is missing: {rel}")
        knowledge[path.name] = path.read_text(encoding="utf-8")
    pkg = Package(cfg["name"], cfg["description"], instructions, starters, knowledge)
    validate(pkg)
    return pkg


def validate(pkg: Package) -> None:
    problems = []
    if not pkg.name.strip() or len(pkg.name) > MAX_NAME:
        problems.append(f"name must be 1 to {MAX_NAME} characters")
    if not pkg.description.strip() or len(pkg.description) > MAX_DESCRIPTION:
        problems.append(f"description must be 1 to {MAX_DESCRIPTION} characters")
    if not pkg.instructions.strip():
        problems.append("instructions are empty")
    if len(pkg.instructions) > MAX_INSTRUCTIONS_CHARS:
        problems.append(f"instructions are {len(pkg.instructions)} characters, the builder takes {MAX_INSTRUCTIONS_CHARS}")
    if not 1 <= len(pkg.starters) <= MAX_STARTERS:
        problems.append(f"{len(pkg.starters)} conversation starters, the builder takes 1 to {MAX_STARTERS}")
    if len(pkg.knowledge) > MAX_FILES:
        problems.append(f"{len(pkg.knowledge)} knowledge files, the builder takes {MAX_FILES}")
    if not pkg.knowledge:
        problems.append("no knowledge files")
    if problems:
        raise PackageError("; ".join(problems))


class Assistant:
    def __init__(self, package: Package, llm):
        self.package, self.llm = package, llm
        self.system = package.system_prompt()

    def ask(self, message: str, history: list[tuple[str, str]] | None = None) -> str:
        convo = "".join(f"{role}: {text}\n" for role, text in (history or []))
        return self.llm.complete(self.system, f"{convo}user: {message}", max_tokens=450)
