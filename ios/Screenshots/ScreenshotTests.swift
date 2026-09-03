import XCTest

/// App Store 截图流水线。跑法(仓库根目录):
///   SB_SHOT_DIR=/abs/out SB_LANG=en   xcodebuild test -project ios/SessionBell.xcodeproj \
///     -scheme SessionBell -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' \
///     -only-testing:SessionBellScreenshots
/// 需要本地 worker(backend-cf: npx wrangler dev --port 8787 --var REVIEW_CODE:demo
/// --var DEMO_SECRET:<secret>)和 simctl status_bar override --time 9:41。
/// 环境变量经 TEST_RUNNER_ 前缀传入:TEST_RUNNER_SB_SHOT_DIR / TEST_RUNNER_SB_LANG /
/// TEST_RUNNER_SB_BACKEND / TEST_RUNNER_SB_SECRET。
final class ScreenshotTests: XCTestCase {
    private var env: [String: String] { ProcessInfo.processInfo.environment }
    private var outDir: String { env["SB_SHOT_DIR"] ?? NSTemporaryDirectory() }
    private var lang: String { env["SB_LANG"] ?? "en" }

    private func langArgs() -> [String] {
        ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "en" ? "en_US" : "zh_CN"]
    }

    /// 优先交给外部的 `simctl io screenshot`(带灵动岛和状态栏覆盖):写 <name>.ready,
    /// 等 <name>.done 出现;没人接手就退回 XCUIScreen 自截。
    private func save(_ name: String) {
        let dir = URL(fileURLWithPath: outDir)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let ready = dir.appendingPathComponent("\(name).ready")
        let done = dir.appendingPathComponent("\(name).done")
        try? "1".write(to: ready, atomically: true, encoding: .utf8)
        for _ in 0..<20 where !FileManager.default.fileExists(atPath: done.path) { sleep(1) }
        if FileManager.default.fileExists(atPath: done.path) { return }
        let png = XCUIScreen.main.screenshot().pngRepresentation
        XCTAssertNoThrow(try png.write(to: dir.appendingPathComponent("\(name).png")))
    }

    func testOnboarding() {
        let app = XCUIApplication()
        app.launchArguments = langArgs() + ["-sb.onboarded", "0", "-sb.backendURL", ""]
        app.launch()
        XCTAssertTrue(app.buttons.firstMatch.waitForExistence(timeout: 10))
        sleep(2)
        save("1-onboarding")
    }

    private func launchConnected(tab: Int) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = langArgs() + [
            "-sb.onboarded", "1", "-sb.tab", "\(tab)",
            "-sb.backendURL", env["SB_BACKEND"] ?? "http://localhost:8787",
            "-sb.backendSecret", env["SB_SECRET"] ?? "demo-secret-for-screens-0001",
        ]
        app.launch()
        return app
    }

    func testTabs() {
        var app = launchConnected(tab: 0)
        XCTAssertTrue(app.staticTexts["checkout"].waitForExistence(timeout: 20))
        sleep(3)
        save("2-tab0")
        app.terminate()
        app = launchConnected(tab: 1)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "MacBook Pro")).firstMatch.waitForExistence(timeout: 20))
        sleep(3)
        save("3-tab1")
    }

    func testDetail() {
        let app = launchConnected(tab: 0)
        let row = app.staticTexts["checkout"]
        XCTAssertTrue(row.waitForExistence(timeout: 20))
        sleep(2)
        row.tap()
        sleep(2)
        app.scrollViews.firstMatch.swipeDown()   // 回到顶部,抵消导航时可能带来的偏移
        sleep(15)   // 终端快照:缓存帧立即出现,12 秒后"画面时间"替换"刷新中"
        save("4-detail")
    }
}
