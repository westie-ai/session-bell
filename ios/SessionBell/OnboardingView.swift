import SwiftUI

/// 首跑三屏:欢迎 → 接入(邀请码 / 配对码 / 自托管)→ 连接 Mac。
/// SBBackend.saved 已存在的老用户不会看到这里(ContentView 里判断)。
struct OnboardingView: View {
    enum Step { case welcome, invite, manual, connectMac }
    @State private var step: Step = .welcome
    let onDone: () -> Void

    var body: some View {
        NavigationStack {
            Group {
                switch step {
                case .welcome: welcome
                case .invite: InviteStep(onSuccess: { step = .connectMac })
                case .manual: ManualStep(onSuccess: { step = .connectMac })
                case .connectMac: ConnectMacStep(onDone: onDone)
                }
            }
            .toolbar {
                if step == .invite || step == .manual {
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
                Button {
                    step = .invite
                } label: {
                    Text("I Have an Invite Code")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)

                Button("Have a pairing code / self-hosted") { step = .manual }
                    .font(.subheadline)
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 24)
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

/// 屏 2a:输入邀请码 → /api/signup → 自动配好一切。
private struct InviteStep: View {
    let onSuccess: () -> Void
    @State private var invite = ""
    @State private var busy = false
    @State private var error = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "ticket")
                .font(.system(size: 44))
                .foregroundStyle(Color.sbAccentDeep)
            Text("Enter Invite Code")
                .font(.title2.bold())
            Text("The person who invited you will send you a code.\nSigning up gives you your own private space.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            TextField("Invite code", text: $invite)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.system(.title3, design: .monospaced))
                .multilineTextAlignment(.center)
                .padding(.vertical, 12)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
                .focused($focused)
                .onSubmit { submit() }
                .padding(.horizontal, 36)

            if !error.isEmpty {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 36)
            }

            Button {
                submit()
            } label: {
                Group {
                    if busy { ProgressView().tint(.white) }
                    else { Text("Get Started").font(.headline) }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .disabled(invite.trimmingCharacters(in: .whitespaces).isEmpty || busy)
            .padding(.horizontal, 28)

            Spacer()
            Spacer()
        }
        .onAppear { focused = true }
    }

    private func submit() {
        let code = invite.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty, !busy else { return }
        busy = true
        error = ""
        Task {
            if let err = await SBBackend.signup(invite: code) {
                error = err
                busy = false
            } else {
                await OnboardingView.registerTokens()
                busy = false
                onSuccess()
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
