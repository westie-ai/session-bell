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

    /// 新模拟器第一次启动会弹通知权限框,自动点允许(中英文按钮都认)。
    override func setUp() {
        continueAfterFailure = true
        addUIInterruptionMonitor(withDescription: "notifications") { alert in
            for t in ["Allow", "允许"] where alert.buttons[t].exists { alert.buttons[t].tap(); return true }
            return false
        }
    }

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

    // MARK: 接入流程(不截图,当回归测试用)
    // 前提:模拟器钥匙串里没有 SessionBell 的项(否则不会进引导页),
    // 且本地 worker 带 DEMO_SECRET。参数域覆盖 hostedBase 指向本地 worker。

    private func launchFresh() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = langArgs() + [
            "-sb.onboarded", "0",   // 不传 -sb.demo:参数域会盖住 App 之后写入的值
            "-sb.hostedBase", env["SB_HOSTED"] ?? "http://localhost:8787",
        ]
        app.launch()
        return app
    }

    // 按钮 / 文案在两种语言下都要能找到:用 label 的中英文候选。
    private func first(_ q: XCUIElementQuery, _ labels: [String], timeout: TimeInterval = 10) -> XCUIElement {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            for l in labels where q[l].exists { return q[l] }
            for l in labels {
                let m = q.containing(NSPredicate(format: "label CONTAINS %@", l)).firstMatch
                if m.exists { return m }
            }
            usleep(300_000)
        }
        return q[labels[0]]
    }
    private var atMacBtn: [String] { ["I'm at my Mac now", "我现在就在 Mac 前"] }
    private var demoBtn: [String] { ["Not right now — show me the demo", "现在不在，先看看演示"] }
    private var codeBtn: [String] { ["My Mac is showing a 6-digit code", "Mac 屏幕上有一个 6 位数字"] }
    private var copyBtn: [String] { ["Copy the line", "复制这一行"] }

    /// 先看看演示 → 直接进 App,任务页顶部是"还没连上 Mac"的卡,demo 任务可见。
    func testDemoFlow() {
        let app = launchFresh()
        let demo = first(app.buttons, demoBtn)
        XCTAssertTrue(demo.waitForExistence(timeout: 10))
        demo.tap()
        XCTAssertTrue(first(app.staticTexts, ["Not connected to a Mac yet", "还没连上 Mac"], timeout: 20).exists)
        XCTAssertTrue(app.staticTexts["checkout"].waitForExistence(timeout: 20))
        sleep(2)
        save("5-demo")
        // 卡片里「我现在就在 Mac 前」→ 真实租户 → 直接落到「连接 Mac」
        first(app.buttons, atMacBtn).tap()
        XCTAssertTrue(first(app.buttons, copyBtn, timeout: 20).exists)
    }

    /// 我在 Mac 前 → 开新租户 → 停在「连接 Mac」,命令里带 6 位码。
    func testOpenSignup() {
        let app = launchFresh()
        let start = first(app.buttons, atMacBtn)
        XCTAssertTrue(start.waitForExistence(timeout: 10))
        start.tap()
        XCTAssertTrue(first(app.buttons, copyBtn, timeout: 20).exists)
        app.navigationBars.firstMatch.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "| bash -s ")).firstMatch.waitForExistence(timeout: 15))
    }

    /// 新引导的每一屏各截一张:首屏 / 我在 Mac 前 / 输码 / 输码成功。
    /// TEST_RUNNER_SB_CODE = 本地 worker 上一个已有 Mac 心跳的租户的 6 位码。
    func testOnboardingScreens() {
        let app = launchFresh()
        XCTAssertTrue(first(app.buttons, atMacBtn).waitForExistence(timeout: 10))
        sleep(2)
        save("onb-1-welcome")

        first(app.buttons, atMacBtn).tap()
        XCTAssertTrue(first(app.buttons, copyBtn, timeout: 20).exists)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "| bash -s ")).firstMatch.waitForExistence(timeout: 15))
        app.navigationBars.firstMatch.tap()   // 触发中断监视器,点掉通知权限弹窗
        sleep(1)
        save("onb-2-atmac")

        first(app.buttons, ["Back", "返回"]).tap()
        first(app.buttons, codeBtn).tap()
        let field = app.textFields.firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        sleep(1)
        save("onb-3-code")

        guard let code = env["SB_CODE"], code.count == 6 else { return }
        field.tap()
        field.typeText(code)
        XCTAssertTrue(first(app.staticTexts, ["Connected to", "已连接"], timeout: 25).exists)
        sleep(2)
        save("onb-4-paired")
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
