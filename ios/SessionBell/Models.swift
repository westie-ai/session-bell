import Foundation
import SwiftUI

enum EventKind: String, Codable {
    case stop
    case notification
    case permission
    case test

    var label: String {
        switch self {
        case .stop: return "任务完成"
        case .notification: return "需要干预"
        case .permission: return "请求授权"
        case .test: return "测试"
        }
    }

    var symbol: String {
        switch self {
        case .stop: return "checkmark.circle.fill"
        case .notification: return "hand.raised.circle.fill"
        case .permission: return "lock.circle.fill"
        case .test: return "bell.circle.fill"
        }
    }

    var color: Color {
        switch self {
        case .stop: return .green
        case .notification: return .orange
        case .permission: return .purple
        case .test: return .blue
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
