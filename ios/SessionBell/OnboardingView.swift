import SwiftUI
import UserNotifications

/// 首跑。第一句话就是前提:SessionBell 需要和一台 Mac 配对。
///   「我现在在 Mac 前」 → 开空间 → 一行命令 + 6 位码,等 Mac 心跳
///   「现在不在」        → 演示租户,任务页常驻一张"回到 Mac 前时跑这一行"的卡,24h 后本地提醒
///   「Mac 上有 6 位数字」→ Mac 先跑了脚本,手机输码 / 扫码进来认领
/// SBBackend.saved 已存在的老用户不会看到这里(ContentView 里判断)。
struct OnboardingView: View {
    enum Step: Equatable { case welcome, atMac, code(prefill: String?), manual }
    @State private var step: Step
    @State private var busy = false
    @State private var error = ""
    let onDone: () -> Void

    init(initialStep: Step = .welcome, onDone: @escaping () -> Void) {
        _step = State(initialValue: initialStep)
        self.onDone = onDone
    }

    var body: some View {
        NavigationStack {
            Group {
                switch step {
                case .welcome: welcome
                case .atMac: ConnectMacStep(onDone: onDone, onEnterCode: { step = .code(prefill: nil) })
                case .code(let prefill):
                    CodeEntryStep(prefill: prefill, onDone: onDone, onManual: { step = .manual })
                case .manual: ManualStep(onSuccess: { step = .atMac })
                }
            }
            .toolbar {
                if step != .welcome {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Back") {
                            if case .code = step, SBBackend.saved != nil { step = .atMac } else { step = .welcome }
                        }
                    }
                }
                if step == .atMac {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Later") { later() }
                    }
                }
            }
        }
        .interactiveDismissDisabled()
    }

    // MARK: 屏 1

    private var welcome: some View {
        ScrollView {
            VStack(spacing: 0) {
                Image(systemName: "bell.badge.waveform.fill")
                    .font(.system(size: 56))
                    .foregroundStyle(Color.sbAccent.gradient)
                    .padding(.top, 36)
                    .padding(.bottom, 18)
                Text("SessionBell pairs with your Mac")
                    .font(.title.bold())
                    .multilineTextAlignment(.center)
                Text("It puts the Claude Code sessions running on your Mac onto this phone's Lock Screen. The next step is one line in the Mac's Terminal — about 30 seconds.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 8)
                    .padding(.horizontal, 12)

                VStack(alignment: .leading, spacing: 14) {
                    featureRow("bell.badge", "Waiting, finished, or needs approval — pushed straight to you",
                               "Stays quiet while you're at the Mac")
                    featureRow("platter.filled.bottom.iphone", "One Lock Screen panel for every Mac",
                               "Waiting / running / done, with live timers")
                    featureRow("checkmark.shield", "Approve permission requests from the Lock Screen",
                               "Allow or deny without going back to the Mac")
                }
                .padding(.horizontal, 8)
                .padding(.top, 28)
                .padding(.bottom, 28)

                VStack(spacing: 10) {
                    if !error.isEmpty {
                        Text(error).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
                    }
                    Button {
                        start(demo: false)
                    } label: {
                        Group {
                            if busy { ProgressView().tint(.white) }
                            else { Label("I'm at my Mac now", systemImage: "laptopcomputer").font(.headline) }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy)

                    Button {
                        start(demo: true)
                    } label: {
                        Text("Not right now — show me the demo")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.bordered)
                    .disabled(busy)

                    Button("Self-hosted server / advanced") { step = .manual }
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding(.top, 8)
                }
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 24)
        }
    }

    /// 真实空间 → 去连 Mac;演示 → 直接进 App 看模拟任务,并约一个 24h 后的提醒。
    private func start(demo: Bool) {
        guard !busy else { return }
        busy = true
        error = ""
        Task {
            if let err = await SBBackend.signup(demo: demo) {
                error = err
                busy = false
                return
            }
            await OnboardingView.registerTokens()
            busy = false
            if demo {
                SBBackend.event("demo_seen")
                OnboardingView.scheduleConnectReminder()
                onDone()
            } else {
                step = .atMac
            }
        }
    }

    private func later() {
        SBBackend.event("connect_later")
        OnboardingView.scheduleConnectReminder()
        onDone()
    }

    private func featureRow(_ icon: String, _ title: LocalizedStringKey, _ sub: LocalizedStringKey) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(Color.sbAccentDeep)
                .frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.medium))
                Text(sub).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: 屏 2a:我在 Mac 前 —— 一行命令 + 6 位码,等心跳

private struct ConnectMacStep: View {
    let onDone: () -> Void
    let onEnterCode: () -> Void
    @State private var short: SBBackend.ShortCode?
    @State private var minting = false
    @State private var copied = false
    @State private var hostFound = ""
    @State private var polling = true
    @State private var waitedLong = false

    private var command: String { SBBackend.oneLiner(code: short?.code ?? "······") }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Paste this one line into Terminal on your Mac and press Return")
                        .font(.subheadline.weight(.medium))
                    Text(command)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
                        .redacted(reason: short == nil ? .placeholder : [])
                    Button {
                        SBBackend.copyToPasteboard(command)
                        copied = true
                        SBBackend.event("command_copied")
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { copied = false }
                    } label: {
                        Label(copied ? "Copied — now ⌘V on the Mac" : "Copy the line",
                              systemImage: copied ? "checkmark" : "doc.on.doc")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(short == nil)
                    if let short, let page = SBBackend.macPageURL(code: short.code) {
                        ShareLink(item: page) {
                            Label("Send to the Mac with AirDrop instead", systemImage: "airplayaudio")
                                .font(.subheadline.weight(.medium))
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        Text("Paste didn't arrive on the Mac? AirDrop opens a page there with a copy button. Or just type it — the number is \(short.pretty). Valid for 15 minutes; it renews by itself.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 6)
            } header: {
                Text("On the Mac")
            } footer: {
                Text("Terminal is in Applications › Utilities, or press ⌘Space and type Terminal.")
            }

            Section {
                if !hostFound.isEmpty {
                    Label("Connected to \(hostFound) 🎉", systemImage: "checkmark.circle.fill")
                        .font(.headline)
                        .foregroundStyle(.green)
                    Label("Look up: the panel is already in the Dynamic Island. Lock the phone and it's on the Lock Screen too.", systemImage: "platter.filled.top.iphone")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Text("From now on it fills in whenever Claude Code stops and waits for you.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Button {
                        onDone()
                    } label: {
                        Text("Open SessionBell")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Waiting for the Mac… this page moves on by itself once it's done.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    if waitedLong {
                        Text("Nothing yet? Make sure the whole line was pasted and you pressed Return. If the Mac asked for permission, allow it.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text("Then")
            }

            if hostFound.isEmpty {
                Section {
                    Button {
                        onEnterCode()
                    } label: {
                        Label("Enter the 6 digits from the Mac", systemImage: "number")
                    }
                } header: {
                    Text("Already ran a command on the Mac and it shows 6 digits?")
                }
            }
        }
        .navigationTitle("Connect your Mac")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            SBBackend.event("connect_seen")
            // 先把命令亮出来,再问通知权限:用户刚点了「我在 Mac 前」,知道弹窗是为了什么,
            // 而且弹窗背后那行命令已经在了。
            await mint()
            await AppDelegate.requestNotifications()
        }
        .task { await poll() }
        .onDisappear { polling = false }
    }

    private func mint() async {
        guard !minting else { return }
        minting = true
        short = await SBBackend.mintShortCode()
        minting = false
    }

    private func poll() async {
        let started = Date()
        while polling && hostFound.isEmpty {
            if let obj = await SBBackend.getJSON("/api/state") as? [String: Any],
               let host = obj.keys.first {
                hostFound = host
                UserDefaults.standard.set(true, forKey: "sb.macSeen")
                SBBackend.event("paired")
                await EventStore.shared.refresh()
                // 引导的最后一步就是第一张卡:灵动岛 / 锁屏上立刻出现面板。
                if #available(iOS 17.2, *) { _ = await LiveActivityManager.shared.reviveDashboard() }
                UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [OnboardingView.reminderId])
                return
            }
            if Date().timeIntervalSince(started) > 120 { waitedLong = true }
            if short?.isExpired ?? false { await mint() }
            try? await Task.sleep(for: .seconds(4))
        }
    }
}

// MARK: 屏 2b:Mac 先跑了脚本 —— 输 6 位数字(扫码进来会预填)

private struct CodeEntryStep: View {
    let prefill: String?
    let onDone: () -> Void
    let onManual: () -> Void
    @State private var code = ""
    @State private var busy = false
    @State private var error = ""
    @State private var host = ""
    @FocusState private var focused: Bool

    var body: some View {
        Form {
            Section {
                TextField("483 920", text: $code)
                    .keyboardType(.numberPad)
                    .font(.system(size: 34, weight: .semibold, design: .monospaced))
                    .multilineTextAlignment(.center)
                    .focused($focused)
                    .onChange(of: code) { _, new in
                        let digits = String(new.filter(\.isNumber).prefix(6))
                        if digits != new { code = digits }
                        if digits.count == 6 { submit() }
                    }
                    .disabled(busy || !host.isEmpty)
                if busy { HStack { ProgressView(); Text("Connecting…").foregroundStyle(.secondary) } }
                if !error.isEmpty { Text(error).font(.footnote).foregroundStyle(.red) }
                if !host.isEmpty {
                    Label("Connected to \(host) 🎉", systemImage: "checkmark.circle.fill")
                        .font(.headline).foregroundStyle(.green)
                    Label("Look up: the panel is already in the Dynamic Island. Lock the phone and it's on the Lock Screen too.", systemImage: "platter.filled.top.iphone")
                        .font(.footnote).foregroundStyle(.secondary)
                    Button {
                        onDone()
                    } label: {
                        Text("Open SessionBell").font(.headline).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                }
            } header: {
                Text("The 6 digits on the Mac screen")
            } footer: {
                Text("After the command finishes on the Mac, the digits are printed in Terminal and shown on the web page that opens. Scanning that page's QR code with the Camera app fills them in for you.")
            }
            Section {
                Button("I have a long pairing code or a self-hosted server") { onManual() }
                    .font(.subheadline)
            }
        }
        .navigationTitle("Digits from the Mac")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            if let prefill, code.isEmpty { code = prefill } else { focused = true }
        }
    }

    private func submit() {
        guard !busy else { return }
        busy = true
        error = ""
        focused = false
        Task {
            await AppDelegate.requestNotifications()
            if let err = await SBBackend.redeemShortCode(code) {
                error = err
                busy = false
                code = ""
                focused = true
                return
            }
            SBBackend.event("code_entered")
            await OnboardingView.registerTokens()
            let state = await SBBackend.getJSON("/api/state") as? [String: Any]
            host = state?.keys.first ?? String(localized: "your Mac")
            UserDefaults.standard.set(true, forKey: "sb.macSeen")
            SBBackend.event("paired")
            await EventStore.shared.refresh()
            if #available(iOS 17.2, *) { _ = await LiveActivityManager.shared.reviveDashboard() }
            busy = false
        }
    }
}

// MARK: 屏 2c:粘配对码,或自托管手动填地址+密钥。

private struct ManualStep: View {
    let onSuccess: () -> Void
    @State private var pairing = ""
    @State private var url = ""
    @State private var secret = ""
    @State private var status = ""

    var body: some View {
        Form {
            Section {
                TextField("Paste pairing code", text: $pairing)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.system(.caption, design: .monospaced))
                    .onChange(of: pairing) { _, code in
                        guard SBBackend.adoptPairingCode(code) else { return }
                        finish()
                    }
            } header: {
                Text("Have a Pairing Code")
            } footer: {
                Text("The base64 string generated by the person who invited you. Paste it and you're done.")
            }

            Section {
                TextField("https://your-worker-address", text: $url)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(.system(.caption, design: .monospaced))
                SecureField("Tenant secret", text: $secret)
                    .font(.system(.caption, design: .monospaced))
                Button("Connect") {
                    SBBackend.save(url: url.trimmingCharacters(in: .whitespacesAndNewlines),
                                   secret: secret.trimmingCharacters(in: .whitespacesAndNewlines))
                    finish()
                }
                .disabled(url.isEmpty || secret.isEmpty)
            } header: {
                Text("Self-Hosted")
            } footer: {
                Text("Deployment guide at github.com/westie-ai/session-bell")
            }

            if !status.isEmpty {
                Text(status)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(status.hasPrefix("✅") ? .green : .red)
            }
        }
        .navigationTitle("Setup")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func finish() {
        status = String(localized: "⏳ Testing connection…")
        Task {
            await AppDelegate.requestNotifications()
            status = await SBBackend.ping()
            if status.hasPrefix("✅") {
                await OnboardingView.registerTokens()
                try? await Task.sleep(for: .seconds(0.8))
                onSuccess()
            }
        }
    }
}

extension OnboardingView {
    static let reminderId = "sb.connect-reminder"

    /// 接入成功后把手机的推送坐标交给后端。
    static func registerTokens() async {
        guard let backend = SBBackend.saved else { return }
        let device = await MainActor.run { EventStore.shared.deviceToken }
        if !device.isEmpty {
            await SBBackend.post("/api/token", body: ["device_token": device],
                                 to: backend.url, secret: backend.secret)
        }
        if #available(iOS 17.2, *) { LiveActivityManager.shared.syncNow() }
    }

    /// 「现在不在 Mac 前」:24 小时后本地提醒一次,配对成功时撤销。
    static func scheduleConnectReminder() {
        let content = UNMutableNotificationContent()
        content.title = String(localized: "Your Mac isn't connected yet")
        content.body = String(localized: "One line in Terminal and your Claude Code sessions land on this Lock Screen.")
        content.sound = .default
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 24 * 3600, repeats: false)
        let req = UNNotificationRequest(identifier: reminderId, content: content, trigger: trigger)
        UNUserNotificationCenter.current().add(req)
    }
}
