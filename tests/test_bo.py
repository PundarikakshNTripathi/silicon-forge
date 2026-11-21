# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bayesian optimization: encoding, samplers, the GP, expected improvement, and the optimizer against a known objective."""
import math

import numpy as np
import pytest

from services.orchestrator.rl.bo import (
    DEFAULT_SPACE,
    GP,
    Dim,
    GPBayesOptimizer,
    Space,
    expected_improvement,
    latin_hypercube,
    random_points,
)

# ----------------------------------------------------------------------------------------------- encoding


def test_float_int_and_choice_dims_round_trip_on_their_grid():
    f, i, c = Dim("a", "float", 10, 20, step=0.5), Dim("b", "int", 1, 9), Dim("c", "choice", choices=("x", "y", "z"))
    for dim, v in ((f, 14.5), (i, 7), (c, "y")):
        assert dim.decode(dim.encode(v)) == v
    assert f.decode(0.0) == 10 and f.decode(1.0) == 20 and f.decode(2.5) == 20  # clamped
    assert c.decode(0.0) == "x" and c.decode(0.999) == "z" and c.decode(1.0) == "z"


def test_floats_are_snapped_to_the_step_so_proposals_repeat_exactly():
    d = Dim("g", "float", 0.1, 0.4, step=0.01)
    assert d.decode(0.2345) == d.decode(0.2349) and d.decode(0.5) == pytest.approx(0.25)


def test_default_space_names_real_librelane_variables():
    assert [d.name for d in DEFAULT_SPACE.dims] == ["FP_CORE_UTIL", "PL_TARGET_DENSITY_PCT", "GRT_ADJUSTMENT", "SYNTH_STRATEGY"]
    k = DEFAULT_SPACE.decode(np.full(4, 0.5))
    assert 25 <= k["FP_CORE_UTIL"] <= 65 and k["SYNTH_STRATEGY"] in DEFAULT_SPACE.dims[3].choices


# ------------------------------------------------------------------------------------------------ samplers


def test_latin_hypercube_has_one_point_per_stratum_in_every_dimension():
    space = Space([Dim("a", "float", 0, 1), Dim("b", "float", 0, 1)])
    n = 8
    pts = latin_hypercube(space, n, seed=3)
    for name in ("a", "b"):
        strata = sorted(int(p[name] * n - 1e-9) if p[name] < 1 else n - 1 for p in pts)
        assert strata == list(range(n))


def test_samplers_are_deterministic_per_seed_and_differ_across_seeds():
    assert random_points(DEFAULT_SPACE, 5, 1) == random_points(DEFAULT_SPACE, 5, 1)
    assert random_points(DEFAULT_SPACE, 5, 1) != random_points(DEFAULT_SPACE, 5, 2)
    assert latin_hypercube(DEFAULT_SPACE, 5, 1) == latin_hypercube(DEFAULT_SPACE, 5, 1)


# ------------------------------------------------------------------------------------------------- GP and EI


def test_gp_interpolates_its_training_points_and_is_uncertain_far_away():
    x = np.array([[0.1], [0.5], [0.9]])
    y = np.array([1.0, 3.0, 2.0])
    gp = GP(noise=1e-6).fit(x, y)
    mean, std = gp.predict(x)
    assert np.allclose(mean, y, atol=1e-2) and np.all(std < 0.05)
    _, far = gp.predict(np.array([[0.3], [0.7]]))
    assert np.all(far > std.max() * 3)


def test_gp_handles_identical_targets_and_duplicate_points_without_crashing():
    x = np.array([[0.2, 0.2], [0.2, 0.2], [0.8, 0.8]])
    gp = GP().fit(x, np.array([1.0, 1.0, 1.0]))
    mean, std = gp.predict(np.array([[0.5, 0.5]]))
    assert np.isfinite(mean).all() and np.isfinite(std).all()


def test_expected_improvement_properties():
    ei = expected_improvement(np.array([1.0, 2.0, 2.0]), np.array([0.5, 0.5, 0.0]), best=1.5, xi=0.0)
    assert ei[1] > ei[0] > 0  # a higher predicted mean is worth more
    assert ei[2] == 0.0  # no uncertainty, no expected improvement
    wide = expected_improvement(np.array([1.0]), np.array([2.0]), best=1.5, xi=0.0)
    narrow = expected_improvement(np.array([1.0]), np.array([0.1]), best=1.5, xi=0.0)
    assert wide > narrow  # uncertainty is worth exploring when the mean is below the best


# ---------------------------------------------------------------------------------------------- optimizer

SPACE2 = Space([Dim("x", "float", 0, 1, step=0.01), Dim("y", "float", 0, 1, step=0.01)])


def branin_like(k):
    """Smooth, multimodal, maximum 1.0 at (0.7, 0.3)."""
    x, y = k["x"], k["y"]
    return math.exp(-12 * ((x - 0.7) ** 2 + (y - 0.3) ** 2)) + 0.3 * math.exp(-30 * ((x - 0.2) ** 2 + (y - 0.8) ** 2))


def run_opt(seed, budget=14):
    opt = GPBayesOptimizer(SPACE2, seed=seed, n_init=4, pool=600)
    best = []
    for _ in range(budget):
        k = opt.suggest()
        opt.observe(k, branin_like(k))
        best.append(max(opt.ys))
    return best, opt


def test_the_first_proposals_are_a_latin_hypercube_then_the_model_takes_over():
    _, opt = run_opt(0, budget=6)
    assert len(opt.xs) == 6 and "lengthscale" in opt.last_model and opt.last_model["pred_std"] >= 0


def test_the_optimizer_never_proposes_a_point_twice():
    _, opt = run_opt(1, budget=14)
    keys = {tuple(np.round(x, 9)) for x in opt.xs}
    assert len(keys) == len(opt.xs)


def test_the_optimizer_is_deterministic_for_a_seed():
    a, _ = run_opt(5, 10)
    b, _ = run_opt(5, 10)
    assert a == b


def test_on_a_known_objective_it_beats_random_search_at_the_same_budget():
    """Sanity of the implementation, not evidence about place and route: on a smooth 2-D function with a known maximum the
    surrogate-guided search should find a better point than random sampling over several seeds."""
    seeds, budget = range(8), 14
    bo = [run_opt(s, budget)[0][-1] for s in seeds]
    rnd = [max(branin_like(k) for k in random_points(SPACE2, budget, s)) for s in seeds]
    assert sum(bo) / len(bo) > sum(rnd) / len(rnd)
    assert max(bo) > 0.97  # and it does find the peak


# ----------------------------------------------------------------------------- a failed flow is an outcome


def test_a_failed_flow_is_recorded_penalised_and_cached_not_raised(tmp_path):
    from types import SimpleNamespace

    from services.orchestrator.eda.errors import ToolRunError
    from services.orchestrator.rl.ppa_env import FAILED_FLOW_REWARD, PpaOptimizationEnv
    from services.orchestrator.state import DesignContract

    calls = []

    class Bridge:
        mode = "wsl"
        config = SimpleNamespace(pdk="sky130A")

        def run_physical_implementation(self, **kw):
            calls.append(kw["knobs"])
            raise ToolRunError("LibreLane produced no final/metrics.json\n[DPL-0036] Detailed placement failed.")

    contract = DesignContract("m", "clk", "rst_n", True, False, [], [], target_clock_ns=10.0)
    env = PpaOptimizationEnv(Bridge(), tmp_path, contract, {"clock_period_ns": 10.0}, {}, rtl_source="module m; endmodule",
                             cache_dir=tmp_path / "cache", quality_only=True)
    m, r = env.evaluate_knobs({"FP_CORE_UTIL": 90}, 1)
    assert r == FAILED_FLOW_REWARD and m.reward == FAILED_FLOW_REWARD and "DPL-0036" in m.extra["flow_failed"]
    assert m.wns_ns == -99.0 and m.lvs_clean is False and m.area_um2 == 0.0
    m2, r2 = env.evaluate_knobs({"FP_CORE_UTIL": 90}, 2)  # the same point again is answered from the cache
    assert r2 == FAILED_FLOW_REWARD and len(calls) == 1 and env.cache_hits == 1


def test_the_failure_reward_is_below_any_real_result():
    from services.orchestrator.rl.ppa_env import FAILED_FLOW_REWARD
    from services.orchestrator.rl.reward import compute_ppa_reward
    from services.orchestrator.state import PPAMetrics

    worst_real = PPAMetrics(wns_ns=-3.0, tns_ns=-50.0, area_um2=2e4, total_power_mw=4.9, cell_count=1, drc_violations=0, lvs_clean=True)
    r = compute_ppa_reward(worst_real, {"max_area_um2": 25000.0, "max_power_mw": 5.0}, {"runtime_penalty": 0.0})
    assert FAILED_FLOW_REWARD < r


def test_failed_runs_are_imputed_just_below_the_worst_valid_result_not_fitted_as_outliers():
    opt = GPBayesOptimizer(SPACE2, seed=0, n_init=2)
    opt.observe({"x": 0.1, "y": 0.1}, 4.0)
    opt.observe({"x": 0.9, "y": 0.9}, 4.6)
    opt.observe({"x": 0.5, "y": 0.5}, -50.0, failed=True)
    t = opt.model_targets()
    assert list(t[:2]) == [4.0, 4.6]
    assert 2.5 < t[2] < 4.0  # below the worst valid result, nowhere near -50
    assert opt.ys[2] == -50.0  # the observed reward is kept for reporting
    p = opt.suggest()  # and the model still produces a proposal
    assert set(p) == {"x", "y"}


def test_when_nothing_has_succeeded_yet_the_model_is_flat_instead_of_crashing():
    opt = GPBayesOptimizer(SPACE2, seed=0, n_init=2)
    opt.observe({"x": 0.1, "y": 0.1}, -50.0, failed=True)
    opt.observe({"x": 0.9, "y": 0.9}, -50.0, failed=True)
    assert list(opt.model_targets()) == [0.0, 0.0]
    assert set(opt.suggest()) == {"x", "y"}
