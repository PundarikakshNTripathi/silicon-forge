# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static signal dependency slicer (Cone of Influence) for minimal-diff RTL repair.

Inspired by Veri-Sure (arXiv:2601.19747): localizes the backward signal dependency cone
of a failing output or assertion signal so the RTL Coder patches only the affected logic.
"""
from __future__ import annotations

import re


class RtlDependencySlicer:
    ASSIGN_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:<=|=)\s*([^;]+);")
    IDENT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")
    SV_KEYWORDS = {
        "module", "endmodule", "input", "output", "inout", "logic", "wire", "reg",
        "always_ff", "always_comb", "always", "posedge", "negedge", "begin", "end",
        "if", "else", "case", "endcase", "default", "assign", "parameter", "localparam",
    }

    def build_dependency_graph(self, rtl_source: str) -> tuple[dict[str, set[str]], dict[str, list[int]]]:
        deps: dict[str, set[str]] = {}
        line_map: dict[str, list[int]] = {}
        for idx, line in enumerate(rtl_source.splitlines(), start=1):
            clean = line.split("//", 1)[0]
            for match in self.ASSIGN_RE.finditer(clean):
                lhs = match.group(1)
                rhs = match.group(2)
                if lhs in self.SV_KEYWORDS:
                    continue
                rhs_signals = {
                    tok for tok in self.IDENT_RE.findall(rhs)
                    if tok not in self.SV_KEYWORDS and not tok.startswith("d") and not tok[0].isdigit()
                }
                deps.setdefault(lhs, set()).update(rhs_signals)
                line_map.setdefault(lhs, []).append(idx)
        return deps, line_map

    def extract_coi_slice(self, rtl_source: str, target_signal: str, context_lines: int = 2) -> str:
        deps, line_map = self.build_dependency_graph(rtl_source)
        visited: set[str] = set()
        stack = [target_signal]
        while stack:
            sig = stack.pop()
            if sig in visited:
                continue
            visited.add(sig)
            for parent in deps.get(sig, set()):
                if parent not in visited:
                    stack.append(parent)

        lines = rtl_source.splitlines()
        keep_indices: set[int] = set()
        for sig in visited:
            for ln in line_map.get(sig, []):
                for offset in range(-context_lines, context_lines + 1):
                    cand = ln + offset
                    if 1 <= cand <= len(lines):
                        keep_indices.add(cand)

        if not keep_indices:
            return rtl_source

        sliced = [f"// Backward Cone-of-Influence slice for signal '{target_signal}' (signals: {sorted(visited)})"]
        for ln in sorted(keep_indices):
            sliced.append(f"{ln:4d} | {lines[ln - 1]}")
        return "\n".join(sliced)
