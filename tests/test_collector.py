import json

from ccdesk.collector import Collector
from ccdesk.ledger import Ledger

REQ = {
    "summary": {"ts": "2026-08-14T02:00:00+00:00", "event": "PermissionRequest"},
    "payload": {"session_id": "s1", "prompt_id": "p1", "tool_name": "Bash",
                "cwd": "/w", "tool_input": {"command": "git status"}},
}
POST = {
    "summary": {"ts": "2026-08-14T02:00:02+00:00", "event": "PostToolUse"},
    "payload": {"session_id": "s1", "prompt_id": "p1", "tool_name": "Bash",
                "tool_input": {"command": "git status"}},
}


def _write(path, events):
    with open(path, "a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def test_request_is_written_to_ledger(tmp_path):
    ev_path = tmp_path / "events.jsonl"
    _write(ev_path, [REQ])
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    stats = Collector(ev_path, led, tmp_path / "state.json").run_once()
    assert stats["requests"] == 1
    merged = led.read_merged()
    assert len(merged) == 1
    assert next(iter(merged.values()))["tool_name"] == "Bash"


def test_outcome_backfills_matching_request(tmp_path):
    ev_path = tmp_path / "events.jsonl"
    _write(ev_path, [REQ, POST])
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    stats = Collector(ev_path, led, tmp_path / "state.json").run_once()
    rec = next(iter(led.read_merged().values()))
    assert rec["outcome"] == "executed"
    assert rec["ts_outcome"] == "2026-08-14T02:00:02+00:00"
    assert stats["orphans"] == 0


def test_second_run_reads_only_new_lines(tmp_path):
    ev_path = tmp_path / "events.jsonl"
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    col = Collector(ev_path, led, tmp_path / "state.json")
    _write(ev_path, [REQ])
    assert col.run_once()["requests"] == 1
    assert col.run_once()["requests"] == 0
    _write(ev_path, [POST])
    stats = col.run_once()
    assert stats["requests"] == 0 and stats["outcomes"] == 1


def test_rotation_resets_offset(tmp_path):
    """轮转后 inode 变化必须触发从 0 重读。

    用 os.replace 而非 unlink+重建：新文件在旧文件仍存在时创建，inode 必定不同，
    否则 inode 可能被复用导致本测试假绿。
    """
    import os

    ev_path = tmp_path / "events.jsonl"
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    col = Collector(ev_path, led, tmp_path / "state.json")
    _write(ev_path, [REQ])
    col.run_once()
    rotated = tmp_path / "events.new.jsonl"
    _write(rotated, [REQ])
    assert os.stat(rotated).st_ino != os.stat(ev_path).st_ino
    os.replace(rotated, ev_path)
    assert col.run_once()["requests"] == 1


def test_garbage_lines_counted_as_skipped_not_fatal(tmp_path):
    ev_path = tmp_path / "events.jsonl"
    ev_path.write_text("{truncated\n", encoding="utf-8")
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    stats = Collector(ev_path, led, tmp_path / "state.json").run_once()
    assert stats["skipped"] == 1
    assert stats["orphans"] == 0


def test_orphan_outcome_counted_not_dropped(tmp_path):
    """无对应请求的结局事件：不写账本（不瞎归属），但必须计入 orphans。

    复现路径：daemon 首启从头读、或宕机超过一个轮转周期后重启，
    对应请求落在轮转备份里而 collector 从不读备份——结局匹配不上
    任何已知 req_id。此前这类事件被静默丢弃，违反「永不静默丢弃」。
    """
    ev_path = tmp_path / "events.jsonl"
    _write(ev_path, [POST])
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    stats = Collector(ev_path, led, tmp_path / "state.json").run_once()
    assert stats == {"requests": 0, "outcomes": 0, "skipped": 0, "orphans": 1}
    assert led.read_merged() == {}


def test_missing_events_file_is_not_fatal(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    stats = Collector(tmp_path / "nope.jsonl", led, tmp_path / "state.json").run_once()
    assert stats == {"requests": 0, "outcomes": 0, "skipped": 0, "orphans": 0}


def _mk(tmp_path):
    from ccdesk.collector import Collector
    from ccdesk.ledger import Ledger
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "b.jsonl")
    return Collector(tmp_path / "events.jsonl", led, tmp_path / "state.json")


def test_known_set_is_reused_across_runs(tmp_path, monkeypatch):
    """第二轮不得再全量 read_merged 重建 known —— 账本大了那是每 3s 一次的全文件扫描。"""
    col = _mk(tmp_path)
    _write(tmp_path / "events.jsonl", [])
    col.run_once()

    calls = []
    original = col.ledger.read_merged
    monkeypatch.setattr(col.ledger, "read_merged",
                        lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    col.run_once()
    assert calls == []


def test_known_set_rebuilds_after_reset(tmp_path):
    """状态被重置时必须重建，不能带着残缺的 known 继续跑（会把请求误判成孤儿）。"""
    col = _mk(tmp_path)
    _write(tmp_path / "events.jsonl", [])
    col.run_once()
    col._known = None
    col.run_once()
    assert col._known is not None


def test_incremental_known_still_backfills_outcome(tmp_path):
    """增量维护后，跨轮次的 request→outcome 回填必须仍然成立。"""
    import json as _json
    events_path = tmp_path / "events.jsonl"
    col = _mk(tmp_path)

    req = {"summary": {"event": "PermissionRequest"},
           "payload": {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
                       "tool_input": {"questions": [{"question": "q"}]}}}
    events_path.write_text(_json.dumps(req) + "\n", encoding="utf-8")
    col.run_once()

    out = {"summary": {"event": "PostToolUse"},
           "payload": {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
                       "tool_input": {"questions": [{"question": "q"}],
                                      "answers": {"q": "A"}}}}
    with open(events_path, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(out) + "\n")
    stats = col.run_once()
    assert stats["outcomes"] == 1, "第二轮的结局必须挂上第一轮的请求，不能变成孤儿"
    assert stats["orphans"] == 0


def test_gate_written_req_id_is_not_treated_as_orphan(tmp_path):
    """闸门与 collector 是两条独立的写入路径。

    判官放行时 CC 不会发 PermissionRequest（hook 直接放行了），所以 collector
    从没在事件流里见过这个 req_id；但闸门已经把它写进账本了。若 known 只认
    collector 自己写过的，这条结局就会被误判成孤儿、outcome 永远回填不上，
    进而每条成功代答都在 600s 后触发 empty_allow 误报。
    """
    import json as _json
    from ccdesk import decisions

    events_path = tmp_path / "events.jsonl"
    events_path.write_text("", encoding="utf-8")
    col = _mk(tmp_path)
    col.run_once()                       # 首轮建立 known

    payload = {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "q", "options": [{"label": "A"}],
                                             "multiSelect": False}]}}
    req_id = decisions.build_req_id(payload)
    decisions.record_decision(col.ledger, req_id, "allow", "judge:haiku", 0.9, 5, 0,
                              payload=payload)

    out = {"summary": {"event": "PostToolUse"},
           "payload": {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
                       "tool_input": {"questions": [{"question": "q",
                                                     "options": [{"label": "A"}],
                                                     "multiSelect": False}],
                                      "answers": {"q": "A"}}}}
    events_path.write_text(_json.dumps(out) + "\n", encoding="utf-8")

    stats = col.run_once()
    assert stats["orphans"] == 0, "闸门写的 req_id 不该被当成孤儿"
    assert stats["outcomes"] == 1
    assert col.ledger.read_merged()[req_id]["outcome"] == "executed"


def test_real_orphan_still_counted(tmp_path):
    """反向保护：真正无主的结局仍要被数出来，不能因为放宽而静默吞掉。"""
    import json as _json
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("", encoding="utf-8")
    col = _mk(tmp_path)
    col.run_once()

    out = {"summary": {"event": "PostToolUse"},
           "payload": {"session_id": "nobody", "prompt_id": "nope", "tool_name": "Bash",
                       "tool_input": {"command": "ls"}}}
    events_path.write_text(_json.dumps(out) + "\n", encoding="utf-8")
    assert col.run_once()["orphans"] == 1
