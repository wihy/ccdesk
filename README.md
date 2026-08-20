# ccdesk — Claude Code 会话值班台

同时开着七八个 Claude Code 会话时，真正的问题不是它们跑得慢，而是**你不知道哪个已经停下来等你了**。
终端窗口叠在一起，某个会话弹了个权限确认或者问了你一个选择题，然后就一直挂在那儿——可能挂了两小时，
而你在另一个窗口里忙别的。

ccdesk 就为这件事：**谁在等你、等了多久、当时问的是什么、你处理完之后它到底跑没跑。**

```
$ ccdesk sessions
会话 8 个，等待中 1 个

●     waiting  soulapp-clone-9b   pid=16151  input needed
      busy     chunhaixu-22       pid=70282
      idle     theta-a0           pid=22270
      …

$ ccdesk recon
对账 5 条请求，异常 2 条（坏行 0）

dangling_request   047429fa21c97a3c  AskUserQuestion «…»  无决策  (78523s)
```

上面第二条是真实产出：两次 AskUserQuestion 发出后**没有任何后续**，一条挂了 21 小时。
不是猜的——事件流里确实没有对应的执行记录。

## 它是什么

一个只跑在本机的常驻服务，三个入口看同一份数据：

| 入口 | 形态 |
|---|---|
| **⌥⌘D** | 弹出面板：会话列表 / 对账异常（可展开看足迹）/ 自身健康 |
| 菜单栏徽标 | `CC 8`，有人在等时变 `CC ●1`，并弹系统通知 |
| CLI | `ccdesk sessions` / `recon` / `trace <id>` / `why <id>` / `gate` / `replay` |

数据来自两个**官方现成的源**，不做任何侵入式改造：

- `claude agents --json` —— 会话枚举与等待状态（含 `waitingFor`：是权限弹窗还是提问）
- `~/.adw/observability/claude/events.jsonl` —— Claude Code hook 事件流，授权请求的明细在这里

采集器把两者对齐成一本 append-only 账本，用 `req_id`（会话+提示+工具+参数指纹）做幂等键，
让「发起 → 决策 → 结局」三段能对上号。对账器再看这三段有没有闭合。

再加一个可选的**闸门**（`PreToolUse` hook，`ccdesk gate install` 装上）：会话弹选择题时先问
daemon 要个决定，能自动答就答、答不了就原样回落到你熟悉的终端弹窗。它永不 `deny`，
任何异常都降级成「问你」。默认不装 —— 装不装、什么时候装，是你的选择。

## 设计上的两个决定

**闸门只挂 AskUserQuestion，不挂全量工具。**
`PreToolUse` hook 理论上能拦每一次工具调用，但本机 `defaultMode=auto` 且
`permissions.allow` 含 `Bash(*)` —— 实测 events.jsonl 尾部 2000 行里 **PostToolUse 422 条、
PermissionRequest 0 条**，也就是除 AskUserQuestion 外没有任何工具会走到权限询问。
挂全量只会给每次工具调用白加一次本机 HTTP 往返，收益为零、爆炸半径却是你所有会话。
哪天收紧了 `permissions`，把 matcher 放宽即可（`ccdesk/gate_install.py:MATCHER`）。

**闸门永不 deny。**
三条铁律：永不 `deny`、永不非零退出、永不打印 traceback。任何异常路径（daemon 挂了、
返回垃圾、响应慢、判官没凭证）都自降级为 `ask`，把选择权交回给你。自降级线 7.5 秒由
内部 watchdog 保证——实测过 Claude Code 侧超时后的兜底行为**不保证拒绝**、且因命令形态
而异，所以不能指望它。「不确定就 ask」比「不确定就放行」重要得多：放错一次是替你做了
个你没做过的决定。

## 三个实测结论（写在 `spikes/`）

设计里每个关键假设都先做了实验，结论都改写了设计：

**U1：外部消息推不动 waiting 会话。** 向一个卡在弹窗上的会话发消息，消息会送达并落进输入框，
但**弹窗仍在、状态不变、不被消费**——直到你亲手点掉弹窗，它才被处理。
两个分支（`input needed` 与真实权限弹窗）现在都有直接实验支撑：2026-08-19 的补验里，
同一条消息在弹窗期间躺了 50 秒纹丝不动，弹窗一被点掉立刻被消费并回了约定的暗号——
这就排除了「消息根本没送达」这种平凡解释。所以程序化推进 `waiting` 会话的唯一入口是闸门。

> 顺带纠正一处口径：真实权限弹窗的 `waitingFor` 实测是 **`permission prompt`**，
> 不是早期文档写的 `dialog open`。ccdesk 只透传这个字段、不按值分支，所以功能不受影响。

**U2：hook timeout 上限 ≥300 秒。** 30/60/120/300 四档全部生效，未见上限。这条决定了闸门
7.5 秒的自降级线不会被 Claude Code 抢先打断。

**U5：闸门可以替你答选择题。** `PreToolUse` 返回 `allow` + `updatedInput`（内含预填的
`answers`）后，AskUserQuestion **不弹窗**，会话把注入的答案当作「用户已回答」直接消费。
`updatedInput` 必须放在 `hookSpecificOutput` **内**（已实测；放错则静默不生效）。
这条让闸门在这台机器上有了真正的用武之地——毕竟这里唯一会弹窗的工具就是它。

## 快速上手

### daemon（已由 launchd 常驻）

```bash
launchctl list | grep ccdesk      # 应看到 com.ccdesk.daemon 且带 PID
curl http://127.0.0.1:8787/health
# {"ok":true,"ts":"…","last_collect_ts":1786948905.47,"collect_age_s":2.16,
#  "collect_errors":0,"session_source":"cli"}
```

健康判据看三个字段：

| 字段 | 健康值 | 不健康说明 |
|---|---|---|
| `collect_age_s` | < 3s（采集线程每 3s 一轮） | 持续增长 = 采集线程已死，重启 daemon |
| `collect_errors` | 持续为 0 | 增长 = 采集抛异常，看 `logs/daemon.log` |
| `session_source` | `cli` | `file` = 主源 `claude agents --json` 不可用、已降级读文件（见故障排查 4）；`unknown` = 还没取过会话 |

> `collect_age_s` 由服务端算好（`time.time() - last_collect_ts`）。别拿 `ts`（ISO 串）去减 `last_collect_ts`（epoch 浮点）——量纲不同，减不了。

plist 在 `launchd/com.ccdesk.daemon.plist`（RunAtLoad + KeepAlive，开机自启、挂了自动拉起）。
**改 plist 后必须重新 bootstrap 才生效**：launchd 加载的是 `~/Library/LaunchAgents/` 下的副本，且
`kickstart -k` 只重启进程、不重读 plist：

```bash
cp launchd/com.ccdesk.daemon.plist ~/Library/LaunchAgents/
launchctl bootout   gui/$(id -u)/com.ccdesk.daemon
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ccdesk.daemon.plist
```

plist 里的 `PATH` 不能省：launchd 不继承登录 shell 的 PATH，默认只有 `/usr/bin:/bin:/usr/sbin:/sbin`，
里面没有 `claude`，会话主源会静默降级成文件真源。

### CLI 六命令

> 下方各命令的输出块均为**某次真实运行的样例**，数字随当时机器上的会话与账本变化，不是固定值。

```bash
~/ccdesk/bin/ccdesk sessions
```
```
会话 4 个，等待中 0 个

      busy     chunhaixu-d1             pid=1490  /Users/chunhaixu
      idle     chunhaixu-8c             pid=45358  /Users/chunhaixu
      busy     soulapp-clone-06         pid=7968   /Users/chunhaixu/SoulApp-clone
      idle     cp-analysisutils-4b      pid=80061  /Users/chunhaixu/cp_analysisutils
```

```bash
~/ccdesk/bin/ccdesk trace 2e4bdb5ed1ed3713   # 任一 req_id，来源见 recon 输出
```
```
req_id  2e4bdb5ed1ed3713
会话    4353e4c3-04c7-4fdc-86c4-cb98a720c5cc  /Users/chunhaixu/SoulApp-clone
工具    AskUserQuestion  «{"questions":[{"header":"异常表现", ...»

① 请求  2026-08-14T03:51:17.097205+00:00   auto
② 决策  —                                  None by None
③ 结局  2026-08-14T03:51:34.636628+00:00   executed
```
三段式时间线：请求（工具+参数摘要）→ 决策 → 结局。P1 没有决策写入方，② 恒为 `—`（见已知限制 1）；
③ 由 `PostToolUse`（`executed`）/ `PermissionDenied`（`denied`，附 `outcome_reason`）回填，真正悬空的请求 ③ 也是 `—`。

```bash
~/ccdesk/bin/ccdesk why dd4ac7a03c92118d
```
```
决定    ask
决定者  judge_unavailable
置信度  —
耗时    8 ms
```
P1 时期这里恒输出 `None`（无人写 decision）。P2 起由 daemon 落账，`决定者` 会告诉你
是哪一层做的决定：`guardrail:multiselect` / `cache` / `judge:haiku` / `judge:low_confidence` /
`judge_unavailable` / `daemon_error`。上面这条是本机的常态——判官没凭证，落回人工。

```bash
~/ccdesk/bin/ccdesk recon    # 授权闭环对账
```
```
对账 3 条请求，异常 2 条（坏行 0）

dangling_request   047429fa21c97a3c  AskUserQuestion «{"questions":[...]» 无决策  (9245s)
dangling_request   935c8425155cd84a  AskUserQuestion «{"questions":[...]» 无决策  (536s)
```
四类异常的可达性要分开说，别一句「全都可达」带过：

| 异常 | 判据 | 本机现状 |
|---|---|---|
| `dangling_request` | 有 request 无 decision >60s | ✅ 可达 |
| `silent_stall` | `decision=ask` 且会话仍 waiting >30min | ✅ **P2 解除**（decision 有写入方了） |
| `empty_allow` | `decision=allow` 但无 outcome >10min | ⚠️ 结构上已解除，但**本机仍不可达** |
| `duplicate_allow` | 同 req_id ≥2 次 allow | ⚠️ 同上 |

后两类都要求 `decision == "allow"`，而 allow 只能由判官（或判官写入的缓存）产生——
本机判官没有可用通道（见「已知限制」1），所以这两类**在配上 `ANTHROPIC_API_KEY` 之前
仍然打不着**。P1 时期是「四类只有一类可达」，现在是「两类可达、两类待判官上线」。

只看 24h 窗口内的请求，历史悬空不会永久重报。


```bash
~/ccdesk/bin/ccdesk gate status      # install / uninstall / status
```
```
闸门未安装  →  /Users/chunhaixu/.claude/settings.json
```
`install` 往 `~/.claude/settings.json` 的 `hooks.PreToolUse` 追加一条 matcher 为
`AskUserQuestion` 的记录，**幂等**（装两次不会出现两条）、**先备份**
（`settings.json.ccdesk-bak.<时间戳>`）、**解析不了就拒绝写**（宁可装不上也不能把你的配置搞没）。
`uninstall` 只删自己那条，绝不动 observe.py 之类别人的 hook。
**已经在跑的会话也会生效，不用重启** —— hook 是每次工具调用时读配置的，不是会话启动时
缓存的。实测证据：本机一个 8/17 14:06 起的会话，在 8/19 15:10 装上闸门后，16:03 的
AskUserQuestion 命中了闸门并被正确降级（账本里那条 `guardrail:multi_question`）。
（这里此前写的是「要重启才会加载」，是没验证就写下的，已按实测改正。）

```bash
~/ccdesk/bin/ccdesk replay --since=24h    # 改规则前的安全网
```
```
重放 1 条请求，决定会变的 0 条
```
拿历史请求跑一遍**当前**的护栏与判官，看哪些决定会变。`ask → allow` 方向会额外标
`⚠️ 规则放松`——那才是危险的方向（把过去你亲自把过关的东西自动放掉）。
只读，不写账本；用空缓存重放，免得历史缓存把结果染成 allow。


### 菜单栏 App

```bash
~/ccdesk/bin/ccdesk-app install     # swift build -c release → 打包装到 ~/Applications/CCDesk.app
~/ccdesk/bin/ccdesk-app start       # 装了 .app 就起 .app，没装则起裸可执行
~/ccdesk/bin/ccdesk-app stop        # 两种形态都能停
~/ccdesk/bin/ccdesk-app status      # App（两种形态分别报）+ daemon + health
```

装完也可以从启动台 / Spotlight 搜 `CCDesk` 打开。菜单栏徽标显示等待计数，点开看三区面板。
开机自启：系统设置 → 通用 → 登录项与扩展 →「登录时打开」里加 `~/Applications/CCDesk.app`。

App 有两种运行形态，**行为差别只在通知**（见已知限制 2）：`.app` 有 bundle identifier，通知可用；
裸可执行（`.build/release/CCDesk`）没有，通知被守卫跳过。两者会抢同一个 status item，所以
`start` 优先起 `.app`、`install` 会先停掉裸实例；`status` 把两种分别列出来，同时在跑就是需要处理的异常。

**⌥⌘D 唤出面板** —— 不依赖徽标是否可见的入口。菜单栏空间不足时 macOS 会**静默隐藏**排在后面的
第三方 status item：本机是刘海屏 + 十几个常驻应用（Lark / Figma / cmux / aTrust…），CC 徽标就这么
没了，且这是环境问题、改代码救不回来。所以热键路径不锚那个已被隐藏的 button，而是在**鼠标所在
那块屏**的右上角（菜单栏下方）放一个 1×1 透明窗口当锚点。写死 ⌥⌘D，不做自定义。
用 Carbon `RegisterEventHotKey` 实现——它是唯一不需要辅助功能（Accessibility）授权的全局热键方案。

组合被别的 App 占用时注册会失败（返回 false），此时前台跑一次能看到 `registered=false`
（`open` 起的进程 NSLog 不落盘，只有前台跑才看得到这行）：

```bash
~/Applications/CCDesk.app/Contents/MacOS/CCDesk     # 前台跑，读启动日志
# CCDesk hotkey opt-cmd-D registered=true
```

**图标**：`Icon/build-icon.sh` 从 SVG 现生成 `AppIcon.icns`，`build-app.sh` 打包时自动调用
（缺 `rsvg-convert` 就用已提交的 `.icns` 兜底，不阻断构建）。两套 SVG 源码不是冗余：
`icon-full.svg` 给 128px 以上，`icon-small.svg` 给 16/32px —— 细行缩到 16px 实测糊成一片，
所以小尺寸简化成「一条会话行 + 一个光标」。改完图标记得 `touch` 一下 .app 让 Finder 重读，
否则看到的还是缓存的旧图标。

**签名**：`build-app.sh` 末尾做一次 ad-hoc 重签（`codesign --sign - --identifier com.ccdesk.app`），
不是开发者签名/公证。这步不能省：swift build 产物的签名标识是 `CCDesk`，与 `CFBundleIdentifier`
不符时 `usernotificationsd` 直接拒授权（日志 `requestAuthorization not allowed: com.ccdesk.app`）。
没有开发者证书，所以 .app 若被拷贝/下载带上 quarantine 标记会被 Gatekeeper 拦，
在 Finder 里右键 →「打开」放行一次即可（`build-app.sh` 就地生成的产物无 quarantine，实测直接打开）。

## `~/.ccdesk/` 文件说明

| 文件 | 含义 |
|---|---|
| `ledger.jsonl` | 请求/决策/结局账本（append-only，req_id 追加式合并） |
| `ledger.bad.jsonl` | 坏行隔离区；**文件不存在 = 尚无坏行**（首条坏行出现才创建） |
| `logs/daemon.log` | daemon 运行日志（含每轮采集统计） |
| `logs/stdout.log` `logs/stderr.log` | launchd 重定向的标准输出/错误 |
| `collector.state.json` | 采集断点（events.jsonl 的 offset+inode，重启后只读新行） |

数据真源是 `~/.adw/observability/claude/events.jsonl`，账本是它的投影。

**重建有边界，不是无损的。** collector 只读**当前**那一个 `events.jsonl`，从不读轮转档
（磁盘上另有 `.1`~`.6` 六个归档，按 ~67MB 轮转，间隔从半天到九天不等）。所以删掉 `~/.ccdesk/`
重启 daemon，只能恢复**当前档时间范围内**的请求与结局，更早的一律不可恢复。
P2 之后重建会丢更多：`decision` / `decided_by` 是闸门写的，events.jsonl 里根本不存在，
删了就再也回不来。真要重建，先备份 `ledger.jsonl`。

## 故障排查

**1. daemon 不起 / 不采集**
`launchctl list | grep ccdesk` 看 PID 是否存在 → 看 `~/.ccdesk/logs/stderr.log` → `/health` 的 `collect_age_s` 若持续增长（远超 3s）说明采集线程已死，需重启 daemon。

**2. App 徽标显示 ⚠︎ / 面板空白**
面板每 3s 并发拉三个接口：`/sessions`（会话区）、`/recon/auth`（异常区）、`/health`（底部健康条）。
徽标 ⚠︎ 由 `/sessions` 失败触发；另两个失败只会让对应区块空着，徽标仍正常。

逐个 curl 定位是哪个挂了：

```bash
curl -s http://127.0.0.1:8787/sessions   # 挂了 → 徽标 ⚠︎
curl -s http://127.0.0.1:8787/recon/auth # 挂了 → 异常区空
curl -s http://127.0.0.1:8787/health     # 挂了 → 健康条空
```

注意 `/sessions` 是**唯一会 fork `claude` 子进程**的路由（实测 240~760ms），它可能在 daemon 本身
活着、另两个接口都正常时单独失败——所以「徽标 ⚠︎ 但 `/health` 返回 ok」是可能的，不矛盾。

**3. 账本有坏行**
坏行不会污染正常数据：被移入 `ledger.bad.jsonl` 并计数。`recon` 输出的「坏行 N」即暴露口；N 增长说明上游 events.jsonl 出现了非预期内容，去 bad.jsonl 里看原文。

**4. `/health` 的 `session_source` 是 `file`**
会话主源 `claude agents --json` 取不到，已降级读 `~/.claude/sessions/*.json`。降级态的输出与主源实测一致，功能不受影响，但要查为什么：`logs/daemon.log` 里有一条 warning 写着降级原因（只在状态**变成** file 时记一条，避免 3s 一次刷爆日志）。已知两个原因：**① plist 缺 `PATH`**（见「daemon」一节）；
**② 机器负载高时 `claude agents --json` 超过 daemon 的 10s 超时**——本机在同时起新会话时
实测到过（`TimeoutExpired: Command '['claude','agents','--json']' timed out after 10.0 seconds`）。
第二种是暂时的，负载回落后 `session_source` 会自己变回 `cli`，期间 `/sessions` 输出与主源一致、
功能不受影响。

**5. 某条请求永远没有 `outcome`**
先比对该工具在 `PermissionRequest` 与 `PostToolUse` 两侧的 `tool_input` 键集。CC 会在执行前改写 `tool_input`（已知：`AskUserQuestion` 的结局侧会多出 `answers` / `annotations`），键集不同则两侧指纹不同、`req_id` 对不上。把新发现的差异键补进 `ccdesk/ledger.py` 的 `VOLATILE_INPUT_KEYS`（按工具名，不要无差别丢键）。

## 已知限制

1. **判官在本机没有可用通道，所以自动代答实际不会发生** —— 判官只认 `ANTHROPIC_API_KEY`
   这一条路，而本机没配（也没有 `apiKeyHelper`）。另一条看似可行的 `claude -p --model haiku`
   实测**单次 42.5s / 42.8s**（两次，含空 settings + 禁 MCP 的最小配置），远超闸门 7.5s 的
   自降级线，塞不进去。于是每条请求都走 spec §9 的降级路径，`decided_by` 如实写
   `judge_unavailable`，回落终端原生弹窗——**等于装了闸门但行为与没装一样**。
   配上 `ANTHROPIC_API_KEY` 后判官自己就活了，代码路径已测（14 条用例）。
2. **自动代答只覆盖「单问题 + 单选 + 合法 label」** —— 这是 U5 唯一实测过的形态。
   多问题 / `multiSelect:true` / 无 options / 判官答了个选项外的字符串，一律降级 `ask`。
   这是有意为之：注入一个不存在的选项等于**伪造用户意图**，比不自动化糟得多。
3. **child session 是采集盲区** —— 带 `CLAUDE_CODE_CHILD_SESSION=1` 的会话不产生
   events.jsonl 事件（实测：本机 10 个会话中 8 个正常采集，该类会话在当前档中 0 条）。
   闸门对这类会话是否生效**未验证**。
4. **通知可用性取决于运行形态** —— `.app`（`ccdesk-app install`）下
   `Bundle.main.bundleIdentifier` = `com.ccdesk.app`，代码里那两处 bundle 守卫放行，通知授权
   实测已授予（系统日志 `didGrant: 1 hasError: 0`）；裸可执行（`ccdesk-app start`）没有
   app bundle、`bundleIdentifier` 是 nil，守卫直接跳过通知，**仍不可用**。徽标与面板两种形态
   都正常。「会话转 waiting 时通知真的弹出来」已人工核对通过。
5. **两处「大账本才会疼」的性能债，已知未修** —— `read_merged(since_ts=...)` 的过滤
   发生在 `read_text()` 与逐行 `json.loads` **之后**，省下的只有 dict 合并，并没有真正
   少读磁盘；而 `/decide` 每次都做全量 `read_merged` 只为取一个整数，偏偏它是闸门
   7.5s 预算内唯一延迟敏感的路径。当前账本 11KB，读一次是微秒级，所以没动——
   正确的修法（seek-to-tail 或维护索引）要两条一起做。
   **触发条件**：`~/.ccdesk/ledger.jsonl` 超过 10MB，或 `ccdesk why` 的「耗时」稳定超 50ms。
6. **`replay` 不调判官，只跑护栏** —— 重放要回答的是「规则改了会不会放松决定」，
   而 LLM 是不确定的，走判官会让同一条记录两次重放给出不同结果，那这个问题就没法回答了。
   所以判官那层如实标注 `judge_skipped_in_replay`，不假装判过。副作用是重放也不会
   产生 API 费用。
7. **`replay` 只能重放 P2 之后的请求** —— 重放需要完整 `tool_input`，而 collector 那侧
   只存 `input_digest` 摘要（体积/隐私考虑）。P2 起由闸门在落账时补上这份原始输入，所以
   P1 时期的老记录重放不了，会被跳过而不是编造。

## P2 已交付

| 项 | 状态 |
|---|---|
| 闸门安装通道（`ccdesk gate install/uninstall/status`） | ✅ 幂等 + 自动备份 + 只删自己 |
| `updatedInput` 代答 | ✅ 沙盒实测坐实（会话从未弹窗，见下） |
| 四层决策 + U5 四道护栏 | ✅ `guardrail → cache → judge → 降级`，永不 deny |
| decision writer + `allow_count` | ✅ `why` 不再恒 None；对账从 1/4 可达变成 2/4（另两类等判官） |
| `ccdesk replay` | ✅ 改规则前先看会把哪些 `ask` 变成 `allow` |
| ledger 大账本窗口读 / collector known 增量 | ✅ 阈值内行为与此前逐字节一致 |
| U1 权限弹窗分支复现 | ✅ 见 `spikes/u1-peer-advance.md` 补验节 |

**代答机制的实测证据**（`spikes/u5-updated-input-auto-answer.md`）：闸门返回
`allow` + `updatedInput`（`updatedInput` 必须放在 `hookSpecificOutput` **内**）后，
会话弹窗特征命中 **0 次**、直接输出 `User answered Claude's questions: · … → 选项A`，
全程无人触碰键盘。反之，`allow` 若**不带** `updatedInput`，CC 会直接丢弃这个 allow
（`if(!updatedInput && requiresUserInteraction()) return null`），工具就悬在那里——
所以闸门对 AskUserQuestion 遇到这种组合会自己降级成 `ask`，行为可预期。

## P3 展望

- 托管会话（csd / tmux 拉起）+ `idle` 会话的回复推进 —— `waiting` 会话已坐实推不动，
  但 `idle` 会话的 peer 推进可用
- 任务进展对账（停摆 / 跑偏 / 虚报完成三类）—— 需要先有 `tasks.json`
- 资源额度对账、与飞书项目的外部真源对账

## 测试

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python3 -m pytest -q    # 161 passed
cd app/CCDesk && swift test                                        # 21 passed
sh app/CCDesk/Tests/CCDeskTests/bundle_structure_test.sh           # 打包产物结构，8 条断言
```

打包产物那条是 shell 测（真跑一次 `build-app.sh` 装到临时目录再断言），没进 `swift test`：
在 `swift test` 里再跑 `swift build -c release` 会撞同一个 `.build` 锁。
