import SwiftUI

/// popover 的数据容器。只被 AppDelegate 在主线程写入，PanelView 只读。
final class PanelModel: ObservableObject {
    @Published var sessions: [Session] = []
    @Published var waitingCount = 0
    @Published var anomalies: [Anomaly] = []
    @Published var health: HealthPayload?
    @Published var unreachable = false
}

/// 三区面板：会话 / 对账异常 / 健康条。纯展示，数据与动作都由外部传入。
struct PanelView: View {
    @ObservedObject var model: PanelModel
    let onOpenCwd: (Session) -> Void
    let onQuit: () -> Void

    @State private var expanded: Set<String> = []

    var body: some View {
        VStack(spacing: 0) {
            sessionZone
            Divider()
            anomalyZone
            Divider()
            footer
        }
        .frame(width: 420, height: 520)
    }

    // MARK: - 一区：会话

    private var sessionZone: some View {
        VStack(spacing: 0) {
            zoneHeader("会话 \(model.sessions.count)") {
                Text("等待 \(model.waitingCount)")
                    .foregroundStyle(model.waitingCount > 0 ? Color.orange : Color.secondary)
            }
            ScrollView {
                LazyVStack(spacing: 0) {
                    if model.sessions.isEmpty {
                        placeholder(model.unreachable ? "daemon 无响应" : "无会话")
                    }
                    ForEach(sortedForPanel(model.sessions), id: \.pid) { session in
                        sessionRow(session)
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
    }

    private func sessionRow(_ session: Session) -> some View {
        let waiting = session.status == "waiting"
        return Button { onOpenCwd(session) } label: {
            HStack(spacing: 6) {
                Text(waiting ? "●" : " ")
                    .foregroundStyle(Color.orange)
                    .font(.system(size: 11))
                Text(session.name)
                    .fontWeight(waiting ? .semibold : .regular)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Text(session.waitingFor ?? session.status)
                    .foregroundStyle(waiting ? Color.orange : Color.secondary)
                    .lineLimit(1)
            }
            .font(.system(size: 12))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(waiting ? Color.orange.opacity(0.12) : Color.clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("跳到 cmux 里这个会话；不在 cmux 里则在 Finder 打开 \(session.cwd)")
    }

    // MARK: - 二区：对账异常

    private var anomalyZone: some View {
        VStack(spacing: 0) {
            zoneHeader("对账异常 \(model.anomalies.count)") {
                Text(model.anomalies.isEmpty ? "闭环" : "待处理")
                    .foregroundStyle(model.anomalies.isEmpty ? Color.secondary : Color.orange)
            }
            ScrollView {
                LazyVStack(spacing: 0) {
                    if model.anomalies.isEmpty {
                        placeholder("无异常")
                    }
                    ForEach(model.anomalies, id: \.reqId) { anomaly in
                        anomalyRow(anomaly)
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
    }

    private func anomalyRow(_ anomaly: Anomaly) -> some View {
        let isOpen = expanded.contains(anomaly.reqId)
        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(anomaly.kind)
                    .fontWeight(.semibold)
                Spacer(minLength: 8)
                Text(anomalyToolLabel(anomaly.detail))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            HStack(spacing: 6) {
                Text("等了 \(formatDuration(anomaly.ageS))")
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Spacer(minLength: 8)
                Text(isOpen ? "▾ 收起" : "▸ 展开")
                    .foregroundStyle(Color.accentColor)
            }
            if isOpen {
                let trace = anomalyFootprint(anomaly, now: Date())
                VStack(alignment: .leading, spacing: 1) {
                    footprintLine("①", "请求", trace.request)
                    footprintLine("②", "决策", trace.decision)
                    footprintLine("③", "结局", trace.outcome)
                    Text(anomaly.detail)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                        .padding(.top, 2)
                }
                .padding(.leading, 14)
                .padding(.top, 2)
            }
        }
        .font(.system(size: 12))
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture {
            if isOpen { expanded.remove(anomaly.reqId) } else { expanded.insert(anomaly.reqId) }
        }
    }

    private func footprintLine(_ index: String, _ label: String, _ value: String) -> some View {
        HStack(spacing: 6) {
            Text(index).foregroundStyle(.secondary)
            Text(label).foregroundStyle(.secondary)
            Text(value).monospacedDigit()
        }
    }

    // MARK: - 三区：健康条

    private var footer: some View {
        HStack(spacing: 8) {
            Text(healthLine)
                .font(.system(size: 11))
                .foregroundStyle(healthy ? Color.secondary : Color.red)
                .lineLimit(1)
            Spacer(minLength: 8)
            Button("退出", action: onQuit)
                .font(.system(size: 11))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
    }

    private var healthy: Bool { model.health?.isHealthy ?? false }

    private var healthLine: String {
        guard let health = model.health else { return "⚠︎ /health 无响应" }
        return "\(healthy ? "✓" : "⚠︎") 采集 \(String(format: "%.1f", health.collectAgeS))s"
            + " · 主源 \(health.sessionSource) · 错误 \(health.collectErrors)"
    }

    // MARK: - 共用零件

    private func zoneHeader<Trailing: View>(_ title: String,
                                           @ViewBuilder trailing: () -> Trailing) -> some View {
        HStack(spacing: 8) {
            Text(title).fontWeight(.semibold)
            Spacer(minLength: 8)
            trailing()
        }
        .font(.system(size: 11))
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .frame(maxWidth: .infinity)
        .background(Color.secondary.opacity(0.08))
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 12))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
