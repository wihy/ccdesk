import AppKit
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let client = DeskClient()
    private var timer: Timer?
    private var knownWaiting: Set<Int> = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "CC …"
        statusItem.menu = NSMenu()
        // 裸 SPM 可执行文件没有 app bundle，UNUserNotificationCenter.current() 会抛
        // bundleProxyForCurrentProcess is nil；只有包进 .app 后通知才可用。
        if Bundle.main.bundleIdentifier != nil {
            UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
        Task { await refresh() }
    }

    private func refresh() async {
        do {
            let payload = try await client.sessions()
            let fresh = newlyWaiting(current: payload.sessions, previous: knownWaiting)
            knownWaiting = Set(payload.sessions.filter { $0.status == "waiting" }.map(\.pid))
            await MainActor.run {
                render(payload)
                fresh.forEach(notify)
            }
        } catch {
            await MainActor.run { statusItem.button?.title = "CC ⚠︎" }
        }
    }

    @MainActor
    private func render(_ payload: SessionsPayload) {
        statusItem.button?.title = payload.waitingCount > 0
            ? "CC ●\(payload.waitingCount)" : "CC \(payload.sessions.count)"
        let menu = NSMenu()
        for session in payload.sessions.sorted(by: { $0.status < $1.status }) {
            let mark = session.status == "waiting" ? "● " : "  "
            let reason = session.waitingFor.map { " — \($0)" } ?? ""
            let item = NSMenuItem(
                title: "\(mark)\(session.name)  [\(session.status)]\(reason)",
                action: #selector(openCwd(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = session.cwd
            menu.addItem(item)
        }
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出", action: #selector(NSApplication.terminate(_:)),
                                keyEquivalent: "q"))
        statusItem.menu = menu
    }

    @objc private func openCwd(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
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
