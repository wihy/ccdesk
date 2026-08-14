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
