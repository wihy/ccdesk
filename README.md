# ccdesk — CC 会话值班台

Claude Code 多会话的值班台：谁在等用户回答、等了多久、当时问了什么、事后有没有下文。
**P1 = observe-only：只观察，不干预** —— 闸门已实现但有意不安装，本期没有任何组件会替你做决定。

## 快速上手

### daemon（已由 launchd 常驻）

```bash
launchctl list | grep ccdesk      # 应看到 com.ccdesk.daemon 且带 PID
curl http://127.0.0.1:8787/health # {"ok": true, ..., "collect_errors": 0}
```

健康判据：`ts` 与 `last_collect_ts` 相差应在 3s 内（采集线程每 3s 一轮）；`collect_errors` 持续为 0。
plist 在 `launchd/com.ccdesk.daemon.plist`（RunAtLoad + KeepAlive，开机自启、挂了自动拉起）。

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
③ 结局  —
```
三段式时间线：请求（工具+参数摘要）→ 决策 → 结局。悬空请求只填 ①，② ③ 为 `—`。

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
~/ccdesk/bin/ccdesk recon    # 授权闭环对账，检出四类异常
```
```
对账 1 条请求，异常 1 条（坏行 0）

dangling_request   2e4bdb5ed1ed3713  AskUserQuestion «{"questions":[...]» 无决策  (27254s)
```
四类异常：`dangling_request`（60s 无决策）/ `empty_allow`（allow 后无结局）/ `duplicate_allow` / `silent_stall`（ask 且会话仍在等，1800s）。
只看 24h 窗口内的请求，历史悬空不会永久重报。

### 菜单栏 App

```bash
cd app/CCDesk && swift build -c release && (.build/release/CCDesk &)
```

菜单栏徽标显示等待计数，点开下拉看各会话详情；daemon 不可达时徽标变 `⚠︎`。

## `~/.ccdesk/` 文件说明

| 文件 | 含义 |
|---|---|
| `ledger.jsonl` | 请求/决策/结局账本（append-only，req_id 追加式合并） |
| `ledger.bad.jsonl` | 坏行隔离区；**文件不存在 = 尚无坏行**（首条坏行出现才创建） |
| `logs/daemon.log` | daemon 运行日志（含每轮采集统计） |
| `logs/stdout.log` `logs/stderr.log` | launchd 重定向的标准输出/错误 |
| `collector.state.json` | 采集断点（events.jsonl 的 offset+inode，重启后只读新行） |

数据真源是 `~/.adw/observability/claude/events.jsonl`，账本只是它的投影——删掉 `~/.ccdesk/` 可无损重建。

## 故障排查

**1. daemon 不起 / 不采集**
`launchctl list | grep ccdesk` 看 PID 是否存在 → 看 `~/.ccdesk/logs/stderr.log` → `/health` 的 `last_collect_ts` 若停滞（距 `ts` 超过几秒）说明采集线程已死，需重启 daemon。

**2. App 徽标显示 ⚠︎**
表示 `/health` 不可达：要么 daemon 没在跑（见上条），要么接口返回形状漂移（版本不匹配）。CLI `curl http://127.0.0.1:8787/health` 可区分这两种情况。

**3. 账本有坏行**
坏行不会污染正常数据：被移入 `ledger.bad.jsonl` 并计数。`recon` 输出的「坏行 N」即暴露口；N 增长说明上游 events.jsonl 出现了非预期内容，去 bad.jsonl 里看原文。

## P1 已知限制

1. **`why` 输出 `决定 None / 决定者 None` 是预期，不是故障** —— P1 observe-only，没有任何组件写 decision 字段（闸门未安装）。P2 闸门上线后此输出才有实义。
2. **通知功能当前不可用** —— 裸 SPM 可执行没有 app bundle，UserNotifications 拒绝工作。App 徽标与下拉列表不受影响，正常工作。
3. **`duplicate_allow` 恒不触发** —— `allow_count` 目前无写入方。P2 闸门写 decision 时必须维护该字段，否则该异常永远是盲区。
4. **闸门未安装（有意）** —— 骨架在 `hooks/ccdesk_gate.py`（连接失败/垃圾输入/超时均自降级为 ask，永不 deny），但不在任何 settings.json 里。先攒真实请求样本，再写白名单。

## P2 展望

- 闸门安装 + 三态决策（allow/ask/deny）—— 骨架已实现，**待安装**；updatedInput 代答路径**待验证**
- decision writer + `allow_count` 写入契约 —— **P2 必做**（否则限制 1/3 无法解除）
- 打包 .app 激活通知 —— **P2**；必须同时加 in-flight guard 修 Timer 竞态
- ledger 超 50MB 时加过滤路由 —— **P2**
- collector known 集合增量维护（账本 >5 万行）—— **P2**
- `dialog open` 分支复现（U1，peer advance 现象）—— **待复现**，spike 记录在 `spikes/u1-peer-advance.md`

## 测试

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python3 -m pytest -v   # 79 passed
cd app/CCDesk && swift test                                       # 3 passed
```
