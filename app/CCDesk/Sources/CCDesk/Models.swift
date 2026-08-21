import Foundation

struct Session: Codable, Equatable {
    let pid: Int
    let sessionId: String
    let cwd: String
    let name: String
    let kind: String
    let status: String
    let waitingFor: String?
    let startedAt: Int
    let source: String

    enum CodingKeys: String, CodingKey {
        case pid
        case sessionId = "session_id"
        case cwd, name, kind, status
        case waitingFor = "waiting_for"
        case startedAt = "started_at"
        case source
    }
}

struct SessionsPayload: Codable {
    let sessions: [Session]
    let waitingCount: Int
    let ts: String

    enum CodingKeys: String, CodingKey {
        case sessions
        case waitingCount = "waiting_count"
        case ts
    }
}

struct Anomaly: Codable {
    let kind: String
    let reqId: String
    let sessionId: String
    let detail: String
    let ageS: Double

    enum CodingKeys: String, CodingKey {
        case kind
        case reqId = "req_id"
        case sessionId = "session_id"
        case detail
        case ageS = "age_s"
    }
}

struct ReconPayload: Codable {
    let anomalies: [Anomaly]
    let checked: Int
}

func newlyWaiting(current: [Session], previous: Set<Int>) -> [Session] {
    current.filter { $0.status == "waiting" && !previous.contains($0.pid) }
}

struct HealthPayload: Codable {
    /// 采集心跳停摆判定线。daemon POLL_SECONDS=3，连丢 5 轮才转红，避免偶发慢采集误报。
    static let staleCollectS = 15.0

    let ok: Bool
    let ts: String
    let lastCollectTs: Double
    let collectAgeS: Double
    let collectErrors: Int
    let sessionSource: String

    enum CodingKeys: String, CodingKey {
        case ok, ts
        case lastCollectTs = "last_collect_ts"
        case collectAgeS = "collect_age_s"
        case collectErrors = "collect_errors"
        case sessionSource = "session_source"
    }

    var isHealthy: Bool { ok && collectErrors == 0 && collectAgeS < Self.staleCollectS }
}

/// 秒 → 人读串，只保留最高两级单位：45s / 1m0s / 2h41m / 1d0h。
func formatDuration(_ seconds: Double) -> String {
    let total = max(0, Int(seconds))
    if total >= 86400 { return "\(total / 86400)d\(total / 3600 % 24)h" }
    if total >= 3600 { return "\(total / 3600)h\(total / 60 % 60)m" }
    if total >= 60 { return "\(total / 60)m\(total % 60)s" }
    return "\(total)s"
}

/// 等待中的置顶（组内保持原相对顺序）。按 status 字典序排会让 waiting 落到最后（M1）。
func sortedForPanel(_ sessions: [Session]) -> [Session] {
    sessions.filter { $0.status == "waiting" }
        + sessions.filter { $0.status != "waiting" }
}

/// 从异常 detail 里取工具名。只有 dangling_request 的 detail 形如 `工具名 «摘要» 无决策`
/// 带工具名，其余 kind 的首个词是中文话术，不能当工具名报出去。
func anomalyToolLabel(_ detail: String) -> String {
    guard let marker = detail.range(of: " «") else { return "" }
    return String(detail[detail.startIndex..<marker.lowerBound])
}

struct AnomalyFootprint {
    let request: String
    let decision: String
    let outcome: String
}

/// 授权闭环三段足迹。daemon 只给 age_s 一个时间量，其起算点随 kind 而变
/// （见 recon_auth.py）：dangling 从 ts_request 起算、empty_allow / silent_stall
/// 从 ts_decision 起算、duplicate_allow 恒 0。还原不出的段一律显示 —，不编造。
/// 结局段恒缺：recon_auth.py 每个分支都以 outcome 缺失为前提。
func anomalyFootprint(_ anomaly: Anomaly, now: Date,
                      timeZone: TimeZone = .current) -> AnomalyFootprint {
    let missing = "—"
    let at = footprintTime(now.addingTimeInterval(-anomaly.ageS), timeZone)
    switch anomaly.kind {
    case "dangling_request":
        return AnomalyFootprint(request: at, decision: missing, outcome: missing)
    case "empty_allow":
        return AnomalyFootprint(request: missing, decision: "allow \(at)", outcome: missing)
    case "silent_stall":
        return AnomalyFootprint(request: missing, decision: "ask \(at)", outcome: missing)
    default:
        return AnomalyFootprint(request: missing, decision: missing, outcome: missing)
    }
}

private func footprintTime(_ date: Date, _ timeZone: TimeZone) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = timeZone
    formatter.dateFormat = "MM-dd HH:mm:ss"
    return formatter.string(from: date)
}

// MARK: - 待决项（人工一键处理）

/// 一个挂起中的选择题。daemon 那边判官和人在并行答，谁先给答案用谁——
/// 所以这里的 remainingS 会一直走，且随时可能因为判官抢先而整项消失。
struct PendingItem: Codable, Equatable, Identifiable {
    let reqId: String
    let question: String
    let header: String
    let options: [PendingOption]
    let sessionName: String
    let cwd: String
    let remainingS: Double

    var id: String { reqId }

    enum CodingKeys: String, CodingKey {
        case reqId = "req_id"
        case question, header, options, cwd
        case sessionName = "session_name"
        case remainingS = "remaining_s"
    }
}

struct PendingOption: Codable, Equatable {
    let label: String
    let description: String
}

struct PendingPayload: Codable {
    let items: [PendingItem]
}

/// `/resolve` 的回执。accepted=false 多半是判官抢先答了，或选项不合法。
struct ResolveResult: Codable {
    let accepted: Bool
    let reason: String?
}
