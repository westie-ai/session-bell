import ActivityKit
import Foundation

/// Ships Live Activity push tokens to the backend over HTTPS — works on any network.
@available(iOS 17.2, *)
final class LiveActivityManager {
    static let shared = LiveActivityManager()

    func bootstrap() {
        Task {
            for await tokenData in Activity<SessionActivityAttributes>.pushToStartTokenUpdates {
                let hex = Self.hex(tokenData)
                await MainActor.run { EventStore.shared.pushToStartToken = hex }
                if let backend = SBBackend.saved {
                    await SBBackend.post("/api/token", body: ["pts_token": hex],
                                         to: backend.url, secret: backend.secret)
                }
            }
        }
        Task {
            for await activity in Activity<SessionActivityAttributes>.activityUpdates {
                // LA attributes are a snapshot from when the card was STARTED —
                // they may point at a retired backend. Only bootstrap from them;
                // never overwrite a live config (that's how we lost westie.ai).
                if SBBackend.saved == nil {
                    SBBackend.save(url: activity.attributes.backend,
                                   secret: activity.attributes.secret)
                }
                Task {
                    for await tokenData in activity.pushTokenUpdates {
                        let be = SBBackend.saved
                            ?? (url: activity.attributes.backend,
                                secret: activity.attributes.secret)
                        await SBBackend.post(
                            "/api/token",
                            body: ["update_token": Self.hex(tokenData)],
                            to: be.url, secret: be.secret)
                    }
                }
                Task {
                    // Tell the backend when this card dies (user dismissed it,
                    // or the system ended it) so Macs stop updating a corpse
                    // and push-to-start a fresh one instead.
                    for await state in activity.activityStateUpdates
                    where state == .ended || state == .dismissed {
                        if let data = activity.pushToken {
                            await SBBackend.post(
                                "/api/token",
                                body: ["ended_token": Self.hex(data)],
                                to: activity.attributes.backend,
                                secret: activity.attributes.secret)
                        }
                        break
                    }
                }
            }
        }
    }

    /// Re-send everything we know; called on app foreground as a safety net.
    func syncNow() {
        Task {
            let token = await MainActor.run { EventStore.shared.pushToStartToken }
            if let backend = SBBackend.saved, !token.isEmpty {
                await SBBackend.post("/api/token", body: ["pts_token": token],
                                     to: backend.url, secret: backend.secret)
            }
            for activity in Activity<SessionActivityAttributes>.activities {
                guard activity.activityState == .active else { continue }
                if let data = activity.pushToken {
                    await SBBackend.post(
                        "/api/token",
                        body: ["update_token": Self.hex(data)],
                        to: activity.attributes.backend,
                        secret: activity.attributes.secret)
                }
            }
        }
    }

    /// Revive the lock-screen dashboard from inside the app: retire every
    /// stale token, end local leftovers, and start a fresh card seeded with
    /// the REAL task list from the backend. Works whatever killed the card.
    func reviveDashboard() async -> String {
        guard let backend = SBBackend.saved else { return "没有后端配置" }
        await SBBackend.post("/api/token", body: ["reset_dashboard": "1"],
                             to: backend.url, secret: backend.secret)
        for activity in Activity<SessionActivityAttributes>.activities {
            await activity.end(nil, dismissalPolicy: .immediate)
        }
        var items = await fetchTaskItems()
        if items.isEmpty {
            items = [.init(project: "SessionBell", host: "手机", status: "running",
                           since: Date().timeIntervalSince1970,
                           detail: "面板已就绪，等待任务事件", agents: 0)]
        }
        let state = SessionActivityAttributes.ContentState(
            tasks: items, updatedAt: Date().timeIntervalSince1970,
            approvalId: nil, approvalSummary: nil)
        do {
            let attrs = SessionActivityAttributes(backend: backend.url, secret: backend.secret)
            let activity = try Activity.request(
                attributes: attrs,
                content: .init(state: state, staleDate: Date().addingTimeInterval(1800)),
                pushType: .token)
            Task {
                for await tokenData in activity.pushTokenUpdates {
                    await SBBackend.post(
                        "/api/token",
                        body: ["update_token": Self.hex(tokenData)],
                        to: backend.url, secret: backend.secret)
                }
            }
            return "面板已唤起 ✓"
        } catch {
            return "失败: \(error.localizedDescription)"
        }
    }

    private func fetchTaskItems() async -> [SessionActivityAttributes.TaskItem] {
        guard let obj = await SBBackend.getJSON("/api/state") as? [String: [String: Any]]
        else { return [] }
        let now = Date().timeIntervalSince1970
        let limits: [String: Double] = ["done": 600, "waiting": 1800, "running": 21600]
        var out: [SessionActivityAttributes.TaskItem] = []
        for (host, blob) in obj {
            guard let ts = blob["ts"] as? Double, now - ts < 3600,
                  let sessions = blob["sessions"] as? [String: [String: Any]] else { continue }
            for (_, e) in sessions {
                guard let status = e["status"] as? String,
                      let since = e["since"] as? Double,
                      now - since <= (limits[status] ?? 86400),
                      let project = e["project"] as? String else { continue }
                out.append(.init(project: project, host: host, status: status, since: since,
                                 detail: e["detail"] as? String, agents: e["agents"] as? Int))
            }
        }
        let order = ["waiting": 0, "running": 1, "done": 2]
        out.sort { (order[$0.status] ?? 3, $0.since) < (order[$1.status] ?? 3, $1.since) }
        return Array(out.prefix(6))
    }

    private static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
