from ccdesk.ledger import Ledger, canonical_input, input_fingerprint, make_req_id


def test_canonical_input_is_key_order_independent():
    a = {"command": "ls", "description": "d"}
    b = {"description": "d", "command": "ls"}
    assert canonical_input(a) == canonical_input(b)
    assert input_fingerprint(a) == input_fingerprint(b)


def test_req_id_is_stable_and_short():
    r1 = make_req_id("s", "p", "Bash", "fp")
    r2 = make_req_id("s", "p", "Bash", "fp")
    assert r1 == r2 and len(r1) == 16


def test_req_id_differs_on_any_field():
    base = make_req_id("s", "p", "Bash", "fp")
    assert make_req_id("s2", "p", "Bash", "fp") != base
    assert make_req_id("s", "p2", "Bash", "fp") != base
    assert make_req_id("s", "p", "Read", "fp") != base
    assert make_req_id("s", "p", "Bash", "fp2") != base


def test_append_then_merge_later_keys_win(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    led.append({"req_id": "r1", "tool_name": "Bash", "decision": None})
    led.append({"req_id": "r1", "decision": "allow", "decided_by": "allowlist:R07"})
    merged = led.read_merged()
    assert set(merged) == {"r1"}
    assert merged["r1"]["tool_name"] == "Bash"       # 早期字段保留
    assert merged["r1"]["decision"] == "allow"        # 后写覆盖
    assert merged["r1"]["decided_by"] == "allowlist:R07"


def test_merge_ignores_none_so_later_null_does_not_erase(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    led.append({"req_id": "r1", "decision": "allow"})
    led.append({"req_id": "r1", "decision": None, "outcome": "executed"})
    merged = led.read_merged()
    assert merged["r1"]["decision"] == "allow"
    assert merged["r1"]["outcome"] == "executed"


def test_bad_lines_are_quarantined_not_dropped(tmp_path):
    path = tmp_path / "l.jsonl"
    bad = tmp_path / "bad.jsonl"
    path.write_text('{"req_id": "ok"}\n{truncated\n', encoding="utf-8")
    led = Ledger(path, bad)
    merged = led.read_merged()
    assert set(merged) == {"ok"}
    assert led.bad_line_count == 1
    assert "{truncated" in bad.read_text(encoding="utf-8")


def test_record_without_req_id_is_rejected(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "bad.jsonl")
    try:
        led.append({"tool_name": "Bash"})
    except ValueError:
        return
    raise AssertionError("append 必须拒绝无 req_id 的记录")


# ---------------------------------------------------------------------------
# A-1 fix: 坏行隔离去重（reviewer 实测坐实：同一坏行被反复追加到 bad.jsonl）
# ---------------------------------------------------------------------------

def test_bad_jsonl_line_count_stays_one_across_repeated_calls(tmp_path):
    """连续调用 read_merged() 三次，bad.jsonl 的行数必须恒为 1（而不是 3）。"""
    path = tmp_path / "l.jsonl"
    bad = tmp_path / "bad.jsonl"
    path.write_text('{"req_id": "ok"}\n{truncated\n', encoding="utf-8")
    led = Ledger(path, bad)
    led.read_merged()
    led.read_merged()
    led.read_merged()
    lines = bad.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_two_distinct_bad_lines_in_same_call_are_both_written(tmp_path):
    """同一次调用中出现两条不同的坏行时，两条都要被写入（去重不能误伤不同坏行）。"""
    path = tmp_path / "l.jsonl"
    bad = tmp_path / "bad.jsonl"
    path.write_text('{"req_id": "ok"}\n{truncated-one\n{truncated-two\n', encoding="utf-8")
    led = Ledger(path, bad)
    merged = led.read_merged()
    assert set(merged) == {"ok"}
    assert led.bad_line_count == 2
    bad_content = bad.read_text(encoding="utf-8")
    assert "{truncated-one" in bad_content
    assert "{truncated-two" in bad_content


def test_bad_line_count_reflects_scan_count_not_write_count(tmp_path):
    """bad_line_count 语义不变：恒为「本次扫描遇到的坏行数」，重复调用仍为 1。"""
    path = tmp_path / "l.jsonl"
    bad = tmp_path / "bad.jsonl"
    path.write_text('{"req_id": "ok"}\n{truncated\n', encoding="utf-8")
    led = Ledger(path, bad)
    for _ in range(3):
        led.read_merged()
        assert led.bad_line_count == 1


def test_restart_with_fresh_ledger_instance_does_not_duplicate_bad_line(tmp_path):
    """模拟进程重启：用同样的 path/bad_path 重新构造 Ledger，bad.jsonl 行数仍应为 1。"""
    path = tmp_path / "l.jsonl"
    bad = tmp_path / "bad.jsonl"
    path.write_text('{"req_id": "ok"}\n{truncated\n', encoding="utf-8")
    led1 = Ledger(path, bad)
    led1.read_merged()
    assert len(bad.read_text(encoding="utf-8").splitlines()) == 1

    # 重新构造实例，模拟 daemon 重启；__init__ 应从 bad_path 重建去重集合。
    led2 = Ledger(path, bad)
    led2.read_merged()
    assert len(bad.read_text(encoding="utf-8").splitlines()) == 1
