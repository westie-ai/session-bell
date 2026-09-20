import Foundation
import SwiftUI

enum EventKind: String, Codable {
    case stop
    case notification
    case permission
    case test

    var label: String {
        switch self {
        case .stop: return String(localized: "Task Finished")
        case .notification: return String(localized: "Needs Attention")
        case .permission: return String(localized: "Permission Request")
        case .test: return String(localized: "Test")
        }
    }

    var symbol: String {
        switch self {
        case .stop: return "checkmark"
        case .notification: return "ellipsis.bubble"
        case .permission: return "checkmark.shield"
        case .test: return "bell"
        }
    }

    var color: Color {
        switch self {
        case .stop: return .sbDone
        case .notification: return .sbWaiting
        case .permission: return .sbAccentText
        case .test: return .sbRunning
        }
    }
}

struct SessionEvent: Identifiable, Codable, Equatable, Hashable {
    let id: String
    let kind: EventKind
    let sessionId: String
    let project: String
    let cwd: String
    let host: String?
    let title: String
    let body: String
    let md: String?
    let date: Date
    var engine: String? = nil
    var source: String? = nil
}

struct CodexTokenUsage: Decodable, Equatable, Hashable {
    let input_tokens: Int?
    let cached_input_tokens: Int?
    let output_tokens: Int?
    let total_tokens: Int?
}

struct CodexUsage: Decodable, Equatable {
    struct Window: Decodable, Equatable, Identifiable {
        let id: String
        let used_pct: Double
        let window_minutes: Double?
        let resets_at: Double?

        var label: String {
            guard let minutes = window_minutes else {
                return id == "primary" ? String(localized: "Primary Window") : String(localized: "Secondary Window")
            }
            if minutes == 10080 { return String(localized: "Weekly Quota") }
            if minutes == 300 { return String(localized: "5-Hour Window") }
            return String(localized: "\(Int(minutes)) minute window")
        }
    }
    struct Limit: Decodable, Equatable, Identifiable {
        let id: String
        let name: String
        let plan: String?
        let windows: [Window]
    }
    let updated_at: Double?
    let available: Bool
    let limits: [Limit]
    let stale: Bool?
    let reason: String?
    let ordinary_usage_allowed: Bool?
}

struct CodexPendingRequest: Decodable, Equatable, Identifiable, Hashable {
    struct Question: Decodable, Equatable, Identifiable, Hashable {
        struct Option: Decodable, Equatable, Hashable {
            let label: String
            let description: String
        }
        let id: String
        let header: String
        let question: String
        let isSecret: Bool?
        let options: [Option]?
    }
    let id: String
    let kind: String
    let summary: String
    let questions: [Question]
}

struct SessionGroup: Identifiable, Hashable {
    let sessionId: String
    let events: [SessionEvent]  // newest first

    var id: String { sessionId }
    var latest: SessionEvent { events[0] }
    var project: String { latest.project }
}
