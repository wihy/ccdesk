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

# 对账窗口（秒）：/recon/auth 只看该窗口内的请求。
# 账本 append-only 无人清理，不过滤会让历史悬空请求被每次 reconcile 永久重报。
RECON_WINDOW_S = float(os.environ.get("CCDESK_RECON_WINDOW", "86400"))

# ── P2 判官 ──────────────────────────────────────────────
# 判官自动作答的置信度门。低于它一律 ask，绝不猜。
JUDGE_MIN_CONFIDENCE = float(os.environ.get("CCDESK_JUDGE_MIN_CONF", "0.85"))
# 判官单次调用预算（秒）。超出即视作不可用，降级 ask。
# 注意这是 GATE_DEADLINE_S(7.5) 内的一段，不能贴着它设。
JUDGE_BUDGET_S = float(os.environ.get("CCDESK_JUDGE_BUDGET", "3.0"))
JUDGE_MODEL = os.environ.get("CCDESK_JUDGE_MODEL", "claude-haiku-4-5-20251001")

# 账本超过它，/ledger 路由自动带 since 过滤（当前实测 9.8KB，纯预留）。
LEDGER_FILTER_BYTES = int(os.environ.get("CCDESK_LEDGER_FILTER_BYTES", str(50 * 1024 * 1024)))
