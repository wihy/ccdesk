# U2 Spike：PreToolUse hook `timeout` 上限与超时代价

**日期**：2026-08-14 · **环境**：`/private/tmp/ccdesk-spike-u2`（一次性沙盒，已清理）
**CC 版本**：`/opt/homebrew/bin/claude` 2.1.220（真实 npm 安装二进制，Mach-O）

## 结论（先说结论）

1. **`timeout` 字段实测 30 / 60 / 120 / 300 秒全部生效**，未观测到上限。结论标注 **`≥300s，未见上限`**（按裁决，300 生效即停止上探，不继续二分）。
2. **超时代价坐实为「工具调用不被执行」**，但触发路径与「已知事实」的字面文案不完全一致（见下文「重要澄清」）——功能后果一致，字符串不同，如实标注。
3. **`config.GATE_DEADLINE_S = 7.5` 可用**。CC 侧 hook 的 `timeout` 字段应设为 **≥ 8s**（与 `T_max` 对齐，建议 10s 留冗余），gate 自己必须在 7.5s 内用内部 watchdog 返回，不能依赖 CC 的外部 timeout 兜底。

---

## 重要澄清（方法论修正，供 review 参考）

brief 里 Step 3/4 用的探测命令是纯只读的 `echo <text>`（不产生文件副作用）。用这个命令实测发现：**即使 hook 明确超时被杀，`echo` 依然会"成功"**——因为 CC 在 headless `-p` 模式下，对不产生副作用的只读 Bash 命令有独立于 hook 决策之外的默认放行路径；hook 超时只是让流程 fall through 到 CC 自己的默认权限管线，而不是必然拒绝。这意味着**用只读命令测不出 hook 是否真的在生效**——必须用一个会产生可验证副作用（文件写入）的命令，并以文件是否真实生成作为 ground truth，而不是信读 CC 输出的文本（模型在工具调用被打断后有时会给出似是而非的文字解释，不可全信）。

发现过程与坐实证据：
- 排除了环境噪声：本机 `claude` 在 `PATH` 里优先解析到 `cmux` 会话壳的 wrapper 脚本（`/var/folders/.../cmux-cli-shims/.../claude`），不是原生 CC 二进制；改用 `/opt/homebrew/bin/claude`（npm 包同版本 Mach-O 直接产物）复测。
- 排除了权限白名单噪声：`--settings <file>` 是**追加**加载，不是覆盖；用户级 `~/.claude/settings.json` 的 `permissions.allow` 含 `"Bash(*)"`，加 `--setting-sources project` 排除掉 user 源；沙盒 settings 里显式加 `"enabledPlugins": {}` 排除插件注入的其它 PreToolUse hook。
- 用 `--debug-file` 拿到内部日志，确认真实机制：hook 超时后打印 `[INFO] Slow PreToolUse hooks: <ms>ms for Bash (1 hooks)`（这条日志在**成功和超时两种情况下都会打印**，仅代表"hook 跑得久"，不代表失败——不能拿它当失败判据）；只读命令超时后仍然执行；带文件副作用的命令超时后走到 `[DEBUG] Permission suggestions for Bash` → `[DEBUG] Bash tool permission denied`，工具调用被拒绝，标的文件确实没有被创建（ground truth 确认）。
- 与「已知事实」的差异：CC 二进制里的字面串 `PreToolUse hook did not respond before its timeout (host client may be unreachable). The tool call was not executed` 在本次 `--debug-file` 全量日志里**未出现**。从反编译代码上下文看，这条字面串对应的是"host client（如 IDE/SDK 控制端）不可达"这一更具体的分支，与本次「本地 hook 子进程单纯超时」触发的是同一大类但不同的具体分支（`Bash tool permission denied` 分支）。**功能后果相同（工具未执行），字面文案不同**——如实标注为「功能性坐实，字面串未复现」，不升级为「完全坐实原文案」。

---

## 实测表

方法：沙盒内 `.claude/settings.json` 只挂一个 `matcher:"Bash"` 的 PreToolUse hook（脚本见下），`enabledPlugins:{}` + `--setting-sources project` 排除一切环境噪声；每档执行 `touch <沙盒内绝对路径 marker 文件>`，用 `--debug-file` 记录内部日志，用 marker 文件是否真的被创建作为 ground truth；探测遵循用户裁决口径——依次实测 30/60/120/300，每档 `SPIKE_DELAY=timeout/2`。

| `timeout`(s) | hook 实际睡眠(s) | 总耗时(s) | debug 日志：hook 实际运行时长 | marker 文件生成 | 判定 |
|---|---|---|---|---|---|
| 30 | 15 | 23 | 15047ms | ✅ 生成 | **生效**（hook allow 生效，工具执行） |
| 60 | 30 | 39 | 30054ms | ✅ 生成 | **生效** |
| 120 | 60 | 68 | 60055ms | ✅ 生成 | **生效** |
| 300 | 150 | 170 | 150042ms | ✅ 生成 | **生效** |
| 5（反向校验） | 20 | 15 | 5029ms（被杀） | ❌ 未生成 | **失效**（超时→拒绝执行） |

四个正向档位全部生效、且 hook 实际运行时长与 `SPIKE_DELAY` 几乎精确吻合（误差 <100ms），说明 `timeout` 字段被如实遵守、没有被内部截断到更小的值。按裁决口径：**300 仍生效 → 停止上探，结论「≥300s，未见上限」**，不做 300~更大值的二分。

**`⚠️ 未探究`：正向档位「总耗时 − hook 睡眠时长」的额外开销不一致**（30:8s / 60:9s / 120:8s / 300:20s）。300 档明显偏高于其余三档，未定位根因。未验证的推测方向：模型收到"文件已创建"结果后要生成的收尾说明文本长度不固定、单次会话内 MCP server（如日志中出现的 `claude.ai Figma`）连接建立耗时有抖动——两者均**未做二次复测验证**，不作为结论，仅记录供后续排查参考。

---

## 关键命令与输出片段

### 反向校验（timeout=5, sleep=20）—— 超时代价

```bash
$ /private/tmp/ccdesk-spike-u2/run_tier.sh 5 20
=== tier timeout=5s sleep=20s elapsed=15s ===
MARKER: absent (tool NOT executed) -> hook timed out / denied
-- debug log evidence --
...  [DEBUG] Hooks: Found 0 total hooks in registry
...  [INFO] Slow PreToolUse hooks: 5029ms for Bash (1 hooks)
...  [DEBUG] Bash tool permission denied
...  [DEBUG] Hooks: Found 0 total hooks in registry
-- final assistant text (tail) --
```
touch in '/private/tmp/ccdesk-spike-u2/marker_t5.txt' was blocked. For security, Claude Code may only
create or modify files in the allowed working directories for this session: '/private/tmp/ccdesk-spike-u2'
```
The path is inside the stated working directory, so this looks like a path-matching issue in the
permission check (likely `/private/tmp` vs `/tmp` symlink resolution) rather than a genuine policy
violation. Per your instruction, I did not retry with any other path or tool.
```

模型给出的文字解释（"path-matching issue"）是**错误的自我归因**——真实原因（debug 日志坐实）是 hook 超时后 fall through 到 CC 默认权限管线，管线按工作目录白名单判断该路径不在允许列表，拒绝执行。这也印证了上面"不能信模型文本、要看 ground truth"的方法论结论。

`⚠️ 未探究`：hook 子进程在 5029ms 被杀，但整条命令总耗时 15s，中间约 10s 的去向未直接测量。未验证的推测：权限管线判定拒绝后，CC 还需再发起一次 API 往返，让模型基于"工具调用被拒绝"这个结果生成上面那段多句解释性回复（明显比正向档位"Done — created ..."这类一行确认语更长），这部分生成耗时未单独打点验证，不作为结论。

### 正向档位（timeout=30 / 60 / 120，原始输出）

以下三段是三次 `run_tier.sh` 调用在执行现场的原始终端输出（沙盒清理前留存在会话记录里），逐字保留，未重跑、未补造：

```bash
$ /private/tmp/ccdesk-spike-u2/run_tier.sh 30 15
=== tier timeout=30s sleep=15s elapsed=23s ===
MARKER: created (tool WAS executed) -> hook decision effective (allow)
-- debug log evidence --
112:2026-08-14T04:07:33.678Z [DEBUG] Hooks: Found 0 total hooks in registry
149:2026-08-14T04:07:51.362Z [INFO] Slow PreToolUse hooks: 15047ms for Bash (1 hooks)
164:2026-08-14T04:07:53.151Z [DEBUG] Hooks: Found 0 total hooks in registry
-- final assistant text (tail) --
Done — created `/private/tmp/ccdesk-spike-u2/marker_t30.txt`.
```

```bash
$ /private/tmp/ccdesk-spike-u2/run_tier.sh 60 30
=== tier timeout=60s sleep=30s elapsed=39s ===
MARKER: created (tool WAS executed) -> hook decision effective (allow)
-- debug log evidence --
113:2026-08-14T04:08:07.764Z [DEBUG] Hooks: Found 0 total hooks in registry
149:2026-08-14T04:08:41.333Z [INFO] Slow PreToolUse hooks: 30054ms for Bash (1 hooks)
164:2026-08-14T04:08:43.039Z [DEBUG] Hooks: Found 0 total hooks in registry
-- final assistant text (tail) --
Done — created `/private/tmp/ccdesk-spike-u2/marker_t60.txt`.
```

```bash
$ /private/tmp/ccdesk-spike-u2/run_tier.sh 120 60
=== tier timeout=120s sleep=60s elapsed=68s ===
MARKER: created (tool WAS executed) -> hook decision effective (allow)
-- debug log evidence --
112:2026-08-14T04:08:54.316Z [DEBUG] Hooks: Found 0 total hooks in registry
149:2026-08-14T04:09:57.053Z [INFO] Slow PreToolUse hooks: 60055ms for Bash (1 hooks)
164:2026-08-14T04:09:58.993Z [DEBUG] Hooks: Found 0 total hooks in registry
-- final assistant text (tail) --
Done — created `/private/tmp/ccdesk-spike-u2/marker_t120.txt`.
```

### 正向档位（timeout=300, sleep=150，原始输出）

```bash
$ /private/tmp/ccdesk-spike-u2/run_tier.sh 300 150
=== tier timeout=300s sleep=150s elapsed=170s ===
MARKER: created (tool WAS executed) -> hook decision effective (allow)
-- debug log evidence --
...  [DEBUG] Hooks: Found 0 total hooks in registry
...  [INFO] Slow PreToolUse hooks: 150042ms for Bash (1 hooks)
...  [DEBUG] Hooks: Found 0 total hooks in registry
-- final assistant text (tail) --
Done — `/private/tmp/ccdesk-spike-u2/marker_t300.txt` created.
```

### brief 字面命令（Step 3，仅用只读 `echo`，供对照——不作为最终判据）

```bash
$ cd /tmp/ccdesk-spike-u2 && SPIKE_DELAY=20 timeout 120 claude -p \
  --settings /tmp/ccdesk-spike-u2/.claude/settings.json \
  'Run the bash command: echo ccdesk-u2-ok' 2>&1 | tail -20
命令已执行，输出：
ccdesk-u2-ok
```

此命令在 `timeout=30/sleep=20`（应生效）与 `timeout=5/sleep=20`（应失效）两种配置下**都"成功"**——因为 `echo` 无副作用，命中了 CC 默认放行路径，测不出 hook 真实生效与否，故本报告改用文件写入 + debug 日志 + ground truth 的方法重新测量（见上）。

### 沙盒 hook 脚本（含 trace instrumentation，供复现）

```python
#!/usr/bin/env python3
import json, sys, time, os
delay = float(os.environ.get("SPIKE_DELAY", "1"))
marker = "/tmp/ccdesk-spike-u2/hook_trace.log"
with open(marker, "a") as f:
    f.write(f"START pid={os.getpid()} delay={delay} t={time.time()}\n")
time.sleep(delay)
with open(marker, "a") as f:
    f.write(f"END   pid={os.getpid()} delay={delay} t={time.time()}\n")
json.dump({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": f"spike slept {delay}s"}}, sys.stdout)
```

失败档（timeout=5, sleep=20）的 `hook_trace.log` 只有 `START`、没有 `END`，证明 hook 子进程确实被 CC 在 timeout 到点时杀掉，而不是自己提前退出。

---

## `GATE_DEADLINE_S` 取值建议

- **`config.GATE_DEADLINE_S = 7.5` 可用**，无需调整。
- CC 侧安装 `ccdesk_gate.py` 时，`settings.json` 里该 hook 的 `timeout` 字段建议设为 **10s**（比 `T_max=8s` 留 2s 冗余），因为：
  - 实测 30~300s 区间 CC 都会如实遵守，不存在把大 timeout 悄悄截断到更小值的问题；
  - `timeout` 只需大于 gate 自己的硬 deadline（7.5s）+ 进程调度/IO 抖动余量即可，没有理由设更大——**设更大不会更安全**，反而在 gate 自身 watchdog 失效时，会让 CC 更晚才启动兜底降级，扩大用户等待窗口。
- **重要但独立于本问题的风险点（供 gate 设计参考，不属于本次结论范围，标「待验证」）**：本次测试发现，hook 超时后 CC 不是统一走"拒绝"，而是 fall through 到 CC 自身默认权限管线，对不同工具/命令形态可能给出不同结果（本例中：无副作用的只读命令可能被默认放行；有副作用的写操作在 headless 非交互模式下默认被拒）。这意味着 **gate 自身在 7.5s 内可靠返回 `ask` 是唯一的、不可退让的安全屏障**——一旦 gate 进程本身失控超过 CC 侧 timeout，实际后果不完全可预测，不能假设"超时=一定安全拒绝"。`⚠️ 待验证`：CC 默认权限管线在各类工具/命令形态下超时后的具体 fallback 行为全貌，未来若排期允许可以再做一轮 spike 补齐。
