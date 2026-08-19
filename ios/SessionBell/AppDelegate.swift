import UIKit
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self

        // 授权请求通知上的操作按钮（长按/下拉展开可见）
        let allow = UNNotificationAction(
            identifier: "SB_ALLOW", title: "✅ 允许", options: [.authenticationRequired])
        let deny = UNNotificationAction(
            identifier: "SB_DENY", title: "❌ 拒绝", options: [.destructive])
        // 任务完成/等待通知：长按直接打字回复下一步指令
        let reply = UNTextInputNotificationAction(
            identifier: "SB_SEND", title: "💬 回复指令", options: [],
            textInputButtonTitle: "发送", textInputPlaceholder: "下一步做什么…")
        center.setNotificationCategories([
            UNNotificationCategory(identifier: "SB_DECIDE", actions: [allow, deny],
                                   intentIdentifiers: [], options: []),
            UNNotificationCategory(identifier: "SB_REPLY", actions: [reply],
                                   intentIdentifiers: [], options: []),
        ])

        // First launch after (re)install: every previous Live Activity is dead —
        // tell the backend to retire their tokens so Macs start a fresh card.
        if !UserDefaults.standard.bool(forKey: "sb.installFlag") {
            UserDefaults.standard.set(true, forKey: "sb.installFlag")
            Task {
                if let backend = SBBackend.saved {
                    await SBBackend.post("/api/token", body: ["reset_dashboard": "1"],
                                         to: backend.url, secret: backend.secret)
                }
            }
        }
        if #available(iOS 17.2, *) {
            LiveActivityManager.shared.bootstrap()
        }
        Task {
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
            await MainActor.run {
                UIApplication.shared.registerForRemoteNotifications()
            }
            await EventStore.shared.refresh()
        }
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in
            EventStore.shared.deviceToken = hex
        }
        // Auto-pair: the Mac pulls device tokens from the backend, so nobody
        // has to copy-paste them anymore.
        Task {
            if let backend = SBBackend.saved {
                await SBBackend.post("/api/token", body: ["device_token": hex],
                                     to: backend.url, secret: backend.secret)
            }
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        print("APNs 注册失败: \(error)")
    }

    // Push arrives while the app is in the foreground.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        let userInfo = notification.request.content.userInfo
        await EventStore.shared.ingest(userInfo: userInfo)
        return [.banner, .sound, .list]
    }

    // User tapped a notification or one of its action buttons.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        switch response.actionIdentifier {
        case "SB_ALLOW":
            await sendDecision("allow", userInfo: userInfo)
        case "SB_DENY":
            await sendDecision("deny", userInfo: userInfo)
        case "SB_SEND":
            let text = (response as? UNTextInputNotificationResponse)?.userText ?? ""
            await sendCommand(text, userInfo: userInfo)
        default:
            await EventStore.shared.ingest(userInfo: userInfo)
            // Plain tap: land on this event's detail page.
            if let sb = userInfo["sb"] as? [String: Any],
               let sessionId = sb["session_id"] as? String {
                EventStore.shared.openSessionId = sessionId
            }
        }
    }

    private func sendCommand(_ text: String, userInfo: [AnyHashable: Any]) async {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let sb = userInfo["sb"] as? [String: Any],
              let sessionId = sb["session_id"] as? String,
              let backend = sb["backend"] as? [String: String],
              let url = backend["url"], let secret = backend["secret"]
        else { return }
        SBBackend.save(url: url, secret: secret)
        await SBBackend.post(
            "/api/command",
            body: ["session_id": sessionId, "text": text],
            to: url, secret: secret)
    }

    private func sendDecision(_ decision: String, userInfo: [AnyHashable: Any]) async {
        guard let sb = userInfo["sb"] as? [String: Any],
              let requestId = sb["request_id"] as? String,
              let backend = sb["backend"] as? [String: String],
              let url = backend["url"], let secret = backend["secret"]
        else { return }
        SBBackend.save(url: url, secret: secret)
        await SBBackend.post(
            "/api/decision",
            body: ["request_id": requestId, "decision": decision],
            to: url, secret: secret)
    }
}
