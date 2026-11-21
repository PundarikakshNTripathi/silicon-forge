# Handshakes, FIFOs and AXI-Lite

Ready/valid interfaces and queues are where most small digital blocks go wrong. These are the rules the common protocols
follow.

## Valid ready handshake rules
A transfer happens on a clock edge where `valid` and `ready` are both high. The source must not wait for `ready` before it
raises `valid`, and once `valid` is high it stays high, with the data unchanged, until the transfer happens. The sink may
raise or drop `ready` at any time. Never make `valid` depend combinationally on `ready` of the same interface, and do not
build a combinational path from an input `ready` to an output `ready` across a block without a register in between.

## Synchronous FIFO pointers full and empty
Use a read pointer and a write pointer that are one bit wider than the address. The FIFO is empty when the pointers are equal
and full when they differ only in the top bit. Write when `wr_en && !full`, read when `rd_en && !empty`, and ignore the
request otherwise. Registering the full and empty flags instead of computing them from the pointers each cycle shortens the
critical path, at the cost of getting the update logic exactly right.

## FIFO data output first word fall through
In a first-word-fall-through FIFO the output shows the oldest entry whenever it is not empty, and a read removes it. In a
standard FIFO the data appears one cycle after the read request. State which one the specification asks for and test the
cycle after a read.

## Skid buffer break a ready path
To register the `ready` path without losing throughput, add a second one-entry buffer that takes the word that arrives while
the downstream stage has just stalled. It lets `ready` be a registered signal while the interface still accepts one word per
cycle.

## AXI-Lite five channels
AXI-Lite has write address (AW), write data (W), write response (B), read address (AR) and read data (R). Each channel has its
own `valid`/`ready` pair and follows the handshake rules above independently. A write is complete when the response (B) has
been accepted. Accept AW and W in either order. A read returns exactly one R beat for each AR. All registers are 32 bits and
accesses are single-beat.

## Counters at full throughput
A block that must accept one word per cycle cannot hold `ready` low for a bubble between words. Check the throughput
requirement against the structure before choosing a multi-cycle FSM.
