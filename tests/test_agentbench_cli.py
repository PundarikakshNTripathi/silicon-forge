# SPDX-License-Identifier: AGPL-3.0-or-later
"""`siliconforge agentbench` end to end with a fake "oracle" model that behaves sensibly in each arm's own protocol: the whole
pipeline (arms, proxy, runner, verifiers, summary, result file, report) runs without a real model or GPU."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.orchestrator.agentbench import report, tasks
from services.orchestrator.main import cli_main

ANSWERS = {"couter.sv": "4", "default bit width": "12"}


class Oracle:
    """First turn: look at the folder. Next turn: give the right answer. Speaks tool calls to frameworks, JSON actions to ours."""

    def __init__(self):

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, obj, status=200):
                payload = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._send({"status": "ok"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                msgs = body.get("messages", [])
                blob = json.dumps(msgs)
                answer = next((a for k, a in ANSWERS.items() if k in blob), "unknown")
                if "response_format" in body:  # our own loop: one JSON action per turn
                    seen_tool = "TOOL RESULT" in blob
                    act = {"say": answer, "tool": None, "args": {}} if seen_tool else {"say": "", "tool": "list_dir", "args": {"path": "."}}
                    msg, finish = {"role": "assistant", "content": json.dumps(act)}, "stop"
                elif body.get("tools"):  # a framework: native tool calling
                    if any(m.get("role") == "tool" for m in msgs):
                        msg, finish = {"role": "assistant", "content": answer}, "stop"
                    else:
                        msg = {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": '{"path": "."}'}}]}
                        finish = "tool_calls"
                else:
                    msg, finish = {"role": "assistant", "content": "OK"}, "stop"
                self._send({"id": "x", "object": "chat.completion", "created": 0, "model": "local",
                            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                            "usage": {"prompt_tokens": 150, "completion_tokens": 12, "total_tokens": 162},
                            "timings": {"predicted_per_second": 50.0}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def oracle():
    o = Oracle()
    yield o
    o.close()


def test_a_full_run_across_all_three_arms_writes_a_result_and_reports_it(oracle, tmp_path, capsys):
    pytest.importorskip("langgraph")
    pytest.importorskip("pydantic_ai")
    rc = cli_main(["agentbench", "run", "--upstream", oracle.url, "--arms", "custom,langgraph,pydantic_ai",
                   "--tasks", "wrong_path,port_width", "--trials", "2", "--out", str(tmp_path), "--tag", "dry"])
    out = capsys.readouterr().out
    assert rc == 0 and "wrote " in out
    (path,) = tmp_path.glob("agentbench-local-dry-*.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == report.SCHEMA and doc["arms"] == ["custom", "langgraph", "pydantic_ai"] and doc["trials"] == 2
    assert len(doc["runs"]) == 3 * 2 * 2
    s = doc["summary"]
    for arm in ("custom", "langgraph", "pydantic_ai"):
        a = s["arms"][arm]
        assert a["tasks"] == 2 and a["solved_runs"] == 4 and a["task_success"]["mean"] == 1.0, (arm, a["by_task"])
        assert a["metrics"]["tools"]["calls"] == 4 and a["metrics"]["recovery"]["failures"] == 0
        assert a["first_prompt_tokens_median"] == 150
    assert set(s["paired_vs_baseline"]) == {"langgraph - custom", "pydantic_ai - custom"} and s["baseline_arm"] == "custom"
    assert all(not d["separated"] for d in s["paired_vs_baseline"].values())  # identical results are not "different"
    assert not any(t["reason"] for t in s["skipped"])
    # the result file is safe to commit: no local paths, no prompt or file content
    text = path.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "Users" not in text and "module counter" not in text
    # and it renders
    assert cli_main(["agentbench", "report", str(path)]) == 0
    rendered = capsys.readouterr().out
    assert "[custom] task success 100.0%" in rendered and "langgraph - custom" in rendered and "not separated" in rendered


def test_simulation_tasks_are_reported_as_skipped_without_a_backend(oracle, tmp_path, capsys):
    rc = cli_main(["agentbench", "run", "--upstream", oracle.url, "--arms", "custom", "--tasks", "fix_latch,port_width",
                   "--trials", "1", "--out", str(tmp_path)])
    assert rc == 0
    doc = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert [s["task"] for s in doc["summary"]["skipped"]] == ["fix_latch"] and doc["summary"]["arms"]["custom"]["tasks"] == 1
    assert "skipped fix_latch" in capsys.readouterr().out


def test_argument_errors_are_clear(oracle, tmp_path, capsys):
    assert cli_main(["agentbench", "run", "--upstream", oracle.url, "--tasks", "nonsense", "--out", str(tmp_path)]) == 2
    assert "unknown task" in capsys.readouterr().err
    assert cli_main(["agentbench", "run", "--upstream", "http://127.0.0.1:1", "--arms", "custom", "--tasks", "port_width",
                     "--out", str(tmp_path)]) == 2
    assert "no model server" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="unknown arm"):
        cli_main(["agentbench", "run", "--upstream", oracle.url, "--arms", "autogen", "--out", str(tmp_path)])
    with pytest.raises(SystemExit, match="only one is"):
        cli_main(["agentbench", "run", "--upstream", oracle.url, "--arms", "langgraph+turbo", "--out", str(tmp_path)])


def test_the_suite_is_well_formed():
    ids = [t.id for t in tasks.SUITE]
    assert len(ids) == len(set(ids)) == 13
    assert {t.category for t in tasks.SUITE} == {"comprehension", "editing", "recovery", "judgement", "safety"}
    assert sum(t.needs_eda for t in tasks.SUITE) == 3
    for t in tasks.SUITE:
        assert t.prompt and t.files and callable(t.verify) and 1 <= t.max_steps <= 30
        assert set(t.tools) <= set(tasks.DEFAULT_TOOLS)
