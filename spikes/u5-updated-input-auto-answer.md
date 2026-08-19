# U5 Spike — 闸门能否用 `updatedInput` 代答 AskUserQuestion

**日期**：2026-08-19 · **结论**：**成立（已实测坐实）** —— `PreToolUse` hook 返回
`allow` + `updatedInput`（内含预填的 `answers`）后，AskUserQuestion **不弹窗**，
会话把注入的答案当作「用户已回答」直接消费。

---

## 为什么这条决定 P2 有没有意义

本机 `~/.claude/settings.json` 的 `permissions.allow` 含 `Bash(*)` 且 `defaultMode=auto`，
所以**唯一会产生 `PermissionRequest` 的工具就是 AskUserQuestion**（P1 账本 5 条记录全部是它）。
若闸门只能做工具授权、答不了选择题，那它在这台机器上几乎无事可做。

---

## 静态证据（CC 2.1.228 二进制）

`runHooks` 里这一行是机制核心：

```js
if (!g.updatedInput && e.requiresUserInteraction?.()) return null;
```

- hook 返回 `allow` 但**不带** `updatedInput`，且工具「需要用户交互」→ **返回 null**，该 allow 被丢弃，回落正常询问
- **带了 `updatedInput` 就跳过这个检查** → 经 `ZRt(...{hookUpdatedInput})` 校验 → `handleHookAllow(updatedInput, ...)`

AskUserQuestion 的实现里：

```js
requiresUserInteraction(){return!0},     // 硬编码 true，无条件
async validateInput({questions:e}){
  if(Mio()!=="html")return{result:!0};   // 非 html 模式直接放行，不校验 answers
  ...
}
```

即：它恒需交互，且 `validateInput` 只看 `questions`、**不检查 `answers`**。

## `answers` 的确切结构（从真实 events.jsonl 的 PostToolUse 样本坐实）

```json
{
  "questions":   [ …原样… ],
  "answers":     { "<问题原文>": "<选中 option 的 label>" },
  "annotations": null
}
```

**值是 `option.label`，不是 `description`。** 两个真实样本均如此。

---

## 实测

沙盒 `/tmp/ccdesk-u5`，探针 hook 对 AskUserQuestion 返回 `allow` + `updatedInput`
（`answers` 预填每个问题的**第一个** option label）。`--settings` 只指向沙盒文件，
**全程未碰 `~/.claude/settings.json`**（收尾 grep `u5_gate` 命中 0）。

**先踩的一个坑**：`claude -p`（print 模式）下 **AskUserQuestion 工具根本不加载**
——会话自己回「不在已加载的工具列表里」。非交互模式没有 UI 收答案，CC 干脆不提供该工具。
这反过来印证了 `requiresUserInteraction` 的语义，但也意味着 **print 模式测不了本题**，
必须用 PTY 起交互式会话。

### 结果

| 观测点 | 结果 |
|---|---|
| hook 是否被调用 | ✅ 收到完整 `tool_input`（含 questions/options） |
| 注入内容 | `{"这次实验应该选 A 还是 B？": "选项A"}` |
| 会话 `status` | **`idle`**（不是 `waiting`）→ 未进入等待态 |
| 弹窗渲染特征（`Enter to select` / `↑/↓ to navigate`） | **命中 0 次** → 弹窗从未出现 |
| 会话产出 | `⏺ User answered Claude's questions: · 这次实验应该选 A 还是 B？ → 选项A`<br>`⏺ USER-PICKED=选项A` |

**全程无人触碰键盘**，「选项A」由 hook 注入。

原始证据：`/tmp/ccdesk-u5-evidence.log`（会话逐字节日志）、`/tmp/ccdesk-u5-hook-evidence.log`（hook 收发）。

---

## 对 P2 设计的影响

- **`ask_question` 风险类的「判官自动作答」可行**，不必降级为「只通知、不自动」
- 代答走 `allow` + `updatedInput`，**不是** `permissionDecision` 三态里的任何一态能单独完成的
- 闸门「永不 deny」的铁律不受影响：代答属于 `allow` 路径

## 未验证 / 边界（不得当已知）

1. **只测了单问题、单选、取第一个 option**。多问题（`questions` 数组 >1）、`multiSelect: true`、
   自由文本（选项外的 "Type something"）均**未测**
2. **注入非法 label**（不在 options 里的字符串）会怎样未测 —— 可能被静默接受，届时等于伪造用户意图
3. `annotations` 字段作用未探究（真实样本里恒为空）
4. 只在 CC **2.1.228** 上测过；`runHooks` 那行是内部实现，**版本升级可能变**，P2 应加一条
   启动自检（构造一次代答，验证机制仍生效），失效时自动降级为「只通知」
