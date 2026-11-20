"""Cocotb golden verification testbench for axi_lite_alu."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


@cocotb.test()
async def test_alu_operations(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    dut.rst_n.value = 0
    dut.valid_i.value = 0
    dut.op_i.value = 0
    dut.a_i.value = 0
    dut.b_i.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    assert int(dut.valid_o.value) == 0
    assert int(dut.result_o.value) == 0

    dut.rst_n.value = 1
    vectors = [
        (0, 15, 27, 42),
        (1, 50, 50, 0),
        (2, 0xF0F0, 0x0FF0, 0x00F0),
        (3, 0x1000, 0x0001, 0x1001),
        (4, 0xAAAA, 0x5555, 0xFFFF),
    ]
    for op, a, b, expected in vectors:
        dut.valid_i.value = 1
        dut.op_i.value = op
        dut.a_i.value = a
        dut.b_i.value = b
        await RisingEdge(dut.clk)
        await RisingEdge(dut.clk)
        assert int(dut.valid_o.value) == 1
        assert int(dut.result_o.value) == expected
        assert int(dut.zero_o.value) == (1 if expected == 0 else 0)
