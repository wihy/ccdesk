"""把 events.jsonl 的原始事件翻译成账本记录。"""
from __future__ import annotations

import json

from ccdesk import ledger

_OUTCOME_BY_EVENT = {"PostToolUse": "executed", "PermissionDenied": "user_denied"}


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
    input_fp = ledger.input_fingerprint(tool_input)
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
    # 已知脆弱点（C-3，本轮不改）：本函数与 to_request_record 各自独立对当前
    # payload 的 tool_input 重新计算 input_fingerprint。如果 CC 在执行前改写过
    # tool_input（hook 的 updatedInput 机制），PermissionRequest 阶段和
    # PostToolUse/PermissionDenied 阶段算出的 input_fp 会不同，导致两侧 req_id
    # 对不上——outcome 会静默匹配不上对应的请求记录（而不是错配到别的请求），
    # 请求会一直挂在"未知结局"状态。排查"为什么这条请求永远没有 outcome"时
    # 优先怀疑这里。
    input_fp = ledger.input_fingerprint(tool_input)
    return {
        "req_id": ledger.make_req_id(session_id, prompt_id, tool_name, input_fp),
        "outcome": outcome,
        "ts_outcome": summary.get("ts"),
    }
