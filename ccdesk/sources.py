"""会话真源：优先 `claude agents --json`，失败降级读 ~/.claude/sessions/*.json。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from ccdesk import config

# 上一次 list_sessions() 实际用的真源："cli"（主源）/ "file"（降级）。
# 首次调用前为 "unknown" —— 不假装已经跑过。/health 透出它，让降级态可被外部观测。
LAST_SOURCE = "unknown"


@dataclass(frozen=True)
class Session:
    pid: int
    session_id: str
    cwd: str
    name: str
    kind: str
    status: str
    waiting_for: str | None
    started_at: int
    source: str


def _run_cli(timeout: float) -> str:
    proc = subprocess.run(
        [config.CLAUDE_BIN, "agents", "--json"],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _from_cli_dict(d: dict) -> "Session | None":
    raw_pid = d.get("pid")
    try:
        pid = int(raw_pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return Session(
        pid=pid,
        session_id=str(d.get("sessionId", "")),
        cwd=str(d.get("cwd", "")),
        name=str(d.get("name", "")),
        kind=str(d.get("kind", "unknown")),
        status=str(d.get("status", "unknown")),
        waiting_for=d.get("waitingFor"),
        started_at=int(d.get("startedAt", 0)),
        source="cli",
    )


def _read_files() -> list[Session]:
    out: list[Session] = []
    try:
        paths = sorted(config.CLAUDE_SESSIONS_DIR.glob("*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _alive(d.get("pid")):
            continue
        out.append(Session(
            pid=int(d["pid"]),
            session_id=str(d.get("sessionId", "")),
            cwd=str(d.get("cwd", "")),
            name=str(d.get("name", "")),
            kind=str(d.get("kind", "unknown")),
            status=str(d.get("status", "unknown")),
            waiting_for=d.get("waitingFor"),
            started_at=int(d.get("startedAt", 0)),
            source="file",
        ))
    return out


def _fallback_to_files(reason: str) -> list[Session]:
    """降级读文件真源，并把「降级了」这件事记下来。

    只在状态**变成** file 时记一条 warning：菜单栏 App 每 3s 打一次 /sessions，
    每次都记会把无轮转的 daemon.log 刷爆。持续态由 /health 的 session_source 透出。
    """
    global LAST_SOURCE
    if LAST_SOURCE != "file":
        logging.warning("会话主源 %s 不可用，降级到文件真源：%s", config.CLAUDE_BIN, reason)
    LAST_SOURCE = "file"
    return _read_files()


def list_sessions(timeout: float = 10.0) -> list[Session]:
    global LAST_SOURCE
    try:
        raw = _run_cli(timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _fallback_to_files(f"{type(exc).__name__}: {exc}")
    if not raw.strip():
        return _fallback_to_files("CLI 无输出（退出码非 0 或空 stdout）")
    try:
        data = json.loads(raw)
    except ValueError:
        return _fallback_to_files("CLI 输出不是合法 JSON")
    if not isinstance(data, list):
        return _fallback_to_files("CLI 输出的 JSON 不是数组")
    sessions = [_from_cli_dict(d) for d in data if isinstance(d, dict)]
    LAST_SOURCE = "cli"
    return [s for s in sessions if s is not None]
