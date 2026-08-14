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
    }
    assert reconcile(records, [_sess(status="busy")], NOW) == []


def test_unparseable_timestamp_does_not_crash():
    rec = _rec(ts_request="not-a-time")
    assert reconcile({"r1": rec}, [_sess()], NOW) == []
