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
| CLI | `ccdesk sessions` / `recon` / `trace <id>` / `why <id>` |

数据来自两个**官方现成的源**，不做任何侵入式改造：

- `claude agents --json` —— 会话枚举与等待状态（含 `waitingFor`：是权限弹窗还是提问）
- `~/.adw/observability/claude/events.jsonl` —— Claude Code hook 事件流，授权请求的明细在这里

采集器把两者对齐成一本 append-only 账本，用 `req_id`（会话+提示+工具+参数指纹）做幂等键，
让「发起 → 决策 → 结局」三段能对上号。对账器再看这三段有没有闭合。

## 设计上的两个决定

**observe-only：只看，不替你做决定。**
闸门（`PreToolUse` hook）代码写好了、十条契约测试全绿，但**有意没有安装**到任何 settings.json。
理由是先攒够真实请求样本再写白名单规则，而不是拍脑袋定策略然后误挡你所有会话。
代价是 `why` 子命令现在恒输出 `None`，四类对账异常里只有一类可达——这些都在「已知限制」里如实写了。

**闸门永不 deny。**
真装上之后，它的三条铁律是：永不 `deny`、永不非零退出、永不打印 traceback。
任何异常路径（daemon 挂了、返回垃圾、响应慢）都自降级为 `ask`，把选择权交回给你。
自降级线 7.5 秒由内部 watchdog 保证——实测过 Claude Code 侧超时后的兜底行为**不保证拒绝**、
且因命令形态而异，所以不能指望它。

## 两个实测结论（写在 `spikes/`）

做之前先验了两件事，结论都改写了设计：

**U1：外部消息推不动 waiting 会话。** 向一个卡在弹窗上的会话发消息，消息会送达并落进输入框，
但**弹窗仍在、状态不变、不被消费**——直到你亲手点掉弹窗，它才被处理。所以「自动回复推进」这条路
对 waiting 会话是死的，只对 idle 会话有效。（仅实测了 `input needed` 分支，`dialog open` 属推断待验证。）

**U2：hook timeout 上限 ≥300 秒。** 30/60/120/300 四档全部生效，未见上限。这条决定了闸门
7.5 秒的自降级线不会被 Claude Code 抢先打断。

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

### CLI 四命令（只读）

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
~/ccdesk/bin/ccdesk why 2e4bdb5ed1ed3713
```
```
决定    None
决定者  None
置信度  —
耗时    — ms
```

```bash
~/ccdesk/bin/ccdesk recon    # 授权闭环对账
```
```
对账 3 条请求，异常 2 条（坏行 0）

dangling_request   047429fa21c97a3c  AskUserQuestion «{"questions":[...]» 无决策  (9245s)
dangling_request   935c8425155cd84a  AskUserQuestion «{"questions":[...]» 无决策  (536s)
```
对账器实现了四类异常，但 **P1 只有 `dangling_request`（60s 无决策）一类真正可达**——
另外三类（`empty_allow` / `duplicate_allow` / `silent_stall`）的判据都要求 `decision` 非 None，
而 P1 observe-only 全树无人写 `decision`，所以它们恒不触发，要等 P2 的 decision writer 上线。
只看 24h 窗口内的请求，历史悬空不会永久重报。

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
会话主源 `claude agents --json` 取不到，已降级读 `~/.claude/sessions/*.json`。降级态的输出与主源实测一致，功能不受影响，但要查为什么：`logs/daemon.log` 里有一条 warning 写着降级原因（只在状态**变成** file 时记一条，避免 3s 一次刷爆日志）。最常见原因是 plist 缺 `PATH`（见「daemon」一节）。

**5. 某条请求永远没有 `outcome`**
先比对该工具在 `PermissionRequest` 与 `PostToolUse` 两侧的 `tool_input` 键集。CC 会在执行前改写 `tool_input`（已知：`AskUserQuestion` 的结局侧会多出 `answers` / `annotations`），键集不同则两侧指纹不同、`req_id` 对不上。把新发现的差异键补进 `ccdesk/ledger.py` 的 `VOLATILE_INPUT_KEYS`（按工具名，不要无差别丢键）。

## P1 已知限制

1. **`why` 输出 `决定 None / 决定者 None` 是预期，不是故障** —— P1 observe-only，没有任何组件写 decision 字段（闸门未安装）。P2 闸门上线后此输出才有实义。
2. **通知可用性取决于运行形态** —— `.app`（`ccdesk-app install`）下 `Bundle.main.bundleIdentifier` = `com.ccdesk.app`，代码里那两处 bundle 守卫放行，通知授权实测已授予（系统日志 `didGrant: 1 hasError: 0`）；裸可执行（`ccdesk-app start`）没有 app bundle、`bundleIdentifier` 是 nil，守卫直接跳过通知，**仍不可用**。徽标与面板两种形态都正常。「会话转 waiting 时通知真的弹出来」尚未人工核对。
3. **四类异常里只有 `dangling_request` 可达** —— `empty_allow` / `silent_stall` / `duplicate_allow` 三个分支都要求 `decision` 非 None（`duplicate_allow` 还额外要 `allow_count`），而 P1 全树无人写 `decision`，所以这三类恒不触发、是盲区。P2 的 decision writer 上线并维护 `allow_count` 后才解除。
4. **闸门未安装（有意）** —— 骨架在 `hooks/ccdesk_gate.py`（连接失败/垃圾输入/超时均自降级为 ask，永不 deny），但不在任何 settings.json 里。先攒真实请求样本，再写白名单。

## P2 展望

- 闸门安装 + 三态决策（allow/ask/deny）—— 骨架已实现，**待安装**；updatedInput 代答路径**待验证**
- decision writer + `allow_count` 写入契约 —— **P2 必做**（否则限制 1/3 无法解除）
- ledger 超 50MB 时加过滤路由 —— **P2**
- collector known 集合增量维护（账本 >5 万行）—— **P2**
- `dialog open` 分支复现（U1，peer advance 现象）—— **待复现**，spike 记录在 `spikes/u1-peer-advance.md`

## 测试

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python3 -m pytest -v   # 87 passed
cd app/CCDesk && swift test                                       # 21 passed
sh app/CCDesk/Tests/CCDeskTests/bundle_structure_test.sh          # 打包产物结构，8 条断言
```

打包产物那条是 shell 测（真跑一次 `build-app.sh` 装到临时目录再断言），没进 `swift test`：
在 `swift test` 里再跑 `swift build -c release` 会撞同一个 `.build` 锁。
