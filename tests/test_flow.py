# SPDX-License-Identifier: AGPL-3.0-or-later
"""The project flow: project model, signoff gates, stages (fake runner) and report. Real tools run in test_flow_real."""
import json
from pathlib import Path

import pytest

from services.orchestrator.eda.runner import RunResult
from services.orchestrator.flow import signoff
from services.orchestrator.flow.report import diff, to_html
from services.orchestrator.flow.stages import ProjectFlow
from services.orchestrator.project import Project, ProjectError, init_project

TOP = """// top
module top (
    input  wire clk,
    input  wire rst_n,
    output reg  y
);
    helper u (.a(clk), .b());
endmodule
"""
HELPER = "module helper(input a, output b); assign b = a; endmodule\n// module not_real;\n"
HDR = "`define WIDTH 8\n"


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "inc").mkdir()
    (tmp_path / "rtl" / "top.sv").write_text(TOP)
    (tmp_path / "rtl" / "helper.sv").write_text(HELPER)
    (tmp_path / "inc" / "defs.vh").write_text(HDR)
    (tmp_path / "siliconforge.yaml").write_text(
        "name: demo\nproject:\n  top: top\n  sources: [rtl/*.sv]\n  include_dirs: [inc]\n  defines: {SYNTHESIS: 1}\n"
        "  clock: {port: clk, period_ns: 12.5}\n  pdk: sky130A\n  vars: {FP_CORE_UTIL: 40}\n  waive: [antenna]\n")
    return tmp_path


# ------------------------------------------------------------------------------------------------ project


def test_project_loads_globs_includes_defines_clock_and_waivers(proj):
    p = Project.load(proj)
    assert [s.name for s in p.sources] == ["helper.sv", "top.sv"]  # sorted glob, absolute, deduplicated
    assert p.top == "top" and p.period_ns == 12.5 and p.defines == {"SYNTHESIS": "1"} and p.waive == ["antenna"]
    assert p.include_dirs == [(proj / "inc").resolve()] and p.validate() == []


def test_modules_ignore_commented_out_declarations(proj):
    mods = Project.load(proj).modules()
    assert set(mods) == {"top", "helper"}  # `// module not_real;` is a comment


def test_validation_names_every_problem_without_running_a_tool(proj):
    (proj / "siliconforge.yaml").write_text(
        "project:\n  top: nope\n  sources: [rtl/*.sv, rtl/missing.sv]\n  include_dirs: [absent]\n  sdc: x.sdc\n"
        "  clock: {port: clk, period_ns: -1}\n  vars: {FP_CORE_UTLI: 40}\n")
    errs = Project.load(proj).validate()
    text = "\n".join(errs)
    for needle in ("source not found: rtl/missing.sv", "include directory not found: absent", "sdc file not found: x.sdc",
                   "period_ns must be positive", "FP_CORE_UTLI"):
        assert needle in text, needle


def test_top_must_exist_and_the_clock_must_be_one_of_its_ports(proj):
    (proj / "siliconforge.yaml").write_text("project:\n  top: ghost\n  sources: [rtl/*.sv]\n")
    assert "top module 'ghost' is not defined" in "\n".join(Project.load(proj).validate())
    (proj / "siliconforge.yaml").write_text("project:\n  top: top\n  sources: [rtl/*.sv]\n  clock: {port: clock, period_ns: 10}\n")
    assert "clock port 'clock' does not appear" in "\n".join(Project.load(proj).validate())


def test_missing_manifest_and_missing_section_say_how_to_fix_it(tmp_path):
    with pytest.raises(ProjectError, match="flow init"):
        Project.load(tmp_path)
    (tmp_path / "siliconforge.yaml").write_text("name: x\n")
    with pytest.raises(ProjectError, match="no `project:` section"):
        Project.load(tmp_path)


def test_init_adds_the_section_and_keeps_other_keys(tmp_path):
    (tmp_path / "siliconforge.yaml").write_text("name: keep\nprofile: dev\npolicy:\n  allowed_models: [a]\n")
    init_project(tmp_path, "top", ["rtl/*.sv"], "clk", 8.0, "gf180mcuD")
    raw = (tmp_path / "siliconforge.yaml").read_text()
    assert "profile: dev" in raw and "allowed_models" in raw and "top: top" in raw
    with pytest.raises(ProjectError, match="already has"):
        init_project(tmp_path, "top", ["x"])


def test_fingerprint_changes_with_source_bytes_constraints_and_settings(proj):
    a = Project.load(proj).fingerprint()
    assert a == Project.load(proj).fingerprint()
    (proj / "rtl" / "helper.sv").write_text(HELPER + "// edit\n")
    b = Project.load(proj).fingerprint()
    assert b != a
    (proj / "siliconforge.yaml").write_text((proj / "siliconforge.yaml").read_text().replace("12.5", "10"))
    assert Project.load(proj).fingerprint() != b


# ------------------------------------------------------------------------------------------------ signoff

GOOD = {
    "timing__setup__ws": 0.4, "timing__setup_vio__count": 0, "timing__hold__ws": 0.1, "timing__hold_vio__count": 0,
    "magic__drc_error__count": 0, "klayout__drc_error__count": 0, "route__drc_errors": 0, "design__lvs_error__count": 0,
}


def test_a_complete_clean_run_is_clean_and_optional_gaps_are_listed_not_passed():
    v = signoff.evaluate(GOOD)
    assert v.status == "clean"
    by = {c.id: c for c in v.checks}
    assert by["antenna"].status == signoff.MISSING and not by["antenna"].required  # absent is not a pass


def test_a_missing_required_metric_makes_the_verdict_incomplete():
    m = dict(GOOD)
    del m["design__lvs_error__count"]
    v = signoff.evaluate(m)
    assert v.status == "incomplete" and [c.id for c in v.missing_required] == ["lvs"]


def test_failures_win_over_everything_and_drc_sums_the_tools():
    m = dict(GOOD, timing__setup__ws=-0.2, klayout__drc_error__count=3, magic__drc_error__count=2)
    v = signoff.evaluate(m)
    assert v.status == "failed"
    ids = {c.id for c in v.failed}
    assert ids == {"setup_ws", "drc"}
    assert next(c for c in v.checks if c.id == "drc").value == 5


def test_a_waiver_is_visible_and_only_lifts_the_failure_it_names():
    m = dict(GOOD, route__antenna_violation__count=4, timing__hold__ws=-0.1)
    v = signoff.evaluate(m, waive={"antenna", "setup_ws"})
    by = {c.id: c for c in v.checks}
    assert by["antenna"].status == signoff.PASS and "WAIVED" in by["antenna"].detail
    assert v.status == "failed" and [c.id for c in v.failed] == ["hold_ws"]  # hold was not waived


def test_corner_table_is_sorted_worst_first():
    m = {"timing__setup__ws__corner:ss": -0.5, "timing__setup__ws__corner:tt": 0.2, "timing__hold__ws__corner:ss": 0.3,
         "timing__hold__ws__corner:tt": 0.1}
    rows = signoff.corner_table(m)
    assert [r["corner"] for r in rows] == ["ss", "tt"] and rows[0]["setup_ns"] == -0.5 and rows[1]["hold_ns"] == 0.1


# ------------------------------------------------------------------------------------ stages, fake runner


class FakeRunner:
    name = "fake"

    def __init__(self, lint_log="", yosys_rc=0, tools=True):
        self.calls, self.lint_log, self.yosys_rc, self.tools = [], lint_log, yosys_rc, tools

    def run(self, cmd, cwd, timeout=600, env=None, isolate=False):
        self.calls.append((cmd, Path(cwd)))
        if not self.tools:
            return RunResult(127, "", "command not found", 0.0)
        if cmd[0] == "verilator" and "--version" in cmd:
            return RunResult(0, "Verilator 5.055", "", 0.0)
        if cmd[0] == "verilator":
            return RunResult(0, "", self.lint_log, 0.1)
        if cmd[0] == "yosys" and "-V" in cmd:
            return RunResult(0, "Yosys 0.71", "", 0.0)
        if cmd[0] == "yosys":
            (Path(cwd) / "synth_stat.txt").write_text("=== helper ===\n   3 cells\n=== top ===\n   120 cells\n")
            (Path(cwd) / "yosys.log").write_text("log")
            return RunResult(self.yosys_rc, "", "", 0.2)
        return RunResult(0, "", "", 0.0)

    def has_tool(self, t):
        return self.tools

    def push(self, d):
        return d

    def pull(self, a, b):
        pass

    def cleanup(self, a, b):
        pass


class FakeLibreLane:
    command = ["librelane"]

    def __init__(self, metrics=None, ok=True):
        self.metrics, self.ok, self.config = metrics, ok, None

    def available(self):
        return self.ok

    def run_config(self, work, cfg, knobs, timeout):
        from services.orchestrator.eda.librelane import LibreLaneFlow

        self.config = cfg
        ppa = LibreLaneFlow.parse_metrics(self.metrics, knobs)
        return ppa, self.metrics


def _flow(proj, runner=None, ll=None, tmp=None):
    p = Project.load(proj)
    return ProjectFlow(p, runner or FakeRunner(), (tmp or proj) / "out", librelane=ll or FakeLibreLane(dict(GOOD)), waive=set(p.waive))


def test_sources_are_staged_with_their_relative_layout_and_headers(proj):
    f = _flow(proj)
    layout = f.stage_sources(proj / "stage")
    assert layout["files"] == ["src/rtl/helper.sv", "src/rtl/top.sv"] and "src/inc" in layout["includes"]
    assert (proj / "stage" / "src" / "inc" / "defs.vh").read_text() == HDR


def test_lint_counts_errors_and_warnings_and_names_the_common_kind(proj):
    log = "%Warning-UNUSEDSIGNAL: a\n%Warning-UNUSEDSIGNAL: b\n%Warning-WIDTH: c\n"
    r = _flow(proj, FakeRunner(lint_log=log)).lint()
    assert r.status == "pass" and r.data["warnings"] == 3 and r.data["by_kind"]["UNUSEDSIGNAL"] == 2 and "UNUSEDSIGNAL" in r.summary
    bad = _flow(proj, FakeRunner(lint_log="%Error: top.sv:3: syntax error\n")).lint()
    assert bad.status == "fail" and bad.data["errors"] == 1


def test_lint_passes_include_dirs_defines_and_the_top_to_verilator(proj):
    r = FakeRunner()
    _flow(proj, r).lint()
    cmd = next(c for c, _ in r.calls if c[0] == "verilator" and "--lint-only" in c)
    assert "--top-module" in cmd and cmd[cmd.index("--top-module") + 1] == "top"
    assert "-Isrc/inc" in cmd and "-DSYNTHESIS=1" in cmd and cmd[-2:] == ["src/rtl/helper.sv", "src/rtl/top.sv"]


def test_a_missing_tool_is_an_error_with_the_reason_never_a_pass(proj):
    r = _flow(proj, FakeRunner(tools=False)).lint()
    assert r.status == "error" and "not available" in r.summary


def test_synth_flattens_before_the_equivalence_check_and_reports_the_whole_design(proj):
    r = FakeRunner()
    res = _flow(proj, r).synth()
    script = (proj / "out" / "synthesis" / "synth.ys").read_text().splitlines()
    assert script.count("flatten") == 2  # gold and gate are both flattened
    assert script.index("equiv_status -assert") > script.index("equiv_induct -undef")
    assert res.status == "pass" and res.data["cells"] == 120 and res.data["lec"] == "proven"  # last count, not the first


def test_failed_and_timed_out_equivalence_are_not_passes(proj):
    assert _flow(proj, FakeRunner(yosys_rc=1)).synth().status == "fail"
    t = _flow(proj, FakeRunner(yosys_rc=124)).synth()
    assert t.status == "error" and "timed out" in t.summary
    assert _flow(proj, FakeRunner()).synth(lec=False).data["lec"] == "not run"


def test_librelane_config_carries_files_includes_defines_sdc_and_vars(proj):
    (proj / "c.sdc").write_text("create_clock -period 12.5 [get_ports clk]\n")
    (proj / "siliconforge.yaml").write_text((proj / "siliconforge.yaml").read_text() + "  sdc: c.sdc\n")
    ll = FakeLibreLane(dict(GOOD))
    f = _flow(proj, ll=ll)
    f.pnr({"GRT_ADJUSTMENT": 0.2})
    c = ll.config
    assert c["VERILOG_FILES"] == ["dir::src/rtl/helper.sv", "dir::src/rtl/top.sv"] and c["DESIGN_NAME"] == "top"
    # listed include dirs, plus the directory of each source (a header next to its user is found without being listed)
    assert c["VERILOG_INCLUDE_DIRS"] == ["dir::src/inc", "dir::src/rtl"] and c["VERILOG_DEFINES"] == ["SYNTHESIS=1"]
    assert c["PNR_SDC_FILE"] == "dir::src/c.sdc" and c["SIGNOFF_SDC_FILE"] == "dir::src/c.sdc"
    assert c["CLOCK_PERIOD"] == 12.5 and c["FP_CORE_UTIL"] == 40 and c["GRT_ADJUSTMENT"] == 0.2  # project vars, then run knobs


def test_pnr_status_follows_the_signoff_verdict(proj):
    ok = _flow(proj, ll=FakeLibreLane(dict(GOOD))).pnr()
    assert ok.status == "pass" and ok.data["verdict"]["status"] == "clean"
    bad = _flow(proj, ll=FakeLibreLane(dict(GOOD, timing__setup__ws=-1.0))).pnr()
    assert bad.status == "fail" and "Setup slack" in bad.summary
    partial = dict(GOOD)
    del partial["design__lvs_error__count"]
    inc = _flow(proj, ll=FakeLibreLane(partial)).pnr()
    assert inc.status == "error" and "incomplete" in inc.summary  # not a pass, not a fail
    assert _flow(proj, ll=FakeLibreLane(ok=False)).pnr().status == "error"


def test_a_waiver_in_the_project_lifts_only_its_check(proj):
    r = _flow(proj, ll=FakeLibreLane(dict(GOOD, route__antenna_violation__count=2))).pnr()
    assert r.status == "pass"
    assert any("WAIVED" in c["detail"] for c in r.data["verdict"]["checks"])


# ---------------------------------------------------------------------------------------------- report


def test_run_builds_a_report_with_provenance_and_an_honest_overall(proj):
    rep = _flow(proj).run()
    assert rep["schema"] == "siliconforge.flow/1" and rep["overall"] == "clean" and rep["runner"] == "fake"
    assert rep["tools"]["yosys"] == "Yosys 0.71" and rep["project"]["fingerprint"] and rep["config_sha256"]
    assert [s["name"] for s in rep["stages"]] == ["lint", "synth", "pnr"]
    only = _flow(proj).run(stages=("lint",))
    assert [s["status"] for s in only["stages"]] == ["pass", "skipped", "skipped"] and only["config_sha256"] is None
    assert _flow(proj, FakeRunner(yosys_rc=1)).run()["overall"] == "failed"
    assert _flow(proj, ll=FakeLibreLane(ok=False)).run()["overall"] == "incomplete"


def test_an_invalid_project_or_unknown_stage_is_rejected_before_any_tool_runs(proj):
    f = _flow(proj)
    with pytest.raises(ValueError, match="unknown stage"):
        f.run(stages=("lint", "tape"))
    (proj / "rtl" / "top.sv").unlink()
    with pytest.raises(ValueError, match="not valid"):
        _flow(proj).run()


def test_html_report_is_self_contained_and_escapes_user_text(proj):
    rep = _flow(proj).run()
    rep["stages"][0]["summary"] = "<script>alert(1)</script>"
    page = to_html(rep)
    assert "<script>" not in page.replace("<script>alert", "") and "&lt;script&gt;" in page
    assert "http://" not in page and "https://" not in page  # no network needed
    for needle in ("Signoff checklist", "Timing by corner" if rep["corners"] else "Stages", "real tools", "FAKE"):
        assert needle in page


def test_incomplete_reports_say_it_is_not_a_pass(proj):
    rep = _flow(proj, ll=FakeLibreLane(ok=False)).run()
    assert "This is not a pass" in to_html(rep)


def test_diff_lists_what_changed_between_reports(proj):
    a = _flow(proj).run()
    b = json.loads(json.dumps(a))
    assert diff(a, b)[0].startswith("same sources")
    b["overall"], b["tools"]["yosys"] = "failed", "Yosys 0.72"
    b["ppa"]["area_um2"] = a["ppa"]["area_um2"] * 1.1 if a["ppa"]["area_um2"] else 1.0
    b["verdict"]["checks"][0]["status"] = "fail"
    lines = "\n".join(diff(a, b))
    assert "overall: clean -> failed" in lines and "tool yosys: Yosys 0.71 -> Yosys 0.72" in lines and "check setup_ws" in lines


# ------------------------------------------------------------------------------ real tools (skipped when absent)


def _real_runner():
    import shutil

    from services.orchestrator.eda.runner import NativeRunner, WslRunner

    if shutil.which("verilator") and shutil.which("yosys"):
        return NativeRunner()
    if shutil.which("wsl"):
        r = WslRunner()
        try:
            if r.has_tool("verilator") and r.has_tool("yosys"):
                return r
        except OSError:
            pass
    pytest.skip("verilator and yosys not available natively or in WSL")


HIER_TOP = """module counter_top (input wire clk, input wire rst_n, input wire en, output wire [3:0] q);
    counter_core u (.clk(clk), .rst_n(rst_n), .en(en), .q(q));
endmodule
"""
HIER_CORE = """module counter_core (input wire clk, input wire rst_n, input wire en, output reg [3:0] q);
    always @(posedge clk or negedge rst_n)
        if (!rst_n) q <= 4'd0; else if (en) q <= q + 4'd1;
endmodule
"""


@pytest.mark.real_eda
def test_real_hierarchical_design_is_linted_and_proven_equivalent(tmp_path):
    """A design with a module instance used to fail equivalence ('No SAT model available for cell'); it is flattened now."""
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "top.v").write_text(HIER_TOP)
    (tmp_path / "rtl" / "core.v").write_text(HIER_CORE)
    (tmp_path / "siliconforge.yaml").write_text("project:\n  top: counter_top\n  sources: [rtl/*.v]\n  clock: {port: clk, period_ns: 10}\n")
    flow = ProjectFlow(Project.load(tmp_path), _real_runner(), tmp_path / "out")
    lint, synth = flow.lint(), flow.synth()
    assert lint.status == "pass", lint.log_tail
    assert synth.status == "pass" and synth.data["lec"] == "proven" and synth.data["cells"] > 4, synth.log_tail


@pytest.mark.real_eda
def test_real_syntax_error_fails_lint_with_the_message(tmp_path):
    (tmp_path / "top.v").write_text("module bad (input clk, output y);\n  assign y = ;\nendmodule\n")
    (tmp_path / "siliconforge.yaml").write_text("project:\n  top: bad\n  sources: [top.v]\n")
    r = ProjectFlow(Project.load(tmp_path), _real_runner(), tmp_path / "out").lint()
    assert r.status == "fail" and r.data["errors"] >= 1 and "syntax" in r.log_tail.lower()


# ------------------------------------------------------------------------------------------------------ CLI


def test_cli_init_check_report_and_diff_round_trip(proj, tmp_path, capsys):
    from services.orchestrator.main import cli_main

    other = tmp_path / "other"
    other.mkdir()
    (other / "t.v").write_text("module t(input clk, output y); assign y = clk; endmodule\n")
    assert cli_main(["flow", "init", str(other), "--top", "t", "--sources", "t.v"]) == 0
    assert cli_main(["flow", "init", str(other), "--top", "t", "--sources", "t.v"]) == 2  # already has a project section
    assert cli_main(["flow", "check", str(other)]) == 0 and "ok: top t" in capsys.readouterr().out
    (other / "t.v").write_text("module u(input clk); endmodule\n")
    assert cli_main(["flow", "check", str(other)]) == 1 and "top module 't' is not defined" in capsys.readouterr().err

    rep = _flow(proj).run()
    a, b = proj / "a", proj / "b"
    for d in (a, b):
        d.mkdir()
        (d / "report.json").write_text(json.dumps(rep))
    assert cli_main(["flow", "report", str(a)]) == 0 and (a / "report.html").read_text().startswith("<!doctype html>")
    capsys.readouterr()
    assert cli_main(["flow", "diff", str(a), str(b)]) == 0 and "same sources" in capsys.readouterr().out
    (a / "report.json").write_text(json.dumps({"schema": "other"}))
    assert cli_main(["flow", "diff", str(a), str(b)]) == 2


def test_reports_carry_no_absolute_paths(proj):
    """Reports are meant to be kept and committed: no local directory or user name may leak into them."""
    ll = FakeLibreLane(dict(GOOD))
    orig = ll.run_config

    def with_paths(work, cfg, knobs, timeout):
        ppa, m = orig(work, cfg, knobs, timeout)
        ppa.run_dir = str(Path(work).resolve())
        ppa.gds_path = str(Path(work).resolve() / "runs" / "sf" / "final" / "gds" / "top.gds")
        ppa.extra["render_png"] = str(Path(work).resolve() / "runs" / "sf" / "final" / "render" / "top.png")
        return ppa, m

    ll.run_config = with_paths
    rep = _flow(proj, ll=ll).run()
    text = json.dumps(rep)
    assert str(proj.resolve()).replace("\\", "\\\\") not in text and str(proj) not in text
    assert rep["ppa"]["gds_path"] == "pnr/runs/sf/final/gds/top.gds" and rep["ppa"]["run_dir"] == "pnr"
    assert rep["ppa"]["extra"]["render_png"] == "pnr/runs/sf/final/render/top.png"
