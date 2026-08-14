#!/usr/bin/env python3
"""PreToolUse 闸门。它只做一件事：问 daemon 要决定，问不到就 ask。

铁律：永不 deny、永不非零退出、永不打印 traceback。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

ENDPOINT = os.environ.get("CCDESK_ENDPOINT", "http://127.0.0.1:8787/decide")
DEADLINE_S = float(os.environ.get("CCDESK_GATE_DEADLINE", "7.5"))
_ALLOWED = ("allow", "ask")          # 注意：deny 不在其中，闸门永不 deny


def emit(decision: str, reason: str) -> None:
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def ask_daemon(payload: dict) -> tuple[str, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=DEADLINE_S) as response:
        body = json.loads(response.read().decode("utf-8"))
    decision = body.get("permissionDecision")
    reason = str(body.get("reason") or "ccdesk")
    if decision in _ALLOWED:
        return decision, reason
    return "ask", f"ccdesk: 不接受的决定 {decision!r}，降级 ask"


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        emit("ask", "ccdesk: hook 输入不可解析")
        return
    if not isinstance(payload, dict):
        emit("ask", "ccdesk: hook 输入不是对象")
        return
    try:
        decision, reason = ask_daemon(payload)
    except Exception as exc:                       # noqa: BLE001 — 兜住一切
        emit("ask", f"ccdesk: 降级 ({type(exc).__name__})")
        return
    emit(decision, reason)


if __name__ == "__main__":
    try:
        main()
    except BaseException:                          # noqa: BLE001 — 连 SystemExit 也兜
        try:
            emit("ask", "ccdesk: 闸门崩溃，降级 ask")
        except Exception:                          # noqa: BLE001
            pass
    sys.exit(0)
