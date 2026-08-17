import json
import subprocess

import pytest

from ccdesk import sources

CLI_SAMPLE = json.dumps([
    {"pid": 52194, "cwd": "/w/a", "kind": "interactive", "startedAt": 1786672906998,
     "sessionId": "89dc43cf", "name": "story-7e", "status": "waiting",
     "waitingFor": "dialog open"},
    {"pid": 45070, "cwd": "/w/b", "kind": "interactive", "startedAt": 1786677948648,
     "sessionId": "d01dee5c", "name": "latte-5d"},
])


def test_parses_cli_output_with_waiting_reason(monkeypatch):
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: CLI_SAMPLE)
    got = sources.list_sessions()
    assert [s.pid for s in got] == [52194, 45070]
    assert got[0].status == "waiting"
    assert got[0].waiting_for == "dialog open"
    assert got[0].source == "cli"


def test_missing_status_key_becomes_unknown_not_crash(monkeypatch):
    """真实输出里存在没有 status 字段的会话，绝不能抛 KeyError。"""
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: CLI_SAMPLE)
    got = sources.list_sessions()
    assert got[1].status == "unknown"
    assert got[1].waiting_for is None


def test_falls_back_to_files_when_cli_fails(monkeypatch, tmp_path):
    def boom(timeout):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)

    monkeypatch.setattr(sources, "_run_cli", boom)
    (tmp_path / "999.json").write_text(json.dumps(
        {"pid": 999, "sessionId": "s1", "cwd": "/w/c", "name": "n", "kind": "interactive",
         "status": "idle", "startedAt": 1}))
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: True)
    got = sources.list_sessions()
    assert len(got) == 1
    assert got[0].source == "file"


def test_file_fallback_skips_dead_pids(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: "")
    (tmp_path / "999.json").write_text(json.dumps({"pid": 999, "sessionId": "s1", "cwd": "/w"}))
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: False)
    assert sources.list_sessions() == []


def test_corrupt_file_is_skipped_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: "")
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: True)
    assert sources.list_sessions() == []


# ---------------------------------------------------------------------------
# B-1: _alive() 本身的三分支异常语义，直接测试（此前全被 monkeypatch 整体替换掉了）
# ---------------------------------------------------------------------------

def test_alive_process_lookup_error_means_dead(monkeypatch):
    def fake_kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(sources.os, "kill", fake_kill)
    assert sources._alive(123) is False


def test_alive_permission_error_means_alive(monkeypatch):
    def fake_kill(pid, sig):
        raise PermissionError()

    monkeypatch.setattr(sources.os, "kill", fake_kill)
    assert sources._alive(123) is True


def test_alive_other_oserror_means_dead(monkeypatch):
    def fake_kill(pid, sig):
        raise OSError("some other os error")

    monkeypatch.setattr(sources.os, "kill", fake_kill)
    assert sources._alive(123) is False


def test_alive_non_int_pid_means_dead():
    assert sources._alive(None) is False
    assert sources._alive("123") is False


# ---------------------------------------------------------------------------
# B-2: _from_cli_dict() 对脏 pid 的类型防护——单条脏记录必须被跳过，而不是整体崩溃/降级
# ---------------------------------------------------------------------------

def _good_record(pid, session_id):
    return {"pid": pid, "cwd": "/w", "name": "n", "kind": "interactive",
            "status": "idle", "startedAt": 1, "sessionId": session_id}


def test_cli_null_pid_record_is_skipped_good_records_survive(monkeypatch):
    sample = json.dumps([
        {"pid": None, "cwd": "/w", "name": "bad", "kind": "interactive",
         "status": "idle", "startedAt": 1, "sessionId": "bad1"},
        _good_record(111, "good1"),
        _good_record(222, "good2"),
    ])
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: sample)
    got = sources.list_sessions()
    assert [s.pid for s in got] == [111, 222]


def test_cli_non_numeric_pid_record_is_skipped_good_records_survive(monkeypatch):
    sample = json.dumps([
        {"pid": "abc", "cwd": "/w", "name": "bad", "kind": "interactive",
         "status": "idle", "startedAt": 1, "sessionId": "bad1"},
        _good_record(111, "good1"),
        _good_record(222, "good2"),
    ])
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: sample)
    got = sources.list_sessions()
    assert [s.pid for s in got] == [111, 222]


def test_cli_non_dict_element_is_skipped_good_records_survive(monkeypatch):
    sample = json.dumps([
        "unexpected-scalar-element",
        _good_record(111, "good1"),
        _good_record(222, "good2"),
    ])
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: sample)
    got = sources.list_sessions()
    assert [s.pid for s in got] == [111, 222]


# ---------------------------------------------------------------------------
# B-3: 补齐六种 CLI 降级触发条件里此前无自动化单测的 4 种
# ---------------------------------------------------------------------------

def _write_one_file_session(tmp_path):
    (tmp_path / "1.json").write_text(json.dumps(
        {"pid": 1, "sessionId": "file-s1", "cwd": "/w", "name": "n", "kind": "interactive",
         "status": "idle", "startedAt": 1}))


def test_cli_binary_missing_falls_back_to_files(monkeypatch, tmp_path):
    """CLI 可执行文件不存在：_run_cli 内部真实的 subprocess.run 抛 FileNotFoundError（OSError 子类）。"""
    def raise_not_found(*args, **kwargs):
        raise FileNotFoundError("claude: command not found")

    monkeypatch.setattr(sources.subprocess, "run", raise_not_found)
    _write_one_file_session(tmp_path)
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: True)
    got = sources.list_sessions()
    assert len(got) == 1
    assert got[0].source == "file"


def test_cli_nonzero_returncode_falls_back_to_files(monkeypatch, tmp_path):
    """CLI 返回非零退出码：mock subprocess.run（而不是 _run_cli），真正覆盖 returncode!=0 分支。"""
    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(sources.subprocess, "run", lambda *a, **kw: FakeCompletedProcess())
    _write_one_file_session(tmp_path)
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: True)
    got = sources.list_sessions()
    assert len(got) == 1
    assert got[0].source == "file"


def test_cli_malformed_json_falls_back_to_files(monkeypatch, tmp_path):
    """CLI 返回非空但非法 JSON。"""
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: "{truncated")
    _write_one_file_session(tmp_path)
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: True)
    got = sources.list_sessions()
    assert len(got) == 1
    assert got[0].source == "file"


def test_cli_json_not_a_list_falls_back_to_files(monkeypatch, tmp_path):
    """CLI 返回合法 JSON 但不是 list。"""
    monkeypatch.setattr(sources, "_run_cli", lambda timeout: '{"a": 1}')
    _write_one_file_session(tmp_path)
    monkeypatch.setattr(sources.config, "CLAUDE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sources, "_alive", lambda pid: True)
    got = sources.list_sessions()
    assert len(got) == 1
    assert got[0].source == "file"
