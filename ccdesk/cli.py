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


def _cmd_gate(action: str) -> int:
    """闸门装卸。这条路径直接改用户的 settings.json，不走 daemon HTTP。"""
    from ccdesk import gate_install

    if action == "status":
        state = gate_install.status()
        if state["installed"]:
            print(f"闸门已安装  matcher={state['matcher']}  →  {gate_install.SETTINGS_PATH}")
        else:
            print(f"闸门未安装  →  {gate_install.SETTINGS_PATH}")
        return 0

    try:
        result = (gate_install.install() if action == "install"
                  else gate_install.uninstall())
    except ValueError as exc:
        print(f"拒绝写入：{exc}", file=sys.stderr)
        return 2

    messages = {
        "installed": f"闸门已安装  matcher={gate_install.MATCHER}  →  {gate_install.SETTINGS_PATH}\n"
                     "已在跑的会话也会生效，不用重启（hook 是每次工具调用时读的）；"
                     "原文件已备份为 settings.json.ccdesk-bak.<时间戳>",
        "already": "闸门早已安装，未做改动",
        "removed": f"闸门已卸载  →  {gate_install.SETTINGS_PATH}（已备份）",
        "absent": "闸门本来就没装，未做改动",
    }
    print(messages[result])
    return 0


def _cmd_replay(since: str) -> int:
    from ccdesk import replay as replay_mod

    seconds = replay_mod.parse_since(since)
    body = _get(f"/replay?since={int(seconds)}")
    rows = body["rows"]
    changed = [r for r in rows if r["changed"]]
    print(f"重放 {len(rows)} 条请求，决定会变的 {len(changed)} 条\n")
    for row in rows:
        if not row["changed"]:
            continue
        loosened = " ⚠️ 规则放松" if row["now"] == "allow" else ""
        print(f"{row['req_id']}  {row.get('tool_name','')}  "
              f"{row['was']} → {row['now']}{loosened}")
    if not rows:
        print("（窗口内没有可重放的请求：P1 老账本没存 tool_input，重放不了）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ccdesk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sessions")
    sub.add_parser("recon")
    for name in ("trace", "why"):
        p = sub.add_parser(name)
        p.add_argument("req_id")
    gate = sub.add_parser("gate")
    gate.add_argument("action", choices=("install", "uninstall", "status"))
    rp = sub.add_parser("replay")
    rp.add_argument("--since", default="24h", help="时间窗，如 30m / 24h / 7d")
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
        if args.cmd == "gate":
            return _cmd_gate(args.action)
        if args.cmd == "replay":
            return _cmd_replay(args.since)
    except ValueError as exc:
        # parse_since 之类已经写好了中文报错，别让它变成裸 traceback
        print(str(exc), file=sys.stderr)
        return 2
    except (urllib.error.URLError, ConnectionError, OSError, TimeoutError) as exc:
        print(f"连不上 ccdesk daemon（{BASE}）：{type(exc).__name__}。"
              f"先看 launchctl list | grep ccdesk", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
