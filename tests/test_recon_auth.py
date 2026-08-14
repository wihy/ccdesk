from ccdesk.recon_auth import reconcile
from ccdesk.sources import Session

NOW = "2026-08-14T03:00:00+00:00"


def _sess(status="idle", sid="s1"):
    return Session(pid=1, session_id=sid, cwd="/w", name="n", kind="interactive",
                   status=status, waiting_for=None, started_at=0, source="cli")


def _rec(**kw):
    base = {"req_id": "r1", "session_id": "s1", "tool_name": "Bash",
            "input_digest": "git status", "ts_request": "2026-08-14T02:00:00+00:00",
            "decision": None, "outcome": None, "allow_count": 0}
    base.update(kw)
    return base


def test_dangling_request_when_no_decision_after_threshold():
    got = reconcile({"r1": _rec()}, [_sess()], NOW)
    assert [a.kind for a in got] == ["dangling_request"]


def test_fresh_undecided_request_is_not_an_anomaly():
    rec = _rec(ts_request="2026-08-14T02:59:50+00:00")
    assert reconcile({"r1": rec}, [_sess()], NOW) == []


def test_empty_allow_when_allowed_but_no_outcome():
    rec = _rec(decision="allow", ts_decision="2026-08-14T02:00:01+00:00")
    got = reconcile({"r1": rec}, [_sess()], NOW)
    assert [a.kind for a in got] == ["empty_allow"]


def test_allow_with_outcome_is_clean():
    rec = _rec(decision="allow", ts_decision="2026-08-14T02:00:01+00:00", outcome="executed")
    assert reconcile({"r1": rec}, [_sess()], NOW) == []


def test_duplicate_allow_detected():
    rec = _rec(decision="allow", outcome="executed", allow_count=2)
    got = reconcile({"r1": rec}, [_sess()], NOW)
    assert [a.kind for a in got] == ["duplicate_allow"]


def test_silent_stall_when_ask_and_session_still_waiting():
    rec = _rec(decision="ask", ts_decision="2026-08-14T02:00:01+00:00")
    got = reconcile({"r1": rec}, [_sess(status="waiting")], NOW)
    assert [a.kind for a in got] == ["silent_stall"]


def test_ask_on_non_waiting_session_is_clean():
    rec = _rec(decision="ask", ts_decision="2026-08-14T02:00:01+00:00")
    assert reconcile({"r1": rec}, [_sess(status="busy")], NOW) == []


def test_clean_ledger_produces_zero_false_positives():
    records = {
        "a": _rec(req_id="a", decision="allow", outcome="executed",
                  ts_decision="2026-08-14T02:00:01+00:00"),
        "b": _rec(req_id="b", decision="ask", outcome="user_denied",
                  ts_decision="2026-08-14T02:00:01+00:00"),
        # F-1 形态：P1 observe-only 无决策写入方，用户拒绝产生 outcome、decision 仍 None
        "c": _rec(req_id="c", outcome="user_denied"),
        # 时延窗口内的新请求（10s 前 < 60s 阈值）
        "d": _rec(req_id="d", ts_request="2026-08-14T02:59:50+00:00"),
        # allow_count=1 是正常单次放行，不是 duplicate
        "e": _rec(req_id="e", decision="allow", outcome="executed",
                  allow_count=1, ts_decision="2026-08-14T02:00:01+00:00"),
    }
    assert reconcile(records, [_sess(status="busy")], NOW) == []


def test_unparseable_timestamp_does_not_crash():
    rec = _rec(ts_request="not-a-time")
    assert reconcile({"r1": rec}, [_sess()], NOW) == []


def test_denied_outcome_without_decision_is_not_dangling():
    """对抗回归：decision=None + outcome=user_denied + 超过 60s 不算悬空。

    P1 observe-only 管线中 decision 字段无人写入，用户在 CC 原生弹窗点拒绝
    只产生 outcome —— 每条被拒请求都被误报 dangling_request 就是狼来了。
    """
    rec = _rec(outcome="user_denied")
    assert reconcile({"r1": rec}, [_sess()], NOW) == []


def test_naive_now_with_aware_ts_does_not_crash():
    """naive now_iso 与带时区 ts 相减抛 TypeError，须安全跳过而非崩溃。"""
    assert reconcile({"r1": _rec()}, [_sess()], "2026-08-14T03:00:00") == []
