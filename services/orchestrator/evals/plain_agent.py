# SPDX-License-Identifier: AGPL-3.0-or-later
"""A plain coding agent as a baseline: a general-purpose CLI agent given the task folder and a way to run Yosys, and nothing from SiliconForge.

This is the comparison the research brief asks for: does gating each step on a tool verdict beat simply handing a coding agent the same tools? The
agent gets `project.v` (the project with the masked region empty), `TASK.md` (the task text the other arms see) and `check.cmd` (reads the project in Yosys
and prints the diagnostic). It edits `project.v`; the part between the markers is taken as its answer and graded by the hidden equivalence check it cannot see.

Two CLIs are supported, both on the user's own subscription and run in an empty temporary folder: `agy` (Gemini 3.8 Flash through the Antigravity
CLI) and `claude -p` (Claude Code in print mode). Neither is asked to read any credential, and the harness never touches them. Whether driving an official
CLI from another program is allowed under a plan is for the plan's owner to check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from services.orchestrator.evals import notsotiny as ns

CLIS = ("agy", "claude")
#: Strings that only appear if the agent went looking for the reference: the dataset file, its folder, the dataset's names and the network sources it comes from.
#: The legitimate check command (`scripts/notsotiny.py check project.v`) contains none of them. A run whose transcript has any is void.
LOOKUP_MARKERS = ("tasks.jsonl", "datasets\\notsotiny", "datasets/notsotiny", "datasets\\\\notsotiny", "huggingface.co", "datasets-server", "golden_module", "HPAI-BSC")


def audit(transcript: str) -> list[str]:
    """The lookup markers found in a run's full transcript (empty if the run stayed in its folder as far as the transcript shows)."""
    return sorted({m for m in LOOKUP_MARKERS if m.lower() in transcript.lower()})
PROMPT = """Complete the Verilog project in project.v. One module's body is missing, between the two marker comments
`{begin}` and `{end}`.

Read TASK.md for the full task, then edit project.v so that the missing body is filled in. You may read any file in this folder, and you can check
that the project reads in Yosys with: {check}
That check only tells you whether the project parses and elaborates; it cannot tell you whether the body is right, so reason carefully about what the
module must do from the rest of the project. Only edit between the markers (or replace the whole module if you prefer). Work only in this folder.
Do not look at any file outside this folder, do not search the disk, and do not use the network: the reference solution exists
somewhere on this machine and looking for it voids the run. When you are done, reply with the single word DONE."""


def check_command(repo_root: Path) -> str:
    return f'"{sys.executable}" "{repo_root / "scripts" / "notsotiny.py"}" check project.v'


def prepare(task: ns.Task, workdir: Path, repo_root: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "project.v").write_text(ns.clean(task.prompt), encoding="utf-8")
    (workdir / "TASK.md").write_text(task.system + "\n", encoding="utf-8")


def _argv(cli: str, prompt: str, model: str | None) -> list[str]:
    if cli == "agy":
        exe = os.environ.get("SILICONFORGE_AGY") or "agy"
        a = [exe, "--output-format", "stream-json", "--mode", "auto", "--dangerously-skip-permissions", "--print-timeout", "600s"]
        if model:
            a += ["--model", model]
        return a + ["--print", prompt]
    if cli == "claude":
        a = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--no-session-persistence",
             "--max-turns", "16", "--allowedTools", "Read", "Edit", "Write", "Bash(python:*)", "Bash(\"*python*\":*)"]
        if model:
            a += ["--model", model]
        return a
    raise ValueError(f"unknown CLI {cli!r}; choose from {CLIS}")


def run(cli: str, task: ns.Task, workdir: Path, repo_root: Path, *, model: str | None = None, timeout: float = 700.0,
        transcripts: Path | None = None) -> dict:
    """Run the agent on one task. Returns {code, seconds, tokens_in, tokens_out, cost_usd, error}; the grade is applied by the caller."""
    prepare(task, workdir, repo_root)
    prompt = PROMPT.format(begin=ns.BEGIN, end=ns.END, check=check_command(repo_root))
    t0 = time.perf_counter()
    err = ""
    out = ""
    try:
        proc = subprocess.run(_argv(cli, prompt, model), cwd=workdir, capture_output=True, text=True, timeout=timeout, check=False,
                              encoding="utf-8", errors="replace")
        out = proc.stdout
        if proc.returncode != 0:
            err = (proc.stderr or out)[-300:]
    except subprocess.TimeoutExpired:
        err = f"timed out after {timeout:.0f} s"
    except FileNotFoundError as exc:
        err = f"{cli} is not installed or not on PATH: {exc}"
    secs = round(time.perf_counter() - t0, 1)
    info = _usage(out)
    markers = audit(out)
    if transcripts is not None:
        transcripts.mkdir(parents=True, exist_ok=True)
        (transcripts / f"{cli}-{task.id}.jsonl").write_text(out, encoding="utf-8")
    project = (workdir / "project.v").read_text(encoding="utf-8", errors="replace")
    code = _answer_from(project, task)
    return {"code": code, "seconds": secs, "error": err, "lookup_markers": markers, **info}


def _answer_from(project: str, task: ns.Task) -> str:
    """The agent's answer: the text between the markers if they are still there, else the whole file's module (the grader splices a whole module)."""
    b, e = project.find(ns.BEGIN), project.find(ns.END)
    if 0 <= b < e:
        return project[b + len(ns.BEGIN): e].strip("\n")
    head = task.golden.split(");", 1)[0] + ");"
    i = project.find(head)
    if i >= 0:
        j = project.find("endmodule", i)
        if j >= 0:
            return project[i: j + len("endmodule")]
    return ""


def _usage(stdout: str) -> dict:
    """Tokens and cost as the CLI reports them (keys differ by CLI; anything missing stays None)."""
    try:
        doc = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"tokens_in": None, "tokens_out": None, "cost_usd": None, "turns": None}
    u = doc.get("usage") or {}
    return {"tokens_in": u.get("input_tokens") or u.get("input"), "tokens_out": u.get("output_tokens") or u.get("output"),
            "cost_usd": doc.get("total_cost_usd"), "turns": doc.get("num_turns")}
