# SPDX-License-Identifier: AGPL-3.0-or-later
"""NotSoTiny (HPAI-BSC, arXiv 2512.20823, Apache-2.0): complete one masked module of a real Tiny Tapeout project, graded by formal equivalence.

Each task gives a whole flattened project with one module's body removed between two marker comments. The answer is that body. A candidate is
graded by splicing it back and asking Yosys (`equiv_simple`, then `equiv_induct -seq 30`, see `evals/equiv.py`) whether the project is equivalent to the
one built from the reference body. No testbench is involved, so a candidate cannot pass by satisfying a weak one; the reference is never shown to an arm.

What the verdict means: `equivalent` is a bounded-induction proof, `not_proven` covers both "different" and "induction could not close it", and `error`
means the tools failed. Tasks the grader cannot handle (the reference is not equivalent to itself within the timeout) are dropped before any model is scored
and listed in the result. The dataset is downloaded to the data directory at a pinned revision and never committed.
"""
from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from services.orchestrator.evals.equiv import check_equivalent
from services.orchestrator.paths import data_root

REPO = "HPAI-BSC/NotSoTiny-26-07"
REVISION = "c561f340e86f522033be5c33d4f2668e375c3260"
LICENSE_NOTE = "HPAI-BSC/NotSoTiny-26-07 is Apache-2.0. Downloaded to your data directory, not redistributed."
BEGIN = "// >>> Module Implementation Begin"
END = "// <<< Module Implementation End"
ROWS_API = "https://datasets-server.huggingface.co/rows"
SUITE = "notsotiny-26-07"


@dataclass
class Task:
    id: str
    project: str
    name: str
    system: str
    prompt: str  # the whole project with the masked region empty
    golden: str  # the full reference module


def dataset_file(root: Path | None = None) -> Path:
    return (root or data_root()) / "datasets" / "notsotiny" / REVISION / "tasks.jsonl"


def fetch(root: Path | None = None, force: bool = False, page: int = 100) -> Path:
    """Download every task into one JSONL file (about 12 requests). Resumable only by rerunning with --force."""
    import httpx

    dest = dataset_file(root)
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    offset = 0
    with httpx.Client(timeout=60.0, follow_redirects=True) as http:
        while True:
            r = http.get(ROWS_API, params={"dataset": REPO, "config": "default", "split": "train", "offset": offset, "length": page,
                                           "revision": REVISION})
            r.raise_for_status()
            body = r.json()
            batch = [x["row"] for x in body["rows"]]
            rows += batch
            offset += len(batch)
            if not batch or offset >= body.get("num_rows_total", 0):
                break
    dest.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    return dest


def load_tasks(root: Path | None = None) -> list[Task]:
    path = dataset_file(root)
    if not path.exists():
        raise RuntimeError("NotSoTiny is not downloaded: run `python scripts/notsotiny.py fetch`")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.append(Task(r["task_id"], r["project_name"], r["task_name"], r["system_message"], r["prompt"], r["golden_module"]))
    return out


def clean(src: str) -> str:
    """Preprocessor line markers (`line 12 "x.v" 1) are not something every Yosys build accepts; turn them into comments."""
    return re.sub(r"(?m)^`line .*$", "", src)


def _region(prompt: str) -> tuple[int, int] | None:
    b, e = prompt.find(BEGIN), prompt.find(END)
    return (b, e) if 0 <= b < e and prompt.count(BEGIN) == 1 else None


def _header_start(task: Task) -> int | None:
    """Where the module's header starts in the project: the reference module's text up to its port list's closing `);`."""
    head = task.golden.split(");", 1)[0] + ");"
    i = task.prompt.find(head)
    return i if i >= 0 else None


def usable(task: Task) -> str | None:
    """None if the task can be built and graded, else the reason it cannot."""
    if _region(task.prompt) is None:
        return "no single masked region in the prompt"
    if _header_start(task) is None:
        return "the reference header is not found in the project"
    return None


def extract_body(reply: str) -> str:
    """The code in a model's reply: the first fenced block if there is one, else the text. The markers are removed if the model repeated them."""
    m = re.search(r"```(?:verilog|systemverilog|v|sv)?\s*\n(.*?)```", reply, re.S | re.I)
    code = (m.group(1) if m else reply).strip("\n")
    return code.replace(BEGIN, "").replace(END, "")


def _splice_body(task: Task, body: str) -> str:
    b, e = _region(task.prompt)
    return task.prompt[: b + len(BEGIN)] + "\n" + body.strip("\n") + "\n" + task.prompt[e:]


def candidate_project(task: Task, reply_code: str) -> str:
    """The project with the candidate in place. If the reply is a whole module (it starts a `module`), it replaces the header, region and `endmodule`."""
    if re.search(r"(?m)^\s*module\s+\w+", reply_code):
        start = _header_start(task)
        b, e = _region(task.prompt)
        end_i = task.prompt.find("endmodule", e)
        if start is None or end_i < 0:
            return clean(task.prompt)
        return clean(task.prompt[:start] + reply_code.strip("\n") + "\n" + task.prompt[end_i + len("endmodule"):])
    return clean(_splice_body(task, reply_code))


def golden_project(task: Task) -> str:
    start = _header_start(task)
    b, e = _region(task.prompt)
    end_i = task.prompt.find("endmodule", e)
    return clean(task.prompt[:start] + task.golden.strip("\n") + "\n" + task.prompt[end_i + len("endmodule"):])


def top_module(project: str) -> str:
    m = re.search(r"(?m)^\s*module\s+(tt_um_\w+)", project) or re.search(r"(?m)^\s*module\s+(\w+)", project)
    return m.group(1) if m else "top"


def syntax_check(runner, workdir: Path, project: str, timeout: float = 120.0) -> tuple[bool, str]:
    """Does Yosys read and elaborate the project? The diagnostic is what a feedback arm may see: information about syntax, not about correctness."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "cand.sv").write_text(project, encoding="utf-8")
    top = top_module(project)
    res = runner.run(["yosys", "-q", "-p", f"read_verilog -sv cand.sv; hierarchy -check -top {top}"], workdir, timeout=timeout)
    if res.ok:
        return True, ""
    lines = [ln for ln in (res.stdout + "\n" + res.stderr).splitlines() if "ERROR" in ln or "error" in ln.lower()]
    return False, "\n".join(lines[-6:]) or (res.stdout + res.stderr)[-400:]


def grade(runner, workdir: Path, task: Task, reply_code: str, *, timeout: float = 180.0) -> dict:
    cand = candidate_project(task, reply_code)
    ok, diag = syntax_check(runner, workdir / "syntax", cand)
    if not ok:
        return {"syntax": False, "verdict": "syntax_error", "detail": diag[:400]}
    top = top_module(cand)
    res = check_equivalent(runner, workdir / "equiv", golden_project(task), cand, top, timeout=timeout)
    return {"syntax": True, "verdict": res.verdict, "detail": res.detail[:300], "elapsed_s": round(res.elapsed_sec, 1)}


def selfcheck(runner, workdir: Path, tasks: list[Task], *, want: int, seed: int = 0, timeout: float = 180.0,
              max_chars: int | None = None, on_progress=None) -> tuple[list[Task], dict[str, str]]:
    """Walk a seeded shuffle of the tasks (those with prompts up to `max_chars`) and keep the first `want` whose reference is equivalent to itself within the timeout. Returns (kept, dropped reasons)."""
    import random

    order = [t for t in tasks if max_chars is None or len(t.prompt) <= max_chars]  # a small model's context window bounds the prompts every arm gets
    random.Random(seed).shuffle(order)
    kept: list[Task] = []
    dropped: dict[str, str] = {}
    for t in order:
        if len(kept) >= want:
            break
        why = usable(t)
        if why is None:
            g = golden_project(t)
            ok, diag = syntax_check(runner, workdir / "self" / t.id, g)
            if not ok:
                why = f"the reference does not read in Yosys: {diag[:120]}"
            else:
                r = check_equivalent(runner, workdir / "self" / t.id / "eq", g, g, top_module(g), timeout=timeout)
                if r.verdict != "equivalent":
                    why = f"the reference is not proven equivalent to itself ({r.verdict}: {r.detail[:80]})"
        if why:
            dropped[t.id] = why
        else:
            kept.append(t)
        if on_progress:
            on_progress(f"{t.id}: {'kept' if not why else 'dropped: ' + why}")
    return kept, dropped


@contextlib.contextmanager
def scrambled_on_disk(root: Path | None = None):
    """While a general-purpose agent runs on this machine, the reference solutions must not sit on disk as readable text: one such agent was caught opening the
    dataset file and reading the answer. The file is replaced by a copy scrambled with a key that exists only in this process, and put back on exit. This
    defeats opening and searching the file; it does not stop an agent that fetches the public dataset from the network, which the run audit
    (`plain_agent.audit`) is for. If the process dies, `scripts/notsotiny.py fetch --force` restores the file."""
    import os

    path = dataset_file(root)
    plain = path.read_bytes()
    key = os.urandom(32)
    path.write_bytes(bytes(b ^ key[i % 32] for i, b in enumerate(plain)))
    try:
        yield
    finally:
        path.write_bytes(plain)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
