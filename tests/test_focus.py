"""把 ccdesk 的会话映射回 cmux 的 workspace 并切过去。

映射链全程实测坐实（2026-08-19，9/9 命中）：
  session.pid → `ps -o tty=` → ttysNNN → `cmux tree --all --json` 里
  匹配 surface.tty → workspace_ref → `cmux select-workspace`
"""
import json

import pytest

from ccdesk import focus

TREE = {
    "windows": [{
        "ref": "window:1",
        "workspaces": [
            {"ref": "workspace:8", "title": "总管监控", "panes": [
                {"ref": "pane:8", "surfaces": [
                    {"ref": "surface:11", "tty": "ttys013", "selected": True},
                    {"ref": "surface:21", "tty": "ttys010", "selected": False},
                ]},
            ]},
            {"ref": "workspace:7", "title": "潮音平台能力", "panes": [
                {"ref": "pane:7", "surfaces": [
                    {"ref": "surface:10", "tty": "ttys014", "selected": True},
                ]},
            ]},
        ],
    }],
}


def test_index_by_tty_flattens_the_tree():
    idx = focus.index_by_tty(TREE)
    assert idx["ttys013"]["workspace_ref"] == "workspace:8"
    assert idx["ttys013"]["surface_ref"] == "surface:11"
    assert idx["ttys013"]["workspace_title"] == "总管监控"
    assert idx["ttys010"]["workspace_ref"] == "workspace:8"      # 同 pane 的第二个
    assert idx["ttys014"]["workspace_ref"] == "workspace:7"


def test_index_tolerates_missing_pieces():
    """cmux 输出结构变了也不能抛——这条路径挂了只是没法跳转，不该让面板出错。"""
    assert focus.index_by_tty({}) == {}
    assert focus.index_by_tty({"windows": None}) == {}
    assert focus.index_by_tty({"windows": [{"workspaces": [{"panes": [{"surfaces": [{}]}]}]}]}) == {}
    assert focus.index_by_tty("not-a-dict") == {}


def test_resolve_returns_workspace_for_known_pid(monkeypatch):
    monkeypatch.setattr(focus, "tty_of_pid", lambda pid: "ttys013")
    monkeypatch.setattr(focus, "_cmux_tree", lambda: TREE)
    got = focus.resolve(12345)
    assert got["workspace_ref"] == "workspace:8"
    assert got["tty"] == "ttys013"


def test_resolve_returns_none_when_pid_has_no_tty(monkeypatch):
    """不在终端里跑的会话（或进程已退出）—— 调用方据此回退到打开 cwd。"""
    monkeypatch.setattr(focus, "tty_of_pid", lambda pid: None)
    monkeypatch.setattr(focus, "_cmux_tree", lambda: TREE)
    assert focus.resolve(12345) is None


def test_resolve_returns_none_when_tty_not_in_cmux(monkeypatch):
    """会话在别的终端（Terminal.app / iTerm）里跑，不归 cmux 管。"""
    monkeypatch.setattr(focus, "tty_of_pid", lambda pid: "ttys099")
    monkeypatch.setattr(focus, "_cmux_tree", lambda: TREE)
    assert focus.resolve(12345) is None


def test_resolve_returns_none_when_cmux_unavailable(monkeypatch):
    """cmux 没装/没跑 —— 同样只是回退，不是错误。"""
    monkeypatch.setattr(focus, "tty_of_pid", lambda pid: "ttys013")
    monkeypatch.setattr(focus, "_cmux_tree", lambda: None)
    assert focus.resolve(12345) is None


def test_focus_session_invokes_select_workspace(monkeypatch):
    calls = []
    monkeypatch.setattr(focus, "resolve",
                        lambda pid: {"workspace_ref": "workspace:8", "surface_ref": "surface:11",
                                     "workspace_title": "总管监控", "tty": "ttys013"})
    monkeypatch.setattr(focus, "_run_cmux", lambda *a: calls.append(a) or (0, "OK workspace:8"))
    ok, detail = focus.focus_session(12345)
    assert ok is True
    assert calls == [("select-workspace", "--workspace", "workspace:8")]
    assert detail["workspace_title"] == "总管监控"


def test_focus_session_reports_failure_without_raising(monkeypatch):
    """cmux 命令失败要如实返回 False，让调用方回退——不能抛异常打穿面板。"""
    monkeypatch.setattr(focus, "resolve",
                        lambda pid: {"workspace_ref": "workspace:8", "surface_ref": "s",
                                     "workspace_title": "t", "tty": "ttys013"})
    monkeypatch.setattr(focus, "_run_cmux", lambda *a: (1, "Error: not_found"))
    ok, detail = focus.focus_session(12345)
    assert ok is False
    assert "not_found" in detail["error"]


def test_focus_session_unmapped_returns_false(monkeypatch):
    monkeypatch.setattr(focus, "resolve", lambda pid: None)
    ok, detail = focus.focus_session(12345)
    assert ok is False
    assert detail["reason"] == "not_in_cmux"


def test_tty_of_pid_normalizes_ps_output(monkeypatch):
    """ps 输出可能带前后空格；不存在的 pid 给 '??' 或空。"""
    monkeypatch.setattr(focus, "_run", lambda *a: (0, "  ttys013  \n"))
    assert focus.tty_of_pid(1) == "ttys013"
    monkeypatch.setattr(focus, "_run", lambda *a: (0, "??\n"))
    assert focus.tty_of_pid(1) is None
    monkeypatch.setattr(focus, "_run", lambda *a: (1, ""))
    assert focus.tty_of_pid(1) is None
