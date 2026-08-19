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


def _decide_deterministically(payload: dict) -> "judge.Verdict":
    """只跑确定性的那几层，绝不调 LLM 判官。

    两个理由，第二个才是主要的：
    * 成本：每条记录一次付费 API 请求、串行，CLI 10s 就超时而 daemon 还在烧。
      安全网不该是整个 CLI 里最贵的命令。
    * **可复现性**：LLM 是不确定的，同一条记录两次重放可能给出不同结果——
      那样「规则改动会不会放松决定」这个问题根本没法回答。重放要看的是规则，
      不是模型今天的心情。

    空缓存：重放看的是规则，不是历史缓存，否则每条都会被染成 allow。
    """
    if payload.get("tool_name") != judge.SUPPORTED_TOOL:
        return judge.Verdict("ask", "guardrail:unsupported_tool")
    reason = judge.guardrail_check(payload.get("tool_input"))
    if reason is not None:
        return judge.Verdict("ask", f"guardrail:{reason}")
    # 护栏放行了，但真实链路下一步要问判官——重放不问，如实标注这一点。
    return judge.Verdict("ask", "judge_skipped_in_replay")


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
        # 时间戳解析不了、或两侧时区意识不一致（一个 aware 一个 naive，相减会
        # 抛 TypeError）都保留：宁可多看一条，不可漏看一条会变松的，更不能让
        # 整条 /replay 路由 500。api.py 的 _within_window 早就防了这一手。
        if now is not None and ts is not None:
            try:
                if (now - ts).total_seconds() > since_s:
                    continue
            except TypeError:
                pass
        verdict = _decide_deterministically({
            "session_id": record.get("session_id", ""),
            "prompt_id": record.get("prompt_id", ""),
            "tool_name": record.get("tool_name", ""),
            "tool_input": tool_input,
        })
        was = record.get("decision")
        rows.append({
            "req_id": req_id,
            "tool_name": record.get("tool_name"),
            "was": was,
            "now": verdict.decision,
            "now_by": verdict.decided_by,
            "changed": was is not None and was != verdict.decision,
        })
    return rows
