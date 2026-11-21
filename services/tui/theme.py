# SPDX-License-Identifier: AGPL-3.0-or-later
"""Textual themes built from the shared palette (`configs/design/tokens.json`, via the generated `tokens.py`)."""
from __future__ import annotations

from textual.theme import Theme

from services.tui.tokens import LAYERS, THEMES

DEFAULT = "siliconforge-dark"


def build_themes() -> list[Theme]:
    out = []
    for mode in ("dark", "light"):
        t = THEMES[mode]
        out.append(Theme(
            name=f"siliconforge-{mode}", primary=t["accent"], secondary=LAYERS["formal"][mode], accent=t["accent"],
            warning=t["repair"], error=t["fail"], success=t["pass"], foreground=t["ink"], background=t["paper"],
            surface=t["paper-2"], panel=t["sunken"], dark=mode == "dark",
            variables={
                "ink-2": t["ink-2"], "ink-3": t["ink-3"], "rule": t["rule"], "rule-strong": t["rule-strong"],
                "chrome": t["chrome"], "chrome-2": t["chrome-2"], "chrome-ink": t["chrome-ink"], "chrome-ink-2": t["chrome-ink-2"],
                "accent-wash": t["accent-wash"], "pass-wash": t["pass-wash"], "fail-wash": t["fail-wash"],
                "repair": t["repair"], "on-accent": t["on-accent"],
                **{f"layer-{k}": v[mode] for k, v in LAYERS.items()},
            },
        ))
    return out


def palette(mode: str) -> dict[str, str]:
    """Flat colour lookup for Rich text (which cannot read CSS variables): tokens plus `layer-<stage>` keys."""
    t = dict(THEMES[mode])
    t.update({f"layer-{k}": v[mode] for k, v in LAYERS.items()})
    return t
