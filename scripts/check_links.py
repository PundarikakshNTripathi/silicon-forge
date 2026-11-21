#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that every external link in the docs still loads.

    python scripts/check_links.py [docs/learning docs/landscape.md ...]   # default: all of docs/ and README.md

Anything but a 2xx/3xx answer is reported. Some sites reject scripted requests (403/429); those are listed separately as
"blocked" rather than "dead", since they usually load in a browser. Exit status 1 only when something is truly dead.
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r"\]\((https?://[^)\s]+)\)|<(https?://[^>\s]+)>")
UA = {"User-Agent": "Mozilla/5.0 (compatible; link-check)"}


def collect(paths: list[Path]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for p in paths:
        files = [p] if p.is_file() else sorted(p.rglob("*.md"))
        for f in files:
            for m in URL.finditer(f.read_text(encoding="utf-8", errors="replace")):
                found.setdefault((m.group(1) or m.group(2)).rstrip(".,;"), []).append(str(f.relative_to(ROOT)))
    return found


def check(url: str) -> tuple[str, int | str]:
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0, headers=UA) as c:
            r = c.get(url)
        return url, r.status_code
    except httpx.HTTPError as exc:
        return url, type(exc).__name__


def main(argv: list[str]) -> int:
    targets = [ROOT / a for a in argv] or [ROOT / "docs", ROOT / "README.md"]
    urls = collect(targets)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, urls))
    dead, blocked = [], []
    for url, status in results:
        if isinstance(status, int) and status < 400:
            continue
        (blocked if status in (403, 429, 999) else dead).append((url, status))
    for url, status in blocked:
        print(f"blocked {status}  {url}  ({', '.join(sorted(set(urls[url])))})")
    for url, status in dead:
        print(f"DEAD    {status}  {url}  ({', '.join(sorted(set(urls[url])))})")
    print(f"{len(urls)} links: {len(urls) - len(dead) - len(blocked)} ok, {len(blocked)} blocked, {len(dead)} dead")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
