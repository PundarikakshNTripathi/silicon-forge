# SPDX-License-Identifier: AGPL-3.0-or-later
"""The README's pictures and citations resolve: every image exists, has alt text and a sane size; every result file it names exists;
every relative link points at a file. (An uncited number cannot be detected mechanically; a broken citation can.)"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
MAX_IMAGE_KB = {".png": 700, ".gif": 1500, ".svg": 120}


def images():
    for m in re.finditer(r"<img\b[^>]*>", README):
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag)
        alt = re.search(r'alt="([^"]*)"', tag)
        yield (src.group(1) if src else ""), (alt.group(1) if alt else None)
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)\s]+)\)", README):
        yield m.group(2), m.group(1)


def test_every_image_exists_has_alt_text_and_is_not_huge():
    seen = 0
    for src, alt in images():
        if src.startswith("http"):
            continue
        seen += 1
        path = ROOT / src
        assert path.is_file(), f"missing image {src}"
        assert alt and len(alt) > 12, f"{src} needs descriptive alt text"
        limit = MAX_IMAGE_KB.get(path.suffix)
        assert limit is None or path.stat().st_size <= limit * 1024, f"{src} is {path.stat().st_size // 1024} KB (limit {limit})"
    assert seen >= 10


def test_every_result_file_the_readme_names_exists():
    for ref in set(re.findall(r"`((?:agentbench|eval|flow|real|verification|local|telemetry|speed|llamacpp)[\w.*-]*\.json)`", README)):
        assert list((ROOT / "benchmarks" / "results").glob(ref)), f"README cites {ref}, which matches no file in benchmarks/results"


def test_relative_links_point_at_files():
    for target in set(re.findall(r"\]\(((?:docs|assets|configs|scripts|services|benchmarks|hardware)/[^)#\s]+)", README)):
        assert (ROOT / target).exists(), f"broken link {target}"
    for target in ("LICENSE", "COMMERCIAL.md", "CONTRIBUTING.md", "AGENTS.md"):
        assert (ROOT / target).exists()
