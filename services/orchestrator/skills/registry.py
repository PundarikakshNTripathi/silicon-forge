"""Domain Skill Pack Registry with lightweight BM25 hybrid retrieval for grounding."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillPack:
    name: str
    content: str
    tokens: list[str]


class SkillRegistry:
    def __init__(self, packs_dir: Path | None = None) -> None:
        self.packs_dir = packs_dir or (Path(__file__).resolve().parent / "packs")
        self.packs: list[SkillPack] = []
        self._load_packs()

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", text.lower())

    def _load_packs(self) -> None:
        if not self.packs_dir.exists():
            return
        for md_file in sorted(self.packs_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            self.packs.append(SkillPack(name=md_file.stem, content=content, tokens=self._tokenize(content)))

    def retrieve(self, query: str, top_k: int = 2) -> list[SkillPack]:
        if not self.packs:
            return []
        q_tokens = set(self._tokenize(query))
        n_docs = len(self.packs)
        avg_len = sum(len(p.tokens) for p in self.packs) / max(1, n_docs)

        # Compute document frequency
        df: dict[str, int] = {}
        for p in self.packs:
            for tok in set(p.tokens):
                df[tok] = df.get(tok, 0) + 1

        scored = []
        k1, b = 1.5, 0.75
        for p in self.packs:
            score = 0.0
            doc_len = len(p.tokens)
            tf_map: dict[str, int] = {}
            for t in p.tokens:
                tf_map[t] = tf_map.get(t, 0) + 1
            for qt in q_tokens:
                if qt not in tf_map:
                    continue
                idf = math.log(1.0 + (n_docs - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5))
                tf = tf_map[qt]
                score += idf * ((tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (doc_len / max(1.0, avg_len)))))
            scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for s, p in scored[:top_k] if s > 0] or self.packs[:top_k]

    def format_context(self, query: str, top_k: int = 2) -> str:
        hits = self.retrieve(query, top_k=top_k)
        return "\n\n".join(f"### Skill Pack: {p.name}\n{p.content}" for p in hits)
