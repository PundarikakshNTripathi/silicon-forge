# SymbiYosys (sby) Formal Verification Patterns & Anti-Vacuity
- **Engines**: Use `smtbmc z3` or `smtbmc bitwuzla` with separate `bmc`, `prove` (k-induction), and `cover` tasks.
- **Reset Assumption**: Constrain initial cycle with `f_past_valid` register (`initial f_past_valid = 1'b0; always @(posedge clk) f_past_valid <= 1'b1;`) to avoid spurious `$past()` failures at cycle 0.
- **Anti-Vacuity**: Always pair safety `assert(...)` checks with `cover(...)` reachability witnesses to confirm `assume(...)` constraints never block valid execution paths.
- **LEC**: Post-synthesis equivalence must run `equiv_make gold gate equiv`, `equiv_simple`, `equiv_induct`, `equiv_status -assert`.
