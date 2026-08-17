import json
import re
import subprocess
import sys
import time
from pathlib import Path

GATE = str(Path(__file__).resolve().parents[1] / "hooks" / "ccdesk_gate.py")
HOOK_INPUT = json.dumps({
    "session_id": "s1", "prompt_id": "p1", "tool_name": "Bash",
    "tool_input": {"command": "git status"}, "hook_event_name": "PreToolUse",
})


def run_gate(stdin_text, endpoint, deadline="7.5", timeout=30):
    env = {"CCDESK_ENDPOINT": endpoint, "CCDESK_GATE_DEADLINE": deadline,
           "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, GATE], input=stdin_text, capture_output=True,
                          text=True, env=env, timeout=timeout)
    return proc


def decision_of(proc):
    return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_connection_refused_falls_back_to_ask():
    proc = run_gate(HOOK_INPUT, "http://127.0.0.1:1/decide")
    assert proc.returncode == 0
    assert decision_of(proc) == "ask"
    assert "Traceback" not in proc.stderr


def test_garbage_stdin_falls_back_to_ask():
    proc = run_gate("{not json", "http://127.0.0.1:1/decide")
    assert proc.returncode == 0
    assert decision_of(proc) == "ask"


def test_empty_stdin_falls_back_to_ask():
    proc = run_gate("", "http://127.0.0.1:1/decide")
    assert proc.returncode == 0
    assert decision_of(proc) == "ask"


def test_output_always_declares_hook_event_name():
    proc = run_gate(HOOK_INPUT, "http://127.0.0.1:1/decide")
    assert json.loads(proc.stdout)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_never_emits_deny_even_if_daemon_says_deny(http_stub):
    endpoint = http_stub({"permissionDecision": "deny", "reason": "no"})
    proc = run_gate(HOOK_INPUT, endpoint)
    assert decision_of(proc) == "ask"


def test_allow_from_daemon_is_passed_through(http_stub):
    endpoint = http_stub({"permissionDecision": "allow", "reason": "allowlist:R07"})
    proc = run_gate(HOOK_INPUT, endpoint)
    assert decision_of(proc) == "allow"
    assert "R07" in json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_malformed_daemon_response_falls_back_to_ask(http_stub):
    endpoint = http_stub({"whatever": 1})
    proc = run_gate(HOOK_INPUT, endpoint)
    assert decision_of(proc) == "ask"


def test_server_error_falls_back_to_ask(http_stub):
    endpoint = http_stub({}, status=500)
    proc = run_gate(HOOK_INPUT, endpoint)
    assert decision_of(proc) == "ask"


def test_slow_daemon_self_downgrades_within_deadline(http_stub):
    endpoint = http_stub({"permissionDecision": "allow"}, delay=10.0)
    start = time.monotonic()
    proc = run_gate(HOOK_INPUT, endpoint, deadline="2.0")
    elapsed = time.monotonic() - start
    assert decision_of(proc) == "ask"
    assert elapsed < 4.0, f"gate 用了 {elapsed:.1f}s，超过自降级线"


def test_bad_deadline_env_falls_back_to_default(http_stub):
    """CCDESK_GATE_DEADLINE 配成非数字：导入期不崩，回落 7.5，闸门照常工作。"""
    # 回落值须可用：快 daemon 照常 allow（若回落成 0/坏值会瞬间降级 ask）
    endpoint = http_stub({"permissionDecision": "allow", "reason": "ok"})
    proc = run_gate(HOOK_INPUT, endpoint, deadline="not-a-number")
    assert proc.returncode == 0
    assert decision_of(proc) == "allow"
    assert "Traceback" not in proc.stderr
    # 拒连端点也仍是合法 ask JSON、exit 0、无 traceback
    proc = run_gate(HOOK_INPUT, "http://127.0.0.1:1/decide", deadline="not-a-number")
    assert proc.returncode == 0
    assert decision_of(proc) == "ask"
    assert "Traceback" not in proc.stderr


def test_drip_feeding_daemon_self_downgrades_within_deadline(http_stub):
    """连接活着、每 0.25s 滴 1 字节、响应永不完成的 daemon：墙钟期限内自降级 ask。"""
    endpoint = http_stub({"permissionDecision": "allow", "reason": "drip"}, drip=True)
    start = time.monotonic()
    proc = run_gate(HOOK_INPUT, endpoint, deadline="2.0")
    elapsed = time.monotonic() - start
    assert proc.returncode == 0
    assert decision_of(proc) == "ask"
    assert elapsed < 4.0, f"gate 用了 {elapsed:.1f}s，超过自降级线"


def test_gate_deadline_s_consistency_with_config():
    """Verify that GATE_DEADLINE_S default in gate matches config.py value."""
    # Read the gate source code
    with open(GATE, 'r') as f:
        gate_source = f.read()

    # Extract the default DEADLINE_S value from gate using regex
    # （解析已移进 _env_deadline() 安全包装，默认字面量仍在 environ.get 的第二参上）
    match = re.search(r'os\.environ\.get\(\s*"CCDESK_GATE_DEADLINE"\s*,\s*"([^"]+)"\s*\)', gate_source)
    assert match, "Could not find DEADLINE_S assignment in gate source"
    gate_default = float(match.group(1))

    # Read and check config.py
    from ccdesk.config import GATE_DEADLINE_S
    config_value = GATE_DEADLINE_S

    # They should match
    assert gate_default == config_value, f"Gate default {gate_default} != config value {config_value}"
