"""人工窗口：把一次决策挂起，让判官和人**并行**去答，谁先给答案用谁。

为什么必须并行（实测数据）：判官常驻进程后单次 5.7~9.7s，人看到通知再点约需
10-15s。串行等两者会超出任何合理的窗口，并行则总时长是 max 而不是 sum ——
判官快时你根本不会被打扰，判官不确定时你还有十几秒可点。

三层超时必须错开，否则互相踩：
    CC hook timeout   40s   最外层（U2 坐实 ≥300s 可设）
    闸门自降级线      25s   闸门自己 watchdog
    本模块的窗口      23s   比闸门短，留出 HTTP 往返

铁律：注入的答案必须是 options 里真实存在的 label。人工通道也不开这个口子 ——
「替用户做一个他没做过的决定」不因为决定来自另一个人就变得可接受。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Item:
    req_id: str
    question: str
    options: list[dict]
    context: dict
    created_at: float
    deadline_at: float
    event: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None
    answered_by: str | None = None

    def labels(self) -> list[str]:
        return [o.get("label") for o in self.options
                if isinstance(o, dict) and o.get("label")]


class Board:
    """挂起中的决策。daemon 单例持有，多线程共享。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Item] = {}

    def open(self, req_id: str, tool_input: dict, context: dict,
             window_s: float) -> Item:
        question_block = (tool_input.get("questions") or [{}])[0]
        now = time.time()
        item = Item(
            req_id=req_id,
            question=str(question_block.get("question") or ""),
            options=[o for o in (question_block.get("options") or [])
                     if isinstance(o, dict)],
            context=dict(context or {}),
            created_at=now,
            deadline_at=now + window_s,
        )
        with self._lock:
            self._items[req_id] = item
        return item

    def list_open(self) -> list[dict]:
        """给面板看的待决项。已过期的不列——用户点一个已经回落终端的题毫无意义。"""
        now = time.time()
        with self._lock:
            rows = []
            for item in self._items.values():
                if item.answer is not None or item.deadline_at <= now:
                    continue
                rows.append({
                    "req_id": item.req_id,
                    "question": item.question,
                    "header": item.context.get("header", ""),
                    "options": [{"label": o.get("label"),
                                 "description": o.get("description", "")}
                                for o in item.options if o.get("label")],
                    "session_id": item.context.get("session_id", ""),
                    "session_name": item.context.get("session_name", ""),
                    "cwd": item.context.get("cwd", ""),
                    "remaining_s": round(item.deadline_at - now, 1),
                })
            return rows

    def resolve(self, req_id: str, answer, by: str) -> bool:
        """给出答案。先到先得，已定的不得覆盖。

        返回是否被采纳——调用方据此判断「我这一票有没有算数」。
        """
        with self._lock:
            item = self._items.get(req_id)
            if item is None or item.answer is not None:
                return False
            if not isinstance(answer, str) or answer not in item.labels():
                # options 之外的字符串一律拒绝，人工通道也不例外
                return False
            item.answer = answer
            item.answered_by = by
        item.event.set()
        return True

    def wait(self, item: Item) -> tuple[str | None, str | None]:
        """阻塞到有人给答案或窗口到点。超时返回 (None, None)。"""
        remaining = item.deadline_at - time.time()
        if remaining > 0:
            item.event.wait(remaining)
        return item.answer, item.answered_by

    def close(self, item: Item) -> None:
        with self._lock:
            self._items.pop(item.req_id, None)
