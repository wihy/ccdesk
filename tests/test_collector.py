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
    Collector(ev_path, led, tmp_path / "state.json").run_once()
    rec = next(iter(led.read_merged().values()))
    assert rec["outcome"] == "executed"
    assert rec["ts_outcome"] == "2026-08-14T02:00:02+00:00"


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


def test_missing_events_file_is_not_fatal(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    stats = Collector(tmp_path / "nope.jsonl", led, tmp_path / "state.json").run_once()
    assert stats == {"requests": 0, "outcomes": 0, "skipped": 0}
