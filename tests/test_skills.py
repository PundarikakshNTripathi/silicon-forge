# SPDX-License-Identifier: AGPL-3.0-or-later
"""Knowledge packs: chunking, retrieval relevance on real diagnostics, the relevance floor, user overrides and agent wiring."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.orchestrator.agents.rtl_coder import RtlCoderAgent
from services.orchestrator.skills.registry import SkillRegistry, split_chunks, tokenize

PROMPTS = Path(__file__).resolve().parents[1] / "configs" / "prompts"


@pytest.fixture(scope="module")
def reg(tmp_path_factory):
    return SkillRegistry(user_dir=tmp_path_factory.mktemp("nouser"))


def top(reg, query, k=1):
    return [c.name for c in reg.retrieve(query, top_k=k)]


# ---------------------------------------------------------------------------------------------------- chunks


def test_packs_split_into_sections_and_each_section_keeps_its_heading():
    chunks = split_chunks("demo", "# Title\nA lead paragraph with enough words to count as its own section here.\n\n## One\nbody one\n\n## Two\nbody two\n")
    assert [c.heading for c in chunks] == ["Title", "One", "Two"]
    assert chunks[1].content.startswith("## One") and chunks[1].name == "demo: One"


def test_tokenizer_splits_identifiers_and_keeps_the_whole_token():
    t = tokenize("FP_CORE_UTIL %Warning-WIDTH")
    assert {"fp_core_util", "fp", "core", "util", "width", "warning"} <= set(t)


def test_the_builtin_packs_are_substantial_and_chunked(reg):
    names = {p.name for p in reg.packs}
    assert {"verilator_warnings", "sv_synthesizable_idioms", "cdc_and_resets", "handshake_and_fifo", "sdc_basics", "librelane_knobs",
            "cocotb_testbenches", "sva_formal_patterns", "openroad_tcl", "sky130_pdk"} <= names
    assert len(reg.chunks) >= 50 and all(len(c.content.split()) >= 10 for c in reg.chunks)


# ------------------------------------------------------------------------------- relevance on real diagnostics


@pytest.mark.parametrize("query,expected", [
    # messages seen in local-model runs
    ("%Error: up_down_counter.sv:33:9: Can't find definition of variable: 'next_wrap'", "Can't find definition of variable"),
    ("axi_lite_alu.sv:118:13: syntax error, unexpected '=', expecting '('  ready_o = valid_reg;", "syntax error unexpected '='"),
    ("Blocking assignment ('=') inside sequential always_ff block. SF-RTL-004", "BLKSEQ"),
    ("Non-blocking assignment ('<=') inside combinational always_comb block. SF-RTL-005", "COMBDLY"),
    ("%Warning-WIDTH: Operator ASSIGN expects 4 bits on the Assign RHS, but Assign RHS's ADD generates 8 bits", "WIDTH"),
    ("Potential inferred latch in always_comb: 'if' without 'else' or default. SF-RTL-006", "LATCH"),
    ("%Warning-MULTIDRIVEN: Signal has multiple driving blocks", "MULTIDRIVEN"),
    ("Example path: result_o ASSIGNW UNOPTFLAT combinational loop", "UNOPTFLAT"),
])
def test_a_real_diagnostic_finds_the_section_about_it(reg, query, expected):
    hits = top(reg, query, k=2)
    assert hits and any(expected in h for h in hits), hits
    assert hits[0].startswith("verilator_warnings")


@pytest.mark.parametrize("query,pack", [
    ("two flop synchronizer metastability crossing clock domains", "cdc_and_resets"),
    ("valid ready handshake source must hold data until ready", "handshake_and_fifo"),
    ("set_input_delay create_clock sdc constraint", "sdc_basics"),
    ("FP_CORE_UTIL PL_TARGET_DENSITY_PCT congestion routing", "librelane_knobs"),
    ("cocotb RisingEdge read value after clock edge", "cocotb_testbenches"),
    ("property uses cover posedge f_past_valid helper signal", "sva_formal_patterns"),
    ("finite state machine next_state enum template", "sv_synthesizable_idioms"),
])
def test_topic_queries_land_in_the_right_pack(reg, query, pack):
    assert top(reg, query)[0].startswith(pack)


def test_a_query_that_matches_nothing_returns_nothing_instead_of_unrelated_text(reg):
    assert reg.retrieve("quarterly marketing budget for the cafeteria") == []
    assert reg.format_context("zzzz qqqq") == ""


def test_the_formal_pack_no_longer_recommends_the_helper_signal_the_guardrail_rejects(reg):
    text = " ".join(c.content for c in reg.chunks if c.pack == "sva_formal_patterns")
    assert "no helper" in text.lower() or "helper signals" in text.lower()
    assert "initial f_past_valid" not in text  # the old advice that made models write it into their properties


# ------------------------------------------------------------------------------------ user packs and wiring


def test_a_user_pack_adds_to_and_overrides_the_builtins(tmp_path):
    (tmp_path / "team_rules.md").write_text("# Team rules\n\n## Naming\nAll clock ports are named clk_core and every reset is rst_core_n.\n")
    (tmp_path / "sdc_basics.md").write_text("# My SDC notes\n\n## create_clock\nOur clock is always 250 MHz on port clk_core.\n")
    r = SkillRegistry(user_dir=tmp_path)
    sources = {p.name: p.source for p in r.packs}
    assert sources["team_rules"] == "user" and sources["sdc_basics"] == "user" and sources["verilator_warnings"] == "builtin"
    assert "250 MHz" in r.format_context("create_clock sdc") and "naming" in top(r, "clk_core reset naming")[0].lower()


def test_the_rtl_coder_puts_relevant_guidance_in_a_repair_prompt_and_nothing_when_irrelevant(reg):
    class Router:
        def __init__(self):
            self.prompts = []

        def complete(self, **kw):
            self.prompts.append(kw["user_prompt"])
            return SimpleNamespace(content="```systemverilog\nmodule m; endmodule\n```")

    router = Router()
    agent = RtlCoderAgent(router, reg, PROMPTS)
    contract = SimpleNamespace(module_name="m")
    agent.repair_rtl(contract, "module m;\n  ready_o = v;\nendmodule", "%Error: m.sv:2:11: syntax error, unexpected '=', expecting '('")
    assert "Relevant guidance" in router.prompts[0] and "assign ready_o" in router.prompts[0]
    agent.repair_rtl(contract, "module m; endmodule", "zzzz qqqq nothing relevant")
    assert "Relevant guidance" not in router.prompts[1]


def test_the_knowledge_tool_answers_and_admits_when_nothing_matches(tmp_path):
    from services.orchestrator.agent.tools import READ, ToolBox
    from services.orchestrator.workspace import init_workspace

    tb = ToolBox(init_workspace(tmp_path / "w", profile="dev", backend="simulated"))
    assert tb.by_name()["knowledge"].permission == READ
    hit = tb.call("knowledge", {"query": "LATCH inferred latch always_comb"})
    assert hit["found"] >= 1 and "LATCH" in hit["sections"][0]["name"]
    assert tb.call("knowledge", {"query": "quarterly cafeteria budget"})["found"] == 0


@pytest.mark.parametrize("query,expected", [("LATCH", "LATCH"), ("width", "WIDTH"), ("MULTIDRIVEN", "MULTIDRIVEN"), ("UNOPTFLAT", "UNOPTFLAT"),
                                            ("cocotb clock", "cocotb_testbenches")])
def test_short_exact_queries_find_their_section_by_heading(reg, query, expected):
    hits = top(reg, query)
    assert hits and expected in hits[0]


@pytest.mark.parametrize("query", ["nothing relevant", "uart", "hello world please help me today", "the cat sat on the mat",
                                   "quarterly marketing budget for the cafeteria"])
def test_short_and_long_irrelevant_queries_return_nothing(reg, query):
    assert reg.retrieve(query) == []
