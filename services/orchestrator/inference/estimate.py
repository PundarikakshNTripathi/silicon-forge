"""Pre-run cost estimate for a routing profile (typical per-stage token volumes; an estimate, not a quote)."""
from __future__ import annotations

from services.orchestrator.inference.catalog import Catalog

# (task tier, typical input tokens, typical output tokens, expected calls) per stage of a simple design.
# Calibrated on bundled examples; larger designs and more repair iterations cost more.
STAGE_PROFILE: dict[str, tuple[str, int, int, float]] = {
    "ARCHITECT": ("frontier_reasoning", 1_800, 900, 1.0),
    "RTL_GEN": ("balanced", 1_700, 1_200, 1.0),
    "FORMAL_VERIFY": ("frontier_reasoning", 2_200, 700, 1.2),
    "RTL_REPAIR": ("frontier_reasoning", 3_000, 1_200, 1.5),
    "PNR_RL_OPT": ("balanced", 900, 250, 0.5),
}


def estimate_profile_cost(catalog: Catalog, profile: str) -> dict:
    per_stage: dict[str, dict] = {}
    total = 0.0
    tokens = 0
    for stage, (tier, tin, tout, calls) in STAGE_PROFILE.items():
        target = catalog.chain(profile, tier)[0]
        price = catalog.price(target.model)
        local = catalog.provider_is_local(target.provider)
        cost = 0.0 if local else price.cost_usd(int(tin * calls), int(tout * calls))
        per_stage[stage] = {"provider": target.provider, "model": target.model, "usd": round(cost, 4)}
        total += cost
        tokens += int((tin + tout) * calls)
    return {"profile": profile, "usd": round(total, 4), "tokens": tokens, "stages": per_stage,
            "note": "Estimate for a small design with 1-2 repair iterations; real cost scales with design size."}
