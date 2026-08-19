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


def test_replay_is_reproducible(monkeypatch):
    """同一份账本重放两次必须逐字相同 —— 这是「规则改动会不会放松决定」
    这个问题能被回答的前提。走判官就做不到（LLM 不确定），所以重放只跑护栏。"""
    monkeypatch.setattr(replay.judge, "_llm_available", lambda: True)
    monkeypatch.setattr(replay.judge, "_call_llm_judge", lambda *a, **k: ("A", 0.99))
    merged = {"r1": _rec("r1", "ask", "2026-08-19T05:59:00+00:00"),
              "r2": _rec("r2", "allow", "2026-08-19T05:59:00+00:00", multiselect=True)}
    assert replay.replay(merged, 3600, NOW) == replay.replay(merged, 3600, NOW)


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


def test_replay_handles_naive_timestamp_without_crashing():
    """时区混用不能把 /replay 整条路由打 500。

    api.py 的 _within_window 早就防了这一手（注释写着「含时区混用」），
    抽到 replay 时把这道防护丢了 —— 实测确实抛
    TypeError: can't subtract offset-naive and offset-aware datetimes。
    """
    merged = {"r1": _rec("r1", "ask", "2026-08-19T05:59:00")}      # 无时区偏移
    rows = replay.replay(merged, 3600, NOW)
    assert len(rows) == 1          # 判不了窗口就保留，不崩


def test_replay_handles_aware_row_with_naive_now():
    """反向组合同样不能崩。"""
    merged = {"r1": _rec("r1", "ask", "2026-08-19T05:59:00+00:00")}
    assert len(replay.replay(merged, 3600, "2026-08-19T06:00:00")) == 1


def test_replay_never_calls_the_llm_judge(monkeypatch):
    """重放必须只跑确定性的那几层（护栏），绝不调 LLM 判官。

    两个理由，第二个才是主要的：
    * 成本：`replay --since=7d` 会对每条记录串行发一次付费 API 请求，
      CLI 10s 就超时了而 daemon 还在继续烧。安全网不该是最贵的命令。
    * **可复现性**：LLM 是不确定的，同一条记录两次重放可能给出不同结果——
      那样「规则改动会不会放松决定」这个问题根本没法回答。重放要看的是
      规则，不是模型今天的心情。
    """
    calls = []
    monkeypatch.setattr(replay.judge, "_llm_available", lambda: True)
    monkeypatch.setattr(replay.judge, "_call_llm_judge",
                        lambda *a, **k: calls.append(1) or ("A", 0.99))
    merged = {"r1": _rec("r1", "ask", "2026-08-19T05:59:00+00:00")}
    rows = replay.replay(merged, 3600, NOW)
    assert calls == [], "重放不得触发任何 LLM 调用"
    assert rows[0]["now"] == "ask"
    assert rows[0].get("now_by") == "judge_skipped_in_replay"
