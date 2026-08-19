"""AskUserQuestion 决策层：护栏 → 缓存 → 判官 → 降级。

设计前提全部来自实测，勿凭想象改：

* **U5 已坐实**闸门可用 `allow` + `updatedInput` 代答，会话不弹窗、直接消费
  注入的答案（机制核心是 CC 内部 `if(!updatedInput && requiresUserInteraction())
  return null`）。
* **但 U5 只测过「单问题 + 单选 + 合法 label」**。多问题、multiSelect、自由文本、
  非法 label 全部未测 —— 未验证的形态在这里一律走 ask，不赌。注入一个 options
  之外的字符串等于伪造用户意图，这是本模块最需要防的事。
* **判官通道在本机不可用**：`claude -p --model haiku` 实测 42.5s / 42.8s（两次，
  含空 settings + 禁 MCP），远超闸门 7.5s 期限；且本机无 ANTHROPIC_API_KEY、
  无 apiKeyHelper。所以 `_llm_available()` 现实中恒 False，走 spec §9 的降级路径
  「护栏 + 缓存命中才自动，其余 ask」。判官代码保留，配上 key 就自动生效。

铁律（与闸门一致）：只产出 allow / ask，永不 deny；任何异常都降级而不是抛出。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import config


@dataclass
class Verdict:
    decision: str                      # 只允许 allow / ask —— 永不 deny
    decided_by: str
    confidence: float | None = None
    updated_input: dict | None = None


def guardrail_check(tool_input) -> str | None:
    """U5 未测边界的硬护栏。返回拒绝原因；None 表示可以继续往下判。"""
    if not isinstance(tool_input, dict):
        return "no_questions"
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return "no_questions"
    if len(questions) > 1:
        return "multi_question"
    question = questions[0]
    if not isinstance(question, dict):
        return "no_questions"
    if question.get("multiSelect"):
        return "multiselect"
    options = question.get("options")
    if not isinstance(options, list) or not options:
        return "no_options"
    if not any(isinstance(o, dict) and o.get("label") for o in options):
        return "no_options"
    return None


def validate_answer(question: dict, answer) -> bool:
    """注入值必须严格等于某个 option.label。

    防的是「判官编了个选项」——那等于替用户做了个他没做过的决定。
    严格相等，不做前缀/模糊匹配。
    """
    if not isinstance(answer, str) or not answer:
        return False
    options = question.get("options")
    if not isinstance(options, list):
        return False
    return any(isinstance(o, dict) and o.get("label") == answer for o in options)


def build_updated_input(tool_input: dict, answer: str) -> dict:
    """answers 的形状由 U5 从真实 PostToolUse 样本坐实：{问题原文: option.label}。

    值是 label 不是 description —— 两个真实样本均如此。
    """
    question = tool_input["questions"][0]
    return {
        "questions": tool_input["questions"],
        "answers": {question.get("question"): answer},
    }


def cache_key(payload: dict) -> str:
    from .ledger import input_fingerprint
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    return f"{tool_name}:{input_fingerprint(tool_input, tool_name)}"


def _llm_available() -> bool:
    """判官通道是否可用。

    只认 API key 这一条通道：`claude -p` 实测 42s，塞不进 7.5s 期限（见模块 docstring）。
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _call_llm_judge(question: dict, budget_s: float) -> tuple[str, float] | None:
    """向 haiku 问「该选哪个」。返回 (label, confidence)；任何异常一律 None。

    有意只用 stdlib urllib —— 全项目零第三方依赖。
    """
    import urllib.request

    options = [o for o in question.get("options", []) if isinstance(o, dict)]
    labels = [o.get("label") for o in options if o.get("label")]
    prompt = (
        "你在替用户回答一个选择题。只能从给定选项里原样选一个，不要改写、不要新造。\n"
        '只输出 JSON：{"answer":"<选项原文>","confidence":<0到1的数字>}\n\n'
        f"问题：{question.get('question')}\n"
        f"可选项：{json.dumps(labels, ensure_ascii=False)}\n"
        + "\n".join(f"- {o.get('label')}：{o.get('description', '')}" for o in options)
    )
    body = json.dumps({
        "model": config.JUDGE_MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
        })
    try:
        with urllib.request.urlopen(request, timeout=budget_s) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = "".join(block.get("text", "") for block in data.get("content", [])
                       if isinstance(block, dict))
        parsed = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return str(parsed["answer"]), float(parsed["confidence"])
    except Exception:              # noqa: BLE001 — 判官失败不是故障，是降级信号
        return None


def decide(payload: dict, cache: dict) -> Verdict:
    """主入口。任何一步不确定都落 ask ——「永不在不确定时 allow」。"""
    if not isinstance(payload, dict):
        return Verdict("ask", "guardrail:no_questions")
    tool_input = payload.get("tool_input")

    reason = guardrail_check(tool_input)
    if reason is not None:
        return Verdict("ask", f"guardrail:{reason}")

    question = tool_input["questions"][0]

    key = cache_key(payload)
    cached = cache.get(key)
    if cached is not None:
        try:
            answer, confidence = cached
        except (TypeError, ValueError):
            answer, confidence = None, None
        # 缓存也要过 label 校验：问题改了/CC 版本变了都可能让旧答案失效。
        if validate_answer(question, answer):
            return Verdict("allow", "cache", confidence,
                           build_updated_input(tool_input, answer))

    if not _llm_available():
        return Verdict("ask", "judge_unavailable")

    try:
        result = _call_llm_judge(question, config.JUDGE_BUDGET_S)
    except Exception:              # noqa: BLE001 — 判官异常绝不能逃到 daemon 外
        return Verdict("ask", "judge_error")
    if result is None:
        return Verdict("ask", "judge_error")

    try:
        answer, confidence = result
        confidence = float(confidence)
    except (TypeError, ValueError):
        return Verdict("ask", "judge_error")

    if not validate_answer(question, answer):
        return Verdict("ask", "guardrail:illegal_label", confidence)
    if confidence < config.JUDGE_MIN_CONFIDENCE:
        return Verdict("ask", "judge:low_confidence", confidence)

    cache[key] = (answer, confidence)
    return Verdict("allow", "judge:haiku", confidence,
                   build_updated_input(tool_input, answer))
