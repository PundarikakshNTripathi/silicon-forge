# Synthesizable SystemVerilog idioms

Patterns that synthesize to the hardware you intend, and that Icarus Verilog, Verilator and Yosys all accept. Write
Verilog-2001-compatible SystemVerilog: `logic`, `always_ff` and `always_comb` are fine; classes, interfaces and dynamic
arrays are not.

## Sequential logic always_ff register template
One clocked block per group of registers, non-blocking assignments only, reset first:

    always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) q <= '0;
      else if (en) q <= d;
    end

Every register is assigned in exactly one block. An enable is an `if`, not a mux written by hand.

## Combinational logic always_comb default assignment
Give every signal a value at the top of the block, then override it. This prevents inferred latches:

    always_comb begin
      y = 1'b0;
      if (sel) y = a;
    end

Use blocking `=` in this block. Declare any temporary (`next_state`) at module scope before the block.

## Reset style asynchronous assert synchronous release
Use the reset polarity and style named in the design contract. For an active-low asynchronous reset the sensitivity list is
`posedge clk or negedge rst_n` and the first `if` tests `!rst_n`. Reset every register that has architectural meaning;
registers that are always written before they are read need no reset.

## Finite state machine template
Declare the state type once, keep the next-state logic combinational and the state register sequential:

    typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
    state_t state, next_state;
    always_ff @(posedge clk or negedge rst_n)
      if (!rst_n) state <= IDLE; else state <= next_state;
    always_comb begin
      next_state = state;
      case (state)
        IDLE: if (start) next_state = RUN;
        RUN:  if (finish) next_state = DONE;
        DONE: next_state = IDLE;
        default: next_state = IDLE;
      endcase
    end

Outputs are decoded from `state` in a separate `always_comb` with a default value.

## Counters wraparound and terminal count
A counter that wraps at N counts to N-1 and resets, not to N: `if (cnt == N-1) cnt <= '0; else cnt <= cnt + 1'b1;`. The width
must hold N-1. Compare with `==` for a terminal count; use `>=` only when the count can skip values.

## Widths signed and unsigned arithmetic
Size every literal. Addition and subtraction keep the width of the widest operand, so capture a carry by widening first:
`{1'b0, a} + {1'b0, b}`. Mixing signed and unsigned operands in one expression makes the whole expression unsigned; declare
signals `signed` consistently or cast with `$signed()`.

## What not to write in synthesizable RTL
No `#delay`, no `initial` blocks (use reset), no `$display` or `$finish`, no `force`/`release`, no tri-state internal buses,
no `casex` (use `casez` with `?` or full `case`), no multiple drivers on one signal, no combinational loops.

## Ports exactly as in the contract
The module name, port names, directions and widths must match the design contract exactly. Do not add, rename or re-width a
port to make an internal problem easier; fix the internals.
