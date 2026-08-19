import json

import pytest

from ccdesk import decisions
from ccdesk.ledger import Ledger, input_fingerprint, make_req_id


def _ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl", tmp_path / "ledger.bad.jsonl")


def test_build_req_id_matches_collector_caliber():
    """闸门侧现算的 req_id 必须与 collector 从 events.jsonl 算的一致，否则两侧对不上号。

    C-3 教训：input_fingerprint 必须两侧都传同一个 tool_name，只改一侧等于没改。
    """
    payload = {
        "session_id": "s1", "prompt_id": "p1", "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "q", "header": "h",
                                      "options": [{"label": "A"}], "multiSelect": False}]},
    }
    expected = make_req_id("s1", "p1", "AskUserQuestion",
                           input_fingerprint(payload["tool_input"], "AskUserQuestion"))
    assert decisions.build_req_id(payload) == expected


def test_build_req_id_survives_garbage_tool_input():
    """闸门永不崩：tool_input 不是 dict 也得算得出 req_id。"""
    assert decisions.build_req_id({"tool_input": "not-a-dict"})
    assert decisions.build_req_id({})


def test_record_decision_appends_row(tmp_path):
    led = _ledger(tmp_path)
    row = decisions.record_decision(led, "r1", "allow", "allowlist:R01", 1.0, 12, 0)
    assert row["decision"] == "allow"
    assert row["decided_by"] == "allowlist:R01"
    assert row["allow_count"] == 1
    merged = led.read_merged()
    assert merged["r1"]["decision"] == "allow"
    assert merged["r1"]["allow_count"] == 1
    assert merged["r1"]["latency_ms"] == 12


def test_allow_count_increments_only_on_allow(tmp_path):
    """duplicate_allow 对账依赖它：只有 allow 才递增，ask 不动。"""
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "allow", "judge:haiku", 0.9, 10, 0)
    merged = led.read_merged()
    assert decisions.current_allow_count(merged, "r1") == 1
    decisions.record_decision(led, "r1", "ask", "timeout_fallback", None, 7500,
                              decisions.current_allow_count(merged, "r1"))
    merged = led.read_merged()
    assert merged["r1"]["decision"] == "ask"
    assert decisions.current_allow_count(merged, "r1") == 1


def test_two_allows_make_allow_count_two(tmp_path):
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "allow", "cache", 0.9, 1, 0)
    decisions.record_decision(led, "r1", "allow", "cache", 0.9, 1,
                              decisions.current_allow_count(led.read_merged(), "r1"))
    assert decisions.current_allow_count(led.read_merged(), "r1") == 2


def test_confidence_none_is_not_written(tmp_path):
    """read_merged 会忽略 None 值，写进去只是白占字节；确认不写空键。"""
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "ask", "guardrail:multiselect", None, 3, 0)
    raw = json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").strip())
    assert "confidence" not in raw


def test_current_allow_count_tolerates_garbage(tmp_path):
    assert decisions.current_allow_count({"r1": {"allow_count": "x"}}, "r1") == 0
    assert decisions.current_allow_count({}, "nope") == 0
