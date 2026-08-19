import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from ccdesk.api import AppState, CollectHealth, make_server
from ccdesk.collector import Collector
from ccdesk.ledger import Ledger


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def server(tmp_path, monkeypatch):
    from ccdesk import api, sources

    # R-TimeFix：相对时间而非写死日历日期——绝对时间戳越过对账窗口后测试会静默变红。
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    led.append({"req_id": "r1", "session_id": "s1", "tool_name": "Bash",
                "input_digest": "git status", "ts_request": _iso(one_hour_ago),
                "decision": None, "outcome": None})
    col = Collector(tmp_path / "events.jsonl", led, tmp_path / "state.json")
    fake = sources.Session(pid=1, session_id="s1", cwd="/w", name="n", kind="interactive",
                           status="waiting", waiting_for="dialog open", started_at=0,
                           source="cli")
    monkeypatch.setattr(api, "list_sessions", lambda: [fake])
    srv = make_server("127.0.0.1", 0, AppState(led, col))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read().decode())


def test_health_ok(server):
    assert get(server, "/health")["ok"] is True


def test_health_exposes_collect_heartbeat(tmp_path):
    """F-B3：/health 透出采集心跳，区分「无事件」与「采集线程已死」。

    last_collect_ts 由 daemon._collect_forever 在每轮 run_once 正常结束后写入；
    这里模拟一次已完成的心跳，断言 /health 原样透出两键且 ok 仍在。
    """
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    col = Collector(tmp_path / "events.jsonl", led, tmp_path / "state.json")
    health = CollectHealth(last_collect_ts=time.time())
    srv = make_server("127.0.0.1", 0, AppState(led, col, health))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        body = get(base, "/health")
    finally:
        srv.shutdown()
    assert body["ok"] is True
    assert isinstance(body["last_collect_ts"], float) and body["last_collect_ts"] > 0
    assert isinstance(body["collect_errors"], int) and body["collect_errors"] == 0
    # I1：健康判据必须能直接读，不能让调用方拿 ISO 串减 epoch 浮点。
    assert isinstance(body["collect_age_s"], float) and 0 <= body["collect_age_s"] < 60


def test_health_exposes_session_source(server, monkeypatch):
    """C1：会话主源降级不再静默——/health 透出 sources.LAST_SOURCE。"""
    from ccdesk import sources

    monkeypatch.setattr(sources, "LAST_SOURCE", "file")
    assert get(server, "/health")["session_source"] == "file"
    monkeypatch.setattr(sources, "LAST_SOURCE", "cli")
    assert get(server, "/health")["session_source"] == "cli"


def test_sessions_exposes_waiting_reason(server):
    body = get(server, "/sessions")
    assert body["sessions"][0]["waiting_for"] == "dialog open"
    assert body["waiting_count"] == 1


def test_ledger_returns_records(server):
    body = get(server, "/ledger")
    assert body["records"][0]["req_id"] == "r1"


def test_recon_auth_reports_dangling(server):
    body = get(server, "/recon/auth")
    assert [a["kind"] for a in body["anomalies"]] == ["dangling_request"]


def post(base, path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def test_decide_rejects_non_askuserquestion_shape(server):
    """非 AskUserQuestion 形态的输入落护栏 —— 闸门只挂 AskUserQuestion，
    真收到别的说明配置有误，一律 ask。"""
    body = post(server, "/decide", {"tool_name": "Read", "tool_input": {}})
    assert body["permissionDecision"] == "ask"
    assert body["reason"] == "guardrail:no_questions"
    assert "updatedInput" not in body


def test_unknown_path_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/nope")
    assert exc.value.code == 404


def test_recon_window_filters_old_records(tmp_path, monkeypatch):
    """R-WINDOW：超出 RECON_WINDOW_S（默认 24h）的悬空记录不进对账。

    夹具：25h 前一条悬空（应被过滤）+ 1h 前一条悬空（应保留）。
    断言只报 1h 那条，且 checked 只数窗口内的。
    """
    from ccdesk import api, sources

    now = datetime.now(timezone.utc)
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    led.append({"req_id": "r_old", "session_id": "s1", "tool_name": "Bash",
                "input_digest": "old cmd", "ts_request": _iso(now - timedelta(hours=25)),
                "decision": None, "outcome": None})
    led.append({"req_id": "r_new", "session_id": "s1", "tool_name": "Bash",
                "input_digest": "new cmd", "ts_request": _iso(now - timedelta(hours=1)),
                "decision": None, "outcome": None})
    col = Collector(tmp_path / "events.jsonl", led, tmp_path / "state.json")
    fake = sources.Session(pid=1, session_id="s1", cwd="/w", name="n", kind="interactive",
                           status="idle", waiting_for=None, started_at=0, source="cli")
    monkeypatch.setattr(api, "list_sessions", lambda: [fake])
    srv = make_server("127.0.0.1", 0, AppState(led, col))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        body = get(base, "/recon/auth")
    finally:
        srv.shutdown()
    assert [a["req_id"] for a in body["anomalies"]] == ["r_new"]
    assert body["checked"] == 1


def test_client_disconnect_does_not_write_traceback(server, capfd):
    """客户端读一半就断开，服务端不得把 traceback 打到 stderr。

    App 面板每 3s 并发拉三个接口，用户关面板/切屏时连接会被中途掐断。
    ThreadingHTTPServer 默认把 BrokenPipe 当未捕获异常打印，实测累积了
    459 次 traceback / 834KB，日志无轮转会一直涨。
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(server)
    capfd.readouterr()                       # 清掉此前噪音，只看本次
    for _ in range(5):                       # 单次可能不触发写，多打几发
        conn = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        conn.sendall(b"GET /sessions HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        conn.close()
    time.sleep(0.5)
    err = capfd.readouterr().err
    assert "BrokenPipeError" not in err
    assert "Traceback" not in err


def test_decide_records_decision_to_ledger(server):
    """P2 的核心解锁点：/decide 必须把决定写进账本，why 与三类对账才有数据。"""
    payload = {"session_id": "s9", "prompt_id": "p9", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "q", "header": "h",
                                             "options": [{"label": "A"}],
                                             "multiSelect": True}]}}
    body = post(server, "/decide", payload)
    assert body["permissionDecision"] == "ask"
    assert body["reason"] == "guardrail:multiselect"

    from ccdesk.decisions import build_req_id
    req_id = build_req_id(payload)
    records = get(server, "/ledger")["records"]
    row = next(r for r in records if r["req_id"] == req_id)
    assert row["decision"] == "ask"
    assert row["decided_by"] == "guardrail:multiselect"
    assert row["allow_count"] == 0
    assert isinstance(row["latency_ms"], int)


def test_decide_on_garbage_still_returns_ask(server):
    """闸门铁律的服务端一侧：输入再离谱也只能给 allow/ask。"""
    for bad in ({"tool_input": "not-a-dict"}, {}, {"tool_input": {"questions": [{}]}}):
        assert post(server, "/decide", bad)["permissionDecision"] == "ask"


def test_decide_allow_carries_updated_input(server, monkeypatch):
    """判官放行时必须带 updatedInput —— 不带的话 CC 会直接丢弃这个 allow（U5 坐实）。"""
    from ccdesk import judge
    monkeypatch.setattr(judge, "_llm_available", lambda: True)
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: ("A", 0.95))
    payload = {"session_id": "s8", "prompt_id": "p8", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "选啥", "header": "h",
                                             "options": [{"label": "A"}, {"label": "B"}],
                                             "multiSelect": False}]}}
    body = post(server, "/decide", payload)
    assert body["permissionDecision"] == "allow"
    assert body["updatedInput"]["answers"] == {"选啥": "A"}


def test_replay_route_returns_rows(server):
    """/replay 是只读的规则重放，CLI 的 replay 子命令依赖它。"""
    body = get(server, "/replay?since=86400")
    assert "rows" in body and isinstance(body["rows"], list)
    assert body["since_s"] == 86400.0


def test_replay_route_tolerates_bad_since(server):
    assert get(server, "/replay?since=abc")["since_s"] == 86400.0
