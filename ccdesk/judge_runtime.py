"""常驻判官进程：用本机已登录的 Claude 跑 haiku，不需要另配 API key。

**为什么必须常驻**（2026-08-21 实测）：
    每次新起进程   claude -p 44.5s / agent-sdk 44.5s —— 塞不进任何合理窗口
    常驻后连问三次 14.2s → 9.7s → 5.7s
那 40s 是进程启动开销（二进制 207MB），一次性付掉就行。这不是优化，
是这条路能不能用的前提。

用 agent-sdk 的 stream-json 模式：起一次进程，之后每次问询走 stdin/stdout。
`episodic-memory` 插件用的就是这个二进制，所以本机必然有。

纪律：判官挂了、答歪了、超时了，都只是「这次判不了」，一律返回 None，
绝不能把异常抛进 /decide —— 那条路径上挂着真实会话。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

# agent-sdk 二进制。跟着 episodic-memory 插件走，版本号会变，用 glob 找。
_SDK_GLOB = str(Path.home() / ".claude/plugins/cache/*/episodic-memory/*"
                "/node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/claude")

_PROMPT = (
    "你在替用户回答一个选择题。**只能从给定选项里原样选一个**，不要改写、不要新造。\n"
    '只输出 JSON：{{"answer":"<选项原文>","confidence":<0到1的数字>}}\n\n'
    "问题：{question}\n可选项：{labels}\n"
)


def sdk_path() -> str | None:
    hits = sorted(glob.glob(os.environ.get("CCDESK_SDK_GLOB", _SDK_GLOB)))
    return hits[-1] if hits else None


def _spawn():
    path = sdk_path()
    if not path:
        raise OSError("找不到 claude-agent-sdk 二进制")
    return subprocess.Popen(
        [path, "--input-format", "stream-json", "--output-format", "stream-json",
         "--verbose", "--model", "haiku", "--permission-mode", "default",
         "--no-session-persistence"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1)


class Runtime:
    """常驻判官。线程安全：agent-sdk 是单会话的，并发问询会串扰，所以串行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc = None
        self._start_error: str | None = None
        self._ensure()

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            self._proc = _spawn()
            self._start_error = None
        except Exception as exc:            # noqa: BLE001 — 起不来只是判官不可用
            self._proc = None
            self._start_error = f"{type(exc).__name__}: {exc}"

    def available(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ask(self, question: str, labels: list[str],
            budget_s: float) -> tuple[str, float] | None:
        """问判官该选哪个。返回 (label, confidence)；判不了一律 None。"""
        with self._lock:                   # 单会话，必须串行
            self._ensure()
            if not self.available():
                return None
            try:
                return self._ask_locked(question, labels, budget_s)
            except Exception:              # noqa: BLE001
                self._drop()
                return None

    def _ask_locked(self, question: str, labels: list[str],
                    budget_s: float) -> tuple[str, float] | None:
        prompt = _PROMPT.format(question=question,
                                labels=json.dumps(labels, ensure_ascii=False))
        self._proc.stdin.write(json.dumps(
            {"type": "user", "message": {"role": "user", "content": prompt}},
            ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

        deadline = time.time() + budget_s
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:                   # EOF = 进程没了
                self._drop()
                return None
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "result":
                continue
            return _parse_answer(str(event.get("result") or ""), labels)
        return None                        # 超时：不 drop，进程还能用

    def _drop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:                  # noqa: BLE001
            try:
                proc.kill()
            except Exception:              # noqa: BLE001
                pass

    def stop(self) -> None:
        with self._lock:
            self._drop()


def _parse_answer(text: str, labels: list[str]) -> tuple[str, float] | None:
    """从模型输出里抠出 {answer, confidence}。模型爱在 JSON 外面裹一句话。"""
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        answer = str(parsed["answer"])
        confidence = float(parsed["confidence"])
    except (ValueError, KeyError, TypeError):
        return None
    if answer not in labels:               # 自创的选项直接丢
        return None
    return answer, confidence
