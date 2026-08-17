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
