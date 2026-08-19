"""决策写入侧。把 /decide 的结论落成账本行。

P1 全树无人写 decision，导致 `why` 恒输出 None、四类对账里三类恒不触发。
这个模块就是那个缺失的写入方。

口径纪律：req_id 的算法必须与 collector 走 events.py 的那条**完全一致**，
否则闸门写的决策行与采集器写的请求行会挂在两个不同的 req_id 上，账本永远对不上号。
"""
from __future__ import annotations

from datetime import datetime, timezone

from .ledger import Ledger, input_fingerprint, make_req_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_req_id(payload: dict) -> str:
    """从 PreToolUse hook payload 现算 req_id。

    C-3 教训：input_fingerprint 必须**两侧都传同一个 tool_name**，由
    ledger.VOLATILE_INPUT_KEYS 按工具剔除结局侧才出现的键（AskUserQuestion 的
    answers / annotations）。只改一侧等于没改。
    """
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {"_raw": str(tool_input)}
    return make_req_id(
        str(payload.get("session_id") or ""),
        str(payload.get("prompt_id") or ""),
        tool_name,
        input_fingerprint(tool_input, tool_name),
    )


def current_allow_count(merged: dict, req_id: str) -> int:
    record = merged.get(req_id) or {}
    try:
        return int(record.get("allow_count") or 0)
    except (TypeError, ValueError):
        return 0


def record_decision(ledger: Ledger, req_id: str, decision: str, decided_by: str,
                    confidence: float | None, latency_ms: int,
                    allow_count_before: int, payload: dict | None = None) -> dict:
    """append 一条决策行。

    allow_count 只在 allow 时递增 —— duplicate_allow 对账（同一 req_id 出现
    ≥2 次 allow = 幂等键失效）唯一的判据就是它。

    带 payload 时顺手补齐 tool_input 等原始输入：collector 那侧只存
    input_digest 摘要（体积/隐私考虑），replay 拿它重放不了。闸门是同步的、
    比 3s 轮询的 collector 先看到请求，由它补这一份最自然；而且只有真正
    过了闸门的请求才会存，量很小。
    """
    row = {
        "req_id": req_id,
        "decision": decision,
        "decided_by": decided_by,
        "latency_ms": int(latency_ms),
        "ts_decision": _now_iso(),
        "allow_count": allow_count_before + (1 if decision == "allow" else 0),
    }
    if confidence is not None:
        row["confidence"] = float(confidence)
    if isinstance(payload, dict):
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            row["tool_input"] = tool_input
            # 与 collector 同一个摘要口径，否则 trace 的「工具」行会是空的 «»
            from .events import _digest
            row["input_digest"] = _digest(tool_input)
        for key in ("tool_name", "session_id", "prompt_id", "cwd", "permission_mode"):
            value = payload.get(key)
            if value:
                row[key] = value
        # 闸门看到请求的时刻就是请求发生的时刻；collector 后来补的
        # ts_request 会合并到同一 req_id 上，不冲突。
        row.setdefault("ts_request", row["ts_decision"])
    ledger.append(row)
    return row
