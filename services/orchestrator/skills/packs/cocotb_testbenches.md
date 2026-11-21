# Writing cocotb testbenches

cocotb drives a simulator from Python. These are the patterns that make tests correct and repeatable, written for cocotb 2.

## Test structure and clock
A test is an `async def` decorated with `@cocotb.test()`. Start the clock first, then reset, then stimulus:

    import cocotb
    from cocotb.clock import Clock
    from cocotb.triggers import RisingEdge

    @cocotb.test()
    async def counts_up(dut):
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        dut.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(dut.clk)
        dut.rst_n.value = 1

`start_soon` runs the clock in the background. Release reset on a clock edge, never at time zero.

## Reading signals right after a clock edge
After `await RisingEdge(dut.clk)` a signal still shows its value from before the edge, because the design has not updated yet.
To read what the edge produced, wait a little more: `await ReadOnly()` for a read at the end of the time step, or `await
FallingEdge(dut.clk)` and sample there. Drive inputs after the edge and sample outputs on the falling edge, and a test
behaves the same in RTL and in gate-level simulation.

## Driving and comparing values
Assign with `.value = 5`; read with `int(dut.q.value)` or `dut.q.value.to_unsigned()`. A value with `x` or `z` bits raises
when converted to an integer: that is a useful failure, it means something was read before it was written. Compare with
`assert int(dut.q.value) == expected, f"q={int(dut.q.value)} expected {expected} at cycle {n}"` so a failure says where.

## Check against a model not against the design
Write the expected behaviour as a few lines of Python and compare cycle by cycle. A test that recomputes the answer with the
same logic as the RTL passes when both are wrong. For protocols, decode the pins independently and compare the decoded bytes.

## Randomness and repeatability
Use `random` seeded from the `RANDOM_SEED` environment variable the runner provides, and print the seed on failure. Constrain
the stimulus (legal addresses, legal opcodes) so that failures are about the design, not about an illegal input.

## Coverage of the interesting cases
Cover reset in the middle of operation, back-to-back operations, the first and last value of every counter, full and empty for
queues, and simultaneous read and write. A test suite that only checks the happy path can pass a design with a broken edge case.

## Failing on purpose
To check that a test can fail, change the RTL (invert one condition) and confirm the test goes red. Mutation testing does
this systematically: tests that do not notice a planted bug are not testing that logic.
