from ccdesk.ledger import (
    VOLATILE_INPUT_KEYS, Ledger, canonical_input, input_fingerprint, make_req_id,
)

# 结局侧才长出来的键（CC 把用户作答并进了 tool_input）。请求侧只有 questions。
ASK_REQUEST_INPUT = {"questions": [{"header": "范围", "options": ["A", "B"]}]}
ASK_OUTCOME_INPUT = {
    "questions": [{"header": "范围", "options": ["A", "B"]}],
    "answers": [{"header": "范围", "answer": "A"}],
    "annotations": {"source": "user"},
}


def test_canonical_input_is_key_order_independent():
    a = {"command": "ls", "description": "d"}
    b = {"description": "d", "command": "ls"}
    assert canonical_input(a) == canonical_input(b)
    assert input_fingerprint(a) == input_fingerprint(b)


# ---------------------------------------------------------------------------
# C-2 fix: 按工具剔除「结局侧才出现」的易变键，让请求侧与结局侧指纹重新对齐
# ---------------------------------------------------------------------------

def test_ask_user_question_fingerprint_ignores_answers_and_annotations():
    """AskUserQuestion 下，结局侧多出的 answers / annotations 不得改变指纹。"""
    assert (input_fingerprint(ASK_REQUEST_INPUT, "AskUserQuestion")
            == input_fingerprint(ASK_OUTCOME_INPUT, "AskUserQuestion"))


def test_volatile_key_stripping_is_per_tool_not_blanket():
    """同样两份输入换成 Bash（不在剔除表里）必须算出不同指纹。

    锁死「不是无差别丢键」——无差别丢 answers/annotations 会让真正带这些字段的
    别的工具产生指纹碰撞，进而错配 outcome。
    """
    assert "Bash" not in VOLATILE_INPUT_KEYS
    assert (input_fingerprint(ASK_REQUEST_INPUT, "Bash")
            != input_fingerprint(ASK_OUTCOME_INPUT, "Bash"))


def test_fingerprint_without_tool_name_keeps_old_behaviour():
    """不传 tool_name 时不剔除任何键——canonical_input 的原语义不变。"""
    assert (input_fingerprint(ASK_REQUEST_INPUT)
            != input_fingerprint(ASK_OUTCOME_INPUT))
    assert input_fingerprint(ASK_REQUEST_INPUT) == input_fingerprint(ASK_REQUEST_INPUT, "Bash")


def test_canonical_input_itself_is_untouched_by_volatile_keys():
    """canonical_input 不参与剔除（_digest 的 fallback 还用着它）。"""
    assert "answers" in canonical_input(ASK_OUTCOME_INPUT)


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


def test_read_merged_without_filter_is_unchanged(tmp_path):
    """小数据量下必须与今天完全一致 —— 不许因为加了过滤参数就改行为。"""
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "b.jsonl")
    led.append({"req_id": "r1", "ts_request": "2026-08-01T00:00:00+00:00"})
    led.append({"req_id": "r2", "ts_request": "2026-08-19T00:00:00+00:00"})
    assert set(led.read_merged()) == {"r1", "r2"}


def test_read_merged_since_filters_old_rows(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "b.jsonl")
    led.append({"req_id": "old", "ts_request": "2026-08-01T00:00:00+00:00"})
    led.append({"req_id": "new", "ts_request": "2026-08-19T00:00:00+00:00"})
    assert set(led.read_merged(since_ts="2026-08-10T00:00:00+00:00")) == {"new"}


def test_read_merged_since_keeps_rows_without_ts(tmp_path):
    """决策行/结局行本身不带 ts_request —— 过滤掉它们会让请求看起来没有决定。"""
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "b.jsonl")
    led.append({"req_id": "r1", "ts_request": "2026-08-19T00:00:00+00:00"})
    led.append({"req_id": "r1", "decision": "allow"})
    merged = led.read_merged(since_ts="2026-08-10T00:00:00+00:00")
    assert merged["r1"]["decision"] == "allow"


def test_read_merged_since_still_counts_bad_lines(tmp_path):
    """过滤不能把坏行统计也一起过滤掉 —— 那会让 recon 的坏行数失真。"""
    path = tmp_path / "l.jsonl"
    led = Ledger(path, tmp_path / "b.jsonl")
    led.append({"req_id": "r1", "ts_request": "2026-08-19T00:00:00+00:00"})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    led.read_merged(since_ts="2026-08-10T00:00:00+00:00")
    assert led.bad_line_count == 1


def test_since_filter_does_not_leave_ghost_records(tmp_path):
    """过滤只看 ts_request，会把老请求行滤掉、却留下它的结局行。

    结局行是 {req_id, outcome, ts_outcome}，没有 tool_name/session_id/digest，
    于是 /ledger 吐出一堆没有主体的幽灵记录，App 面板和 trace 渲染成空行。
    请求行被滤掉时，它的附属行也该一起走。
    """
    led = Ledger(tmp_path / "l.jsonl", tmp_path / "b.jsonl")
    led.append({"req_id": "old", "ts_request": "2026-08-01T00:00:00+00:00",
                "tool_name": "AskUserQuestion", "session_id": "s"})
    led.append({"req_id": "old", "outcome": "executed",
                "ts_outcome": "2026-08-01T00:00:05+00:00"})
    led.append({"req_id": "new", "ts_request": "2026-08-19T00:00:00+00:00",
                "tool_name": "AskUserQuestion", "session_id": "s"})
    led.append({"req_id": "new", "decision": "ask", "decided_by": "judge_unavailable"})

    merged = led.read_merged(since_ts="2026-08-10T00:00:00+00:00")
    assert set(merged) == {"new"}, "老请求的结局行不该单独留下来变成幽灵"
    assert merged["new"]["decision"] == "ask"     # 窗口内的附属行仍要合并
