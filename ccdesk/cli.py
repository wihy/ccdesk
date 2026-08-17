"""ccdesk 命令行。只读 daemon HTTP，不直接碰文件。"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from ccdesk import config

BASE = f"http://{config.API_HOST}:{config.API_PORT}"


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _find(req_id: str) -> dict | None:
    for record in _get("/ledger")["records"]:
        if record.get("req_id") == req_id:
            return record
    return None


def _cmd_sessions() -> int:
    body = _get("/sessions")
    print(f"会话 {len(body['sessions'])} 个，等待中 {body['waiting_count']} 个\n")
    for s in body["sessions"]:
        mark = "◀ 等待" if s.get("status") == "waiting" else "     "
        reason = f"  ({s['waiting_for']})" if s.get("waiting_for") else ""
        print(f"{mark} {s.get('status','?'):<8} {s.get('name',''):<24} "
              f"pid={s.get('pid')}  {s.get('cwd','')}{reason}")
    return 0


def _cmd_trace(req_id: str) -> int:
    record = _find(req_id)
    if record is None:
        print(f"找不到 req_id={req_id}", file=sys.stderr)
        return 1
    print(f"req_id  {record['req_id']}")
    print(f"会话    {record.get('session_id')}  {record.get('cwd','')}")
    print(f"工具    {record.get('tool_name')}  «{record.get('input_digest','')}»\n")
    for label, ts, extra in (
        ("① 请求", record.get("ts_request"), record.get("permission_mode") or ""),
        ("② 决策", record.get("ts_decision"),
         f"{record.get('decision')} by {record.get('decided_by')}"),
        ("③ 结局", record.get("ts_outcome"), record.get("outcome") or ""),
    ):
        print(f"{label}  {ts or '—':<34} {extra}")
    return 0


def _cmd_why(req_id: str) -> int:
    record = _find(req_id)
    if record is None:
        print(f"找不到 req_id={req_id}", file=sys.stderr)
        return 1
    print(f"决定    {record.get('decision')}")
    print(f"决定者  {record.get('decided_by')}")
    print(f"置信度  {record.get('confidence', '—')}")
    print(f"耗时    {record.get('latency_ms', '—')} ms")
    return 0


def _cmd_recon() -> int:
    body = _get("/recon/auth")
    anomalies = body["anomalies"]
    print(f"对账 {body['checked']} 条请求，异常 {len(anomalies)} 条"
          f"（坏行 {body.get('bad_line_count', 0)}）\n")
    for a in anomalies:
        print(f"{a['kind']:<18} {a['req_id']}  {a['detail']}  ({a['age_s']:.0f}s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ccdesk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sessions")
    sub.add_parser("recon")
    for name in ("trace", "why"):
        p = sub.add_parser(name)
        p.add_argument("req_id")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "sessions":
            return _cmd_sessions()
        if args.cmd == "recon":
            return _cmd_recon()
        if args.cmd == "trace":
            return _cmd_trace(args.req_id)
        if args.cmd == "why":
            return _cmd_why(args.req_id)
    except (urllib.error.URLError, ConnectionError, OSError, TimeoutError) as exc:
        print(f"连不上 ccdesk daemon（{BASE}）：{type(exc).__name__}。"
              f"先看 launchctl list | grep ccdesk", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
