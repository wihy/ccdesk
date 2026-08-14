"""append-only 授权决策账本。只有 daemon 写它。"""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path


def canonical_input(tool_input: dict) -> str:
    return json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_fingerprint(tool_input: dict) -> str:
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

    def read_merged(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        self.bad_line_count = 0
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return merged
        bad_lines: list[str] = []
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
            slot = merged.setdefault(rid, {})
            for key, value in rec.items():
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
