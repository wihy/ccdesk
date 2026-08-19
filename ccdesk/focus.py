"""把 ccdesk 的会话映射回 cmux 的 workspace，并切过去。

**映射链**（全程实测坐实，2026-08-19 本机 9 个会话 9/9 命中）：

    session.pid → `ps -p <pid> -o tty=` → ttysNNN
                → `cmux tree --all --json` 里匹配 surface.tty
                → workspace_ref → `cmux select-workspace --workspace <ref>`

几条实测结论，改这个模块前先看：

* **只切到 workspace 就够，不需要 surface 级操作。** 实测 9 个会话里，claude 所在的
  surface 全都是所属 workspace 的选中项，`select-workspace` 会自动带上它。
* **surface 级精确聚焦这条路走不通**：`cmux rpc canvas.select_tab` 只在 canvas layout
  模式下可用，普通 workspace 会报 `invalid_state: Workspace is not in canvas layout`。
* **`select-workspace` 不会把 cmux 窗口带到前台**（实测：执行前后前台都还是原来那个
  应用）。所以调用方还得自己 activate 一下 cmux，这一步留在 GUI 层做。
* `tab-action --action focus|select|activate` 全都报 `Unknown tab action`，别再试了。

纪律：这条路径上的任何失败都只意味着「没法跳转，回退到打开 cwd」，
绝不能抛异常——它挂在面板的点击事件上。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# cmux CLI。环境变量优先（cmux 自己给会话注入的就是这个），否则用 app bundle 里的实体。
CMUX_BIN = os.environ.get(
    "CCDESK_CMUX_BIN",
    os.environ.get("CMUX_CLAUDE_HOOK_CMUX_BIN",
                   "/Applications/cmux.app/Contents/Resources/bin/cmux"))
CMUX_TIMEOUT_S = float(os.environ.get("CCDESK_CMUX_TIMEOUT", "10"))


def _run(*args: str, timeout: float = CMUX_TIMEOUT_S) -> tuple[int, str]:
    """跑一个子进程，返回 (rc, stdout)。任何异常都折成 rc=1，不外抛。"""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


def _run_cmux(*args: str) -> tuple[int, str]:
    if not Path(CMUX_BIN).exists():
        return 1, "cmux 不存在"
    # CMUX_QUIET 压掉「legacy 别名」之类的提示行，免得混进 JSON。
    env = dict(os.environ, CMUX_QUIET="1")
    try:
        proc = subprocess.run([CMUX_BIN, *args], capture_output=True, text=True,
                              timeout=CMUX_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def tty_of_pid(pid: int) -> str | None:
    """进程的 tty 短名（ttys013）。拿不到就 None。

    不在终端里跑的进程、以及已经退出的 pid，ps 会给 `??` 或空。
    """
    rc, out = _run("ps", "-p", str(pid), "-o", "tty=")
    if rc != 0:
        return None
    tty = out.strip()
    if not tty or tty == "??":
        return None
    return tty


def _cmux_tree() -> dict | None:
    rc, out = _run_cmux("tree", "--all", "--json")
    if rc != 0 or not out:
        return None
    try:
        tree = json.loads(out)
    except ValueError:
        return None
    return tree if isinstance(tree, dict) else None


def index_by_tty(tree) -> dict[str, dict]:
    """把 cmux 的 window→workspace→pane→surface 树压成 {tty: 定位信息}。

    结构变了就返回空表 —— 跳转失效只是回退到开文件夹，不该把面板打挂。
    """
    index: dict[str, dict] = {}
    if not isinstance(tree, dict):
        return index
    for window in tree.get("windows") or []:
        if not isinstance(window, dict):
            continue
        for workspace in window.get("workspaces") or []:
            if not isinstance(workspace, dict):
                continue
            for pane in workspace.get("panes") or []:
                if not isinstance(pane, dict):
                    continue
                for surface in pane.get("surfaces") or []:
                    if not isinstance(surface, dict):
                        continue
                    tty = surface.get("tty")
                    if not tty:
                        continue
                    index[str(tty)] = {
                        "tty": str(tty),
                        "window_ref": window.get("ref"),
                        "workspace_ref": workspace.get("ref"),
                        "workspace_title": workspace.get("title"),
                        "surface_ref": surface.get("ref"),
                        "surface_selected": bool(surface.get("selected")),
                    }
    return index


def resolve(pid: int) -> dict | None:
    """pid → cmux 定位信息。映射不上就 None（调用方据此回退到打开 cwd）。"""
    tty = tty_of_pid(pid)
    if tty is None:
        return None
    tree = _cmux_tree()
    if tree is None:
        return None
    return index_by_tty(tree).get(tty)


def focus_session(pid: int) -> tuple[bool, dict]:
    """把 cmux 切到该会话所在的 workspace。

    返回 (是否成功, 明细)。失败一律返回 False + 原因，不抛异常。
    **不负责把 cmux 窗口带到前台** —— 实测 select-workspace 不会 activate，
    那一步由 GUI 层（CCDesk.app）做。
    """
    located = resolve(pid)
    if located is None:
        return False, {"reason": "not_in_cmux",
                       "detail": "该会话不在 cmux 里（或 cmux 没跑），回退到打开目录"}
    rc, out = _run_cmux("select-workspace", "--workspace", located["workspace_ref"])
    if rc != 0:
        return False, {"reason": "cmux_failed", "error": out or "cmux 无输出",
                       **located}
    return True, dict(located)
