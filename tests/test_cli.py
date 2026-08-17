from ccdesk import cli

LEDGER = {"records": [{
    "req_id": "abc123", "session_id": "s1", "tool_name": "Bash",
    "input_digest": "git status", "ts_request": "2026-08-14T02:00:00+00:00",
    "decision": "allow", "decided_by": "allowlist:R07", "ts_decision": "2026-08-14T02:00:01+00:00",
    "outcome": "executed", "ts_outcome": "2026-08-14T02:00:02+00:00"}]}
SESSIONS = {"sessions": [{"pid": 1, "name": "n", "status": "waiting",
                          "waiting_for": "dialog open", "cwd": "/w"}],
            "waiting_count": 1}


def _stub(monkeypatch, mapping):
    monkeypatch.setattr(cli, "_get", lambda path: mapping[path])


def test_sessions_prints_waiting_marker(monkeypatch, capsys):
    _stub(monkeypatch, {"/sessions": SESSIONS})
    assert cli.main(["sessions"]) == 0
    out = capsys.readouterr().out
    assert "waiting" in out and "dialog open" in out


def test_trace_prints_three_stage_timeline(monkeypatch, capsys):
    _stub(monkeypatch, {"/ledger": LEDGER})
    assert cli.main(["trace", "abc123"]) == 0
    out = capsys.readouterr().out
    assert "02:00:00" in out and "02:00:01" in out and "02:00:02" in out


def test_trace_unknown_id_returns_1(monkeypatch, capsys):
    _stub(monkeypatch, {"/ledger": LEDGER})
    assert cli.main(["trace", "nope"]) == 1


def test_why_prints_decider_and_reason(monkeypatch, capsys):
    _stub(monkeypatch, {"/ledger": LEDGER})
    assert cli.main(["why", "abc123"]) == 0
    assert "allowlist:R07" in capsys.readouterr().out


def test_recon_prints_anomalies(monkeypatch, capsys):
    _stub(monkeypatch, {"/recon/auth": {
        "anomalies": [{"kind": "empty_allow", "req_id": "abc123", "session_id": "s1",
                       "detail": "已放行但未见执行", "age_s": 900.0}],
        "checked": 1, "bad_line_count": 0}})
    assert cli.main(["recon"]) == 0
    assert "empty_allow" in capsys.readouterr().out


def test_daemon_unreachable_returns_2_without_traceback(monkeypatch, capsys):
    def boom(path):
        raise ConnectionRefusedError()

    monkeypatch.setattr(cli, "_get", boom)
    assert cli.main(["sessions"]) == 2
    assert "daemon" in capsys.readouterr().err
