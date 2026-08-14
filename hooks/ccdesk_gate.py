#!/usr/bin/env python3
"""PreToolUse 闸门。它只做一件事：问 daemon 要决定，问不到就 ask。

铁律：永不 deny、永不非零退出、永不打印 traceback。
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import urllib.request

ENDPOINT = os.environ.get("CCDESK_ENDPOINT", "http://127.0.0.1:8787/decide")
_ALLOWED = ("allow", "ask")          # 注意：deny 不在其中，闸门永不 deny


def _env_deadline() -> float:
    """解析 CCDESK_GATE_DEADLINE；配错（非数字/NaN/inf/非正数）一律回落 7.5。

    必须在导入期绝不抛异常：这条路径上任何 ValueError 都会变成 traceback + 非零退出。
    """
    try:
        value = float(os.environ.get("CCDESK_GATE_DEADLINE", "7.5"))
    except ValueError:
        return 7.5
    if not math.isfinite(value) or value <= 0:
        return 7.5
    return value


DEADLINE_S = _env_deadline()


def emit(decision: str, reason: str) -> None:
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _request(payload: dict) -> tuple[str, str]:
    """单次 HTTP 问询。urlopen 的 timeout 只约束单次 socket 操作，仅作第一道防线。"""
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


def ask_daemon(payload: dict) -> tuple[str, str]:
    """墙钟总期限版问询：POST + 解析整体放进 daemon 线程，主线程 join(DEADLINE_S)。

    防「连接活着、每 <DEADLINE_S 滴一个字节、响应永不完成」的 daemon——那种情况
    单次 socket 超时永不触发，必须由主线程按墙钟到点自降级，绝不交给 CC 外部超时。
    """
    result: dict = {"done": False, "outcome": None}

    def worker() -> None:
        try:
            result["outcome"] = _request(payload)
        except BaseException as exc:                # noqa: BLE001 — 线程内兜住一切
            result["outcome"] = ("ask", f"ccdesk: 降级 ({type(exc).__name__})")
        finally:
            result["done"] = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(DEADLINE_S)
    if result["done"]:
        return result["outcome"]
    # join 超时：结果容器不再读（进程马上退出、daemon 线程随进程被杀，无竞态消费）
    return "ask", f"ccdesk: daemon {DEADLINE_S}s 内未给决定，降级 ask"


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
