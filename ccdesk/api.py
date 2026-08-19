"""本地回环 HTTP。CLI 与菜单栏 App 的唯一数据入口。"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from ccdesk import config, decisions, judge, sources
from ccdesk.collector import Collector
from ccdesk.ledger import Ledger
from ccdesk.recon_auth import reconcile
from ccdesk.replay import replay as replay_rules
from ccdesk.sources import list_sessions

OBSERVE_ONLY_REASON = "ccdesk: observe-only (P1)"


@dataclass
class CollectHealth:
    """采集线程心跳（F-B3）：daemon 的采集循环负责更新，/health 负责透出。

    用于区分「无事件」与「线程已死」——last_collect_ts 停止前进即线程死亡。
    """

    last_collect_ts: float = 0.0
    collect_errors: int = 0


@dataclass
class AppState:
    ledger: Ledger
    collector: Collector
    health: CollectHealth = field(default_factory=CollectHealth)
    # 判官结论缓存，键=tool_name+参数指纹。进程内即可，重启重建不影响正确性。
    judge_cache: dict = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within_window(records: dict, now: datetime) -> dict:
    """丢弃 ts_request 早于 RECON_WINDOW_S 的记录（R-WINDOW）。

    账本 append-only 无人清理，不过滤会让历史悬空请求被每次 reconcile 永久重报。
    ts_request 缺失或解析失败（含时区混用）的记录**保留**——reconcile 自身对
    不可解析时间安全，而丢弃它们等于静默扩大漏报面。
    """
    kept: dict = {}
    for req_id, rec in records.items():
        ts = rec.get("ts_request")
        if not isinstance(ts, str):
            kept[req_id] = rec
            continue
        try:
            age = (now - datetime.fromisoformat(ts)).total_seconds()
        except (ValueError, TypeError):
            kept[req_id] = rec
            continue
        if age <= config.RECON_WINDOW_S:
            kept[req_id] = rec
    return kept


def make_server(host: str, port: int, state: AppState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def handle_one_request(self) -> None:
            """客户端提前断开不是服务端故障，别往 stderr 打 traceback。

            App 面板每 3s 并发拉三个接口，用户关面板/切屏时连接会被中途掐断。
            不吞的话 ThreadingHTTPServer 会把它当未捕获异常打印——实测累积
            459 次 / 834KB，而 stderr.log 没有轮转。
            """
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def _send(self, body: dict, status: int = 200) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                # collect_age_s 由服务端算：ts 是 ISO 串、last_collect_ts 是 epoch
                # 浮点，量纲不同没法相减，客户端拿到两者也判不出健康度（I1）。
                self._send({"ok": True, "ts": _now_iso(),
                            "last_collect_ts": state.health.last_collect_ts,
                            "collect_age_s": time.time() - state.health.last_collect_ts,
                            "collect_errors": state.health.collect_errors,
                            # 会话真源现状："cli" 主源 /"file" 降级 /"unknown" 尚未取过。
                            # 降级不再静默（C1）。
                            "session_source": sources.LAST_SOURCE})
            elif path == "/sessions":
                sessions = list_sessions()
                self._send({
                    "sessions": [asdict(s) for s in sessions],
                    "waiting_count": sum(1 for s in sessions if s.status == "waiting"),
                    "ts": _now_iso(),
                })
            elif path == "/ledger":
                # 账本小的时候全量读（当前 9.8KB，这才是常态）；真长到
                # LEDGER_FILTER_BYTES 以上才退化成窗口读，免得每次请求都全文件扫描。
                since_ts = None
                try:
                    if state.ledger.path.stat().st_size > config.LEDGER_FILTER_BYTES:
                        cutoff = datetime.now(timezone.utc) - timedelta(
                            seconds=config.RECON_WINDOW_S)
                        since_ts = cutoff.isoformat()
                except OSError:
                    pass
                merged = state.ledger.read_merged(since_ts=since_ts)
                self._send({"records": list(merged.values()),
                            "bad_line_count": state.ledger.bad_line_count,
                            "filtered_since": since_ts})
            elif path == "/recon/auth":
                now = datetime.now(timezone.utc)
                recent = _within_window(state.ledger.read_merged(), now)
                anomalies = reconcile(recent, list_sessions(), now.isoformat())
                self._send({"anomalies": [asdict(a) for a in anomalies],
                            "checked": len(recent),
                            "bad_line_count": state.ledger.bad_line_count})
            elif path == "/replay":
                query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                try:
                    since_s = float(query.get("since", ["86400"])[0])
                except (TypeError, ValueError):
                    since_s = 86400.0
                now = datetime.now(timezone.utc)
                rows = replay_rules(state.ledger.read_merged(), since_s, now.isoformat())
                self._send({"rows": rows, "since_s": since_s})
            else:
                self._send({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            if self.path.split("?", 1)[0] != "/decide":
                self._send({"error": "not found"}, status=404)
                return

            started = time.monotonic()
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            try:
                verdict = judge.decide(payload, state.judge_cache)
            except Exception:            # noqa: BLE001 — daemon 侧异常绝不能变成会话阻塞
                logging.exception("judge 异常，降级 ask")
                verdict = judge.Verdict("ask", "daemon_error")
            latency_ms = int((time.monotonic() - started) * 1000)

            # 落账失败不改变已经出的决定——会话不能为了记账等在那里。
            try:
                req_id = decisions.build_req_id(payload)
                merged = state.ledger.read_merged()
                decisions.record_decision(
                    state.ledger, req_id, verdict.decision, verdict.decided_by,
                    verdict.confidence, latency_ms,
                    decisions.current_allow_count(merged, req_id), payload=payload)
            except Exception:            # noqa: BLE001
                logging.exception("decision 落账失败")

            body = {"permissionDecision": verdict.decision, "reason": verdict.decided_by}
            if verdict.updated_input is not None:
                body["updatedInput"] = verdict.updated_input
            self._send(body)

        def log_message(self, *args) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)
