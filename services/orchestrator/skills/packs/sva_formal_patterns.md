# Formal verification with SymbiYosys: patterns and anti-vacuity

SymbiYosys (`sby`) runs three kinds of task against a design: `bmc` (bounded model check: no property fails within N cycles
of reset), `prove` (k-induction: the property holds for all time), and `cover` (a witness trace exists for a condition).

## Typed property IR you never write SVA yourself
In SiliconForge you emit typed property objects (reset value, invariant, handshake stability, mutual exclusion, combinational
equivalence, latency bound, cover reachability). The compiler turns them into SymbiYosys source and adds the clocking, the
reset handling and the history register that `$past` needs. So a property contains only plain Verilog expressions over the
contract ports: no `cover property`, no `assert property`, no `@(posedge clk)`, no `|->`, no `##`, no `$past`, and no helper
signals such as `f_past_valid`. A property that mentions a name outside the port list is rejected before it is run.

## Anti-vacuity pair every assert with a cover
A proof can hold only because its condition can never happen. Pair each safety property with a cover property for the state
that triggers it, and treat an unreachable cover as a failure of the property set: either an assumption over-constrains the
design or the trigger is wrong. A green proof with a failed cover proves nothing.

## Reset and the first cycle
Properties about the previous cycle are meaningless in the first cycle after reset. The compiler guards for that; if you ever
write a property by hand, gate every `$past()` use with a register that is 0 in the first cycle and 1 afterwards.

## Reading a failed proof
The counterexample trace gives the failing property, the cycle, and the input values that lead to it. Find the signal named in
the failing property, follow its cone of influence in the RTL (the registers and inputs that can change it), and fix that
logic. Do not weaken the property to make it pass unless the property is what is wrong.

## Bounded versus unbounded
`bmc` finding no failure within N cycles only shows that no bug appears that early. `prove` with k-induction can show a
property holds forever, but needs the property to be inductive; if induction fails with a counterexample that starts from an
unreachable state, strengthen the property set with an invariant that rules the state out, not with an assumption that
changes the design's behaviour.

## Equivalence after synthesis
After synthesis the gate netlist must be equivalent to the RTL: build a miter of the two (`equiv_make gold gate equiv`), try
`equiv_simple`, then `equiv_induct`, and require `equiv_status -assert`. A design with module instances must be flattened
before the equivalence passes can run on it.
