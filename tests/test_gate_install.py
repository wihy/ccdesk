"""闸门安装通道测试。

settings.json 是用户的命根子：装要幂等、要备份、解析不了就拒绝写、
卸载只删自己那条。这些不是锦上添花，是这个模块存在的全部理由。
"""
import json

import pytest

from ccdesk import gate_install


def _write(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_install_into_empty_settings(tmp_path):
    p = _write(tmp_path, {})
    assert gate_install.install(p) == "installed"
    entries = json.loads(p.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == "AskUserQuestion"
    assert entries[0]["hooks"][0]["args"][0].endswith("ccdesk_gate.py")


def test_install_into_missing_file(tmp_path):
    p = tmp_path / "nonexistent.json"
    assert gate_install.install(p) == "installed"
    assert p.exists()


def test_install_is_idempotent(tmp_path):
    p = _write(tmp_path, {})
    gate_install.install(p)
    assert gate_install.install(p) == "already"
    assert len(json.loads(p.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]) == 1


def test_install_preserves_existing_hooks(tmp_path):
    """绝不能覆盖用户已有的 observe.py 等 hook —— 那会把全局可观测性搞挂。"""
    existing = {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [
        {"type": "command", "command": "python3", "args": ["/x/observe.py"]}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "echo"}]}]},
        "permissions": {"defaultMode": "auto"}}
    p = _write(tmp_path, existing)
    gate_install.install(p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data["hooks"]["PreToolUse"]) == 2
    assert data["hooks"]["Stop"] == existing["hooks"]["Stop"]
    assert data["permissions"] == existing["permissions"]
    assert any(e["hooks"][0]["args"] == ["/x/observe.py"] for e in data["hooks"]["PreToolUse"])


def test_install_writes_backup(tmp_path):
    p = _write(tmp_path, {"hooks": {}})
    gate_install.install(p)
    backups = list(tmp_path.glob("settings*.ccdesk-bak.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"hooks": {}}


def test_uninstall_removes_only_ours(tmp_path):
    existing = {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [
        {"type": "command", "command": "python3", "args": ["/x/observe.py"]}]}]}}
    p = _write(tmp_path, existing)
    gate_install.install(p)
    assert gate_install.uninstall(p) == "removed"
    entries = json.loads(p.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["hooks"][0]["args"] == ["/x/observe.py"]


def test_uninstall_when_absent(tmp_path):
    assert gate_install.uninstall(_write(tmp_path, {})) == "absent"
    assert gate_install.uninstall(tmp_path / "nope.json") == "absent"


def test_status_roundtrip(tmp_path):
    p = _write(tmp_path, {})
    assert gate_install.status(p) == {"installed": False, "matcher": None}
    gate_install.install(p)
    assert gate_install.status(p) == {"installed": True, "matcher": "AskUserQuestion"}
    gate_install.uninstall(p)
    assert gate_install.status(p) == {"installed": False, "matcher": None}


def test_malformed_settings_is_never_overwritten(tmp_path):
    """解析不了就拒绝写 —— 宁可装不上，也不能把用户的配置搞没。"""
    p = tmp_path / "settings.json"
    p.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(ValueError):
        gate_install.install(p)
    assert p.read_text(encoding="utf-8") == "{ this is not json"


def test_non_object_settings_is_rejected(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ValueError):
        gate_install.install(p)


def test_status_on_malformed_does_not_raise(tmp_path):
    """status 是只读查询，坏文件也得给个答案而不是抛栈。"""
    p = tmp_path / "settings.json"
    p.write_text("{oops", encoding="utf-8")
    assert gate_install.status(p) == {"installed": False, "matcher": None}


def test_hook_entry_points_at_real_gate_file():
    """装上去的路径必须真实存在，否则每次工具调用都会 hook 失败。"""
    from pathlib import Path
    entry = gate_install.hook_entry()
    assert Path(entry["hooks"][0]["args"][0]).exists()
    assert Path(entry["hooks"][0]["command"]).exists()
