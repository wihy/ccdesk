"""授权闭环对账：请求 → 决策 → 结局，三段是否闭合。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ccdesk import config
from ccdesk.sources import Session


@dataclass(frozen=True)
class Anomaly:
    kind: str
    req_id: str
    session_id: str
    detail: str
    age_s: float


def _age(then: object, now: datetime) -> float | None:
    if not isinstance(then, str):
        return None
    try:
        return (now - datetime.fromisoformat(then)).total_seconds()
    except (ValueError, TypeError):
        return None


def _int(val: object) -> int:
    try:
        return int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def reconcile(records: dict[str, dict], sessions: list[Session], now_iso: str) -> list[Anomaly]:
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        return []

    waiting = {s.session_id for s in sessions if s.status == "waiting"}
    out: list[Anomaly] = []

    for req_id, rec in records.items():
        session_id = str(rec.get("session_id", ""))
        digest = str(rec.get("input_digest", ""))[:80].replace("\n", " ")
        decision = rec.get("decision")

        # 有 outcome 的请求按定义已闭合（如用户在 CC 原生弹窗拒绝），不悬空。
        if decision is None and not rec.get("outcome"):
            age = _age(rec.get("ts_request"), now)
            if age is not None and age > config.DANGLING_REQUEST_S:
                out.append(Anomaly("dangling_request", req_id, session_id,
                                   f"{rec.get('tool_name')} «{digest}» 无决策", age))
            continue

        if _int(rec.get("allow_count", 0)) > 1:
            out.append(Anomaly("duplicate_allow", req_id, session_id,
                               f"同一请求被 allow {rec['allow_count']} 次", 0.0))
            continue

        if decision == "allow" and not rec.get("outcome"):
            age = _age(rec.get("ts_decision"), now)
            if age is not None and age > config.EMPTY_ALLOW_S:
                out.append(Anomaly("empty_allow", req_id, session_id,
                                   f"已放行但未见执行：{digest}", age))
            continue

        if decision == "ask" and not rec.get("outcome") and session_id in waiting:
            age = _age(rec.get("ts_decision"), now)
            if age is not None and age > config.SILENT_STALL_S:
                out.append(Anomaly("silent_stall", req_id, session_id,
                                   f"会话仍在等：{digest}", age))

    return out
