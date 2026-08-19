"""判官层测试。

核心纪律：U5 只坐实了「单问题 + 单选 + 合法 label」这一种形态可代答，
其余形态一律未验证 —— 未验证的一概走 ask，不赌。
"""
from ccdesk import judge

SINGLE = {"questions": [{"question": "选 A 还是 B？", "header": "选择",
                         "options": [{"label": "选项A", "description": "d1"},
                                     {"label": "选项B", "description": "d2"}],
                         "multiSelect": False}]}


def _payload(tool_input, tool_name="AskUserQuestion"):
    return {"session_id": "s", "prompt_id": "p", "tool_name": tool_name,
            "tool_input": tool_input}


# ── 护栏 ────────────────────────────────────────────────────────────

def test_guardrail_passes_single_question_single_select():
    assert judge.guardrail_check(SINGLE) is None


def test_guardrail_rejects_multiselect():
    ti = {"questions": [dict(SINGLE["questions"][0], multiSelect=True)]}
    assert judge.guardrail_check(ti) == "multiselect"


def test_guardrail_rejects_multiple_questions():
    ti = {"questions": SINGLE["questions"] * 2}
    assert judge.guardrail_check(ti) == "multi_question"


def test_guardrail_rejects_empty_options():
    ti = {"questions": [{"question": "q", "header": "h", "options": [], "multiSelect": False}]}
    assert judge.guardrail_check(ti) == "no_options"


def test_guardrail_rejects_options_without_labels():
    ti = {"questions": [{"question": "q", "options": [{"description": "d"}], "multiSelect": False}]}
    assert judge.guardrail_check(ti) == "no_options"


def test_guardrail_rejects_non_askuserquestion_shape():
    assert judge.guardrail_check({"command": "ls"}) == "no_questions"
    assert judge.guardrail_check("not-a-dict") == "no_questions"
    assert judge.guardrail_check({"questions": []}) == "no_questions"
    assert judge.guardrail_check({"questions": [None]}) == "no_questions"


# ── 铁律 ────────────────────────────────────────────────────────────

def test_decide_never_returns_deny():
    """铁律：无论输入多离谱，都不得返回 deny，也不得抛异常。"""
    for bad in ({}, {"questions": None}, {"questions": [{}]},
                {"questions": [{"options": None}]}, "garbage", None):
        assert judge.decide(_payload(bad), {}).decision in ("allow", "ask")


def test_decide_returns_ask_when_guardrail_trips():
    v = judge.decide(_payload({"questions": [dict(SINGLE["questions"][0], multiSelect=True)]}), {})
    assert v.decision == "ask"
    assert v.decided_by == "guardrail:multiselect"
    assert v.updated_input is None


# ── label 合法性（U5 边界 2：防伪造用户意图）──────────────────────────

def test_answer_must_be_an_existing_label():
    q = SINGLE["questions"][0]
    assert judge.validate_answer(q, "选项A") is True
    assert judge.validate_answer(q, "选项C") is False
    assert judge.validate_answer(q, "") is False
    assert judge.validate_answer(q, None) is False
    assert judge.validate_answer(q, "选项") is False       # 前缀不算


def test_build_updated_input_shape():
    """answers 的键是问题原文、值是 option.label —— U5 从真实 PostToolUse 样本坐实。"""
    ui = judge.build_updated_input(SINGLE, "选项A")
    assert ui["questions"] == SINGLE["questions"]
    assert ui["answers"] == {"选 A 还是 B？": "选项A"}


# ── 缓存 ────────────────────────────────────────────────────────────

def test_cache_hit_short_circuits_judge(monkeypatch):
    called = []
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: called.append(1))
    cache = {judge.cache_key(_payload(SINGLE)): ("选项A", 0.95)}
    v = judge.decide(_payload(SINGLE), cache)
    assert v.decision == "allow"
    assert v.decided_by == "cache"
    assert v.updated_input["answers"] == {"选 A 还是 B？": "选项A"}
    assert called == []


def test_poisoned_cache_is_rejected(monkeypatch):
    """缓存里存了个不存在的选项（换了版本/改了问题），不得照单全收。"""
    monkeypatch.setattr(judge, "_llm_available", lambda: False)
    cache = {judge.cache_key(_payload(SINGLE)): ("已不存在的选项", 0.99)}
    assert judge.decide(_payload(SINGLE), cache).decision == "ask"


# ── 判官与降级 ───────────────────────────────────────────────────────

def test_judge_unavailable_degrades_to_ask(monkeypatch):
    """本机的实际路径：无 API key、CLI 42s 用不了 —— 不报错、不阻断、如实标注。"""
    monkeypatch.setattr(judge, "_llm_available", lambda: False)
    v = judge.decide(_payload(SINGLE), {})
    assert v.decision == "ask"
    assert v.decided_by == "judge_unavailable"


def test_judge_error_degrades_to_ask(monkeypatch):
    monkeypatch.setattr(judge, "_llm_available", lambda: True)
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: None)
    v = judge.decide(_payload(SINGLE), {})
    assert v.decision == "ask"
    assert v.decided_by == "judge_error"


def test_low_confidence_degrades_to_ask(monkeypatch):
    monkeypatch.setattr(judge, "_llm_available", lambda: True)
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: ("选项A", 0.5))
    v = judge.decide(_payload(SINGLE), {})
    assert v.decision == "ask"
    assert v.decided_by == "judge:low_confidence"
    assert v.updated_input is None


def test_high_confidence_illegal_label_degrades_to_ask(monkeypatch):
    """判官高置信但答了个不存在的选项 —— 仍必须拒绝。这条防的是伪造用户意图。"""
    monkeypatch.setattr(judge, "_llm_available", lambda: True)
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: ("选项Z", 0.99))
    v = judge.decide(_payload(SINGLE), {})
    assert v.decision == "ask"
    assert v.decided_by == "guardrail:illegal_label"


def test_high_confidence_legal_label_allows(monkeypatch):
    monkeypatch.setattr(judge, "_llm_available", lambda: True)
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: ("选项B", 0.9))
    v = judge.decide(_payload(SINGLE), {})
    assert v.decision == "allow"
    assert v.decided_by == "judge:haiku"
    assert v.confidence == 0.9
    assert v.updated_input["answers"] == {"选 A 还是 B？": "选项B"}


def test_successful_judgement_populates_cache(monkeypatch):
    monkeypatch.setattr(judge, "_llm_available", lambda: True)
    monkeypatch.setattr(judge, "_call_llm_judge", lambda *a, **k: ("选项B", 0.9))
    cache = {}
    judge.decide(_payload(SINGLE), cache)
    assert cache[judge.cache_key(_payload(SINGLE))] == ("选项B", 0.9)


def test_judge_exception_does_not_escape(monkeypatch):
    """判官内部炸了也不能把异常抛给 daemon —— 那会变成会话阻塞。"""
    monkeypatch.setattr(judge, "_llm_available", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(judge, "_call_llm_judge", boom)
    assert judge.decide(_payload(SINGLE), {}).decision == "ask"
