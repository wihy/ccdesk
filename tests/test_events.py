import json
from pathlib import Path

from ccdesk import events

FIXTURE = Path(__file__).parent / "fixtures" / "events_sample.jsonl"

PERM_REQ = {
    "summary": {"ts": "2026-08-14T02:57:03.526604+00:00", "event": "PermissionRequest",
                "session_id": "9975af37", "cwd": "/w", "tool_name": "Bash"},
    "payload": {"session_id": "9975af37", "cwd": "/w", "prompt_id": "7910b5b5",
                "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                "tool_input": {"command": "git status", "description": "st"}},
}


def test_parse_line_returns_none_on_garbage():
    assert events.parse_line("{truncated") is None
    assert events.parse_line("") is None


def test_permission_request_becomes_request_record():
    rec = events.to_request_record(PERM_REQ)
    assert rec["tool_name"] == "Bash"
    assert rec["session_id"] == "9975af37"
    assert rec["input_digest"] == "git status"
    assert rec["ts_request"] == "2026-08-14T02:57:03.526604+00:00"
    assert len(rec["req_id"]) == 16
    assert rec["decision"] is None and rec["outcome"] is None


def test_non_permission_event_is_ignored():
    ev = {"summary": {"event": "PreToolUse"}, "payload": {}}
    assert events.to_request_record(ev) is None


def test_post_tool_use_becomes_executed_outcome():
    ev = {
        "summary": {"ts": "2026-08-14T02:57:05.000000+00:00", "event": "PostToolUse"},
        "payload": {"session_id": "9975af37", "prompt_id": "7910b5b5", "tool_name": "Bash",
                    "tool_input": {"command": "git status", "description": "st"}},
    }
    out = events.to_outcome_record(ev)
    assert out["outcome"] == "executed"
    assert out["req_id"] == events.ledger.make_req_id(
        "9975af37", "7910b5b5", "Bash",
        events.ledger.input_fingerprint({"command": "git status", "description": "st"}))


def test_permission_denied_becomes_neutral_denied_outcome_with_reason():
    """I6：PermissionDenied 不等于「人拒绝了」。

    实测本机全部 3 条 PermissionDenied 的 reason 都是 "Classifier unavailable"
    （系统侧分类器故障）。结局名必须中性（denied），并带上原因供审计区分。
    """
    ev = {
        "summary": {"ts": "2026-08-14T02:57:06.000000+00:00", "event": "PermissionDenied"},
        "payload": {"session_id": "9975af37", "prompt_id": "7910b5b5", "tool_name": "Bash",
                    "reason": "Classifier unavailable",
                    "tool_input": {"command": "git status", "description": "st"}},
    }
    out = events.to_outcome_record(ev)
    assert out["outcome"] == "denied"
    assert out["outcome_reason"] == "Classifier unavailable"


def test_post_tool_use_has_no_outcome_reason():
    """PostToolUse 没有 reason；写 None，read_merged 会忽略它、不覆盖已有值。"""
    ev = {
        "summary": {"ts": "t", "event": "PostToolUse"},
        "payload": {"session_id": "s", "prompt_id": "p", "tool_name": "Bash",
                    "tool_input": {"command": "ls"}},
    }
    assert events.to_outcome_record(ev)["outcome_reason"] is None


def test_request_and_outcome_share_req_id():
    rec = events.to_request_record(PERM_REQ)
    ev = {"summary": {"ts": "t", "event": "PostToolUse"}, "payload": dict(PERM_REQ["payload"])}
    out = events.to_outcome_record(ev)
    assert rec["req_id"] == out["req_id"]


def test_ask_user_question_request_and_outcome_share_req_id_end_to_end():
    """C-2 端到端锁死：CC 在 PostToolUse 前把 answers/annotations 并进 tool_input，
    请求侧与结局侧仍须算出同一个 req_id（否则 outcome 永远回填不上）。

    夹具形状取自真实 events.jsonl：请求侧键集 ('questions',)，
    结局侧键集 ('annotations','answers','questions')。
    """
    questions = [{"header": "范围", "multiSelect": False,
                  "options": [{"label": "只建需求", "description": "d"}]}]
    req = {
        "summary": {"ts": "t1", "event": "PermissionRequest", "session_id": "sess-x", "cwd": "/w"},
        "payload": {"session_id": "sess-x", "cwd": "/w", "prompt_id": "prompt-a",
                    "hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion",
                    "tool_input": {"questions": questions}},
    }
    post = {
        "summary": {"ts": "t2", "event": "PostToolUse"},
        "payload": {"session_id": "sess-x", "cwd": "/w", "prompt_id": "prompt-a",
                    "tool_name": "AskUserQuestion",
                    "tool_input": {"questions": questions,
                                   "answers": [{"header": "范围", "answer": "只建需求"}],
                                   "annotations": {"source": "user"}}},
    }
    rec = events.to_request_record(req)
    out = events.to_outcome_record(post)
    assert rec["req_id"] == out["req_id"]
    assert out["outcome"] == "executed"


def test_ask_user_question_different_questions_still_differ():
    """剔除易变键不能把「不同的提问」也糊成同一条——否则会错配 outcome。"""
    def _req(header):
        return {
            "summary": {"ts": "t", "event": "PermissionRequest", "session_id": "s", "cwd": "/w"},
            "payload": {"session_id": "s", "cwd": "/w", "prompt_id": "p",
                        "tool_name": "AskUserQuestion",
                        "tool_input": {"questions": [{"header": header}]}},
        }
    assert events.to_request_record(_req("A"))["req_id"] != \
        events.to_request_record(_req("B"))["req_id"]


def test_different_prompt_id_yields_different_req_id():
    # C-1 回归：同一 session_id / tool_name / tool_input，但两个不同 prompt_id
    # 的 PermissionRequest，必须产生两个不同的 req_id —— 这是 match_key 三元组
    # 碰撞问题（少了 prompt_id）修复后的关键锁死点。
    same_input = {"command": "git status", "description": "st"}
    req_a = {
        "summary": {"ts": "t1", "event": "PermissionRequest", "session_id": "sess-x", "cwd": "/w"},
        "payload": {"session_id": "sess-x", "cwd": "/w", "prompt_id": "prompt-a",
                    "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                    "tool_input": dict(same_input)},
    }
    req_b = {
        "summary": {"ts": "t2", "event": "PermissionRequest", "session_id": "sess-x", "cwd": "/w"},
        "payload": {"session_id": "sess-x", "cwd": "/w", "prompt_id": "prompt-b",
                    "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                    "tool_input": dict(same_input)},
    }
    rec_a = events.to_request_record(req_a)
    rec_b = events.to_request_record(req_b)
    assert rec_a["req_id"] != rec_b["req_id"]

    # 各自的 PostToolUse outcome 必须精确回到自己那条请求上，不能串台。
    out_a = events.to_outcome_record({
        "summary": {"ts": "t1o", "event": "PostToolUse"},
        "payload": dict(req_a["payload"]),
    })
    out_b = events.to_outcome_record({
        "summary": {"ts": "t2o", "event": "PostToolUse"},
        "payload": dict(req_b["payload"]),
    })
    assert out_a["req_id"] == rec_a["req_id"]
    assert out_b["req_id"] == rec_b["req_id"]
    assert out_a["req_id"] != out_b["req_id"]


def test_synthetic_fixture_parses_without_exception():
    records = [events.parse_line(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    assert all(r is not None for r in records)

    perm_req = next(r for r in records if r["summary"]["event"] == "PermissionRequest")
    req_rec = events.to_request_record(perm_req)
    assert req_rec is not None
    assert req_rec["tool_name"] == "Bash"
    assert req_rec["req_id"] == events.ledger.make_req_id(
        "sess-0001", "prompt-0001", "Bash",
        events.ledger.input_fingerprint({"command": "echo hello", "description": "demo"}))

    post_tool_use = next(r for r in records if r["summary"]["event"] == "PostToolUse")
    out_rec = events.to_outcome_record(post_tool_use)
    assert out_rec is not None
    assert out_rec["outcome"] == "executed"
