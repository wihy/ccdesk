"""本地回环 HTTP。CLI 与菜单栏 App 的唯一数据入口。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ccdesk import config
from ccdesk.collector import Collector
from ccdesk.ledger import Ledger
from ccdesk.recon_auth import reconcile
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
                self._send({"ok": True, "ts": _now_iso(),
                            "last_collect_ts": state.health.last_collect_ts,
                            "collect_errors": state.health.collect_errors})
            elif path == "/sessions":
                sessions = list_sessions()
                self._send({
                    "sessions": [asdict(s) for s in sessions],
                    "waiting_count": sum(1 for s in sessions if s.status == "waiting"),
                    "ts": _now_iso(),
                })
            elif path == "/ledger":
                merged = state.ledger.read_merged()
                self._send({"records": list(merged.values()),
                            "bad_line_count": state.ledger.bad_line_count})
            elif path == "/recon/auth":
                now = datetime.now(timezone.utc)
                recent = _within_window(state.ledger.read_merged(), now)
                anomalies = reconcile(recent, list_sessions(), now.isoformat())
                self._send({"anomalies": [asdict(a) for a in anomalies],
                            "checked": len(recent),
                            "bad_line_count": state.ledger.bad_line_count})
            else:
                self._send({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if self.path.split("?", 1)[0] == "/decide":
                self._send({"permissionDecision": "ask", "reason": OBSERVE_ONLY_REASON})
            else:
                self._send({"error": "not found"}, status=404)

        def log_message(self, *args) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)
