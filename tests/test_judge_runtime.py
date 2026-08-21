"""常驻判官进程：把 40s 的启动开销一次性付掉，之后每次问询只付模型时间。

实测（2026-08-21）：
  一次性起进程再问三次 → 14.2s / 9.7s / 5.7s
  每次新起进程 → 44.5s（claude -p 与 agent-sdk 都是这个量级）
所以常驻是这条路能用的前提，不是优化。
"""
import json

import pytest

from ccdesk import judge_runtime


class FakeProc:
    """假的 agent-sdk 进程：按预设脚本吐 stream-json 事件。"""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.written = []
        self.terminated = False
        self._pending = []

    class _Stdin:
        def __init__(self, outer): self.outer = outer
        def write(self, s): self.outer.written.append(s)
        def flush(self): 
            if self.outer._scripted:
                self.outer._pending = list(self.outer._scripted.pop(0))
        def close(self): pass

    class _Stdout:
        def __init__(self, outer): self.outer = outer
        def readline(self):
            if self.outer._pending:
                return self.outer._pending.pop(0)
            return ""          # EOF

    @property
    def stdin(self): return self._Stdin(self)
    @property
    def stdout(self): return self._Stdout(self)
    def poll(self): return None if not self.terminated else 1
    def terminate(self): self.terminated = True
    def kill(self): self.terminated = True
    def wait(self, timeout=None): return 0


def _result(text):
    return [json.dumps({"type": "result", "result": text}) + "\n"]


def test_ask_parses_result_event(monkeypatch):
    proc = FakeProc([_result('{"answer":"选项A","confidence":0.93}')])
    monkeypatch.setattr(judge_runtime, "_spawn", lambda: proc)
    rt = judge_runtime.Runtime()
    assert rt.ask("选哪个？", ["选项A", "选项B"], budget_s=5) == ("选项A", 0.93)
    rt.stop()


def test_ask_tolerates_prose_around_json(monkeypatch):
    """模型爱在 JSON 外面裹一句话，得能从中把对象抠出来。"""
    proc = FakeProc([_result('好的，我的判断是：{"answer":"选项B","confidence":0.88} 供参考')])
    monkeypatch.setattr(judge_runtime, "_spawn", lambda: proc)
    rt = judge_runtime.Runtime()
    assert rt.ask("q", ["选项A", "选项B"], budget_s=5) == ("选项B", 0.88)
    rt.stop()


def test_garbage_output_returns_none(monkeypatch):
    proc = FakeProc([_result("我觉得都行")])
    monkeypatch.setattr(judge_runtime, "_spawn", lambda: proc)
    rt = judge_runtime.Runtime()
    assert rt.ask("q", ["选项A"], budget_s=5) is None
    rt.stop()


def test_dead_process_returns_none_without_raising(monkeypatch):
    """判官进程挂了只是「这次判不了」，绝不能把异常抛进 /decide。"""
    proc = FakeProc([])          # 立刻 EOF
    monkeypatch.setattr(judge_runtime, "_spawn", lambda: proc)
    rt = judge_runtime.Runtime()
    assert rt.ask("q", ["选项A"], budget_s=5) is None
    rt.stop()


def test_spawn_failure_is_not_fatal(monkeypatch):
    def boom():
        raise OSError("二进制不存在")
    monkeypatch.setattr(judge_runtime, "_spawn", boom)
    rt = judge_runtime.Runtime()
    assert rt.available() is False
    assert rt.ask("q", ["选项A"], budget_s=5) is None
    rt.stop()


def test_concurrent_asks_are_serialized(monkeypatch):
    """agent-sdk 是单会话的，两个请求同时喂进去会串扰，必须串行。"""
    import threading
    proc = FakeProc([_result('{"answer":"选项A","confidence":0.9}'),
                     _result('{"answer":"选项B","confidence":0.9}')])
    monkeypatch.setattr(judge_runtime, "_spawn", lambda: proc)
    rt = judge_runtime.Runtime()
    seen = []
    def worker(tag):
        seen.append(rt.ask("q", ["选项A", "选项B"], budget_s=5))
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=10)
    assert len([s for s in seen if s]) == 2, "两次问询都该有结果，不能互相吃掉"
    rt.stop()


def test_prompt_contains_only_the_given_labels(monkeypatch):
    """喂给判官的选项必须原样，别让它有机会自创一个。"""
    proc = FakeProc([_result('{"answer":"甲","confidence":0.9}')])
    monkeypatch.setattr(judge_runtime, "_spawn", lambda: proc)
    rt = judge_runtime.Runtime()
    rt.ask("要选哪个", ["甲", "乙"], budget_s=5)
    sent = "".join(proc.written)
    assert "甲" in sent and "乙" in sent
    assert "原样" in sent or "不要新造" in sent or "只能" in sent
    rt.stop()
