# Icarus Verilog compile errors and how to fix them

Icarus Verilog (`iverilog -g2012`) is the simulator behind the cocotb tests and the evaluations. Its messages are terse; these
are the ones models hit most often and what each really means.

## is not a valid l-value for a procedural assignment
A signal is assigned inside an `always` or `initial` block but is declared as a net: an `output` port with no type (which is a
`wire`), or a `wire`. Declare it as a variable instead: `output reg out` in Verilog-2001 or `output logic out` in
SystemVerilog. Signals driven with `assign` stay `wire`; signals driven inside `always` blocks must be `reg` or `logic`.

## has already been declared in this scope
The same name is declared twice, most often a port that is declared again inside the body (`output out;` and then `reg out;`).
Declare it once. If a port needs to be a variable, give it the type in the port list (`output reg out`) and delete the second
declaration.

## Declarations must precede statements
A variable is declared after a statement in a block. Move every declaration to the top of the `begin ... end` block or to
module scope. A variable with an initializer (`logic x = 0;`) inside a block also needs `automatic` or must move to module
scope.

## can not select part of scalar
A bit or range select (`x[3:0]`, `x[2]`) is applied to a signal declared without a width, so it is one bit wide. Declare the
width where the signal is declared: `input [7:0] x`, not `input x`.

## Can not assign non-array identifier to array
An unpacked array is being assigned from a plain vector, or the reverse. Use a packed vector (`logic [7:0] x`) when you want one
number, or index the array element by element.

## Unknown module type
The design instantiates a module that is not in the file: a gate, a helper or a sub-block that the task did not provide. Define
the helper in the same file, or replace the instance with the expression it stands for. Do not rely on `include` of a file that
does not exist.

## Enumeration name has an inferred value that overflowed
An enumeration has more names than its declared width can number. Widen the type (`enum logic [2:0]`), or let the tool choose
the width by writing `enum {A, B, C}`.

## Unbased SystemVerilog literal cannot have a size
A literal such as `4'1` was written. The unsized fill literals are `'0`, `'1`, `'x` and `'z` with no size; for a sized value
write all the bits (`4'b1111`) or use a replication (`{4{1'b1}}`).

## syntax error I give up
The parser lost track after an earlier error, usually a missing `;`, an unmatched `end`, or a Markdown fence left in the file.
Fix the first error that was reported; later ones are often consequences.

## timescale warnings can be ignored
A warning that a timescale is inherited from another file is not an error. Do not add `timescale` directives to fix it.
