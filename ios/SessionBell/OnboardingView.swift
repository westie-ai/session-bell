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
                        Button("返回") { step = .welcome }
                    }
                }
                if step == .connectMac {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("稍后连接") { onDone() }
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
                .foregroundStyle(.orange.gradient)
                .padding(.bottom, 20)
            Text("把 Agent 装进锁屏")
                .font(.largeTitle.bold())
            Text("Claude Code 等本地编程 agent 的\n手机指挥台")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)

            VStack(alignment: .leading, spacing: 16) {
                featureRow("bell.badge", "任务等你、跑完、要授权 — 推送直达",
                           "在电脑前时不会吵你")
                featureRow("platter.filled.bottom.iphone", "锁屏面板聚合所有 Mac 的任务",
                           "等待 / 运行 / 完成,实时走秒")
                featureRow("checkmark.shield", "授权请求锁屏一键批准",
                           "允许 / 拒绝,不用回电脑")
            }
            .padding(.horizontal, 32)
            .padding(.top, 36)

            Spacer()
            Spacer()

            VStack(spacing: 10) {
                Button {
                    step = .invite
                } label: {
                    Text("我有邀请码")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)

                Button("已有配对码 / 自托管") { step = .manual }
                    .font(.subheadline)
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 24)
        }
    }

    private func featureRow(_ icon: String, _ title: String, _ sub: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(.orange)
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
                .foregroundStyle(.orange)
            Text("输入邀请码")
                .font(.title2.bold())
            Text("邀请你的人会把邀请码发给你。\n注册后你会得到一个独立的专属空间。")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            TextField("邀请码", text: $invite)
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
                    else { Text("开始使用").font(.headline) }
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
                TextField("粘贴配对码", text: $pairing)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.system(.caption, design: .monospaced))
                    .onChange(of: pairing) { _, code in
                        guard SBBackend.adoptPairingCode(code) else { return }
                        finish()
                    }
            } header: {
                Text("有配对码")
            } footer: {
                Text("邀请你的人生成的那串 base64。粘上即自动完成。")
            }

            Section {
                TextField("https://你的 Worker 地址", text: $url)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(.system(.caption, design: .monospaced))
                SecureField("租户密钥", text: $secret)
                    .font(.system(.caption, design: .monospaced))
                Button("连接") {
                    SBBackend.save(url: url.trimmingCharacters(in: .whitespacesAndNewlines),
                                   secret: secret.trimmingCharacters(in: .whitespacesAndNewlines))
                    finish()
                }
                .disabled(url.isEmpty || secret.isEmpty)
            } header: {
                Text("自托管")
            } footer: {
                Text("部署指南见 github.com/westie-ai/session-bell")
            }

            if !status.isEmpty {
                Text(status)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(status.hasPrefix("✅") ? .green : .red)
            }
        }
        .navigationTitle("接入")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func finish() {
        status = "⏳ 测试连接…"
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
                stepRow(no: "1", title: "下载并安装 SessionBell.pkg") {
                    Button {
                        UIPasteboard.general.string = "\(SBBackend.hostedBase)/SessionBell.pkg"
                        flash($copiedPkg)
                    } label: {
                        Label(copiedPkg ? "已拷贝 ✓" : "sessionbell.westie.ai/SessionBell.pkg",
                              systemImage: copiedPkg ? "checkmark" : "doc.on.doc")
                            .font(.system(.caption, design: .monospaced))
                    }
                }
                stepRow(no: "2", title: "拷贝配对命令,到 Mac 终端里粘贴回车") {
                    Button {
                        UIPasteboard.general.string = pairCommand
                        flash($copiedCmd)
                    } label: {
                        Label(copiedCmd ? "已拷贝 ✓ 同一 Apple 账号的 Mac 直接 ⌘V"
                                        : "sessionbell pair ••••••",
                              systemImage: copiedCmd ? "checkmark" : "doc.on.doc")
                            .font(.system(.caption, design: .monospaced))
                    }
                }
            } header: {
                Text("在 Mac 上(两步)")
            } footer: {
                Text("拷贝后靠通用剪贴板直达 Mac;也可以用隔空投送把命令发过去。")
            }

            Section {
                if !hostFound.isEmpty {
                    Label("已连接 \(hostFound) 🎉", systemImage: "checkmark.circle.fill")
                        .font(.headline)
                        .foregroundStyle(.green)
                    Button {
                        onDone()
                    } label: {
                        Text("进入 SessionBell")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("等待 Mac 的第一个心跳…")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    if waitedLong {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("还没连上?按顺序检查:")
                                .font(.footnote.weight(.semibold))
                            Text("① pkg 装完了吗(装完终端里才有 sessionbell 命令)\n② 命令是完整粘贴的吗(很长,别截断)\n③ Mac 弹的权限框都点了允许吗")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                Text("连接状态")
            }
        }
        .navigationTitle("连接 Mac")
        .navigationBarTitleDisplayMode(.inline)
        .task { await poll() }
        .onDisappear { polling = false }
    }

    private func stepRow(no: String, title: String,
                         @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(no)
                    .font(.caption.bold())
                    .frame(width: 20, height: 20)
                    .background(.orange.opacity(0.15), in: Circle())
                    .foregroundStyle(.orange)
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
