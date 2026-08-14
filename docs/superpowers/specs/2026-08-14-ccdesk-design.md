# ccdesk 设计方案（Claude Code 会话值班台）

**日期**：2026-08-14 · **状态**：设计已定稿，待实施 · **代号**：`ccdesk`

> 命名说明：CC 内置 TUI 叫 **FleetView**（`claude agents`）。本系统不与之重名，定位是它之外的
> 「常驻值班台」：菜单栏可见 + 自动授权 + 对账 + 可追溯。

---

## 一、一句话结论

用 **官方 `claude agents --json` 做会话真源**、**官方 `PreToolUse` hook 做授权闸门**，外挂一个
launchd 常驻 Python daemon 做三态决策与四类对账，再用 Swift 菜单栏 App 做常驻展示与人工兜底 ——
在不改变现有工作方式的前提下，把「哪个会话在等、等什么、要不要放行、放行后跑没跑」变成可见、可自动、可追溯。

---

## 二、已坐实的事实（本次实测，非推断）

| 事实 | 证据 | 对设计的影响 |
|---|---|---|
| 会话枚举 + 等待检测已官方提供 | `claude agents --json` 返回 `pid/cwd/kind/sessionId/name/status`，等待时带 `status:"waiting"` + **`waitingFor:"dialog open"`**。实测 9 个会话，含一个真实 waiting | **不自建会话发现层**。该 CLI 为主源 |
| 程序化授权通道存在且唯一 | CLI 二进制内含 `hookSpecificOutput.permissionDecision` ∈ `allow`/`deny`/`ask`，文档串明确标注 **PreToolUse only** | 自动授权只能在 `PreToolUse` hook 里做 |
| 决策不能异步外包 | `permissionDecision=defer` 存在，但串明确写 **"defer is print-mode only"**，交互模式忽略 | 交互式会话必须**同步**出决定 |
| hook 超时代价是硬伤 | 串：`PreToolUse hook did not respond before its timeout … The tool call was not executed` | 闸门必须**自己**在超时前降级返回 `ask`，绝不让 CC 判超时 |
| 入站 peer 消息有审批闸门 | 二进制含 `peer_inbound_gate` / `peer_inbound_approval` / `peer_blocked` | 「外部推进」**不是无条件可用**，列为 P0 spike |
| 全量事件已在采集 | `~/.claude/settings.json` 全 hook → `~/.claude/hooks/observe.py` → `~/.adw/observability/claude/events.jsonl`（61MB + 5 轮转档），含 `PermissionRequest`（带 `tool_name`/`tool_input`/`session_id`/`prompt_id`） | 追踪与对账的原始素材已存在，**不重复采集** |
| peer 通信通道存在 | `/tmp/cc-socks/<pid>.sock`；`ListAgents` 实测列出 7 个会话 | 回复推进的候选通道 |

**未验证项（禁止当成已知可行）**：

| 编号 | 未验证内容 | 处置 |
|---|---|---|
| U1 | 向 `status=waiting`（弹窗态）的会话发 peer 消息，会话是否会消费并推进 | P0 spike，用一次性沙盒会话验证，**不得拿用户在跑的会话做实验** |
| U2 | hook `timeout` 字段的上限与默认值 | P0 spike，实测 8s / 30s / 60s 是否被接受 |
| U3 | FleetView 的 `peek-reply` 是否有可脚本化入口 | P1 探查；若有则优先复用，替代自研推进通道 |
| U4 | `waitingFor` 的完整取值域（目前只观测到 `dialog open`） | P1 边跑边收集，写进枚举表 |

---

## 三、架构

```
┌── 真源层（全部已存在，零改造）
│   claude agents --json                    会话真源：status / waitingFor（主）
│   ~/.claude/sessions/<pid>.json           同数据的文件快照（CLI 不可用时降级）
│   /tmp/cc-socks/<pid>.sock                peer 通道（推进候选，待 U1）
│   ~/.adw/observability/claude/events.jsonl 全量 hook 事件（授权请求明细）
│   ~/.claude/projects/**/<sid>.jsonl       transcript（任务进展对账真源）
│
├── 闸门层（新 · 跑在每个会话进程内）
│   ccdesk-gate.py    PreToolUse hook。全系统唯一有权返回 permissionDecision 的地方
│        │ HTTP 127.0.0.1:8787（回环，仅本机）
├── 中枢层（新 · launchd 常驻 Python daemon）
│   collector   轮询 CLI + 尾随 events.jsonl → 归一化写账本
│   judge       三态决策：白名单 → LLM 判官 → 挂起
│   driver      推进执行（peer / csd tmux），受 U1 结论约束
│   reconciler  四类对账
│   api         本地 HTTP，服务 gate 与 App
│        │
└── 展示层（新 · Swift 菜单栏 / 刘海 App）
    CCDesk.app   状态徽标 + 通知 + 一键 allow/deny/回复；对账走 App 内详情视图
```

### 边界纪律

1. **gate 内零业务规则**。它只做：POST 给 daemon → 等最多 `T_max` → 到点自返 `ask`。
   规则与判官全在 daemon，改规则不需要重启任何会话。
2. **单向写**。只有 daemon 写 `~/.ccdesk/`；gate 与 App 一律走 HTTP。杜绝多进程抢写 JSON。
3. **三级 fail-open**：daemon 不可达 → `ask`；judge 异常 → 挂起转人工；App 未开 → daemon 照常决策。
   **任何一环挂掉都不会让会话卡死，也永不误 `deny`。**

---

## 四、数据模型

目录 `~/.ccdesk/`：

| 文件 | 内容 | 写者 |
|---|---|---|
| `registry.json` | 会话快照（`claude agents --json` 归一化 + 派生字段） | daemon |
| `ledger.jsonl` | **授权决策账本**，append-only，一条请求一行，状态原地演进由后续行覆盖 | daemon |
| `tasks.json` | 会话 ↔ 任务绑定（任务标题、外部真源 id、期望产物） | daemon |
| `rules/allowlist.yaml` | 确定性白名单规则 | 人工编辑，daemon 热加载 |
| `cache/verdicts.db` | 判官结论缓存，键=`tool_name + 参数指纹` | daemon |
| `logs/daemon.log` | 运行日志 | daemon |

### 账本记录（`ledger.jsonl` 单条）

```json
{
  "req_id": "sha256(session_id+prompt_id+tool_name+input_fp)[:16]",
  "ts_request": "2026-08-14T03:02:12.107+00:00",
  "session_id": "9975af37-...", "pid": 12588, "cwd": "/Users/chunhaixu/cp_analysisutils",
  "session_name": "cp-analysisutils-bb",
  "tool_name": "Bash", "input_fp": "sha256(canonical(tool_input))[:12]",
  "input_digest": "git status --porcelain",
  "risk_class": "readonly|workspace_write|local_reversible|ask_question|external_write|destructive",
  "decision": "allow|deny|ask",
  "decided_by": "allowlist:R07|judge:haiku|human:menubar|timeout_fallback",
  "confidence": 0.0,
  "latency_ms": 42,
  "ts_decision": "...",
  "outcome": "executed|blocked|user_denied|unknown",
  "ts_outcome": "...",
  "trace_id": "<OTLP trace id，与 events.jsonl 对齐>"
}
```

**`req_id` 幂等键**是全系统骨架：同一条授权请求无论被 gate 写一次、被 collector 从 events.jsonl 再看到一次、
被 App 点一次，都归并到同一条。四类对账全部挂在它上面。

**`outcome` 回填**：daemon 尾随 `events.jsonl`，用 `session_id + prompt_id + tool_name` 匹配后续
`PostToolUse`（→ `executed`）或 `PermissionDenied`（→ `user_denied`）；超过 10 分钟未见 → `unknown`。
这是「批了到底有没有生效」的唯一判据。

---

## 五、三态决策引擎

```
PreToolUse 请求
   │
   ├─ 第 1 层 白名单（确定性，μs 级，本地）
   │    命中 allow 规则 → allow（decided_by=allowlist:<规则id>）
   │    命中红线规则   → ask（永不自动 deny，把选择权交回人）
   │
   ├─ 第 2 层 判官缓存（相同 tool+参数指纹曾被判过）
   │    命中 → 沿用历史结论，等价白名单速度
   │
   ├─ 第 3 层 LLM 判官（`claude-haiku-4-5-20251001`，极短 prompt，预算 ≤3s）
   │    confidence ≥ 0.85 且落在授权白名单类别内 → allow
   │    否则 → 挂起
   │
   └─ 第 4 层 人工窗口（挂起后 daemon 推通知给 App，等 T_remain）
        期内人点了 → 按人的决定返回
        到点未处理 → ask（回落终端原生弹窗，行为=现状）
```

### 时间预算（`T_max` 默认 8s）

| 段 | 预算 | 超时行为 |
|---|---|---|
| gate→daemon HTTP | 300ms | 失败即 `ask` |
| 白名单 + 缓存 | <10ms | — |
| LLM 判官 | 3s | 超时转挂起 |
| 人工窗口 | 剩余 4.2s | 到点 `ask` |
| **gate 自降级线** | **7.5s** | gate 主动返回 `ask`，绝不让 CC 判超时 |

> 人工窗口只有几秒是**物理限制**（`defer` 仅 print 模式可用），不是设计缺陷。
> 它的真实价值是「你正盯着屏幕时能一键放行」；不在跟前时自然回落终端弹窗，零损失。

### 风险分级与默认策略

| risk_class | 判定依据 | 默认 |
|---|---|---|
| `readonly` | Read/Grep/Glob/NotebookRead、`git status\|diff\|log\|show`、`ls/cat/head/wc`、只读 MCP | **自动 allow** |
| `workspace_write` | Edit/Write，且目标路径在会话 `cwd` 内、非 `.git/`、非 `~/.claude/`、非系统路径 | **自动 allow** |
| `local_reversible` | 编译/测试/lint/`pod install`/包管理器安装 | **自动 allow** |
| `ask_question` | AskUserQuestion | **判官选**（≥0.85 才自动） |
| `external_write` | git push / MR / 发飞书 / 部署 / 任何出网写 | **永不自动** |
| `destructive` | `rm -rf`、`git reset --hard`、删分支、覆盖未跟踪文件、cwd 外写 | **永不自动**，且高亮告警 |

红线是**兜底而非唯一防线**：未能明确归类的一律落 `ask`（默认拒绝自动化，不是默认放行）。

---

## 六、推进通道

两种等待，物理通道不同：

| 等待类型 | 识别 | 通道 | 状态 |
|---|---|---|---|
| 等授权（工具调用） | gate 收到 PreToolUse | `permissionDecision` 返回值 | ✅ 已坐实 |
| 等回复（会话停在那要你说话） | `claude agents --json` 报 `status=waiting`/`idle` 持续 >2 分钟（可配 `reply_wait_seconds`） | peer socket（`SendMessage`）| ⚠️ 依赖 U1 |
| 托管会话 | 由 ccdesk 拉起 | `csd`（tmux）send / converse | ✅ 工具已存在 |

**U1 未验证前，回复推进只对托管会话开放**；对手开会话仅做「通知 + 定位到那个终端窗口」。
若 U1 验证失败，P3 的回复推进降级为「只服务托管会话」，不影响 P1/P2 交付价值。

---

## 七、四类对账口径

所有对账都基于 `ledger.jsonl` + `registry.json` + transcript，**每个数字必须能点回原始记录**。

### 1) 授权事件闭环对账（P1）

对每条 `req_id` 检查：`ts_request` → `decision` → `outcome` 三段是否闭合。

| 异常 | 判据 | 含义 |
|---|---|---|
| 悬空请求 | 有 request 无 decision，>60s | 闸门或 daemon 掉了 |
| 空放行 | `decision=allow` 但 `outcome=unknown`，>10min | 批了但没生效 |
| 重复批 | 同 `req_id` 出现 ≥2 次 allow | 幂等键失效 |
| 静默卡死 | `decision=ask` 后会话 `status=waiting` 持续 >30min | 漏批，人没看到 |

### 2) 会话任务进展对账（P3）

`tasks.json` 记录「交给它的任务」，与 transcript 实际推进比对：

- **停摆**：`status=idle` 且最后一条助手消息不含完成声明，持续 >15 分钟（可配 `stall_minutes`）
- **跑偏**：最近 20 次工具调用触及的路径与任务声明范围（`tasks.json.scope_paths`）无交集
- **虚报完成**：出现完成声明，但任务定义的验收产物（文件/测试/提交）不存在

> 「虚报完成」只做**证据缺失提示**，不自动判定失败 —— 校验器误报比漏报危险（三态：pass / fail / 升级人工复核）。

### 3) 资源/额度对账（P4）

从 `events.jsonl` 已有的 token 字段按 `session_id` 聚合 → 按 `tasks.json` 归到任务头上。
输出：每任务花费、空转会话（有消耗无进展）、5h 窗口占用分布。
**口径必须随数字一同呈现**（窗口、是否含 subagent、是否含缓存读）。

### 4) 与外部真源对账（P4）

`tasks.json.external_ref` 挂飞书项目 story_id / 本地 TODO。做双向差集：
真源有而无会话在做（漏干）、会话在做而真源无对应（计划外）、多个会话做同一条（重复干）。
适配器可插拔，首期只接飞书项目。

---

## 八、可追踪与排查

**一条请求的完整足迹**（任一环都能反查）：

```
req_id ──┬─ ledger.jsonl        决策全过程 + 谁决的 + 耗时
         ├─ events.jsonl        原始 PermissionRequest / PostToolUse（含完整 tool_input）
         ├─ trace_id → OTLP     已上报的链路（复用现有 collector）
         └─ transcript jsonl    该轮对话上下文
```

排查入口（CLI，与 App 同源）：

```bash
ccdesk trace <req_id>       # 打完整足迹时间线
ccdesk why <req_id>         # 只打「为什么这么决的」：命中规则 / 判官理由 / 置信度
ccdesk recon --kind=auth    # 跑一次授权闭环对账，列全部异常
ccdesk sessions             # 当前会话表（等价 claude agents --json + 派生状态）
ccdesk replay --since=1h    # 用历史请求重放当前规则集，看规则改动会改变哪些决定
```

`replay` 是规则变更的安全网：改白名单前先重放，看会不会把过去的 `ask` 变成 `allow`。

---

## 九、失败模式与降级

| 故障 | 表现 | 降级 | 用户感知 |
|---|---|---|---|
| daemon 挂 | gate HTTP 失败 | gate 立即返 `ask` | 回到今天的体验 |
| daemon 慢 | 超 7.5s | gate 自返 `ask` | 同上 |
| judge（LLM）不可用 | 第 3 层异常 | 降级为「白名单命中才自动，其余 ask」 | 自动化率下降，不出错 |
| App 未启动 | 无通知 | daemon 照常决策，事后可在 App 补看 | 少了实时提醒 |
| 账本损坏 | jsonl 尾部截断 | 按行解析，坏行进 `ledger.bad.jsonl` 并告警 | 对账标注「本窗口有 N 行不可解析」 |
| `claude agents` CLI 变更 | 解析失败 | 降级读 `~/.claude/sessions/*.json` | 有降级告警 |

**永不做的三件事**：永不自动 `deny`；永不在不确定时 `allow`；永不静默丢弃一条请求（丢弃也要进账本）。

---

## 十、测试策略

| 层 | 方式 | 关键用例 |
|---|---|---|
| 白名单规则 | 纯函数单测 | 每条规则的正例/反例；路径逃逸（`cwd/../..`）；`rm -rf` 各变体不得命中 allow |
| gate | 契约测试（mock daemon） | daemon 200/500/超时/连接拒绝 → 均返回合法 hook JSON；**任何异常都不得抛栈**（抛栈=hook 失败=工具被挡） |
| 时间预算 | 计时断言 | 注入 10s 慢 daemon，gate 必须在 7.5s±0.3s 返回 `ask` |
| 账本幂等 | 属性测试 | 同一请求重复投递 N 次，账本仍只有一条 `req_id` |
| 对账器 | 构造账本夹具 | 四类异常各造一个，必须全被检出；正常账本零误报 |
| 端到端 | 沙盒会话 | 一次性 `claude` 会话触发只读命令 → 自动放行；触发 `rm -rf` → 落 `ask` |

**沙盒纪律**：所有端到端测试只在临时目录的一次性会话上跑，**绝不对用户正在工作的会话做实验**。

---

## 十一、分期与验收

| 期 | 内容 | 验收标准（可判定） |
|---|---|---|
| **P0** | spike：U1 peer 推进可行性、U2 hook timeout 上限、gate 骨架 | U1/U2 有明确结论并写回本文档；gate 在 mock 下四种异常全部返回合法 JSON |
| **P1** | collector + registry + 账本 + 菜单栏 App 展示与通知 + **授权闭环对账** | 9 个会话状态实时可见；`waiting` 会话 5s 内出通知；`ccdesk trace` 能打出任一请求完整足迹；四类授权异常检出率 100%（夹具） |
| **P2** | 三态决策引擎 + gate 接入 + 人工一键处理 + `replay` | 只读类自动放行率 >90%；红线类自动放行 **0 次**；日常使用中无一次因 ccdesk 导致工具被误挡 |
| **P3** | 托管会话（csd）+ 回复推进 + **任务进展对账** | 能托管拉起并自动跑完一个多轮任务；停摆/跑偏/虚报三类检出可用 |
| **P4** | **资源额度对账** + **外部真源对账** | 每会话 token 能归到任务；与飞书项目双向差集可出 |

**P1 之前不接 gate**：先只观察不干预，攒够真实请求样本再写白名单，避免拍脑袋定规则。

---

## 十二、明确不做（YAGNI）

- 不做多机 / 远程会话管理（只管本机）
- 不做 Web dashboard（菜单栏 App + CLI 已覆盖）
- 不重写 `observe.py`，不新增第二套事件采集
- 不自建会话发现（用官方 CLI）
- 不做权限规则的可视化编辑器（YAML + `replay` 足够）
- 不替代 FleetView 的 TUI 能力（attach/查看仍用 `claude agents`）
