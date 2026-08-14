"""会话真源：优先 `claude agents --json`，失败降级读 ~/.claude/sessions/*.json。"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from ccdesk import config


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


def list_sessions(timeout: float = 10.0) -> list[Session]:
    try:
        raw = _run_cli(timeout)
    except (subprocess.TimeoutExpired, OSError):
        raw = ""
    if raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, list):
            sessions = [_from_cli_dict(d) for d in data if isinstance(d, dict)]
            return [s for s in sessions if s is not None]
    return _read_files()
