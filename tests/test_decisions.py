import json

import pytest

from ccdesk import decisions
from ccdesk.ledger import Ledger, input_fingerprint, make_req_id


def _ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl", tmp_path / "ledger.bad.jsonl")


def test_build_req_id_matches_collector_caliber():
    """闸门侧现算的 req_id 必须与 collector 从 events.jsonl 算的一致，否则两侧对不上号。

    C-3 教训：input_fingerprint 必须两侧都传同一个 tool_name，只改一侧等于没改。
    """
    payload = {
        "session_id": "s1", "prompt_id": "p1", "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "q", "header": "h",
                                      "options": [{"label": "A"}], "multiSelect": False}]},
    }
    expected = make_req_id("s1", "p1", "AskUserQuestion",
                           input_fingerprint(payload["tool_input"], "AskUserQuestion"))
    assert decisions.build_req_id(payload) == expected


def test_build_req_id_survives_garbage_tool_input():
    """闸门永不崩：tool_input 不是 dict 也得算得出 req_id。"""
    assert decisions.build_req_id({"tool_input": "not-a-dict"})
    assert decisions.build_req_id({})


def test_record_decision_appends_row(tmp_path):
    led = _ledger(tmp_path)
    row = decisions.record_decision(led, "r1", "allow", "allowlist:R01", 1.0, 12, 0)
    assert row["decision"] == "allow"
    assert row["decided_by"] == "allowlist:R01"
    # 决策行本身不带 allow_count —— 它由 read_merged 数 allow 行聚合出来
    assert "allow_count" not in row
    merged = led.read_merged()
    assert merged["r1"]["decision"] == "allow"
    assert merged["r1"]["allow_count"] == 1
    assert merged["r1"]["latency_ms"] == 12


def test_allow_count_increments_only_on_allow(tmp_path):
    """duplicate_allow 对账依赖它：只有 allow 才递增，ask 不动。"""
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "allow", "judge:haiku", 0.9, 10, 0)
    merged = led.read_merged()
    assert decisions.current_allow_count(merged, "r1") == 1
    decisions.record_decision(led, "r1", "ask", "timeout_fallback", None, 7500,
                              decisions.current_allow_count(merged, "r1"))
    merged = led.read_merged()
    assert merged["r1"]["decision"] == "ask"
    assert decisions.current_allow_count(merged, "r1") == 1


def test_two_allows_make_allow_count_two(tmp_path):
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "allow", "cache", 0.9, 1, 0)
    decisions.record_decision(led, "r1", "allow", "cache", 0.9, 1,
                              decisions.current_allow_count(led.read_merged(), "r1"))
    assert decisions.current_allow_count(led.read_merged(), "r1") == 2


def test_confidence_none_is_not_written(tmp_path):
    """read_merged 会忽略 None 值，写进去只是白占字节；确认不写空键。"""
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "ask", "guardrail:multiselect", None, 3, 0)
    raw = json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").strip())
    assert "confidence" not in raw


def test_current_allow_count_tolerates_garbage(tmp_path):
    assert decisions.current_allow_count({"r1": {"allow_count": "x"}}, "r1") == 0
    assert decisions.current_allow_count({}, "nope") == 0


def test_decision_row_carries_tool_input_for_replay(tmp_path):
    """replay 要能重放，账本里就必须有 tool_input。

    collector 那侧只存 input_digest 摘要（体积/隐私考虑），重放不了；
    而闸门侧是同步的、比 collector 先看到请求，由它补齐这份原始输入最自然，
    且只有真正过了闸门的 AskUserQuestion 才会存，量很小。
    """
    led = _ledger(tmp_path)
    payload = {"session_id": "s1", "prompt_id": "p1", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "q", "options": [{"label": "A"}],
                                             "multiSelect": False}]}}
    decisions.record_decision(led, decisions.build_req_id(payload), "ask",
                              "judge_unavailable", None, 5, 0, payload=payload)
    row = led.read_merged()[decisions.build_req_id(payload)]
    assert row["tool_input"] == payload["tool_input"]
    assert row["tool_name"] == "AskUserQuestion"
    assert row["session_id"] == "s1"
    assert row["ts_request"]


def test_decision_row_without_payload_stays_minimal(tmp_path):
    """不传 payload 时不得凭空造字段。"""
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "ask", "x", None, 1, 0)
    row = led.read_merged()["r1"]
    assert "tool_input" not in row


def test_decision_row_has_input_digest_for_trace(tmp_path):
    """trace 的「工具」行读 input_digest；不写它那行就是空的 «»。口径与 collector 一致。"""
    led = _ledger(tmp_path)
    payload = {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "选哪个？"}]}}
    rid = decisions.build_req_id(payload)
    decisions.record_decision(led, rid, "ask", "judge_unavailable", None, 5, 0, payload=payload)
    digest = led.read_merged()[rid]["input_digest"]
    assert digest and "选哪个" in digest


def test_allow_count_is_race_free_under_concurrency(tmp_path):
    """两个线程同时为同一 req_id 落 allow，最终 allow_count 必须是 2。

    存绝对值 + read-modify-write 在 ThreadingHTTPServer 下会双双读到 0、
    双双写 1，read_merged 又是 last-wins，于是 duplicate_allow（recon 里
    「幂等键失效」的唯一判据）被静默架空。改成由账本里的 allow 行计数得出，
    append-only 本身就是原子的，没有中间状态可竞争。
    """
    import threading
    led = _ledger(tmp_path)
    payload = {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "q"}]}}
    rid = decisions.build_req_id(payload)

    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        decisions.record_decision(led, rid, "allow", "cache", 0.9, 1, 0, payload=payload)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert decisions.current_allow_count(led.read_merged(), rid) == 2


def test_allow_count_counts_only_allow_rows(tmp_path):
    led = _ledger(tmp_path)
    decisions.record_decision(led, "r1", "allow", "cache", 0.9, 1, 0)
    decisions.record_decision(led, "r1", "ask", "judge_unavailable", None, 1, 0)
    decisions.record_decision(led, "r1", "allow", "cache", 0.9, 1, 0)
    assert decisions.current_allow_count(led.read_merged(), "r1") == 2


def test_tool_input_is_size_capped(tmp_path):
    """闸门落账写的是完整 tool_input，账本 append-only 无人清理、权限 0644。

    collector 那侧特意只存 200 字符摘要（体积/隐私考虑），这边不能无上限地
    把整份问题文本连同可能粘贴其中的代码/凭证原样落盘。超限就退回只存摘要。
    """
    led = _ledger(tmp_path)
    huge = "x" * 50_000
    payload = {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": huge,
                                             "options": [{"label": "A"}],
                                             "multiSelect": False}]}}
    rid = decisions.build_req_id(payload)
    decisions.record_decision(led, rid, "ask", "judge_unavailable", None, 1, payload=payload)
    row = led.read_merged()[rid]
    assert "tool_input" not in row, "超限的 tool_input 不该落盘"
    assert row["input_digest"], "但摘要仍要保留，否则 trace 没得看"
    assert row.get("tool_input_omitted") == "size_cap"


def test_normal_tool_input_still_stored(tmp_path):
    led = _ledger(tmp_path)
    payload = {"session_id": "s", "prompt_id": "p", "tool_name": "AskUserQuestion",
               "tool_input": {"questions": [{"question": "正常大小", "options": [{"label": "A"}],
                                             "multiSelect": False}]}}
    rid = decisions.build_req_id(payload)
    decisions.record_decision(led, rid, "ask", "x", None, 1, payload=payload)
    assert led.read_merged()[rid]["tool_input"]["questions"][0]["question"] == "正常大小"
