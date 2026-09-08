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
}

struct SessionGroup: Identifiable, Hashable {
    let sessionId: String
    let events: [SessionEvent]  // newest first

    var id: String { sessionId }
    var latest: SessionEvent { events[0] }
    var project: String { latest.project }
}
