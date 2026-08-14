import XCTest
@testable import CCDesk

final class ModelsTests: XCTestCase {
    func testDecodesSessionsPayload() throws {
        let json = """
        {"sessions":[{"pid":52194,"session_id":"89dc","cwd":"/w","name":"story-7e",
        "kind":"interactive","status":"waiting","waiting_for":"dialog open",
        "started_at":1,"source":"cli"}],"waiting_count":1,"ts":"t"}
        """.data(using: .utf8)!
        let payload = try JSONDecoder().decode(SessionsPayload.self, from: json)
        XCTAssertEqual(payload.waitingCount, 1)
        XCTAssertEqual(payload.sessions.first?.waitingFor, "dialog open")
    }

    func testDecodesSessionWithoutWaitingReason() throws {
        let json = """
        {"sessions":[{"pid":1,"session_id":"a","cwd":"/w","name":"n","kind":"interactive",
        "status":"idle","waiting_for":null,"started_at":1,"source":"cli"}],
        "waiting_count":0,"ts":"t"}
        """.data(using: .utf8)!
        let payload = try JSONDecoder().decode(SessionsPayload.self, from: json)
        XCTAssertNil(payload.sessions.first?.waitingFor)
    }

    func testNewlyWaitingDetectsOnlyFreshOnes() {
        let previous: Set<Int> = [1]
        let current = [
            Session(pid: 1, sessionId: "a", cwd: "/w", name: "old", kind: "interactive",
                    status: "waiting", waitingFor: "dialog open", startedAt: 0, source: "cli"),
            Session(pid: 2, sessionId: "b", cwd: "/w", name: "new", kind: "interactive",
                    status: "waiting", waitingFor: "dialog open", startedAt: 0, source: "cli"),
        ]
        XCTAssertEqual(newlyWaiting(current: current, previous: previous).map(\.pid), [2])
    }
}
