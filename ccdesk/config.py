"""全部路径与阈值常量的单一来源。"""
from __future__ import annotations

import os
from pathlib import Path

CCDESK_HOME = Path(os.environ.get("CCDESK_HOME", Path.home() / ".ccdesk"))
LEDGER_PATH = CCDESK_HOME / "ledger.jsonl"
LEDGER_BAD_PATH = CCDESK_HOME / "ledger.bad.jsonl"
LOG_PATH = CCDESK_HOME / "logs" / "daemon.log"

EVENTS_PATH = Path(
    os.environ.get(
        "CCDESK_EVENTS_PATH",
        Path.home() / ".adw" / "observability" / "claude" / "events.jsonl",
    )
)
CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
CLAUDE_BIN = os.environ.get("CCDESK_CLAUDE_BIN", "claude")

API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("CCDESK_API_PORT", "8787"))

# 闸门自降级线（秒）。Task 1 的 U2 spike 结论若不同，改这里。
GATE_DEADLINE_S = float(os.environ.get("CCDESK_GATE_DEADLINE", "7.5"))

# 对账阈值（秒）
DANGLING_REQUEST_S = 60
EMPTY_ALLOW_S = 600
SILENT_STALL_S = 1800
