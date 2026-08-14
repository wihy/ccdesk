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


def test_permission_denied_becomes_user_denied_outcome():
    ev = {
        "summary": {"ts": "2026-08-14T02:57:06.000000+00:00", "event": "PermissionDenied"},
        "payload": {"session_id": "9975af37", "prompt_id": "7910b5b5", "tool_name": "Bash",
                    "tool_input": {"command": "git status", "description": "st"}},
    }
    assert events.to_outcome_record(ev)["outcome"] == "user_denied"


def test_request_and_outcome_share_req_id():
    rec = events.to_request_record(PERM_REQ)
    ev = {"summary": {"ts": "t", "event": "PostToolUse"}, "payload": dict(PERM_REQ["payload"])}
    out = events.to_outcome_record(ev)
    assert rec["req_id"] == out["req_id"]


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
