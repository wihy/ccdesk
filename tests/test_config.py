"""config 的 env 解析必须在导入期绝不抛异常。

hooks/ccdesk_gate.py 的 _env_deadline 专门做过这件事，注释写着
「必须在导入期绝不抛异常」；新增的三个 knob 又把这个坑挖回来了：
CCDESK_JUDGE_BUDGET=3s 会让 float('3s') 在 import ccdesk.config 时炸，
而每个模块都 import 它 —— daemon 直接起不来，且此时 logging 还没配好，
daemon.log 里什么都看不到。
"""
import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize("var,bad", [
    ("CCDESK_JUDGE_MIN_CONF", "high"),
    ("CCDESK_JUDGE_BUDGET", "3s"),
    ("CCDESK_LEDGER_FILTER_BYTES", "50M"),
    ("CCDESK_TOOL_INPUT_MAX", "8k"),
    ("CCDESK_RECON_WINDOW", "1d"),
    ("CCDESK_API_PORT", "port"),
    ("CCDESK_GATE_DEADLINE", "nan"),
])
def test_malformed_env_does_not_break_import(var, bad):
    code = "import ccdesk.config as c; print('OK', c.JUDGE_BUDGET_S, c.API_PORT)"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env={var: bad, "PATH": "/usr/bin:/bin",
                               "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1])})
    assert proc.returncode == 0, f"{var}={bad} 让 config 导入失败:\n{proc.stderr}"
    assert "OK" in proc.stdout
