import Foundation

enum DeskError: Error { case unreachable }

struct DeskClient {
    let base = URL(string: "http://127.0.0.1:8787")!

    func sessions() async throws -> SessionsPayload {
        try await get(SessionsPayload.self, path: "/sessions")
    }

    func recon() async throws -> ReconPayload {
        try await get(ReconPayload.self, path: "/recon/auth")
    }

    func health() async throws -> HealthPayload {
        try await get(HealthPayload.self, path: "/health")
    }

    private func get<T: Decodable>(_ type: T.Type, path: String) async throws -> T {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw DeskError.unreachable
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

/// `/focus` 的返回。ok=false 时调用方回退到打开 cwd。
struct FocusResult: Decodable {
    let ok: Bool
    let reason: String?
    let workspaceTitle: String?

    enum CodingKeys: String, CodingKey {
        case ok, reason
        case workspaceTitle = "workspace_title"
    }
}

extension DeskClient {
    /// 请求 daemon 把 cmux 切到该会话所在的 workspace。
    ///
    /// daemon **只切 workspace，不会把 cmux 窗口带到前台**（实测
    /// `select-workspace` 不 activate），置前由调用方自己做。
    func focus(pid: Int) async throws -> FocusResult {
        var request = URLRequest(url: base.appendingPathComponent("/focus"))
        request.httpMethod = "POST"
        request.timeoutInterval = 8       // 内含一次 cmux tree + 一次 select
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["pid": pid])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw DeskError.unreachable
        }
        return try JSONDecoder().decode(FocusResult.self, from: data)
    }
}

extension DeskClient {
    /// 拉取挂起中的待决项。空列表是常态。
    func pending() async throws -> PendingPayload {
        try await get(PendingPayload.self, path: "/pending")
    }

    /// 替用户答一个待决项。
    ///
    /// accepted=false 不是错误：判官可能刚好抢先答了，或这一项已经过期。
    /// 面板据此提示，而不是把它当失败。
    func resolve(reqId: String, answer: String) async throws -> ResolveResult {
        var request = URLRequest(url: base.appendingPathComponent("/resolve"))
        request.httpMethod = "POST"
        request.timeoutInterval = 5
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(
            withJSONObject: ["req_id": reqId, "answer": answer])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw DeskError.unreachable
        }
        return try JSONDecoder().decode(ResolveResult.self, from: data)
    }
}
