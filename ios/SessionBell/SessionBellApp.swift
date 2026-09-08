import SwiftUI

@main
struct SessionBellApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var store = EventStore.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(store)
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        Task { await store.refresh() }
                    }
                }
                // 扫 Mac 屏幕上的二维码 → https://sessionbell.westie.ai/p/483920 → 直接进输码页(已预填)
                .onOpenURL { url in
                    if let code = SBBackend.shortCode(from: url) { store.pendingPairCode = code }
                }
                .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
                    if let url = activity.webpageURL, let code = SBBackend.shortCode(from: url) {
                        store.pendingPairCode = code
                    }
                }
        }
    }
}
