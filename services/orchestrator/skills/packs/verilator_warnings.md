# Verilator lint messages and how to fix them

Verilator is run with `--lint-only -Wall`. An `%Error` stops the flow; a `%Warning-NAME` is reported and counted. The fix for
each common message is below. Change only the lines the message names.

## WIDTH WIDTHTRUNC WIDTHEXPAND width mismatch
The two sides of an assignment or operator have different bit widths (for example an 8-bit expression assigned to a 4-bit
signal, or a 32-bit `integer` added to a 4-bit counter). Make the widths agree: size literals (`4'd1`, not `1`), slice
deliberately (`sum[3:0]`), or zero-extend with a concatenation (`{4'd0, nibble}`). Do not silence the warning with a pragma
when the truncation is a real bug.

## UNUSEDSIGNAL UNUSEDPARAM unused signal or parameter
A signal or parameter is declared and never read. It is a warning, not an error. Delete it, or use it. Unused input bits that
belong to a fixed port list are fine; do not change the ports to remove the warning.

## UNDRIVEN UNASSIGNED signal never assigned
A signal is read but nothing drives it, or a vector has bits nothing drives. Assign it in a combinational block or with
`assign`, or remove it. An undriven output port is always a bug.

## CASEINCOMPLETE case statement without default
A `case` does not cover every value and has no `default`. Add `default:` that assigns every signal the block drives. In a
combinational block this also prevents an inferred latch.

## LATCH inferred latch combinational block does not assign on every path
In `always_comb`, a signal is assigned under an `if` but not in the `else`, or in some `case` arms only. Put a default
assignment for every driven signal at the top of the block (`y = 1'b0;`) and then override it, or complete every branch.

## BLKSEQ blocking assignment in a sequential block
Inside `always_ff @(posedge clk)` use non-blocking assignment `<=` for registers. A blocking `=` there is reported by
Verilator (BLKSEQ) and by SiliconForge as `SF-RTL-004`.

## COMBDLY non-blocking assignment in a combinational block
Inside `always_comb` or `always @*` use blocking assignment `=`. A non-blocking `<=` that is a real assignment there is
reported as COMBDLY and as `SF-RTL-005`. A `<=` that is a less-or-equal comparison (`if (count <= LIMIT)`) is not an
assignment and is fine.

## MULTIDRIVEN signal driven from more than one block
Two `always` blocks, or an `assign` and an `always`, drive the same signal. Each signal must have exactly one driver: move
all assignments to one block, or split the signal into two.

## UNOPTFLAT combinational loop
The path printed under "Example path" is a loop through combinational logic, often because a vector is both read and
written in one `always_comb`. Break the loop with a register, or split the vector into separate signals so that each bit
depends only on earlier ones. A genuine combinational loop is never intended in synchronous RTL.

## Can't find definition of variable undeclared signal
A name is used and never declared, typically a temporary inside `always_comb` (`next_state`, `next_count`). Declare it before
use at module scope: `logic [3:0] next_count;`. The error line shows the first use.

## syntax error unexpected '=' statement outside a block
A line such as `ready_o = valid_reg;` sits at module level, where only declarations, `assign`, instances and `always` blocks
are allowed. Write `assign ready_o = valid_reg;` for continuous logic, or move the statement inside an `always_comb` block.

## syntax error expecting reserved word as an identifier
A signal is named with a keyword (`input`, `output`, `reg`, `wire`, `logic`, `type`, `bit`, `do`, `default`). Rename it, for
example `data_in`, `reg_q`.

## SiliconForge static lint rules SF-RTL
SF-RTL-001: a `#delay` was found; delays are not synthesizable, remove them. SF-RTL-002: an `initial` block outside
`` `ifdef FORMAL ``; use the reset instead. SF-RTL-003: a `$display`-style task in RTL (warning). SF-RTL-004: blocking `=` in
`always_ff`. SF-RTL-005: non-blocking `<=` assignment in `always_comb`. SF-RTL-006: possible latch, an `if` without `else`
in `always_comb` (warning).
