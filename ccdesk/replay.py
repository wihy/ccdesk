"""拿历史请求重放当前规则集，看规则改动会改变哪些决定。

这是改判官/护栏之前的安全网：先看会不会把过去的 ask 变成 allow —— 那个方向
的变化才是危险的（把过去人工把关过的东西自动放掉）。

只读：不写账本、不碰会话、不调真判官以外的任何东西。
"""
from __future__ import annotations

import re
from datetime import datetime

from . import judge

_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str) -> float:
    """'30m' / '24h' / '7d' / 裸秒数 → 秒。"""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(text))
    if not match:
        raise ValueError(f"看不懂的时间窗：{text!r}（用 30m / 24h / 7d 这种写法）")
    return float(match.group(1)) * _SINCE_UNITS.get(match.group(2) or "s", 1)


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def replay(merged: dict, since_s: float, now_iso: str) -> list[dict]:
    now = _parse_ts(now_iso)
    rows: list[dict] = []
    for req_id, record in merged.items():
        tool_input = record.get("tool_input")
        if not isinstance(tool_input, dict):
            # P1 老记录只存了 input_digest 没存 tool_input，重放不了。
            # 跳过而不是编一个 —— 编出来的重放结论比没有更糟。
            continue
        ts = _parse_ts(record.get("ts_request"))
        # 时间戳解析不了就保留：宁可多看一条，不可漏看一条会变松的。
        if now is not None and ts is not None and (now - ts).total_seconds() > since_s:
            continue
        verdict = judge.decide({
            "session_id": record.get("session_id", ""),
            "prompt_id": record.get("prompt_id", ""),
            "tool_name": record.get("tool_name", ""),
            "tool_input": tool_input,
        }, {})                       # 空缓存：重放看的是规则，不是历史缓存
        was = record.get("decision")
        rows.append({
            "req_id": req_id,
            "tool_name": record.get("tool_name"),
            "was": was,
            "now": verdict.decision,
            "changed": was is not None and was != verdict.decision,
        })
    return rows
