# LibreLane flow variables and what moving them does

These are the variables the optimizer and the project flow set most often, with the direction each pushes the result. The
effects are tendencies on small digital blocks with the open sky130 flow, not guarantees; measure on your design.

## CLOCK_PERIOD target clock period
The period in nanoseconds that timing is closed against. A shorter period makes synthesis and the timing repair work harder
and can raise area and power; a longer one relaxes timing. If setup slack is negative after a clean run, the honest options
are a longer period, fewer logic levels in the RTL, or a more delay-oriented synthesis strategy.

## FP_CORE_UTIL core utilization percent
The share of the core area that standard cells occupy. Higher utilization gives a smaller die and shorter wires but less room
for the router and the repair buffers, so congestion, DRC violations and slew or capacitance violations become more likely.
Typical starting values are 40 to 50 percent for the sky130 flow; lower it when routing fails or electrical limits are
violated.

## PL_TARGET_DENSITY_PCT global placement density
How tightly the placer packs cells locally, in percent. It should sit above the core utilization by a margin. Too high
produces congested regions; too low spreads cells and lengthens wires. Moving it together with FP_CORE_UTIL is more useful
than moving either alone.

## GRT_ADJUSTMENT global routing capacity adjustment
A fraction (0 to 1) by which the global router reduces the routing capacity it believes each region has. A larger value
makes it avoid congestion earlier and leaves detail-routing slack, at the price of longer routes. Raise it when detailed
routing reports many violations; lower it when wires are longer than they need to be.

## SYNTH_STRATEGY synthesis script
Selects the logic optimization script in synthesis. The AREA strategies (`AREA 0` to `AREA 3`) favour a smaller netlist,
the DELAY strategies (`DELAY 0` to `DELAY 4`) favour a shorter critical path and usually a larger one. Try a DELAY strategy
when setup slack is the problem and an AREA strategy when area is.

## Reading a failed signoff
Setup violations: relax CLOCK_PERIOD or use a DELAY strategy. Hold violations: normally repaired by the flow; if they remain,
look at the clock skew rather than the logic. DRC after routing: lower FP_CORE_UTIL and PL_TARGET_DENSITY_PCT, or raise
GRT_ADJUSTMENT. Max slew, capacitance or fanout violations: lower utilization and let the repair steps insert buffers; very
high fanout nets in the RTL (a reset or enable driving hundreds of flops) are the usual cause and are worth restructuring.

## Variables must exist
A variable that LibreLane does not know is an error or is silently ignored depending on the version. Use only the variables
named in the PDK configuration's allowlist; do not invent names such as `FP_UTILIZATION` or `SYNTH_OPT_LEVEL`.
