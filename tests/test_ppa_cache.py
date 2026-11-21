"""EDA result cache: identical RTL + knobs reuse a layout, and the cached artifacts appear in the new run."""
from __future__ import annotations

from types import SimpleNamespace

from services.orchestrator.rl.ppa_env import PpaOptimizationEnv
from services.orchestrator.state import PPAMetrics
from tests.conftest import load_example


class FakeBridge:
    mode = "wsl"

    def __init__(self) -> None:
        self.calls = 0
        self.config = SimpleNamespace(pdk="sky130A")

    def run_physical_implementation(self, run_dir, contract, rtl_source, knobs, episode_idx):
        self.calls += 1
        pnr = run_dir / f"pnr_ep_{episode_idx:02d}"
        final = pnr / "runs" / "sf" / "final"
        (final / "gds").mkdir(parents=True)
        (final / "render").mkdir(parents=True)
        (final / "gds" / "x.gds").write_bytes(b"gds")
        (final / "render" / "x.png").write_bytes(b"png")
        (final / "metrics.json").write_text("{}")
        return PPAMetrics(wns_ns=0.5, tns_ns=0, area_um2=1000, total_power_mw=1, cell_count=10, drc_violations=0,
                          lvs_clean=True, knobs=dict(knobs), mode="wsl", run_dir=str(pnr),
                          gds_path=str(final / "gds" / "x.gds"))


def _env(bridge, run_dir, cache_dir, contract):
    return PpaOptimizationEnv(bridge, run_dir, contract, {"clock_period_ns": 10.0, "max_area_um2": 25000, "max_power_mw": 5},
                              {}, rtl_source="module m; endmodule", cache_dir=cache_dir)


def test_cached_episode_is_free_and_materializes_its_artifacts(tmp_path):
    contract, *_ = load_example("uart_tx")
    bridge = FakeBridge()
    run_a, run_b, cache = tmp_path / "a", tmp_path / "b", tmp_path / "cache"
    run_a.mkdir(); run_b.mkdir()  # noqa: E702

    m1, _ = _env(bridge, run_a, cache, contract).evaluate(3, 1)
    assert bridge.calls == 1 and not m1.cached

    env_b = _env(bridge, run_b, cache, contract)
    m2, _ = env_b.evaluate(3, 1)
    assert bridge.calls == 1 and m2.cached and env_b.cache_hits == 1  # no second layout run
    # the new run owns copies of the GDS and render, so its Layout tab works
    assert (run_b / "pnr_ep_01" / "runs" / "sf" / "final" / "gds" / "x.gds").exists()
    assert (run_b / "pnr_ep_01" / "runs" / "sf" / "final" / "render" / "x.png").exists()
    assert m2.gds_path and m2.gds_path.startswith(str(run_b))

    # different knobs are a different cache entry
    _env(bridge, run_b, cache, contract).evaluate(4, 2)
    assert bridge.calls == 2
