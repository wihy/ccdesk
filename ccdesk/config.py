"""全部路径与阈值常量的单一来源。"""
from __future__ import annotations

import math
import os
from pathlib import Path

def _num(name: str, default: float, cast=float, positive: bool = True):
    """读一个数值型环境变量，配错就回落默认值。

    **导入期绝不抛异常**：每个模块都 import 本文件，这里抛一次 ValueError
    就是 daemon 起不来、而且此时 logging 还没配好，daemon.log 里什么都看不到。
    hooks/ccdesk_gate.py 的 _env_deadline 早就是这么写的（注释：「必须在导入期
    绝不抛异常」），这些 knob 不该把那个坑重新挖开。
    """
    raw = os.environ.get(name)
    if raw is None:
        return cast(default)
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return cast(default)
    if isinstance(value, float) and not math.isfinite(value):
        return cast(default)
    if positive and value <= 0:
        return cast(default)
    return value


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
API_PORT = _num("CCDESK_API_PORT", 8787, int)

# 闸门自降级线（秒）。Task 1 的 U2 spike 结论若不同，改这里。
GATE_DEADLINE_S = _num("CCDESK_GATE_DEADLINE", 7.5)

# 对账阈值（秒）
DANGLING_REQUEST_S = 60
EMPTY_ALLOW_S = 600
SILENT_STALL_S = 1800

# 对账窗口（秒）：/recon/auth 只看该窗口内的请求。
# 账本 append-only 无人清理，不过滤会让历史悬空请求被每次 reconcile 永久重报。
RECON_WINDOW_S = _num("CCDESK_RECON_WINDOW", 86400.0)

# ── P2 判官 ──────────────────────────────────────────────
# 判官自动作答的置信度门。低于它一律 ask，绝不猜。
JUDGE_MIN_CONFIDENCE = _num("CCDESK_JUDGE_MIN_CONF", 0.85)
# 判官单次调用预算（秒）。超出即视作不可用，降级 ask。
# 注意这是 GATE_DEADLINE_S(7.5) 内的一段，不能贴着它设。
JUDGE_BUDGET_S = _num("CCDESK_JUDGE_BUDGET", 3.0)
JUDGE_MODEL = os.environ.get("CCDESK_JUDGE_MODEL", "claude-haiku-4-5-20251001")

# 账本超过它，/ledger 路由自动带 since 过滤（当前实测 9.8KB，纯预留）。
LEDGER_FILTER_BYTES = _num("CCDESK_LEDGER_FILTER_BYTES", 50 * 1024 * 1024, int)

# 闸门落账时 tool_input 的体积上限（字符）。超限只留 input_digest 摘要。
# 账本 append-only 无人清理、权限 0644，不能让粘贴进选项里的代码/凭证长期留存。
TOOL_INPUT_MAX_CHARS = _num("CCDESK_TOOL_INPUT_MAX", 8000, int)
