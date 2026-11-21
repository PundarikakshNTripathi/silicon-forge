# SPDX-License-Identifier: AGPL-3.0-or-later
"""HFModel hands out batched samples one at a time (no torch needed: the draw is replaced)."""
from services.orchestrator.evals.hf import HFModel


def make(draws):
    m = HFModel.__new__(HFModel)  # skip loading a model
    from collections import defaultdict, deque

    m._pool = defaultdict(deque)
    m.calls = []

    def fake_draw(system, user, temperature, count):
        m.calls.append((user, temperature, count))
        return [(f"{user}-{i}", 10, 5, 0.1) for i in range(count)]

    m._draw = fake_draw
    return m


def test_n_samples_of_a_prompt_are_drawn_in_one_batch_and_handed_out_in_order():
    m = make(None)
    llm = m.llm(n_hint=4)
    got = [llm("s", "p1", 0.8).text for _ in range(4)]
    assert got == ["p1-0", "p1-1", "p1-2", "p1-3"] and m.calls == [("p1", 0.8, 4)]
    assert llm("s", "p2", 0.8).text == "p2-0" and len(m.calls) == 2  # a new prompt draws again


def test_greedy_requests_draw_a_single_sample():
    m = make(None)
    llm = m.llm(n_hint=8)
    llm("s", "p", 0.0)
    assert m.calls == [("p", 0.0, 1)]


def test_replies_carry_token_counts_and_latency():
    r = make(None).llm(2)("s", "p", 0.7)
    assert (r.prompt_tokens, r.completion_tokens, r.latency_s) == (10, 5, 0.1)
