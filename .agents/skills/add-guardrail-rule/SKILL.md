---
name: add-guardrail-rule
description: Add a new anti-hallucination guardrail rule (lint, contract, PDK, Tcl allowlist, formal vacuity) with a test that fails without it. Use when LLM output slips past an existing gate.
---
1. Reproduce the escape as a minimal RTL/IR/Tcl snippet and add a failing test in `tests/test_guardrails.py`.
2. Pick the gate: port contract / lint / PDK cells / flow-var+Tcl allowlist / formal vacuity / LEC (`services/orchestrator/guardrails/`).
3. Allocate a rule id `SF-GRD-<gate><nn>` (or `SF-RTL-nnn` for the C++ linter in `services/telemetry_engine/src/rtl_static_check.cpp`, plus a case in its `tests/test_telemetry.cpp`).
4. Implement with severity ERROR (blocks) or WARNING; include the line number and an actionable message (it is fed back to the RTL agent).
5. `python scripts/dev.py test && python scripts/dev.py lint`; document the rule in `docs/guardrails.md`.
