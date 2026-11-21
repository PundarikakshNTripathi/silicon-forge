# SPDX-License-Identifier: AGPL-3.0-or-later
"""VerilogEval v2, spec-to-RTL (NVlabs/verilog-eval, MIT license): 156 problems, each a prompt, a reference module and a
self-checking testbench.

The dataset is downloaded at run time from a pinned commit into the data directory and is never committed. Grading uses the
benchmark's own testbench with Icarus Verilog: a candidate passes when it compiles with the reference and the testbench
reports zero mismatches. The testbench output is **not** shown to the model: it is the scoring test. What a repair loop may
see is the compile diagnostic of the candidate on its own, which is information about syntax and elaboration only.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from services.orchestrator.paths import data_root

REPO = "NVlabs/verilog-eval"
PINNED_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
EXPECTED_PROBLEMS = 156
SUITE = "verilogeval-spec2rtl"
LICENSE_NOTE = "NVlabs/verilog-eval is MIT licensed (Copyright NVIDIA Research Projects). Downloaded to your data directory, not redistributed."

SYSTEM_PROMPT = (
    "You write synthesizable Verilog for a hardware benchmark. Implement the module described by the user, named exactly "
    "TopModule, with exactly the ports described. Reply with the module only, in one ```verilog code block: no testbench, "
    "no explanation. Use Verilog-2001 or SystemVerilog constructs that Icarus Verilog accepts (`logic`, `always_comb` and "
    "`always_ff` are fine)."
)

#: Problems whose own reference cannot pass their own testbench (found by `evals selfcheck`). Excluded from every run and
#: listed in every result file, so the denominator is honest: 155, not 156.
EXCLUDED = {
    "Prob099_m2014_q6c": "the prompt and testbench use outputs Y2 and Y4, the reference module declares Y1 and Y3, so no "
                         "candidate (the reference included) can compile with the testbench",
}

_MISMATCH = re.compile(r"Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples")
_PROB = re.compile(r"^(Prob\d+_[A-Za-z0-9_]+)_prompt\.txt$")


@dataclass
class Problem:
    id: str
    prompt: str
    ref: str
    test: str

    @property
    def number(self) -> int:
        return int(self.id[4:7])


@dataclass
class Grade:
    passed: bool
    compiled: bool
    mismatches: int | None = None
    samples: int | None = None
    detail: str = ""  # tool output for the human reading the result, never given to a model


def dataset_dir(commit: str = PINNED_COMMIT) -> Path:
    return data_root() / "datasets" / f"verilog-eval-{commit[:8]}"


def _http_get(url: str) -> bytes:
    import httpx

    r = httpx.get(url, follow_redirects=True, timeout=120)
    r.raise_for_status()
    return r.content


def fetch(commit: str = PINNED_COMMIT, force: bool = False, opener=None) -> Path:
    """Download and unpack the spec-to-RTL problems. `opener(url) -> bytes` is injectable so tests need no network."""
    dest = dataset_dir(commit)
    if dest.is_dir() and any(dest.glob("Prob*_prompt.txt")) and not force:
        return dest
    url = f"https://github.com/{REPO}/archive/{commit}.zip"
    blob = (opener or _http_get)(url)
    tmp = dest.with_name(dest.name + ".part")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            parts = Path(info.filename).parts
            if len(parts) >= 3 and parts[1] == "dataset_spec-to-rtl" and not info.is_dir():
                (tmp / parts[-1]).write_bytes(z.read(info))
            elif len(parts) == 2 and parts[1] == "LICENSE":
                (tmp / "LICENSE").write_bytes(z.read(info))
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)
    return dest


def load_problems(root: Path | None = None, include_excluded: bool = False) -> list[Problem]:
    root = root or dataset_dir()
    out = []
    for prompt_file in sorted(root.glob("Prob*_prompt.txt")):
        m = _PROB.match(prompt_file.name)
        if not m:
            continue
        pid = m.group(1)
        if pid in EXCLUDED and not include_excluded:
            continue
        ref, test = root / f"{pid}_ref.sv", root / f"{pid}_test.sv"
        if ref.exists() and test.exists():
            out.append(Problem(pid, prompt_file.read_text(encoding="utf-8"), ref.read_text(encoding="utf-8"),
                               test.read_text(encoding="utf-8")))
    return out


def select(problems: list[Problem], count: int | None, seed: int = 0) -> list[Problem]:
    """A reproducible subset: `count` problems drawn with `seed`, returned in problem order. None means all."""
    import random

    if not count or count >= len(problems):
        return list(problems)
    chosen = random.Random(seed).sample(range(len(problems)), count)
    return [problems[i] for i in sorted(chosen)]


def user_prompt(p: Problem) -> str:
    return p.prompt.strip()


def parse_testbench(output: str) -> tuple[int, int] | None:
    """`(mismatches, samples)` from the testbench's summary line, or None when it is missing (a crash or a timeout)."""
    hits = _MISMATCH.findall(output)
    return (int(hits[-1][0]), int(hits[-1][1])) if hits else None


def extract_module(reply: str) -> str:
    """The code block of a reply. Falls back to the whole text; strips a leading <think> block some models print."""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    blocks = re.findall(r"```(?:systemverilog|verilog|sv|v)?\s*\n(.*?)```", reply, flags=re.S | re.I)
    if blocks:
        with_top = [b for b in blocks if re.search(r"\bmodule\s+TopModule\b", b)]
        return (with_top or blocks)[-1].strip()
    return reply.strip()


_WAVE_DUMP = re.compile(r"initial\s+begin\s*\$dumpfile\([^)]*\)\s*;\s*\$dumpvars\([^;]*\)\s*;\s*end", re.S)
TESTBENCH_PATCH = (
    "removed the waveform-dump block ($dumpfile/$dumpvars): it refers to `tb_mismatch` before its declaration, which "
    "Icarus Verilog 14 rejects ('Unable to bind wire/reg/memory'); it writes a VCD and has no effect on the mismatch count"
)


def patch_testbench(text: str) -> str:
    """Drop the waveform-dump block from a VerilogEval testbench. Scoring is unaffected: `selfcheck` grades every
    problem's own reference through the patched testbench and all of them must pass."""
    return _WAVE_DUMP.sub("", text)


COMPILE_CHECK = ["iverilog", "-g2012", "-Wall", "-s", "TopModule", "-o", "/dev/null", "dut.sv"]


def compile_diagnostic(runner, workdir: Path, rtl: str, timeout: float = 60.0) -> str:
    """Compile the candidate alone. Returns '' when it elaborates, else the tool's messages. Safe to show a model."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "dut.sv").write_text(rtl, encoding="utf-8", newline="\n")
    res = runner.run(COMPILE_CHECK, workdir, timeout=timeout)
    if res.ok:
        return ""
    return (res.stderr or res.stdout).strip()[:2000] or f"iverilog exited with {res.returncode}"


def grade(runner, workdir: Path, problem: Problem, rtl: str, timeout: float = 120.0) -> Grade:
    """Score a candidate with the benchmark's own testbench. Never reuse `detail` as model feedback."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "dut.sv").write_text(rtl, encoding="utf-8", newline="\n")
    (workdir / "ref.sv").write_text(problem.ref, encoding="utf-8", newline="\n")
    (workdir / "test.sv").write_text(patch_testbench(problem.test), encoding="utf-8", newline="\n")
    (workdir / "grade.sh").write_text(
        "iverilog -Wall -g2012 -s tb -o sim dut.sv test.sv ref.sv || exit 90\nvvp -n sim\n", encoding="utf-8", newline="\n")
    res = runner.run(["bash", "grade.sh"], workdir, timeout=timeout)
    if res.returncode == 90:
        return Grade(False, False, detail=(res.stderr or res.stdout).strip()[:1500])
    if res.returncode == 124:
        return Grade(False, True, detail="timeout")
    parsed = parse_testbench(res.stdout + "\n" + res.stderr)
    if parsed is None:
        return Grade(False, True, detail=f"no testbench summary (exit {res.returncode}): {(res.stderr or res.stdout)[-400:]}")
    mism, samples = parsed
    return Grade(mism == 0 and samples > 0, True, mism, samples)


def describe() -> dict:
    return {"suite": SUITE, "source": f"https://github.com/{REPO}", "commit": PINNED_COMMIT, "problems": EXPECTED_PROBLEMS,
            "license": "MIT", "note": LICENSE_NOTE}


def write_manifest(root: Path) -> None:
    (root / "MANIFEST.json").write_text(json.dumps(describe(), indent=2), encoding="utf-8")
