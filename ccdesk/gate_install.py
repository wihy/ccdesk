"""把闸门装进 / 卸出 ~/.claude/settings.json。

纪律（每条都有对应测试）：
* **幂等** —— 装两次不会出现两条。
* **非破坏** —— 先备份；解析不了一律拒绝写。用户的 settings.json 丢不起，
  宁可装不上也不能把它搞没。
* **只删自己** —— 卸载按 args 里的 ccdesk_gate.py 认领，绝不动别人的 hook
  （observe.py 之类挂了会把全局可观测性搞挂）。

**matcher 为什么精确匹配 AskUserQuestion**：本机 defaultMode=auto 且
permissions.allow 含 Bash(*)，实测 events.jsonl 尾部 2000 行 PostToolUse 422 条、
PermissionRequest 0 条 —— 也就是说除 AskUserQuestion 外没有工具会走到权限询问。
挂全量 matcher 只会给每次工具调用白加一次本机 HTTP 往返，收益为零。
哪天收紧了 permissions，再把这里放宽。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

MATCHER = "AskUserQuestion"
GATE_PATH = str(Path(__file__).resolve().parent.parent / "hooks" / "ccdesk_gate.py")
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def hook_entry() -> dict:
    return {
        "matcher": MATCHER,
        "hooks": [{
            "type": "command",
            "command": sys.executable,
            "args": [GATE_PATH],
            # CC 侧 timeout 只是最外层保险，必须大于闸门自己的自降级线（25s），
            # 否则 CC 先超时、闸门的决定还没送出去就作废了。
            # U2 坐实 timeout ≥300s 可设，40 有充足余量。真正的兜底仍是闸门
            # 内部 watchdog —— CC 超时后是 fall through 到默认权限管线、
            # 不保证拒绝，所以不能指望它。
            "timeout": 40,
        }],
    }


def _is_ours(entry: dict) -> bool:
    for hook in entry.get("hooks", []) or []:
        for arg in hook.get("args", []) or []:
            if str(arg).endswith("ccdesk_gate.py"):
                return True
    return False


def _load(settings_path: Path) -> dict:
    try:
        raw = Path(settings_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{settings_path} 不是合法 JSON，拒绝写入：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{settings_path} 顶层不是对象，拒绝写入")
    return data


def _save(settings_path: Path, data: dict) -> None:
    settings_path = Path(settings_path)
    if settings_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(settings_path, settings_path.with_name(
            f"{settings_path.name}.ccdesk-bak.{stamp}"))
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_name(settings_path.name + ".ccdesk-tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(settings_path)          # 原子替换，避免写一半被读到


def install(settings_path: Path = SETTINGS_PATH) -> str:
    data = _load(settings_path)
    hooks = data.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        raise ValueError("hooks.PreToolUse 不是数组，拒绝写入")
    if any(_is_ours(e) for e in entries if isinstance(e, dict)):
        return "already"
    entries.append(hook_entry())
    _save(settings_path, data)
    return "installed"


def uninstall(settings_path: Path = SETTINGS_PATH) -> str:
    try:
        data = _load(settings_path)
    except ValueError:
        return "absent"
    entries = (data.get("hooks") or {}).get("PreToolUse")
    if not isinstance(entries, list):
        return "absent"
    kept = [e for e in entries if not (isinstance(e, dict) and _is_ours(e))]
    if len(kept) == len(entries):
        return "absent"
    data["hooks"]["PreToolUse"] = kept
    _save(settings_path, data)
    return "removed"


def status(settings_path: Path = SETTINGS_PATH) -> dict:
    try:
        data = _load(settings_path)
    except ValueError:
        return {"installed": False, "matcher": None}
    entries = (data.get("hooks") or {}).get("PreToolUse") or []
    if not isinstance(entries, list):
        return {"installed": False, "matcher": None}
    for entry in entries:
        if isinstance(entry, dict) and _is_ours(entry):
            return {"installed": True, "matcher": entry.get("matcher")}
    return {"installed": False, "matcher": None}
