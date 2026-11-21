"""ISA toolchain: spec validation, assembler/disassembler, golden model, timing checker, generators, standalone bundle."""
from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from services.orchestrator import isa
from services.orchestrator.isa.spec import parse_spec

ROOT = Path(__file__).resolve().parents[1]
PIO16 = ROOT / "hardware" / "isa" / "pio16"


@pytest.fixture(scope="module")
def spec():
    return isa.load_spec(PIO16 / "isa.yaml")


@pytest.fixture(scope="module")
def uart(spec):
    return isa.Assembler(spec).assemble((PIO16 / "uart_tx.asm").read_text(encoding="utf-8"))


def asm(spec, text):
    return isa.Assembler(spec).assemble(text)


def raw():
    return yaml.safe_load((PIO16 / "isa.yaml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------- spec checks

def test_example_spec_loads(spec):
    assert spec.word_bits == 16 and len(spec.instructions) == 10 and spec.max_addr == 31


def test_overlapping_encodings_are_rejected():
    r = raw()
    r["instructions"].append({"name": "clash", "encoding": "0011 0000 ---- ----"})  # a subset of `pins`
    with pytest.raises(isa.IsaError, match="overlap"):
        parse_spec(r)


def test_wrong_length_missing_operand_and_bad_template_are_rejected():
    r = raw()
    r["instructions"][1]["encoding"] = "0001 vvvvvvvv ---"
    with pytest.raises(isa.IsaError, match="15 bits"):
        parse_spec(r)
    r = raw()
    r["instructions"][1]["operands"] = {}
    with pytest.raises(isa.IsaError, match="no operand definition"):
        parse_spec(r)
    r = raw()
    r["instructions"][1]["asm"] = "ldx {nope}"
    with pytest.raises(isa.IsaError, match="asm template"):
        parse_spec(r)


def test_enum_must_fit_its_field():
    r = {"name": "t", "word_bits": 8, "state": {"pc": 4}, "enums": {"e": {"a": 0, "big": 7}},
         "instructions": [{"name": "x", "encoding": "0000 0 dd -", "asm": "x {d}",
                           "operands": {"d": {"name": "d", "enum": "e"}}}]}
    with pytest.raises(isa.IsaError, match="does not fit"):
        parse_spec(r)


# ----------------------------------------------------------------------------------------------- assembler

def test_assembles_known_words(spec):
    p = asm(spec, "ldx 0x2a\npins 0xff\ndelay 3\nhalt\n")
    assert p.image() == [0x12A0, 0x3FF0, 0x5030, 0xF000]


def test_labels_directives_and_expressions(spec):
    p = asm(spec, ".equ N, 2 + 3\nstart: ldx N * 2   ; comment\n  jmp end // other comment\n.org 5\nend: halt\n")
    assert p.labels == {"start": 0, "end": 5} and p.words[0] == 0x10 << 8 | 10 << 4 and p.words[1] == 0x6005
    assert p.words[5] == 0xF000


def test_errors_are_collected_with_line_numbers(spec):
    with pytest.raises(isa.AsmError) as ei:
        asm(spec, "ldx 300\nfrobnicate\njmp nowhere\njpin 9, 0\ndup: nop\ndup: nop\n")
    msg = "\n".join(ei.value.errors)
    assert "line 1" in msg and "does not fit in 8 bits" in msg
    assert "line 2" in msg and "cannot parse" in msg
    assert "line 3" in msg and "unknown symbol" in msg
    assert "line 4" in msg and "p=9" in msg
    assert "line 6" in msg and "defined twice" in msg


def test_flexible_spacing_and_case(spec):
    assert asm(spec, "  LDX   5 \nJPIN 2,3\n").words == asm(spec, "ldx 5\njpin 2, 3\n").words


def test_program_must_fit_the_program_counter(spec):
    with pytest.raises(isa.AsmError, match="does not fit in the program counter"):
        asm(spec, ".org 40\nnop\n")


def test_round_trip_property(spec):
    """assemble -> decode -> disassemble -> assemble reproduces every word, for random operands of every instruction."""
    rng = random.Random(1234)
    for instr in spec.instructions:
        for _ in range(40):
            word = instr.match
            for op in instr.operands:
                v = rng.choice(list(op.enum.values())) if op.enum else rng.randrange(1 << op.field.width)
                word |= v << op.field.lsb
            text = isa.disassemble_word(spec, word)
            # labels are plain addresses in this spec; the disassembly must reassemble to the same word
            assert asm(spec, text).words[0] == word, (instr.name, text)
            assert isa.decode(spec, word)[0].name == instr.name


def test_every_word_decodes_to_at_most_one_instruction(spec):
    for w in range(1 << spec.word_bits):
        hits = [i for i in spec.instructions if w & i.mask == i.match]
        assert len(hits) <= 1


# ----------------------------------------------------------------------------------------------- golden model

def test_uart_program_runs_and_ends_with_the_line_idle(spec, uart):
    m = isa.Machine(spec, uart)
    assert m.run() == len([p for p in uart.placed])  # straight-line: every instruction retires once
    assert m.halted and m.s.pins == 0x01 and m.s.oe == 0x01
    assert m.cycles == 1 + 11 * 16 + 1  # oe, eleven (pins + delay 14) pairs of 16 cycles (idle, start, 8 data, stop), halt


def test_state_writes_wrap_to_their_widths(spec):
    m = isa.Machine(spec, asm(spec, "ldx 0\ndjnz 1\nhalt\n"))
    m.step()
    m.step()  # x = 0 - 1 wraps to 255, so it is non-zero and jumps back to 1
    assert m.s.x == 255 and m.s.pc == 1
    with pytest.raises(AttributeError):
        m.s.bogus = 1


def test_djnz_loop_counts_down(spec):
    m = isa.Machine(spec, asm(spec, "ldx 3\nloop: pins 1\ndjnz loop\nhalt\n"))
    m.run()
    assert m.s.x == 0 and m.retired == 1 + 3 * 2 + 1


def test_input_pin_branch(spec):
    prog = asm(spec, "jpin 2, hit\nldy 1\nhalt\nhit: ldy 9\nhalt\n")
    for pins, want in ((0b100, 9), (0b000, 1)):
        m = isa.Machine(spec, prog)
        m.inputs = {"pins": pins}
        m.run()
        assert m.s.y == want


def test_illegal_instruction_policies(spec):
    with pytest.raises(isa.IllegalInstruction):
        isa.Machine(spec, [0xFFFF]).step()
    m = isa.Machine(spec, [0xFFFF], illegal="halt")
    m.step()
    assert m.halted


# --------------------------------------------------------------------------------------------- timing checker

def test_uart_gaps_are_proven_exact(uart):
    rep = isa.check_timing(uart)
    assert rep.ok, rep.text()
    gaps = [g for g in rep.static if g.src >= 0 and g.dst > 1]
    assert gaps and all(g.exact and g.min_cycles == 16 for g in gaps)
    assert any("proven by the control-flow graph" in n for n in rep.notes)


def test_wrong_expectation_is_reported_on_both_views(spec):
    rep = isa.check_timing(asm(spec, "pins 1\ndelay 14\n.expect 15\npins 0\nhalt\n"))
    assert not rep.ok
    text = rep.text()
    assert "expected 15 cycles" in text and "outside the static range" in text


def test_unbounded_loop_has_an_open_maximum(spec):
    rep = isa.static_gaps(asm(spec, "pins 1\nloop: jpin 0, loop\npins 0\nhalt\n"))
    gap = next(g for g in rep.static if g.src == 0)
    assert gap.max_cycles is None and any("not bounded" in n for n in rep.notes)


def test_bound_pragma_closes_a_loop_into_a_range(spec):
    src = "ldx 4\npins 1\nloop: delay 2\n.bound 3\ndjnz loop\npins 0\nhalt\n"
    rep = isa.static_gaps(asm(spec, src))
    gap = next(g for g in rep.static if g.src == 1)  # from the first pin event to the second
    # shortest: pins(1) + delay(3) + djnz(1) = 5; longest adds three more trips through delay+djnz (4 each)
    assert (gap.min_cycles, gap.max_cycles) == (5, 5 + 3 * 4)
    # the real counter runs 4 iterations; that is outside the claimed bound, but the trace measures what happens
    assert [c for _, d, c in isa.trace_gaps(asm(spec, src)) if d == 4] == [1 + 4 * 4]


def test_expect_never_reached_is_a_problem(spec):
    rep = isa.check_timing(asm(spec, "pins 1\nhalt\n.expect 4\npins 0\n"))
    assert not rep.ok and "never ran" in rep.text()


# ------------------------------------------------------------------------------------------------- generators

def test_generated_decoder_uses_the_specs_masks(spec):
    v = isa.generate_all  # noqa: F841 - imported for coverage of the public surface
    from services.orchestrator.isa.gen import decoder_formal, verilog_decoder, verilog_header

    dec = verilog_decoder(spec)
    for i in spec.instructions:
        assert f"is_{i.name} = (instr & 16'h{i.mask:04x}) == 16'h{i.match:04x}" in dec
    assert "assign illegal" in dec and "`default_nettype none" in dec
    hdr = verilog_header(spec)
    assert "`define PIO16_LDX_V(w) (w[11:4])" in hdr and "`define PIO16_JPIN_P(w) (w[10:8])" in hdr
    sv, sby = decoder_formal(spec)
    assert "assert ((hits & (hits - 1'b1)) == 0)" in sv and "mode prove" in sby


def test_docs_have_a_diagram_per_instruction(spec, tmp_path):
    files = isa.generate_all(PIO16 / "isa.yaml", tmp_path)
    names = {f.name for f in files}
    assert {"isa.md", "pio16_isa.py", "pio16_decode.v", "pio16_isa.vh", "pio16_decode.sby"} <= names
    assert {f"{i.name}.svg" for i in spec.instructions} <= names
    md = (tmp_path / "docs" / "isa.md").read_text(encoding="utf-8")
    assert "![ldx encoding](isa/ldx.svg)" in md and "| `pins` |" in md
    svg = (tmp_path / "docs" / "isa" / "jpin.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and "aria-label=\"jpin encoding\"" in svg and ">p<" in svg


def test_bundle_is_standalone_and_agrees_with_the_package(spec, uart, tmp_path):
    isa.generate_all(PIO16 / "isa.yaml", tmp_path)
    script = tmp_path / "use_bundle.py"
    script.write_text(
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "import pio16_isa as b\n"
        "p = b.assemble(open(sys.argv[2]).read())\n"
        "m = b.Machine(b.load(), p); m.run()\n"
        "print(','.join(f'{w:04x}' for w in p.image()), m.cycles, m.s.pins)\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "-I", str(script), str(tmp_path / "tools"), str(PIO16 / "uart_tx.asm")],
                         capture_output=True, text=True, check=True, cwd=tmp_path).stdout.split()
    m = isa.Machine(spec, uart)
    m.run()
    assert out[0] == ",".join(f"{w:04x}" for w in uart.image()) and int(out[1]) == m.cycles and int(out[2]) == m.s.pins
    assert "services" not in (tmp_path / "tools" / "pio16_isa.py").read_text(encoding="utf-8").split("SPEC_DICT")[0]


def test_bundle_refuses_a_broken_spec(tmp_path):
    bad = tmp_path / "bad.yaml"
    r = raw()
    r["instructions"].append({"name": "clash", "encoding": "0011 0000 ---- ----"})
    bad.write_text(yaml.safe_dump(r), encoding="utf-8")
    with pytest.raises(isa.IsaError):
        isa.bundle_source(bad)


# --------------------------------------------------------------------------------------------- real tools

@pytest.mark.real_eda
def test_generated_decoder_matches_python_on_every_word(spec, real_backend, tmp_path):
    """Simulate the generated Verilog decoder in Icarus on all 65,536 words and compare with the Python decoder."""
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge
    from services.orchestrator.isa.gen import verilog_decoder

    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    runner = EDABridge(cfg).backend.runner
    names = [i.name for i in spec.instructions]
    (tmp_path / "dec.v").write_text(verilog_decoder(spec), encoding="utf-8")
    wires = "".join(f"  wire h_{n};\n" for n in names)
    conns = ", ".join(f".is_{n}(h_{n})" for n in names)
    cat = ", ".join(f"h_{n}" for n in reversed(names))
    (tmp_path / "tb.v").write_text(
        f"module tb;\n  reg [15:0] w; wire ill;\n{wires}  pio16_decode d(.instr(w), {conns}, .illegal(ill));\n"
        f"  integer i;\n  initial begin\n    for (i = 0; i < 65536; i = i + 1) begin\n      w = i[15:0]; #1;\n"
        f"      $display(\"%b %b\", {{{cat}}}, ill);\n    end\n    $finish;\n  end\nendmodule\n", encoding="utf-8")
    assert runner.run(["iverilog", "-g2005", "-o", "dec.vvp", "tb.v", "dec.v"], tmp_path).ok
    res = runner.run(["vvp", "dec.vvp"], tmp_path)
    lines = [ln for ln in res.stdout.splitlines() if ln and ln[0] in "01"]
    assert len(lines) == 65536
    for w, ln in enumerate(lines):
        bits, ill = ln.split()
        hit = isa.decode(spec, w)
        want = ["0"] * len(names)
        if hit:
            want[len(names) - 1 - names.index(hit[0].name)] = "1"
        assert bits == "".join(want) and ill == ("0" if hit else "1"), f"word {w:04x}"


@pytest.mark.real_eda
def test_decoder_is_formally_one_hot(spec, real_backend, tmp_path):
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge

    isa.generate_all(PIO16 / "isa.yaml", tmp_path)
    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    runner = EDABridge(cfg).backend.runner
    res = runner.run(["sby", "-f", "pio16_decode.sby"], tmp_path / "formal", timeout=300)
    assert res.ok and "DONE (PASS" in res.log, res.log[-800:]
