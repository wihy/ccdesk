"""replay 是规则变更的安全网：改判官/护栏前先看会不会把过去的 ask 变成 allow。"""
import pytest

from ccdesk import replay

NOW = "2026-08-19T06:00:00+00:00"


def _rec(req_id, decision, ts, multiselect=False, label="A"):
    return {"req_id": req_id, "decision": decision, "ts_request": ts,
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "q", "header": "h",
                                          "options": [{"label": label}],
                                          "multiSelect": multiselect}]}}


def test_replay_flags_changed_decisions():
    """历史上是 allow，但现在的护栏会拦掉 multiSelect —— 必须标出来。"""
    merged = {"r1": _rec("r1", "allow", "2026-08-19T05:59:00+00:00", multiselect=True)}
    rows = replay.replay(merged, 3600, NOW)
    assert len(rows) == 1
    assert rows[0]["was"] == "allow"
    assert rows[0]["now"] == "ask"
    assert rows[0]["changed"] is True


def test_replay_marks_unchanged():
    merged = {"r1": _rec("r1", "ask", "2026-08-19T05:59:00+00:00", multiselect=True)}
    rows = replay.replay(merged, 3600, NOW)
    assert rows[0]["changed"] is False


def test_replay_respects_window():
    merged = {"old": _rec("old", "ask", "2026-08-01T00:00:00+00:00")}
    assert replay.replay(merged, 3600, NOW) == []


def test_replay_skips_records_without_tool_input():
    """P1 账本里的老记录没存 tool_input，重放不了 —— 跳过而不是崩。"""
    merged = {"r1": {"req_id": "r1", "decision": "ask",
                     "ts_request": "2026-08-19T05:59:00+00:00"}}
    assert replay.replay(merged, 3600, NOW) == []


def test_replay_does_not_use_live_cache(monkeypatch):
    """重放看的是规则，不是历史缓存 —— 否则每条都会被缓存染成 allow。"""
    seen = []
    real_decide = replay.judge.decide

    def spy(payload, cache):
        seen.append(cache)
        return real_decide(payload, cache)

    monkeypatch.setattr(replay.judge, "decide", spy)
    replay.replay({"r1": _rec("r1", "ask", "2026-08-19T05:59:00+00:00")}, 3600, NOW)
    assert seen and all(c == {} for c in seen)


def test_replay_never_writes_ledger(tmp_path):
    """只读 —— 重放不得往账本里加任何东西。"""
    from ccdesk.ledger import Ledger
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "b.jsonl")
    led.append({"req_id": "r1", "ts_request": "2026-08-19T05:59:00+00:00"})
    before = (tmp_path / "l.jsonl").read_bytes()
    replay.replay(led.read_merged(), 3600, NOW)
    assert (tmp_path / "l.jsonl").read_bytes() == before


def test_replay_tolerates_bad_timestamps():
    merged = {"r1": _rec("r1", "ask", "not-a-timestamp")}
    assert len(replay.replay(merged, 3600, NOW)) == 1     # 无法判定窗口就保留


@pytest.mark.parametrize("text,seconds", [
    ("30m", 1800), ("24h", 86400), ("7d", 604800), ("120", 120), ("1h", 3600),
])
def test_parse_since(text, seconds):
    assert replay.parse_since(text) == seconds


def test_parse_since_rejects_garbage():
    with pytest.raises(ValueError):
        replay.parse_since("later")
