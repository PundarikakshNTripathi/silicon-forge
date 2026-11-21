# SPDX-License-Identifier: AGPL-3.0-or-later
"""Knowledge packs with chunked BM25 retrieval.

A pack is a Markdown file; each `## ` section is one chunk, and chunks are what get retrieved. Retrieving whole files (the first
version) meant a three-document corpus where the scores carried almost no information, and an empty match fell back to
"return the first packs anyway", which put unrelated text into prompts. Now:

* the unit of retrieval is a section, so a lint warning code finds the paragraph about that warning, not a whole file;
* section headings count three times, because they are what a section is about;
* there is a relevance floor: a query that matches nothing well returns nothing, and the caller adds no context;
* built-in packs ship with the package; your own packs live in `<data dir>/skills/` and override a built-in of the same name.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from services.orchestrator.paths import data_root

K1, B = 1.4, 0.75
HEADING_WEIGHT = 3
#: Relevance floors, set from measured scores (71 sections): irrelevant queries, including ones made of words that happen to
#: occur in a pack, topped out near 4; genuine diagnostics and topic queries scored 13 or more. A long query must clear
#: MIN_SCORE. A short one (up to SHORT_QUERY words) cannot score that high however exact it is, so it must instead name a word
#: that is in the section's heading and clear SHORT_MIN_SCORE.
MIN_SCORE = 8.0
SHORT_MIN_SCORE = 3.0
SHORT_QUERY = 3


@dataclass
class SkillChunk:
    pack: str
    heading: str
    content: str
    tokens: list[str] = field(default_factory=list, repr=False)

    @property
    def name(self) -> str:
        return f"{self.pack}: {self.heading}" if self.heading else self.pack


@dataclass
class SkillPack:
    name: str
    content: str
    tokens: list[str]
    source: str = "builtin"  # builtin | user
    chunks: list[SkillChunk] = field(default_factory=list, repr=False)


def tokenize(text: str) -> list[str]:
    """Lower-cased words, with identifiers also split at underscores so `FP_CORE_UTIL` matches `core` and `util`, and
    `%Warning-WIDTH` matches `width`."""
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9_]+", text):
        low = tok.lower()
        out.append(low)
        if "_" in low:
            out.extend(p for p in low.split("_") if p)
    return out


def split_chunks(pack: str, text: str) -> list[SkillChunk]:
    """Sections of a pack. Text before the first `## ` heading is its own chunk named after the pack's `# ` title."""
    title = ""
    m = re.match(r"\s*#\s+(.+)", text)
    if m:
        title = m.group(1).strip()
    parts = re.split(r"(?m)^##\s+", text)
    chunks: list[SkillChunk] = []
    lead = parts[0].strip()
    if lead and len(lead.split()) >= 8:
        chunks.append(SkillChunk(pack, title, lead))
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        content = f"## {head.strip()}\n{body.strip()}".strip()
        if body.strip():
            chunks.append(SkillChunk(pack, head.strip(), content))
    for c in chunks:
        c.tokens = tokenize(c.content) + tokenize(c.heading) * (HEADING_WEIGHT - 1) + tokenize(pack)
    return chunks


class SkillRegistry:
    def __init__(self, packs_dir: Path | None = None, user_dir: Path | None = None) -> None:
        self.packs_dir = packs_dir or (Path(__file__).resolve().parent / "packs")
        self.user_dir = user_dir if user_dir is not None else data_root() / "skills"
        self.packs: list[SkillPack] = []
        self.chunks: list[SkillChunk] = []
        self._load()
        self._df: dict[str, int] = {}
        for c in self.chunks:
            for tok in set(c.tokens):
                self._df[tok] = self._df.get(tok, 0) + 1
        self._avg = sum(len(c.tokens) for c in self.chunks) / max(1, len(self.chunks))

    # ------------------------------------------------------------------------------------------- loading

    def _load(self) -> None:
        found: dict[str, tuple[str, str]] = {}
        for folder, source in ((self.packs_dir, "builtin"), (self.user_dir, "user")):
            if folder and folder.is_dir():
                for f in sorted(folder.glob("*.md")):
                    found[f.stem] = (f.read_text(encoding="utf-8"), source)  # a user pack replaces a built-in of the same name
        for name in sorted(found):
            text, source = found[name]
            chunks = split_chunks(name, text)
            self.packs.append(SkillPack(name, text, tokenize(text), source, chunks))
            self.chunks.extend(chunks)

    # ------------------------------------------------------------------------------------------ retrieval

    def score(self, query_tokens: set[str], chunk: SkillChunk) -> float:
        tf_map: dict[str, int] = {}
        for t in chunk.tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        n = len(self.chunks)
        total = 0.0
        for qt in query_tokens:
            tf = tf_map.get(qt)
            if not tf:
                continue
            df = self._df.get(qt, 0)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            total += idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * len(chunk.tokens) / max(1.0, self._avg)))
        return total

    def retrieve(self, query: str, top_k: int = 2, min_score: float | None = None) -> list[SkillChunk]:
        """The best chunks for a query, at most `top_k`, only those that are relevant (see the floors above). May be empty."""
        if not self.chunks:
            return []
        q = set(tokenize(query))
        short = len(q) <= SHORT_QUERY
        scored = sorted(((self.score(q, c), c) for c in self.chunks), key=lambda sc: sc[0], reverse=True)
        out = []
        for s, c in scored[:top_k]:
            if short:
                relevant = s >= (SHORT_MIN_SCORE if min_score is None else min_score) and bool(q & set(tokenize(c.heading)))
            else:
                relevant = s >= (MIN_SCORE if min_score is None else min_score)
            if relevant:
                out.append(c)
        return out

    def format_context(self, query: str, top_k: int = 2, min_score: float | None = None) -> str:
        hits = self.retrieve(query, top_k, min_score)
        return "\n\n".join(f"### {c.name}\n{c.content}" for c in hits)
