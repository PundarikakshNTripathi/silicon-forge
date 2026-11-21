# Clock-domain crossings and resets

A signal that crosses between two unrelated clocks can be sampled while it changes (metastability). These are the standard
structures that make that safe, and what each is for.

## Two-flop synchronizer single-bit level crossing
For one slowly changing bit: two flip-flops in series in the receiving domain, nothing but wires between them, and no logic
in front of the first flop that could glitch. It is safe for a level that stays stable for at least two receiving-clock
periods. It is not safe for a pulse that may be shorter than that, or for several bits that must change together.

## Pulse synchronizer single-cycle event crossing
To move a one-cycle event: toggle a flip-flop in the source domain, synchronize the toggle with a two-flop synchronizer, then
detect the edge (XOR of the synchronized bit and its delayed copy) in the receiving domain. The source must not produce
another event until the previous one has been seen.

## Gray code multi-bit counter pointer crossing
A multi-bit value crossing as plain binary can be sampled mid-change with several bits wrong. A Gray-coded value changes one
bit per step, so a sampled value is either the old or the new one. Convert binary to Gray (`b ^ (b >> 1)`) before the
crossing and synchronize each bit; this is how asynchronous FIFO pointers cross.

## Handshake crossing multi-bit data
Hold the data stable, raise a request that is synchronized into the other domain, capture the data there, then send an
acknowledge back through a synchronizer. The data bus itself is not synchronized; it is stable while the request is seen.

## Reset synchronizer asynchronous assert synchronous release
Assert reset asynchronously, release it synchronously. Two flip-flops in the domain, both reset to the active level, with
the second flop's output as that domain's reset: the release is aligned to the clock and no register leaves reset on a clock
edge that is too close to the release.

## Reset rules every register and every domain
Each clock domain has its own reset release. Reset all control state (FSM state, valid bits, counters that gate behaviour).
Unreset data registers can power up as any value, so never let them reach an output before they have been written.

## What a check can and cannot show
SiliconForge has no clock-domain-crossing analysis yet, so nothing here is checked automatically. A structural tool could
find a path between two clock domains that has no synchronizer; whether a given synchronizer structure is correct for the
protocol is a design question that needs simulation with different clock ratios and, for important crossings, formal
properties. Keep designs for this tool single-clock unless the specification requires otherwise.
