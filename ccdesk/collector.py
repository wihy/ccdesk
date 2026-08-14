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

        # 已知的 req_id 集合。请求侧与结局侧现在算出的是**同一个 req_id**
        # （events.to_outcome_record 直接返回 req_id），所以不需要 match_key 索引。
        known: set[str] = set(self.ledger.read_merged())

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
                    if outcome["req_id"] in known:
                        self.ledger.append({
                            "req_id": outcome["req_id"],
                            "outcome": outcome["outcome"],
                            "ts_outcome": outcome["ts_outcome"],
                        })
                        stats["outcomes"] += 1
                    else:
                        # 孤儿结局：req_id 不在已知集合（请求落在轮转备份里、
                        # 或 hook 改写 input_fp 导致两侧对不上）。不归属就
                        # 不瞎归属——不写账本，但必须数出来，不静默丢弃
                        # 「工具确实执行过」的证据。
                        stats["orphans"] += 1

        self._save_state(offset, stat.st_ino)
        return stats
