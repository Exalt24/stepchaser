"""A tiny knowledge base: markdown files split on '## route_id: Title' headings.

Small on purpose. The whole base is about 700 words, so the router puts all of it in the prompt, and the guard
checks every figure in an answer against it. A retrieval index would add a way to miss the right section.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Section:
    route: str
    title: str
    text: str


def load(directory: str | Path) -> list[Section]:
    sections: list[Section] = []
    for path in sorted(Path(directory).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for block in re.split(r"(?m)^## ", text)[1:]:
            head, _, body = block.partition("\n")
            route, _, title = head.partition(":")
            sections.append(Section(route.strip(), title.strip() or route.strip(), body.strip()))
    if not sections:
        raise FileNotFoundError(f"no knowledge sections found in {directory}")
    return sections


def routes(sections: list[Section]) -> list[str]:
    return sorted({s.route for s in sections})
