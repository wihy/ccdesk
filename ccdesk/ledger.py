"""append-only 授权决策账本。只有 daemon 写它。"""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path


# 「结局侧才会长出来」的 tool_input 键，按工具名剔除。
#
# 实测坐实（2026-08-17，2020 对 PreToolUse↔PostToolUse 受控对照）：Claude Code 自己
# 在 PostToolUse 之前把用户的 answers / annotations 并进了 tool_input——请求侧键集是
# ('questions',)，结局侧变成 ('annotations','answers','questions')，同一次交互两侧
# 指纹不同，outcome 永远回填不上（线上 2024 条 outcome 命中 0 条）。
# 剩下 2017 对（Bash/Read/Edit/Write…）input_fp 完全一致，所以指纹作 join key 的
# 设计本身没问题，只需按工具精确剔除这几个键——不做无差别丢键，否则会错配。
VOLATILE_INPUT_KEYS = {"AskUserQuestion": frozenset({"answers", "annotations"})}


def canonical_input(tool_input: dict) -> str:
    return json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_fingerprint(tool_input: dict, tool_name: str = "") -> str:
    volatile = VOLATILE_INPUT_KEYS.get(tool_name)
    if volatile:
        tool_input = {k: v for k, v in tool_input.items() if k not in volatile}
    raw = canonical_input(tool_input).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def make_req_id(session_id: str, prompt_id: str, tool_name: str, input_fp: str) -> str:
    raw = "|".join([session_id or "", prompt_id or "", tool_name or "", input_fp or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Ledger:
    def __init__(self, path: Path, bad_path: Path) -> None:
        self.path = Path(path)
        self.bad_path = Path(bad_path)
        self.bad_line_count = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Seed the dedup set from bad lines already quarantined by a prior
        # process lifetime, so read_merged() never re-appends the same bad
        # line after a restart. Missing/unreadable bad_path just means an
        # empty seed — never fatal.
        self._quarantined_hashes: set[str] = set()
        try:
            existing = self.bad_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        for line in existing.splitlines():
            if line.strip():
                self._quarantined_hashes.add(self._line_hash(line))

    @staticmethod
    def _line_hash(line: str) -> str:
        return hashlib.sha256(line.encode("utf-8")).hexdigest()

    def append(self, record: dict) -> None:
        if not record.get("req_id"):
            raise ValueError("ledger record 必须带 req_id")
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def read_merged(self, since_ts: str | None = None) -> dict[str, dict]:
        """合并读账本。

        since_ts 给的是**大账本时的减负开关**，缺省 None 时行为与不带它时
        逐字节一致 —— 当前账本才 9.8KB，这条路径平时根本不会走。坏行统计
        不受过滤影响，否则 recon 报的「坏行 N」会失真。
        """
        merged: dict[str, dict] = {}
        self.bad_line_count = 0
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return merged
        bad_lines: list[str] = []
        dropped: set[str] = set()   # 请求行已出窗的 req_id
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad_lines.append(line)
                continue
            rid = rec.get("req_id")
            if not rid:
                bad_lines.append(line)
                continue
            if since_ts is not None:
                row_ts = rec.get("ts_request")
                if isinstance(row_ts, str) and row_ts < since_ts:
                    # 请求行出窗：连同它此前已并入的内容一起丢弃。
                    dropped.add(rid)
                    merged.pop(rid, None)
                    continue
                # 决策行/结局行没有 ts_request，本身判不了窗口——跟着它们的
                # 请求行走。请求行被丢了还留着它们，/ledger 会吐出一堆没有
                # tool_name/session_id 的幽灵记录，面板和 trace 渲染成空行。
                if rid in dropped:
                    continue
            slot = merged.setdefault(rid, {})
            # allow_count 由「账本里有几行 allow」聚合得出，不接受行内绝对值。
            # 存绝对值就得 read-modify-write，而 daemon 是多线程的：两个并发
            # /decide 会双双读到 0、双双写 1，last-wins 之后 duplicate_allow
            # （recon 判「幂等键失效」的唯一依据）就被静默架空了。
            # append-only 本身是原子的，没有中间状态可竞争。
            if rec.get("decision") == "allow":
                slot["allow_count"] = slot.get("allow_count", 0) + 1
            for key, value in rec.items():
                if key == "allow_count":
                    continue
                if value is not None:
                    slot[key] = value
        # bad_line_count is scan-count semantics: how many bad lines this
        # scan encountered, regardless of whether they were already
        # quarantined by an earlier call. Do not conflate with write-count.
        self.bad_line_count = len(bad_lines)
        new_bad = [
            line for line in bad_lines
            if self._line_hash(line) not in self._quarantined_hashes
        ]
        if new_bad:
            self.bad_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.bad_path, "a", encoding="utf-8") as fh:
                for line in new_bad:
                    fh.write(line + "\n")
                    self._quarantined_hashes.add(self._line_hash(line))
        return merged
