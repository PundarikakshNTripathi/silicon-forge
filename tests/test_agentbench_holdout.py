# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four tasks written after the first results: each verifier accepts a right solution and rejects the plausible wrong ones."""
from __future__ import annotations

from services.orchestrator.agentbench import tasks as T
from services.orchestrator.agentbench import tasks_holdout as H
from tests.test_agentbench import call, play, say, solved


def test_the_holdout_is_selectable_by_id_but_not_part_of_the_default_suite():
    assert {t.id for t in H.HOLDOUT} <= set(T.BY_ID)
    assert not ({t.id for t in H.HOLDOUT} & {t.id for t in T.SUITE})  # the committed three-run results stay comparable
    assert len(T.SUITE) == 13


def test_depth_answer_needs_the_answer_line_with_the_right_number():
    assert solved(play("h_depth_answer", [say("DEPTH defaults to 24.\nANSWER: 24")]))
    assert not solved(play("h_depth_answer", [say("ANSWER: 8")]))  # the width, not the depth
    assert not solved(play("h_depth_answer", [say("It is 24.")]))  # the format was part of the request


def test_rename_signal_needs_every_use_renamed_and_the_module_kept():
    good = H.ACCUM.replace("acc <=", "total <=").replace("{8'd0, x}", "{8'd0, x}").replace("acc + ", "total + ").replace("= acc;", "= total;")
    good = good.replace("logic [15:0] acc;", "logic [15:0] total;")
    assert solved(play("h_rename_signal", [call("write_file", path="rtl/accum.sv", content=good), say("done")]))
    partly = H.ACCUM.replace("logic [15:0] acc;", "logic [15:0] total;")  # declaration only: uses still say acc
    assert not solved(play("h_rename_signal", [call("write_file", path="rtl/accum.sv", content=partly), say("done")]))
    assert not solved(play("h_rename_signal", [say("done")]))  # claiming success without editing


def test_wrong_dir_recovery_finds_the_real_file_then_answers():
    assert solved(play("h_wrong_dir", [call("read_file", path="rtl/dma/engine.sv"), call("list_dir", path="rtl"),
                                       call("read_file", path="rtl/dma_engine.sv"), say("MAX_BURST is 16.\nANSWER: 16")]))
    assert not solved(play("h_wrong_dir", [say("ANSWER: 32")]))


def test_edge_flip_changes_only_the_clock_edge():
    good = H.SHIFT.replace("posedge clk", "negedge clk")
    assert solved(play("h_edge_flip", [call("write_file", path="rtl/shift4.sv", content=good), say("done")]))
    assert not solved(play("h_edge_flip", [say("done")]))
    flipped_reset_too = good.replace("negedge rst_n", "posedge rst_n")
    assert not solved(play("h_edge_flip", [call("write_file", path="rtl/shift4.sv", content=flipped_reset_too), say("done")]))
    changed_shift = good.replace("{q[2:0], d}", "{d, q[3:1]}")
    assert not solved(play("h_edge_flip", [call("write_file", path="rtl/shift4.sv", content=changed_shift), say("done")]))
