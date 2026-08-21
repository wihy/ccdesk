#!/usr/bin/env python3
"""PreToolUse 闸门。它只做一件事：问 daemon 要决定，问不到就 ask。

铁律：永不 deny、永不非零退出、永不打印 traceback。

自降级线 25s：daemon 那边判官与人工并行答（判官实测 5.7~9.7s，人看到通知
再点约需 10-15s），25s 能同时容下两者。三层超时错开：
    CC hook timeout 40s > 本文件 25s > daemon 人工窗口 23s
本文件**有意不 import ccdesk.config**（hook 进程要极简、要能独立跑），
所以默认值与 config 的一致性靠 tests/test_gate.py 那条测试盯着。
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

# CC 内部对这些工具走 requiresUserInteraction() 分支：
#   if (!updatedInput && requiresUserInteraction()) return null
# 即**不带 updatedInput 的 allow 会被直接丢弃**，工具就悬在那里不动。
# U5 spike 坐实（2026-08-19, CC 2.1.228）。目前已知只有 AskUserQuestion。
_NEEDS_UPDATED_INPUT = frozenset({"AskUserQuestion"})


def _env_deadline() -> float:
    """解析 CCDESK_GATE_DEADLINE；配错（非数字/NaN/inf/非正数）一律回落 25.0。

    必须在导入期绝不抛异常：这条路径上任何 ValueError 都会变成 traceback + 非零退出。
    """
    try:
        value = float(os.environ.get("CCDESK_GATE_DEADLINE", "25.0"))
    except ValueError:
        return 25.0
    if not math.isfinite(value) or value <= 0:
        return 25.0
    return value


DEADLINE_S = _env_deadline()


def emit(decision: str, reason: str, updated_input: dict | None = None) -> None:
    """输出 hook 决定。

    updatedInput 的位置由沙盒实测坐实（2026-08-19, CC 2.1.228）：放在
    hookSpecificOutput **内**生效 —— 实测会话从未弹窗、直接输出
    `User answered ... → 选项A`，全程无人碰键盘。
    """
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }
    if updated_input is not None:
        output["updatedInput"] = updated_input
    sys.stdout.write(json.dumps({"hookSpecificOutput": output},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def normalize(decision: str, reason: str, updated_input, tool_name: str):
    """把「决定 + 代答」收敛成 CC 真正能消费的组合。

    两条规则：
    * ask 不带 updatedInput —— 带了会让 CC 误以为有代答。
    * 需要用户交互的工具（AskUserQuestion），allow 必须带 updatedInput，
      否则 CC 会丢弃这个 allow、工具悬着不动。与其把决定交给 CC 丢弃，
      不如自己降级成 ask：行为可预期，且仍不违反「永不 deny」。
      普通工具不受此限 —— 它们的 allow 本来就不需要 updatedInput。
    """
    if not isinstance(updated_input, dict) or not updated_input:
        updated_input = None
    if decision != "allow":
        return decision, reason, None
    if tool_name in _NEEDS_UPDATED_INPUT and updated_input is None:
        return "ask", f"ccdesk: allow 缺 updatedInput，降级 ask（原因 {reason}）", None
    return decision, reason, updated_input


def _request(payload: dict) -> tuple[str, str, dict | None]:
    """单次 HTTP 问询。urlopen 的 timeout 只约束单次 socket 操作，仅作第一道防线。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=DEADLINE_S) as response:
        body = json.loads(response.read().decode("utf-8"))
    decision = body.get("permissionDecision")
    reason = str(body.get("reason") or "ccdesk")
    updated_input = body.get("updatedInput")
    if not isinstance(updated_input, dict):
        updated_input = None            # 垃圾就当没给，不许塞给 CC
    if decision in _ALLOWED:
        return decision, reason, updated_input
    return "ask", f"ccdesk: 不接受的决定 {decision!r}，降级 ask", None


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
            result["outcome"] = ("ask", f"ccdesk: 降级 ({type(exc).__name__})", None)
        finally:
            result["done"] = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(DEADLINE_S)
    if result["done"]:
        return result["outcome"]
    # join 超时：结果容器不再读（进程马上退出、daemon 线程随进程被杀，无竞态消费）
    return "ask", f"ccdesk: daemon {DEADLINE_S}s 内未给决定，降级 ask", None


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
        decision, reason, updated_input = ask_daemon(payload)
    except Exception as exc:                       # noqa: BLE001 — 兜住一切
        emit("ask", f"ccdesk: 降级 ({type(exc).__name__})")
        return
    emit(*normalize(decision, reason, updated_input,
                    str(payload.get("tool_name") or "")))


if __name__ == "__main__":
    try:
        main()
    except BaseException:                          # noqa: BLE001 — 连 SystemExit 也兜
        try:
            emit("ask", "ccdesk: 闸门崩溃，降级 ask")
        except Exception:                          # noqa: BLE001
            pass
    sys.exit(0)
