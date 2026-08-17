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

    // MARK: - /health 解码（夹具取自真实响应：curl -s http://127.0.0.1:8787/health）

    func testDecodesHealthPayloadFromRealResponse() throws {
        let json = """
        {"ok": true, "ts": "2026-08-17T08:59:57.693025+00:00",
         "last_collect_ts": 1786957197.312296, "collect_age_s": 0.38074707984924316,
         "collect_errors": 0, "session_source": "cli"}
        """.data(using: .utf8)!
        let health = try JSONDecoder().decode(HealthPayload.self, from: json)
        XCTAssertTrue(health.ok)
        XCTAssertEqual(health.ts, "2026-08-17T08:59:57.693025+00:00")
        XCTAssertEqual(health.lastCollectTs, 1786957197.312296, accuracy: 0.000001)
        XCTAssertEqual(health.collectAgeS, 0.38074707984924316, accuracy: 0.000001)
        XCTAssertEqual(health.collectErrors, 0)
        XCTAssertEqual(health.sessionSource, "cli")
    }

    func testHealthPayloadIsHealthyWhenFreshAndErrorFree() throws {
        // daemon POLL_SECONDS=3，阈值 15s 内属正常心跳。
        XCTAssertTrue(health(ageS: 0.4).isHealthy)
        XCTAssertTrue(health(ageS: 14.9).isHealthy)
    }

    func testHealthPayloadUnhealthyOnStaleCollectOrErrorsOrNotOk() throws {
        XCTAssertFalse(health(ageS: 15.0).isHealthy, "采集心跳停摆 15s 即不健康")
        XCTAssertFalse(health(ageS: 0.4, errors: 1).isHealthy, "采集报错即不健康")
        XCTAssertFalse(health(ageS: 0.4, ok: false).isHealthy, "ok=false 即不健康")
    }

    // MARK: - 时长格式化（期望值先写死，再实现）

    func testFormatDurationBoundaries() {
        XCTAssertEqual(formatDuration(45), "45s")
        XCTAssertEqual(formatDuration(59), "59s")
        XCTAssertEqual(formatDuration(60), "1m0s")
        XCTAssertEqual(formatDuration(3599), "59m59s")
        XCTAssertEqual(formatDuration(3600), "1h0m")
        XCTAssertEqual(formatDuration(9663), "2h41m")
        XCTAssertEqual(formatDuration(86400), "1d0h")
    }

    func testFormatDurationClampsNonPositive() {
        XCTAssertEqual(formatDuration(0), "0s")
        XCTAssertEqual(formatDuration(-5), "0s")
    }

    // MARK: - 会话排序（M1：等待必须置顶）

    func testSortedForPanelPutsWaitingFirst() {
        // 字典序 busy < idle < waiting 会把等待的排到最后，与值班台目的相反。
        let input = [
            stub(pid: 1, name: "ai-bc", status: "busy"),
            stub(pid: 2, name: "theta-a0", status: "idle"),
            stub(pid: 3, name: "soulapp-clone-9b", status: "waiting"),
        ]
        XCTAssertEqual(sortedForPanel(input).map(\.pid), [3, 1, 2])
    }

    func testSortedForPanelKeepsRelativeOrderWithinGroups() {
        let input = [
            stub(pid: 1, status: "idle"),
            stub(pid: 2, status: "waiting"),
            stub(pid: 3, status: "busy"),
            stub(pid: 4, status: "waiting"),
            stub(pid: 5, status: "unknown"),
        ]
        XCTAssertEqual(sortedForPanel(input).map(\.pid), [2, 4, 1, 3, 5])
    }

    // MARK: - /recon/auth 解码（夹具取自真实响应，含 Anomaly 未声明的 bad_line_count）

    func testDecodesReconPayloadFromRealResponse() throws {
        let json = #"""
        {"anomalies": [{"kind": "dangling_request", "req_id": "047429fa21c97a3c",
          "session_id": "358e656b-ce0c-41bb-833c-50a8bffbc524",
          "detail": "AskUserQuestion «{\"questions\":[{\"header\":\"标识位置\"» 无决策",
          "age_s": 17524.814361}],
         "checked": 4, "bad_line_count": 0}
        """#.data(using: .utf8)!
        let payload = try JSONDecoder().decode(ReconPayload.self, from: json)
        XCTAssertEqual(payload.checked, 4)
        XCTAssertEqual(payload.anomalies.count, 1)
        let anomaly = payload.anomalies[0]
        XCTAssertEqual(anomaly.kind, "dangling_request")
        XCTAssertEqual(anomaly.reqId, "047429fa21c97a3c")
        XCTAssertEqual(anomaly.sessionId, "358e656b-ce0c-41bb-833c-50a8bffbc524")
        XCTAssertEqual(anomaly.ageS, 17524.814361, accuracy: 0.000001)
        XCTAssertTrue(anomaly.detail.hasPrefix("AskUserQuestion «"))
    }

    // MARK: - 异常行右侧工具名（只有 dangling 的 detail 带工具名）

    func testAnomalyToolLabelExtractsToolNameOnlyForDanglingShape() {
        XCTAssertEqual(anomalyToolLabel("AskUserQuestion «{\"a\":1}» 无决策"), "AskUserQuestion")
        XCTAssertEqual(anomalyToolLabel("Bash «git status» 无决策"), "Bash")
        // 其余 kind 的 detail 不含工具名，不能把首个词当工具名报出去。
        XCTAssertEqual(anomalyToolLabel("同一请求被 allow 3 次"), "")
        XCTAssertEqual(anomalyToolLabel("已放行但未见执行：git status"), "")
        XCTAssertEqual(anomalyToolLabel(""), "")
    }

    // MARK: - 三段足迹（请求 / 决策 / 结局）

    func testAnomalyFootprintForDanglingRequest() {
        // age_s 由 ts_request 起算 → 能还原请求时刻；决策/结局按定义缺失。
        let trace = anomalyFootprint(anomaly(kind: "dangling_request", ageS: 9663),
                                     now: fixedNow, timeZone: shanghai)
        XCTAssertEqual(trace.request, "08-17 14:18:57")
        XCTAssertEqual(trace.decision, "—")
        XCTAssertEqual(trace.outcome, "—")
    }

    func testAnomalyFootprintForEmptyAllowAndSilentStall() {
        // 这两类 age_s 由 ts_decision 起算 → 能还原决策时刻，请求时刻未知。
        let allow = anomalyFootprint(anomaly(kind: "empty_allow", ageS: 700),
                                     now: fixedNow, timeZone: shanghai)
        XCTAssertEqual(allow.request, "—")
        XCTAssertEqual(allow.decision, "allow 08-17 16:48:20")
        XCTAssertEqual(allow.outcome, "—")

        let stall = anomalyFootprint(anomaly(kind: "silent_stall", ageS: 700),
                                     now: fixedNow, timeZone: shanghai)
        XCTAssertEqual(stall.decision, "ask 08-17 16:48:20")
        XCTAssertEqual(stall.outcome, "—")
    }

    func testAnomalyFootprintOutcomeAlwaysMissing() {
        // recon_auth.py 每个分支都要求 not rec["outcome"]，故凡上报的异常结局必缺。
        for kind in ["dangling_request", "duplicate_allow", "empty_allow", "silent_stall"] {
            XCTAssertEqual(anomalyFootprint(anomaly(kind: kind, ageS: 700),
                                            now: fixedNow, timeZone: shanghai).outcome, "—",
                           "kind=\(kind)")
        }
    }

    // MARK: - 夹具

    private let fixedNow = Date(timeIntervalSince1970: 1786957200)  // 2026-08-17 17:00:00 +08
    private let shanghai = TimeZone(identifier: "Asia/Shanghai")!

    private func health(ageS: Double, errors: Int = 0, ok: Bool = true) -> HealthPayload {
        HealthPayload(ok: ok, ts: "t", lastCollectTs: 1, collectAgeS: ageS,
                      collectErrors: errors, sessionSource: "cli")
    }

    private func stub(pid: Int, name: String = "n", status: String) -> Session {
        Session(pid: pid, sessionId: "s\(pid)", cwd: "/w", name: name, kind: "interactive",
                status: status, waitingFor: nil, startedAt: 0, source: "cli")
    }

    private func anomaly(kind: String, ageS: Double) -> Anomaly {
        Anomaly(kind: kind, reqId: "r", sessionId: "s", detail: "d", ageS: ageS)
    }
}
