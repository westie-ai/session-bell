import ActivityKit
import Foundation

/// One dashboard activity aggregating every Claude session across all Macs.
struct SessionActivityAttributes: ActivityAttributes {
    public struct TaskItem: Codable, Hashable {
        var project: String
        var host: String
        var status: String       // "running" | "waiting" | "done"
        var since: TimeInterval  // unix seconds
        var detail: String?      // prompt excerpt / waiting reason
        var agents: Int?         // running subagent count
        var sub: Bool?           // spawned by another session — render nested
        var mode: String?        // permission mode when not default (plan/auto…)

        var sinceDate: Date { Date(timeIntervalSince1970: since) }
    }

    public struct ContentState: Codable, Hashable {
        var tasks: [TaskItem]    // sorted by the Mac: waiting, running, done
        var updatedAt: TimeInterval

        // Pending permission request, when Claude is blocked on an approval.
        var approvalId: String?
        var approvalSummary: String?

        var waitingCount: Int { tasks.filter { $0.status == "waiting" }.count }
        var runningCount: Int { tasks.filter { $0.status == "running" }.count }
        var activeCount: Int { waitingCount + runningCount }
        var hasApproval: Bool { !(approvalId ?? "").isEmpty }
    }

    var backend: String  // "https://xxx.vercel.app" — where tokens/decisions go
    var secret: String   // shared secret, sent as x-sb-secret header
}
