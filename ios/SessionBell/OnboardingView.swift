import SwiftUI

/// 首跑:欢迎(开始使用 / 先看看演示 / 配对码·自托管)→ 连接 Mac。
/// 开放注册,点「开始使用」直接开一个空租户;演示走共享 demo 租户,
/// 之后在任务页横幅里一键换成自己的空间(从 .connectMac 进来)。
/// SBBackend.saved 已存在的老用户不会看到这里(ContentView 里判断)。
struct OnboardingView: View {
    enum Step { case welcome, manual, connectMac }
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
                case .manual: ManualStep(onSuccess: { step = .connectMac })
                case .connectMac: ConnectMacStep(onDone: onDone)
                }
            }
            .toolbar {
                if step == .manual {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Back") { step = .welcome }
                    }
                }
                if step == .connectMac {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Connect Later") { onDone() }
                    }
                }
            }
        }
        .interactiveDismissDisabled()
    }

    private var welcome: some View {
        VStack(spacing: 0) {
            Spacer()
            Image(systemName: "bell.badge.waveform.fill")
                .font(.system(size: 64))
                .foregroundStyle(Color.sbAccent.gradient)
                .padding(.bottom, 20)
            Text("Your Agents, on the Lock Screen")
                .font(.largeTitle.bold())
            Text("A phone command center for\nClaude Code and other local coding agents")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)

            VStack(alignment: .leading, spacing: 16) {
                featureRow("bell.badge", "Waiting, finished, or needs approval — pushed straight to you",
                           "Stays quiet while you're at the Mac")
                featureRow("platter.filled.bottom.iphone", "One Lock Screen panel for every Mac",
                           "Waiting / running / done, with live timers")
                featureRow("checkmark.shield", "Approve permission requests from the Lock Screen",
                           "Allow or deny without going back to the Mac")
            }
            .padding(.horizontal, 32)
            .padding(.top, 36)

            Spacer()
            Spacer()

            VStack(spacing: 10) {
                if !error.isEmpty {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                }
                Button {
                    start(demo: false)
                } label: {
                    Group {
                        if busy { ProgressView().tint(.white) }
                        else { Text("Get Started").font(.headline) }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .disabled(busy)

                Button {
                    start(demo: true)
                } label: {
                    Text("Try the demo first")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.bordered)
                .disabled(busy)

                Button("Have a pairing code / self-hosted") { step = .manual }
                    .font(.subheadline)
                    .padding(.top, 2)
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 24)
        }
    }

    /// 开放注册:真实空间 → 去连 Mac;演示 → 直接进 App 看模拟任务。
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
            if demo { onDone() } else { step = .connectMac }
        }
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

/// 屏 2b:粘配对码,或自托管手动填地址+密钥。
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
            status = await SBBackend.ping()
            if status.hasPrefix("✅") {
                await OnboardingView.registerTokens()
                try? await Task.sleep(for: .seconds(0.8))
                onSuccess()
            }
        }
    }
}

/// 屏 3:连接 Mac —— 拷贝配对命令,轮询心跳。
private struct ConnectMacStep: View {
    let onDone: () -> Void
    @State private var copiedCmd = false
    @State private var copiedPkg = false
    @State private var hostFound = ""
    @State private var polling = true
    @State private var waitedLong = false

    private var pairCommand: String {
        "sessionbell pair \(SBBackend.pairingCode ?? "")"
    }

    var body: some View {
        List {
            Section {
                stepRow(no: "1", title: "Download and install SessionBell.pkg") {
                    Button {
                        UIPasteboard.general.string = "\(SBBackend.hostedBase)/SessionBell.pkg"
                        flash($copiedPkg)
                    } label: {
                        Label(copiedPkg ? "Copied ✓" : "sessionbell.westie.ai/SessionBell.pkg",
                              systemImage: copiedPkg ? "checkmark" : "doc.on.doc")
                            .font(.system(.caption, design: .monospaced))
                    }
                }
                stepRow(no: "2", title: "Copy the pair command, paste it in Terminal on the Mac and press Return") {
                    Button {
                        UIPasteboard.general.string = pairCommand
                        flash($copiedCmd)
                    } label: {
                        Label(copiedCmd ? "Copied ✓ Press ⌘V on a Mac with the same Apple Account"
                                        : "sessionbell pair ••••••",
                              systemImage: copiedCmd ? "checkmark" : "doc.on.doc")
                            .font(.system(.caption, design: .monospaced))
                    }
                }
            } header: {
                Text("On the Mac (two steps)")
            } footer: {
                Text("Universal Clipboard carries it to the Mac after copying. AirDrop works too.")
            }

            Section {
                if !hostFound.isEmpty {
                    Label("Connected to \(hostFound) 🎉", systemImage: "checkmark.circle.fill")
                        .font(.headline)
                        .foregroundStyle(.green)
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
                        Text("Waiting for the Mac's first heartbeat…")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    if waitedLong {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Not connected yet? Check in order:")
                                .font(.footnote.weight(.semibold))
                            Text("① Is the pkg installed? (The sessionbell command only exists after that.)\n② Was the whole command pasted? (It's long, don't truncate it.)\n③ Did you allow every permission prompt on the Mac?")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                Text("Connection")
            }
        }
        .navigationTitle("Connect Mac")
        .navigationBarTitleDisplayMode(.inline)
        .task { await poll() }
        .onDisappear { polling = false }
    }

    private func stepRow(no: String, title: LocalizedStringKey,
                         @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(no)
                    .font(.caption.bold())
                    .frame(width: 20, height: 20)
                    .background(Color.sbAccent.opacity(0.22), in: Circle())
                    .foregroundStyle(Color.sbAccentDeep)
                Text(title).font(.subheadline.weight(.medium))
            }
            content().padding(.leading, 28)
        }
        .padding(.vertical, 4)
    }

    private func flash(_ flag: Binding<Bool>) {
        flag.wrappedValue = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { flag.wrappedValue = false }
    }

    private func poll() async {
        let started = Date()
        while polling && hostFound.isEmpty {
            if let obj = await SBBackend.getJSON("/api/state") as? [String: Any],
               let host = obj.keys.first {
                hostFound = host
                await EventStore.shared.refresh()
                return
            }
            if Date().timeIntervalSince(started) > 120 { waitedLong = true }
            try? await Task.sleep(for: .seconds(5))
        }
    }
}

extension OnboardingView {
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
}
