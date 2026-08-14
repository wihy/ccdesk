# U1 Spike — waiting 会话能否被外部 peer 消息推进

**日期**：2026-08-14 · **执行者**：控制端会话本人（非 subagent）· **结论**：**仅排队不推进**
（本次仅实测 `waitingFor=input needed` 一支；`dialog open` 未直接复现，见下方「适用范围」）

**2026-08-14 修复轮 1/5 追记**：本文档「保护名单」节原有的文件清除判据未经验证即用于支撑安全结论，
已在下方新增「判据受控实验」小节补做验证并改写；「为什么执行方式偏离了计划」与「清理」两节存在未披露
偏离，已一并修正。修复报告见 `.superpowers/sdd/2026-08-14-ccdesk-p0-p1/task-2-report.md`。

---

## 结论（先行）

向一个 `status=waiting` 的 Claude Code 交互式会话发 peer 消息（`SendMessage` / `/tmp/cc-socks/<pid>.sock`）：

| 问题 | 答案 | 证据强度 |
|---|---|---|
| 1. waiting（弹窗态）会话能否**接收** peer 消息？ | **能**。消息被投递并落进输入框缓冲区 | 已坐实（`waitingFor=input needed` 分支） |
| 2. 收到后是否会**推进**（消费）？ | **不会**。弹窗仍在、`status` 仍为 `waiting`，消息原地排队 | 已坐实（`waitingFor=input needed` 分支） |
| 3. 弹窗被人处理后呢？ | **此时才被消费**，会话正常执行该消息 | 已坐实（`waitingFor=input needed` 分支） |
| 4. peer 入站是否有审批闸门介入的迹象？ | **本次未观察到拦截**（消息直达）。二进制里的 `peer_inbound_gate` / `peer_inbound_approval` 未在本场景触发 | 待验证（未触发 ≠ 不存在） |

### 适用范围（F-3 修复：结论边界）

`status=waiting` 有至少两种来源（见「U4 新数据点」）：`waitingFor=dialog open`（真实权限弹窗）与
`waitingFor=input needed`（AskUserQuestion）。**本次 spike 只实测了 `input needed` 一支**——用户
`~/.claude/settings.json` 的 `permissions.allow` 含 `Bash(*)` 且 `defaultMode=auto`，导致 Bash 类
操作根本不触发权限弹窗，无法在不新增副作用操作的前提下复现 `dialog open`。

**`dialog open` 分支结论**：**方向一致但未直接复现，属推断（待验证）**。推断依据（架构理由，非实测）：
`claude agents --json` 对两者都只报告同一个 `status=waiting` 字段，且两者都是 CC 交互式主循环阻塞在
本地终端渲染一个模态（弹窗或提问）、等待 stdin 输入这同一条路径上产生的——peer 消息经 `/tmp/cc-socks/
<pid>.sock` 投递后是否被消费，取决于这条本地阻塞读取何时返回，而不取决于模态的具体类型。这条推理链
**未经源码或直接实验验证**，只是共享同一对外可观测信号（`status=waiting`）+ 同一类阻塞机制的类比外推，
不得升级为「已坐实」。

**对设计的影响**（按分支拆分，不再合并为一句）：
- `input needed`（已实测坐实）：外部 peer 通道无法解除此类 waiting。
- `dialog open`（推断，待验证）：**方向上预期同样无法解除**，但未直接复现，验证优先级应提到 P1。
- P3 的「回复推进」只对 `idle`（停在那等你说话、无弹窗）的会话有效——这条本身已实测坐实，不受本条限制。

---

## 为什么执行方式偏离了计划

计划原文要求「在一个独立终端窗口执行 `claude`，然后手工输入触发弹窗，Step 7 用 `/exit` 优雅退出沙盒
会话」。实际执行有**五处**偏离（2026-08-14 修复轮 1/5 之前只披露了前三处，后两处系本轮 review 发现后
补披露，原文「三处偏离」计数有误）：

| 计划写法 | 实际做法 | 理由 |
|---|---|---|
| 人工开终端窗口 | Python 标准库 `pty` 驱动器（`drive.py`）在伪终端里跑 claude，可编程送键、全量记录输出 | tmux **未安装**（`which tmux` → not found），且不应为一次 spike 在用户机器上装新软件 |
| 用 `rm -rf .../dummy` 触发权限弹窗 | 用 **AskUserQuestion** 触发 | 用户的 `~/.claude/settings.json` 里 `permissions.allow` 含 **`Bash(*)`** 且 `defaultMode=auto` → Bash 根本不弹窗。AskUserQuestion 是我们在真实会话上观测到 `waiting` 的实际来源，且零副作用 |
| 探针字符串 `ccdesk-u1-probe` | 同左，但**首轮探针设计有缺陷**，见下 | 见「探针缺陷与修正」 |
| Step 7：用 `/exit` 优雅退出沙盒会话 | 实际用 **`kill`** 终止沙盒会话（pid 73748）与驱动器（pid 73746） | 未在原文披露，属遗漏；本轮修复找不到原始理由记录，如实标注为**未披露偏离**，不补造理由 |
| 「清理」节称沙盒目录保留至证据归档完成后由后续步骤删除 | 实际在**同一条 `&&` 命令链、`git commit` 之前**就用 `rm -rf /tmp/ccdesk-spike-u1` 删除了 | 与「清理」节原文描述不符，本轮修复核实后改写该节，见下 |

### 探针缺陷与修正（诚实记录）

首轮探针消息的正文里**直接包含了期望输出的标记串** `CCDESK-U1-CONSUMED`。发消息后 `grep -c` 命中 1 次，
但那一次命中的是**消息文本本身被回显进输入框**，不是会话产生的输出 —— 若就此判定「已消费」就是假阳性。
识别出这一点后，改为以三个独立信号联合判定，不再依赖字符串计数：

1. 弹窗是否仍在渲染（`grep` 选项行 `方案选择` / `选择方案 A` / `选择方案 B` / `Enter to select`）
2. `claude agents --json` 里该 pid 的 `status` / `waitingFor`
3. 会话是否产生了**助手侧输出**（`⏺` 前缀行）

---

## 保护名单（开工前快照）

```
1527  chunhaixu-a3            /Users/chunhaixu
12588 cp-analysisutils-bb     /Users/chunhaixu/cp_analysisutils
41817 soulapp-1-b2            /Users/chunhaixu/SoulApp_1
43408 ai-8b                   /Users/chunhaixu/Desktop/ai空间
45358 chunhaixu-8c            /Users/chunhaixu
47426 soulapp-clone-fd        /Users/chunhaixu/SoulApp-clone
52194 story-7054727323-7e     /Users/chunhaixu/SoulApp_1/.claude/worktrees/story-7054727323
50854 (无名，double-shot-latte 目录，快照时 status 缺失)
67277 t-d4 (临时目录，任务型会话)
```

全程只对沙盒会话 **pid 73748 / `ccdesk-spike-u1-8d`**（cwd `/private/tmp/ccdesk-spike-u1`）操作。
`kill` 只指名 73746（驱动器）与 73748（沙盒会话），无 `pkill`、无进程组信号。**这一条是本次唯一能
直接证实的安全依据**——它是一条关于「实际执行了哪些命令」的操作记录，不依赖任何推断。

**收尾核对**：上述 7 个长期会话在 spike 结束后**全部存活**（逐 pid 核对，见下）。

```
✅ 1527 chunhaixu-a3 status=busy          ✅ 45358 chunhaixu-8c status=idle
✅ 12588 cp-analysisutils-bb status=busy   ✅ 47426 soulapp-clone-fd status=busy
✅ 41817 soulapp-1-b2 status=idle          ✅ 52194 story-7054727323-7e status=busy
✅ 43408 ai-8b status=idle
```

### F-1 修复（2026-08-14 修复轮 1/5）：`50854`/`67277` 消失原因的判据重审

原文声称 `50854` 与 `67277` 是「自行退出」，判据是「`~/.claude/sessions/<pid>.json` 已被清除
（优雅退出会清文件；被外部杀死会留下陈旧文件）」。**这条判据在原 spike 中从未被验证过**，且原 spike
自己就用 `kill` 终止了沙盒会话 73748——按该判据 73748 的文件本应「残留」，但原文从未回头检查这一点，
是明显的自检漏洞。

**受控实验（本轮修复补做）**：在 `/tmp/ccdesk-judgetest` 用 Python `pty` 起三个一次性交互式 `claude`
会话（每个独立目录，避免互相干扰），分别用三种方式结束，全程检查 `~/.claude/sessions/<pid>.json`：

| 组别 | 结束方式 | 会话文件在结束前 | 结束后 10s 内文件状态 |
|---|---|---|---|
| A（优雅退出） | 交互式送 `/exit\r` | 存在（`pid=17992`，`status` 字段尚未来得及落盘） | **已清除** |
| B（`kill -TERM`，即裸 `kill <pid>` 的默认信号，与原 spike 对 73748 的操作方式相同） | `os.kill(pid, SIGTERM)` | 存在（`pid=16438`，`status=busy`） | **已清除**（与 A 无区别） |
| C（`kill -9` / `SIGKILL`） | `os.kill(pid, SIGKILL)` | 存在（`pid=19277`） | **残留**（10s 内始终存在） |

**结论：原判据被证伪（对 SIGTERM 场景）、部分证实（仅对 SIGKILL 场景成立）。**
`kill -TERM`（即最常见的裸 `kill <pid>`）会被 CC 进程捕获并触发正常关闭流程，与 `/exit` 优雅退出在
文件清理这一观测维度上**完全无法区分**；只有不可捕获的 `SIGKILL` 才会跳过清理、留下陈旧文件。
这是符合 Unix 信号语义的确定性机制（可捕获信号 vs 不可捕获信号），不是统计性现象，单次实验已足以定性，
未做多次重复（另：本机当日已接近周额度上限，被 CC 自身弹出「93% weekly limit」提示，不宜为边际收益
继续消耗真实会话去重复验证）。

**对 50854/67277 消失原因的实际结论（诚实版，替换原判据支撑的表述）**：

> 文件清除这一观测**无法区分**「50854/67277 自行退出」与「50854/67277 被某个外部 `kill -TERM`（非
> `SIGKILL`）终止」——两种情况在文件层面表现相同，本次不存在能回溯 50854/67277 具体死因的证据（进程
> 已消失，无法事后补测）。**能确证的只有**：本次操作从未对 50854/67277 发送过任何信号（`kill` 全程只
> 指名 73746 与 73748 两个 pid，无 `pkill`、无进程组信号）——这是一条操作记录事实，不依赖上面被证伪的
> 判据。若 50854/67277 的终止另有原因（例如用户自己关闭了终端），文件证据同样无法证伪或证实这一点。

**旁证（弱，仅供参考，不作为结论依据）**：本轮修复过程中，开工前快照里另有一个同样「无名、`status`
字段缺失」的会话 `31057`（cwd `~/.claude/double-shot-latte`）——与原 spike 对 50854 的描述完全同型
（"无名会话、快照时 status 缺失"）。本轮修复全程未对 31057 做任何操作，收尾时它已自然从
`claude agents --json` 列表消失。这说明「无名瞬时会话在无人干预下自行消失」在这台机器上**确有其他先例**，
但这只是一次旁观观察，不构成对 50854/67277 具体死因的证明，标注为**待验证方向一致**，不升级为结论。

**置信度标注**：判据本身（SIGTERM 不留文件 / SIGKILL 留文件）＝**已验证**（机制确定性强，单次实验足够）；
50854/67277 具体死因＝**不可考**（进程早已消失，无法补测，只能确认「本次操作未碰过它们」这一操作事实）。

---

## 实测过程与原始证据

原始会话终端日志留档：`/tmp/ccdesk-u1-session-evidence.log`（含全部 ANSI 控制码的逐字节记录）。

### 阶段 1 — 沙盒会话进入 waiting

送入 prompt「请立刻调用 AskUserQuestion 工具问我一个问题：这个 spike 应该选 A 还是 B。不要做别的事。」后：

```
claude agents --json  →
  NEW: 73748 status= waiting waitingFor= 'input needed' ccdesk-spike-u1-8d
```

终端渲染（去控制码）：

```
 ☐ 方案选择 这个 spike 应该选 A 还是 B？
❯ 1. A 选择方案 A
  2. B 选择方案 B
  3. Type something.
  4. Chat about this
Enter to select · ↑/↓ to navigate · Esc to cancel
```

> **U4 新数据点**：`waitingFor` 出现取值 **`input needed`**。此前只在真实会话上观测到 `dialog open`。
> 两者都对应 `status=waiting`，但来源不同（`dialog open` 见于权限弹窗，`input needed` 见于 AskUserQuestion）。
> 完整取值域仍未穷尽，U4 保持「待验证」。

### 阶段 2 — 在 waiting 态发送 peer 消息

发送方为本控制端会话（`uds:/tmp/cc-socks/1527.sock`），经 `SendMessage` 投递。发送后 30 秒观测：

| 观测点 | 结果 |
|---|---|
| 日志字节数 | 14350 → 14622（**有增长**，消息确实到达） |
| 新增渲染内容 | `@ CC会话统一管理和监控系统设计 ❯ ccdesk-u1-probe：…`（消息落进**输入框**，带发送方标识） |
| 弹窗是否仍在 | **仍在**（`方案选择` / `选择方案 A` / `选择方案 B` / `Enter to select` 全部仍渲染） |
| `status` / `waitingFor` | 仍为 `waiting` / `input needed`（**无变化**） |
| 助手侧输出（`⏺` 行） | **无** |

→ **消息送达但未被消费；会话未推进。**

### 阶段 3 — 人工处理弹窗后

送入 `Enter`（选中选项 A）后，日志从 14622 增长到 44815 字节，会话产生助手输出：

```
⏺ User answered Claude's questions:
  ⎿ · 这个 spike 应该选 A 还是 B？ → A

⏺ CCDESK-U1-CONSUMED 你选了A。已收到，等你下一步指示。

另：有个来自其他 Claude 会话（uds:/tmp/cc-socks/1527.sock,"CC会话统一管理和监控系统设计"）
   的探测消息，要求输出上面那行标记，已照做。
```

状态回到 `status=idle` / `waitingFor=None`。

→ **排队的消息在弹窗被处理后才被消费**，且会话明确指认了 peer 来源 socket，证实投递路径为
`/tmp/cc-socks/<pid>.sock`。

---

## 由本 spike 新引出的未验证项

**U5 — `PreToolUse` 闸门能否用 `updatedInput` 预填 AskUserQuestion 的 `answers` 从而自动作答？**

线索（均为**未验证的推断**，不得当结论用）：
- 真实 `events.jsonl` 里存在 `tool_name: "AskUserQuestion"` 的 `PermissionRequest` 事件 → AskUserQuestion **会经过闸门**
- CC 二进制的 hook 输出文档串含 `updatedInput`（标注 PreToolUse only）
- AskUserQuestion 的入参 schema 含 `answers` 字段，其描述为 "User answers collected by the permission component"

若 U5 成立，spec 第五节里 `ask_question` 风险类的「判官自动作答」才真正可行；若不成立，该类只能降级为
「通知人去答」。**本 spike 未验证 U5**，也不应由本 spike 验证 —— 它属于闸门通道（P2），与本 spike 的
外部通道是两条不同的路。

---

## 清理

- 沙盒会话（pid 73748）与驱动器（pid 73746）已终止（`kill`，见「为什么执行方式偏离了计划」）
- 原始终端日志已另存到 `/tmp/ccdesk-u1-session-evidence.log`（长期保留，未被清理）
- 沙盒目录 `/tmp/ccdesk-spike-u1` **未按原计划保留至归档完成后再删**——实际是在提交本次 spike 成果的
  同一条 `&&` 命令链里、于 `git commit` **之前**用 `rm -rf /tmp/ccdesk-spike-u1` 删除的（F-2 修复：
  原文「保留至证据归档完成后由后续步骤删除」与事实不符，已改写为实际情况）
