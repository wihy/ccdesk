"""尾随 events.jsonl，把授权请求与结局写进账本。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ccdesk import events
from ccdesk.ledger import Ledger


class Collector:
    def __init__(self, events_path: Path, ledger: Ledger, state_path: Path) -> None:
        self.events_path = Path(events_path)
        self.ledger = ledger
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # 已知 req_id 集合。None = 尚未建立，下轮全量重建。
        # 每轮全量 read_merged 在账本上万行后就是每 3s 一次全文件扫描，
        # 所以首轮之后靠本轮新增的 req_id 增量维护。
        self._known: set[str] | None = None

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"offset": 0, "inode": 0}

    def _save_state(self, offset: int, inode: int) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"offset": offset, "inode": inode}), encoding="utf-8")
        tmp.replace(self.state_path)

    def run_once(self) -> dict:
        stats = {"requests": 0, "outcomes": 0, "skipped": 0, "orphans": 0}
        try:
            stat = os.stat(self.events_path)
        except OSError:
            return stats

        state = self._load_state()
        offset = state.get("offset", 0)
        if state.get("inode") != stat.st_ino or offset > stat.st_size:
            offset = 0
            # 轮转/回绕意味着要从头重读，此时 known 也必须重建：
            # 拿着半截集合往下跑会把正常请求误判成孤儿结局。
            self._known = None

        # 已知的 req_id 集合。请求侧与结局侧现在算出的是**同一个 req_id**
        # （events.to_outcome_record 直接返回 req_id），所以不需要 match_key 索引。
        if self._known is None:
            self._known = set(self.ledger.read_merged())
        known = self._known
        refreshed = False        # 本轮是否已因疑似孤儿重建过 known

        # 必须按二进制读：offset 是字节偏移，文本模式下 errors="replace" 会让
        # len(line.encode()) 与实际消耗字节数不等，断点续读会逐渐错位。
        with open(self.events_path, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):        # 半行，留到下轮
                    break
                offset += len(raw)
                event = events.parse_line(raw.decode("utf-8", errors="replace"))
                if event is None:
                    stats["skipped"] += 1
                    continue
                record = events.to_request_record(event)
                if record is not None:
                    self.ledger.append(record)
                    known.add(record["req_id"])
                    stats["requests"] += 1
                    continue
                outcome = events.to_outcome_record(event)
                if outcome is not None:
                    if outcome["req_id"] not in known and not refreshed:
                        # 账本有**两个**写入方：collector 自己，和闸门（decisions）。
                        # 判官放行时 CC 不发 PermissionRequest，所以这个 req_id
                        # collector 从没在事件流里见过，但闸门早写进账本了。
                        # 判孤儿之前先付一次全量重建的代价确认——每轮最多一次，
                        # 只在真出现疑似孤儿时才发生，常态仍是增量。
                        self._known = set(self.ledger.read_merged())
                        known = self._known
                        refreshed = True
                    if outcome["req_id"] in known:
                        self.ledger.append({
                            "req_id": outcome["req_id"],
                            "outcome": outcome["outcome"],
                            "ts_outcome": outcome["ts_outcome"],
                            # None 会被 read_merged 忽略，PostToolUse 不会写空原因。
                            "outcome_reason": outcome.get("outcome_reason"),
                        })
                        stats["outcomes"] += 1
                    else:
                        # 孤儿结局：req_id 确实不在账本里（请求落在轮转备份里、
                        # 或 hook 改写 input_fp 导致两侧对不上）。不归属就
                        # 不瞎归属——不写账本，但必须数出来，不静默丢弃
                        # 「工具确实执行过」的证据。
                        stats["orphans"] += 1

        self._save_state(offset, stat.st_ino)
        return stats
