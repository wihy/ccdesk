import AppKit
import Carbon.HIToolbox
import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let client = DeskClient()

    /// cmux 的 bundle id 与安装路径。实测取自
    /// `/Applications/cmux.app/Contents/Info.plist` 的 CFBundleIdentifier，
    /// 并与运行中进程的 bundle id 核对过。
    private static let cmuxBundleID = "com.cmuxterm.app"
    private static let cmuxAppPath = "/Applications/cmux.app"
    private let model = PanelModel()
    private var popover: NSPopover!
    private var timer: Timer?
    private var pendingTimer: Timer?
    /// 已经推过通知的待决项，避免每秒轮询把同一题反复弹出来。
    private var notifiedPending: Set<String> = []
    // 热键路径的 1×1 透明锚点窗口（徽标被 macOS 隐藏时 statusItem.button 不在屏上，
    // 锚它会弹到离屏位置）。popover 关闭即释放。
    private var anchorWindow: NSWindow?
    private var knownWaiting: Set<Int> = []
    // I10：Timer 每 3s 派一个游离 Task，daemon 慢时（/sessions 会 fork claude 子进程，
    // 实测 240~760ms）会并发多个 refresh，knownWaiting 的读—改—写错序 → 重复/伪新通知。
    // popover 打开时还会额外拉一次，并发概率更高。正在跑就跳过这轮。
    private var isRefreshing = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "CC …"
        statusItem.button?.target = self
        statusItem.button?.action = #selector(togglePanel(_:))

        popover = NSPopover()
        popover.behavior = .transient
        popover.contentSize = NSSize(width: 420, height: 520)
        popover.delegate = self
        popover.contentViewController = NSHostingController(
            rootView: PanelView(model: model,
                                onOpenCwd: { [weak self] in self?.openSession($0) },
                                onAnswer: { [weak self] in self?.answer($0, with: $1) },
                                onQuit: { NSApp.terminate(nil) }))

        // ⌥⌘D：不依赖徽标可见性的入口。菜单栏排满时 macOS 会静默隐藏排后面的第三方
        // status item，本机（刘海屏 + 十几个常驻应用）就点不到 CC 徽标。
        // 注册结果必须落日志：被别的 App 占了组合时 RegisterEventHotKey 直接 false，
        // 没有任何界面反馈，用户只会以为"快捷键没反应"。
        let hotKeyOK = registerHotKey(keyCode: UInt32(kVK_ANSI_D),
                                      modifiers: UInt32(optionKey | cmdKey)) {
            [weak self] in self?.showPanelAtScreenCorner()
        }
        NSLog("CCDesk hotkey opt-cmd-D registered=%@", hotKeyOK ? "true" : "false")

        // 裸 SPM 可执行文件没有 app bundle，UNUserNotificationCenter.current() 会抛
        // bundleProxyForCurrentProcess is nil；只有包进 .app 后通知才可用。
        if Bundle.main.bundleIdentifier != nil {
            UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
        // 待决项单独用更快的节拍：窗口只有 23s，3s 一轮意味着最坏情况白白烧掉
        // 大半个窗口才让你看见。这个接口很轻（内存里的列表，不 fork 子进程）。
        pendingTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { await self?.refreshPending() }
        }
        Task { await refresh() }
    }

    @objc private func togglePanel(_ sender: Any?) {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(sender)
            return
        }
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        // 打开即拉一次，否则最坏会看到 3s 前的旧数据。
        Task { await refresh() }
    }

    /// 热键路径：在鼠标所在那块屏的右上角（菜单栏下方）放一个 1×1 透明窗口当锚点。
    /// 点徽标的路径不走这里，仍锚 statusItem.button。
    private func showPanelAtScreenCorner() {
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        let frame = screenFrame(containing: NSEvent.mouseLocation,
                               in: NSScreen.screens.map(\.visibleFrame))
        let window = NSWindow(contentRect: anchorRect(inScreenFrame: frame),
                              styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.ignoresMouseEvents = true
        window.level = .statusBar
        window.orderFrontRegardless()
        anchorWindow = window

        guard let anchor = window.contentView else { return }
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .minY)
        Task { await refresh() }
    }

    @MainActor
    private func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        // 三个接口并发；各自独立降级——/sessions 挂了不该把健康条也一起清空。
        async let sessionsTask = client.sessions()
        async let reconTask = client.recon()
        async let healthTask = client.health()

        do {
            let payload = try await sessionsTask
            let fresh = newlyWaiting(current: payload.sessions, previous: knownWaiting)
            knownWaiting = Set(payload.sessions.filter { $0.status == "waiting" }.map(\.pid))
            model.sessions = payload.sessions
            model.waitingCount = payload.waitingCount
            model.unreachable = false
            statusItem.button?.title = payload.waitingCount > 0
                ? "CC ●\(payload.waitingCount)" : "CC \(payload.sessions.count)"
            fresh.forEach(notify)
        } catch {
            model.unreachable = true
            statusItem.button?.title = "CC ⚠︎"
        }
        // 异常清单取不到时保留上一次，避免瞬时失败让异常"凭空消失"；
        // 健康条相反——取不到就必须显式转红，不能拿旧值假装还活着。
        if let recon = try? await reconTask { model.anomalies = recon.anomalies }
        model.health = try? await healthTask
    }

    /// 点击会话行：优先把 cmux 切到它所在的 workspace 并置前；
    /// 映射不上（会话不在 cmux 里 / cmux 没跑 / 命令失败）才回退到打开 cwd。
    /// 拉待决项。比主刷新快得多，所以只碰这一个接口，不连带刷会话/对账。
    @MainActor
    private func refreshPending() async {
        guard let payload = try? await client.pending() else { return }
        let previous = Set(model.pending.map(\.reqId))
        model.pending = payload.items

        // 新出现的才推通知。判官可能几秒内就抢答了，那种情况下这一题
        // 根本不该打扰你——所以通知只在它真的挂住时发。
        let current = Set(payload.items.map(\.reqId))
        for item in payload.items where !previous.contains(item.reqId)
            && !notifiedPending.contains(item.reqId) {
            notifiedPending.insert(item.reqId)
            notifyPending(item)
        }
        notifiedPending.formIntersection(current)   // 决完的从记忆里摘掉
    }

    /// 人在面板上点了一个选项。
    @MainActor
    private func answer(_ item: PendingItem, with option: PendingOption) {
        // 先本地摘掉，别让用户对着一张已经点过的卡片再点一次
        model.pending.removeAll { $0.reqId == item.reqId }
        Task {
            _ = try? await client.resolve(reqId: item.reqId, answer: option.label)
            await refreshPending()
        }
    }

    private func notifyPending(_ item: PendingItem) {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let content = UNMutableNotificationContent()
        content.title = "\(item.sessionName.isEmpty ? "会话" : item.sessionName) 在等你选"
        content.body = item.question
        content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "pending-\(item.reqId)",
                                  content: content, trigger: nil))
    }

    private func openSession(_ session: Session) {
        Task { @MainActor in
            var switched = false
            if let result = try? await client.focus(pid: session.pid), result.ok {
                switched = true
            }
            if switched {
                // daemon 只切 workspace，不会 activate —— 实测 select-workspace
                // 执行前后前台应用不变。置前这一步必须在 GUI 侧补。
                activateCmux()
            } else {
                NSWorkspace.shared.open(URL(fileURLWithPath: session.cwd))
            }
        }
    }

    /// 把 cmux 窗口带到前台。找不到就静默放弃——此时 workspace 已经切好了，
    /// 用户自己切过去也能看到正确的那个。
    private func activateCmux() {
        if let app = NSRunningApplication
            .runningApplications(withBundleIdentifier: Self.cmuxBundleID).first {
            app.activate(options: [.activateAllWindows])
            return
        }
        let url = URL(fileURLWithPath: Self.cmuxAppPath)
        if FileManager.default.fileExists(atPath: url.path) {
            NSWorkspace.shared.openApplication(at: url,
                                               configuration: NSWorkspace.OpenConfiguration())
        }
    }

    private func notify(_ session: Session) {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let content = UNMutableNotificationContent()
        content.title = "会话在等：\(session.name)"
        content.body = session.waitingFor ?? "需要你处理"
        let request = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }
}

extension AppDelegate: NSPopoverDelegate {
    /// 两条路径都会走到这里；点徽标那条 anchorWindow 是 nil，空转。
    func popoverDidClose(_ notification: Notification) {
        anchorWindow?.close()
        anchorWindow = nil
    }
}
