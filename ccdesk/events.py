"""把 events.jsonl 的原始事件翻译成账本记录。"""
from __future__ import annotations

import json

from ccdesk import ledger

# PermissionDenied 只说明「这次调用被拒了」，不说明是**谁**拒的。实测本机全部
# 3 条 PermissionDenied 的 payload.reason 都是 "Classifier unavailable"（系统侧
# 分类器故障），0 条是用户拒绝——写成 user_denied 会把系统故障记成人的决定，
# 审计追责方向反了。故结局名保持中性 denied，把原因原样带上（outcome_reason）。
_OUTCOME_BY_EVENT = {"PostToolUse": "executed", "PermissionDenied": "denied"}


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _digest(tool_input: dict) -> str:
    for key in ("command", "file_path", "pattern", "url"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    return ledger.canonical_input(tool_input)[:200]


def to_request_record(event: dict) -> dict | None:
    summary = event.get("summary") or {}
    payload = event.get("payload") or {}
    if summary.get("event") != "PermissionRequest":
        return None
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {"_raw": str(tool_input)}
    session_id = str(payload.get("session_id") or summary.get("session_id") or "")
    tool_name = str(payload.get("tool_name") or summary.get("tool_name") or "")
    prompt_id = str(payload.get("prompt_id") or "")
    input_fp = ledger.input_fingerprint(tool_input, tool_name)
    return {
        "req_id": ledger.make_req_id(session_id, prompt_id, tool_name, input_fp),
        "ts_request": summary.get("ts"),
        "session_id": session_id,
        "prompt_id": prompt_id,
        "cwd": payload.get("cwd") or summary.get("cwd"),
        "tool_name": tool_name,
        "input_fp": input_fp,
        "input_digest": _digest(tool_input),
        "permission_mode": payload.get("permission_mode"),
        "decision": None,
        "decided_by": None,
        "outcome": None,
    }


def to_outcome_record(event: dict) -> dict | None:
    summary = event.get("summary") or {}
    payload = event.get("payload") or {}
    outcome = _OUTCOME_BY_EVENT.get(summary.get("event"))
    if outcome is None:
        return None
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {"_raw": str(tool_input)}
    session_id = str(payload.get("session_id") or summary.get("session_id") or "")
    tool_name = str(payload.get("tool_name") or summary.get("tool_name") or "")
    prompt_id = str(payload.get("prompt_id") or "")
    # 已核实（2026-08-14）：PermissionRequest / PostToolUse / PermissionDenied 三类
    # payload 顶层都带 prompt_id，所以这里可以直接现算 req_id 与请求侧对齐，
    # 不需要 match_key 这层中间索引。
    #
    # C-3 已坐实并已修：CC 确实会在 PostToolUse 前改写 tool_input（把用户的
    # answers / annotations 并进 AskUserQuestion 的入参），两侧算出的 input_fp
    # 因此对不上，outcome 静默回填不上。修法是把 tool_name 一起传给
    # input_fingerprint，由 ledger.VOLATILE_INPUT_KEYS 按工具剔除这些结局侧才
    # 出现的键。**两侧必须传同一个 tool_name**，只改一侧等于没改。
    # 再出现「某工具的请求永远没有 outcome」时，先比对两侧 tool_input 的键集，
    # 差异键补进 VOLATILE_INPUT_KEYS。
    input_fp = ledger.input_fingerprint(tool_input, tool_name)
    return {
        "req_id": ledger.make_req_id(session_id, prompt_id, tool_name, input_fp),
        "outcome": outcome,
        "ts_outcome": summary.get("ts"),
        # 拒绝原因原样留痕：区分「系统分类器不可用」与真正的人为拒绝。
        "outcome_reason": payload.get("reason"),
    }
