"""人工窗口：把一次决策挂起，等判官或人，谁先给答案用谁。

时间预算实测依据：
  判官常驻进程后 5.7~9.7s（首次含启动 14.2s）；人看到通知再点约需 10-15s。
  串行等两者会超 25s，所以必须并行 —— 总时长是 max 不是 sum。
"""
import threading
import time

import pytest

from ccdesk import pending


def _q(question="选 A 还是 B？", labels=("选项A", "选项B")):
    return {"questions": [{"question": question, "header": "选择",
                           "options": [{"label": x} for x in labels],
                           "multiSelect": False}]}


def test_open_puts_item_in_queue():
    board = pending.Board()
    item = board.open("r1", _q(), {"session_id": "s1", "cwd": "/w"}, window_s=5)
    listed = board.list_open()
    assert len(listed) == 1
    assert listed[0]["req_id"] == "r1"
    assert listed[0]["question"] == "选 A 还是 B？"
    assert [o["label"] for o in listed[0]["options"]] == ["选项A", "选项B"]
    assert listed[0]["remaining_s"] > 0
    board.close(item)


def test_human_answer_wakes_the_waiter():
    """人点了 → 阻塞中的 /decide 立刻拿到答案，不等满窗口。"""
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=5)

    def click():
        time.sleep(0.15)
        assert board.resolve("r1", "选项B", by="human") is True

    threading.Thread(target=click, daemon=True).start()
    t0 = time.time()
    answer, by = board.wait(item)
    assert answer == "选项B"
    assert by == "human"
    assert time.time() - t0 < 2, "应当被唤醒，而不是等满窗口"


def test_judge_answer_also_wakes_the_waiter():
    """判官先到也一样——两条路共用同一个唤醒机制。"""
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=5)
    threading.Thread(target=lambda: (time.sleep(0.1),
                                     board.resolve("r1", "选项A", by="judge:haiku")),
                     daemon=True).start()
    answer, by = board.wait(item)
    assert (answer, by) == ("选项A", "judge:haiku")


def test_first_answer_wins():
    """判官和人同时给答案时，先到的赢，后到的不得覆盖。"""
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=5)
    assert board.resolve("r1", "选项A", by="judge:haiku") is True
    assert board.resolve("r1", "选项B", by="human") is False, "已定的不能被改"
    assert board.wait(item) == ("选项A", "judge:haiku")


def test_timeout_returns_none():
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=0.2)
    t0 = time.time()
    answer, by = board.wait(item)
    assert answer is None and by is None
    assert 0.15 < time.time() - t0 < 2


def test_illegal_answer_is_rejected():
    """注入 options 之外的字符串等于伪造用户意图——人工通道也不能开这个口子。"""
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=5)
    assert board.resolve("r1", "选项Z", by="human") is False
    assert board.resolve("r1", "", by="human") is False
    assert board.resolve("r1", None, by="human") is False
    board.close(item)


def test_resolve_unknown_req_id_is_false():
    assert pending.Board().resolve("nope", "选项A", by="human") is False


def test_closed_item_leaves_the_board():
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=5)
    board.close(item)
    assert board.list_open() == []
    assert board.resolve("r1", "选项A", by="human") is False


def test_expired_items_are_swept_from_listing():
    """过期项不能一直挂在面板上——用户点一个已经回落终端的题毫无意义。"""
    board = pending.Board()
    item = board.open("r1", _q(), {}, window_s=0.1)
    time.sleep(0.2)
    assert board.list_open() == []
    board.close(item)


def test_concurrent_opens_are_independent():
    board = pending.Board()
    a = board.open("ra", _q("问题A"), {}, window_s=5)
    b = board.open("rb", _q("问题B"), {}, window_s=5)
    assert len(board.list_open()) == 2
    board.resolve("ra", "选项A", by="human")
    assert board.wait(a)[0] == "选项A"
    assert board.list_open()[0]["req_id"] == "rb"
    board.close(a); board.close(b)


def test_list_open_carries_session_context():
    """面板要显示「哪个会话在问」，不然一堆待决项分不清谁是谁。"""
    board = pending.Board()
    item = board.open("r1", _q(), {"session_id": "s1", "session_name": "soulapp-f3",
                                   "cwd": "/Users/x/SoulApp"}, window_s=5)
    row = board.list_open()[0]
    assert row["session_name"] == "soulapp-f3"
    assert row["cwd"] == "/Users/x/SoulApp"
    board.close(item)
