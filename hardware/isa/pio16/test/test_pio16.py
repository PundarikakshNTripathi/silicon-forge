"""cocotb tests for the pio16 reference design.

* test_lockstep: constrained-random programs run on the RTL and on the golden model generated from isa.yaml; the full
  architectural state is compared after every instruction, and the clock gap between instructions is compared with the
  model's cycle counts. Functional coverage accumulates into coverage.json across seeds.
* test_uart_byte: the assembly UART transmitter drives a pin; an independent decoder (it knows nothing about this RTL)
  reads the pin and must see 0x55 in a clean frame with every edge exactly on the bit grid.
* test_illegal_word_halts: an undefined encoding halts the engine, as the model does.

Environment: SF_SEEDS (number of random programs, default 40), SF_TOOLS (directory with sf_lockstep.py / sf_protocols.py).
"""
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gen" / "tools"))
sys.path.insert(0, os.environ.get("SF_TOOLS") or str(HERE.parents[3] / "services" / "orchestrator" / "verify" / "tools"))

import pio16_isa as isa  # noqa: E402  (generated: assembler, model, random programs)
from sf_lockstep import CoverGroup, lockstep  # noqa: E402
from sf_protocols import uart  # noqa: E402

COVERAGE_FILE = os.environ.get("SF_COVERAGE", str(HERE / "coverage.json"))


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.run.value = 0
    dut.prog_we.value = 0
    dut.prog_addr.value = 0
    dut.prog_data.value = 0
    dut.pins_in.value = 0
    for _ in range(3):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def load(dut, words):
    for addr in range(32):
        dut.prog_we.value = 1
        dut.prog_addr.value = addr
        dut.prog_data.value = words[addr] if addr < len(words) else 0xFFFF  # pad with an undefined word
        await FallingEdge(dut.clk)
    dut.prog_we.value = 0


def rtl_state(dut):
    return {"pc": int(dut.pc.value), "x": int(dut.x.value), "y": int(dut.y.value), "pins": int(dut.pins_out.value),
            "oe": int(dut.oe.value), "halted": int(dut.halted.value)}


def make_coverage(spec):
    cov = CoverGroup("pio16")
    cov.point("instr", bins=[i.name for i in spec.instructions])
    cov.point("pins_value", bins={"zero": 0, "all_ones": 255, "mixed": lambda v: 0 < v < 255})
    cov.point("x_after", bins={"zero": 0, "one": 1, "max": 255, "other": lambda v: 1 < v < 255})
    cov.point("branch", bins=["djnz_taken", "djnz_not_taken", "jpin_taken", "jpin_not_taken"])
    cov.point("delay_len", bins={"zero": 0, "short": lambda v: 0 < v <= 8, "long": lambda v: v > 8})
    return cov


@cocotb.test()
async def test_lockstep(dut):
    spec = isa.load()
    cov = make_coverage(spec)
    n_programs = int(os.environ.get("SF_SEEDS", "40"))
    base_seed = int(os.environ.get("RANDOM_SEED", "1"))
    compared = 0
    for k in range(n_programs):
        rng = random.Random(base_seed * 1000 + k)
        await reset(dut)
        words = isa.random_program(spec, rng, length=rng.randrange(6, 32),
                                   weights={"delay": 0.4, "halt": 0.3, "nop": 0.5}, end_with="halt")
        await load(dut, words)
        model = isa.Machine(spec, words, illegal="halt")
        state = {"gap": None, "prev_cycles": None}

        def drive(m, dut=dut, rng=rng):
            """Same random input pins for the model and the RTL, chosen before either runs the instruction."""
            pins = rng.randrange(256)
            m.inputs = {"pins": pins}
            dut.pins_in.value = pins

        async def step_rtl(result, dut=dut, state=state):
            if state["gap"] is None:
                dut.run.value = 1
                state["gap"] = 0
            gap = 0
            while True:
                await FallingEdge(dut.clk)
                gap += 1
                if int(dut.retire.value) == 1:
                    break
                assert gap < 600, "RTL never retired an instruction"
            if state["prev_cycles"] is not None:
                assert gap == state["prev_cycles"], (
                    f"clock gap {gap} after the previous instruction, the model says it takes {state['prev_cycles']}")
            state["prev_cycles"] = result.cycles

        def on_step(r, model=model):
            cov.sample("instr", r.instr.name)
            cov.sample("pins_value", model.s.pins)
            cov.sample("x_after", model.s.x)
            if r.instr.name == "djnz":
                cov.sample("branch", "djnz_taken" if model.s.pc == r.operands["t"] else "djnz_not_taken")
            if r.instr.name == "jpin":
                cov.sample("branch", "jpin_taken" if model.s.pc == r.operands["t"] else "jpin_not_taken")
            if r.instr.name == "delay":
                cov.sample("delay_len", r.operands["n"])

        compared += await lockstep(step_rtl, lambda dut=dut: rtl_state(dut), model, steps=250, on_step=on_step,
                                   before_step=drive)
    report = cov.save(COVERAGE_FILE)
    dut._log.info(f"lockstep: {compared} instructions compared over {n_programs} programs; "
                  f"functional coverage {report['coverage']:.1%}")
    assert compared > 500


@cocotb.test()
async def test_uart_byte(dut):
    spec = isa.load()
    prog = isa.assemble((HERE.parent / "uart_tx.asm").read_text())
    await reset(dut)
    await load(dut, prog.image())
    dut.run.value = 1
    edges, t, last = [], 0, None
    for _ in range(400):
        await FallingEdge(dut.clk)
        level = (int(dut.pins_out.value) & 1) if int(dut.oe.value) & 1 else 1  # an undriven pin idles high
        if level != last:
            edges.append((t, level))
            last = level
        t += 1
        if int(dut.halted.value):
            break
    frames = uart(edges, bit_time=16)
    assert len(frames) == 1, frames
    f = frames[0]
    assert f["byte"] == 0x55 and f["framing_ok"], f
    assert f["max_edge_error"] == 0.0, f"edges are off the 16-cycle bit grid: {f}"
    assert spec.word_bits == 16


@cocotb.test()
async def test_illegal_word_halts(dut):
    spec = isa.load()
    await reset(dut)
    await load(dut, [0xFFFF])
    model = isa.Machine(spec, [0xFFFF], illegal="halt")
    dut.run.value = 1
    model.step()
    for _ in range(3):
        await FallingEdge(dut.clk)
    assert rtl_state(dut) == model.s.snapshot()
