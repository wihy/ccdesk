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


def test_decide_rejects_non_askuserquestion_tool(server):
    """闸门只挂 AskUserQuestion，真收到别的工具说明配置有误 —— 在工具类型这一层就拦掉，
    不能等到形状检查（那样 matcher 一旦放宽，任何带 questions 的工具都会被代答）。"""
    body = post(server, "/decide", {"tool_name": "Read", "tool_input": {}})
    assert body["permissionDecision"] == "ask"
    assert body["reason"] == "guardrail:unsupported_tool"
    assert "updatedInput" not in body


def test_decide_rejects_malformed_askuserquestion_shape(server):
    """工具对但形状不对，落形状护栏。"""
    body = post(server, "/decide", {"tool_name": "AskUserQuestion", "tool_input": {}})
    assert body["permissionDecision"] == "ask"
    assert body["reason"] == "guardrail:no_questions"


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
    # 没有 allow 行就不该有 allow_count 键（recon 侧用 .get(...,0) 兜底）
    assert "allow_count" not in row
    assert isinstance(row["latency_ms"], int)


def test_decide_on_garbage_still_returns_ask(server):
    """闸门铁律的服务端一侧：输入再离谱也只能给 allow/ask。"""
    for bad in ({"tool_input": "not-a-dict"}, {}, {"tool_input": {"questions": [{}]}}):
        assert post(server, "/decide", bad)["permissionDecision"] == "ask"


def test_decide_allow_carries_updated_input(server, monkeypatch):
    """判官放行时必须带 updatedInput —— 不带的话 CC 会直接丢弃这个 allow（U5 坐实）。

    /decide 现在走「判官 + 人工并行」，所以要 mock 常驻判官而不是旧的同步入口；
    否则判官不可用、没人点，请求会一直等到 23s 窗口到点。
    """
    from ccdesk import judge

    class FakeRT:
        def available(self): return True
        def ask(self, q, labels, budget_s): return ("A", 0.95)

    monkeypatch.setattr(judge, "_judge_runtime", lambda: FakeRT())
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


def test_ledger_route_does_not_filter_small_ledger(server):
    """小账本走全量读 —— 阈值内行为必须与加过滤前完全一致。"""
    body = get(server, "/ledger")
    assert body["filtered_since"] is None
    assert body["records"][0]["req_id"] == "r1"


def test_ledger_route_filters_when_over_threshold(server, monkeypatch):
    """超阈值才退化成窗口读。用 monkeypatch 把阈值调到 0 来验证这条分支真的存在。"""
    from ccdesk import config
    monkeypatch.setattr(config, "LEDGER_FILTER_BYTES", 0)
    assert get(server, "/ledger")["filtered_since"] is not None


def test_ledger_never_claims_allow_without_updated_input(server):
    """账本必须记会话实际收到的决定，不能记 daemon 自己算的。

    闸门对 AskUserQuestion 会把「allow 但没有 updatedInput」降级成 ask
    （CC 会丢弃这种 allow）。daemon 若照旧记 allow，审计链就与现实相反，
    还会让 empty_allow 对着一个从未发生的放行报警。
    两边用同一条规则，就不会分叉。
    """
    from ccdesk import judge
    import ccdesk.api as api_mod

    # 造一个「判官给了 allow 但没给 updated_input」的畸形裁决
    monkey = judge.Verdict("allow", "judge:haiku", 0.99, None)
    orig = judge.decide
    api_mod.judge.decide = lambda p, c: monkey
    try:
        payload = {"session_id": "sX", "prompt_id": "pX", "tool_name": "AskUserQuestion",
                   "tool_input": {"questions": [{"question": "q", "header": "h",
                                                 "options": [{"label": "A"}],
                                                 "multiSelect": False}]}}
        body = post(server, "/decide", payload)
        assert body["permissionDecision"] == "ask"
        assert "updatedInput" not in body

        from ccdesk.decisions import build_req_id
        row = next(r for r in get(server, "/ledger")["records"]
                   if r["req_id"] == build_req_id(payload))
        assert row["decision"] == "ask", "账本不得声称 allow"
        assert "allow_count" not in row, "allow_count 不得为一次没发生的放行递增"
    finally:
        api_mod.judge.decide = orig


def test_focus_route_reports_unmapped_session(server, monkeypatch):
    """会话不在 cmux 里时，路由要如实说不行，让 App 回退到打开目录。"""
    from ccdesk import focus as focus_mod
    monkeypatch.setattr(focus_mod, "resolve", lambda pid: None)
    body = post(server, "/focus", {"pid": 4242})
    assert body["ok"] is False
    assert body["reason"] == "not_in_cmux"


def test_focus_route_switches_workspace(server, monkeypatch):
    from ccdesk import focus as focus_mod
    calls = []
    monkeypatch.setattr(focus_mod, "resolve",
                        lambda pid: {"workspace_ref": "workspace:8", "surface_ref": "surface:11",
                                     "workspace_title": "总管监控", "tty": "ttys013"})
    monkeypatch.setattr(focus_mod, "_run_cmux",
                        lambda *a: calls.append(a) or (0, "OK workspace:8"))
    body = post(server, "/focus", {"pid": 4242})
    assert body["ok"] is True
    assert body["workspace_title"] == "总管监控"
    assert calls == [("select-workspace", "--workspace", "workspace:8")]


def test_focus_route_rejects_bad_pid(server):
    for bad in ({}, {"pid": "abc"}, {"pid": None}):
        body = post(server, "/focus", bad)
        assert body["ok"] is False
        assert body["reason"] == "bad_pid"


def test_pending_route_empty_by_default(server):
    """没有待决项是常态，不是错误。"""
    body = get(server, "/pending")
    assert body["items"] == []


def test_pending_and_resolve_roundtrip(server):
    """面板拿到待决项 → 点一个选项 → 阻塞中的 /decide 立刻拿到答案。"""
    import threading
    payload = {"session_id": "sp", "prompt_id": "pp", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "端到端选哪个", "header": "h",
                                             "options": [{"label": "甲"}, {"label": "乙"}],
                                             "multiSelect": False}]}}
    result = {}

    def call_decide():
        result["body"] = post(server, "/decide", payload)

    t = threading.Thread(target=call_decide, daemon=True)
    t.start()

    # 等它挂到待决板上
    deadline = time.time() + 5
    items = []
    while time.time() < deadline:
        items = get(server, "/pending")["items"]
        if items:
            break
        time.sleep(0.1)
    assert items, "/decide 应当把请求挂进待决板"
    assert items[0]["question"] == "端到端选哪个"
    assert [o["label"] for o in items[0]["options"]] == ["甲", "乙"]
    assert items[0]["remaining_s"] > 0

    accepted = post(server, "/resolve", {"req_id": items[0]["req_id"], "answer": "乙"})
    assert accepted["accepted"] is True

    t.join(timeout=10)
    assert result["body"]["permissionDecision"] == "allow"
    assert result["body"]["reason"] == "human"
    assert result["body"]["updatedInput"]["answers"] == {"端到端选哪个": "乙"}
    assert get(server, "/pending")["items"] == [], "决完要从板上摘掉"


def test_resolve_rejects_illegal_answer(server):
    assert post(server, "/resolve", {"req_id": "nope", "answer": "x"})["accepted"] is False
    assert post(server, "/resolve", {"answer": "x"})["reason"] == "bad_req_id"
