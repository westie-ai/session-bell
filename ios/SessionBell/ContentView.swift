import SwiftUI
import UserNotifications

struct ContentView: View {
    @EnvironmentObject var store: EventStore
    @State private var copied = false
    @State private var copiedPTS = false
    @State private var copiedPair = false
    @State private var localTestResult = ""

    @State private var navPath = NavigationPath()
    @AppStorage("sb.tab") private var selectedTab = 0
    @AppStorage("sb.onboarded") private var onboarded = false
    @State private var showOnboarding = false

    var body: some View {
        TabView(selection: $selectedTab) {
            tasksTab
                .tabItem { Label("任务", systemImage: "bolt.horizontal.circle") }
                .badge(approvalBadge)
                .tag(0)
            usageTab
                .tabItem { Label("用量", systemImage: "gauge.with.needle") }
                .tag(1)
            settingsTab
                .tabItem { Label("设置", systemImage: "gearshape") }
                .tag(2)
        }
        .task {
            if #available(iOS 17.2, *) { LiveActivityManager.shared.syncNow() }
        }
        .onReceive(store.$openSessionId) { sessionId in
            guard let sessionId,
                  let group = store.groups.first(where: { $0.sessionId == sessionId })
            else { return }
            selectedTab = 0
            navPath = NavigationPath()
            navPath.append(group)
            store.openSessionId = nil
        }
        .onReceive(store.$pendingApproval) { approval in
            if approval != nil { selectedTab = 0 }
        }
        .onAppear {
            if !onboarded && SBBackend.saved == nil { showOnboarding = true }
        }
        .fullScreenCover(isPresented: $showOnboarding) {
            OnboardingView {
                showOnboarding = false
                onboarded = true
                Task { await store.refresh() }
            }
        }
    }

    /// 待批准且还在有效期内 → 任务 Tab 红点
    private var approvalBadge: Int {
        guard let approval = store.pendingApproval,
              Date().timeIntervalSince(approval.date) < 600 else { return 0 }
        return 1
    }

    // MARK: 任务 Tab

    private var tasksTab: some View {
        NavigationStack(path: $navPath) {
            List {
                approvalSection
                liveTasksSection
                sessionsSection
            }
            .overlay {
                if store.pendingApproval == nil && store.liveGroups.isEmpty && store.groups.isEmpty {
                    ContentUnavailableView {
                        Label("还没有任务", systemImage: "bell")
                    } description: {
                        Text(SBBackend.saved == nil
                             ? "先完成接入,Mac 连上来之后任务会出现在这里。"
                             : "在 Mac 上给 agent 派个活:任务会实时出现在这里和锁屏上,完成时手机响铃。")
                    } actions: {
                        if SBBackend.saved == nil {
                            Button("开始接入") { showOnboarding = true }
                                .buttonStyle(.borderedProminent)
                        }
                    }
                }
            }
            .navigationTitle("任务")
            .navigationDestination(for: SessionGroup.self) {
                SessionPage(sessionId: $0.sessionId, project: $0.project)
            }
            .navigationDestination(for: EventStore.LiveTask.self) {
                SessionPage(sessionId: $0.sessionId, project: $0.project, host: $0.host)
            }
            .refreshable { await store.refresh() }
            .toolbar {
                if !store.events.isEmpty {
                    Button("清空", role: .destructive) { store.clearAll() }
                }
            }
        }
    }

    // MARK: 用量 Tab

    private var hasUsage: Bool {
        store.liveGroups.contains {
            !$0.usage.isEmpty || $0.usageFraction != nil || $0.sessionFraction != nil
        }
    }

    private var usageTab: some View {
        NavigationStack {
            List {
                ForEach(store.liveGroups.filter {
                    !$0.usage.isEmpty || $0.usageFraction != nil || $0.sessionFraction != nil
                }) { group in
                    Section("💻 \(group.host)") {
                        UsageDashboard(group: group)
                    }
                }
            }
            .overlay {
                if !hasUsage {
                    ContentUnavailableView {
                        Label("暂无用量数据", systemImage: "gauge.with.needle")
                    } description: {
                        Text("Mac 上跑过任务后,这里显示官方口径的本周额度和高级模型用量,与 /usage 同源。")
                    }
                }
            }
            .navigationTitle("用量")
            .refreshable { await store.refresh() }
        }
    }

    // MARK: 设置 Tab

    private var settingsTab: some View {
        NavigationStack {
            List {
                Section("接入") {
                    backendConfigRow
                    // 加第二台电脑时最常来找的东西——别让它只活在引导第三屏里。
                    if let code = SBBackend.pairingCode {
                        Button {
                            UIPasteboard.general.string = "sessionbell pair \(code)"
                            copiedPair = true
                            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { copiedPair = false }
                        } label: {
                            Label(copiedPair ? "已拷贝 ✓" : "拷贝配对命令(接入新电脑)",
                                  systemImage: copiedPair ? "checkmark" : "doc.on.doc")
                        }
                    }
                }
                machinesSection
                deviceSection
                Section("关于") {
                    LabeledContent("版本",
                        value: "\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?") (\(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"))")
                    Link(destination: URL(string: "https://github.com/westie-ai/session-bell")!) {
                        Label("开源仓库 · westie-ai/session-bell", systemImage: "chevron.left.forwardslash.chevron.right")
                    }
                    Button {
                        showOnboarding = true
                    } label: {
                        Label("重新打开接入引导", systemImage: "arrow.counterclockwise")
                    }
                }
            }
            .navigationTitle("设置")
        }
    }

    @ViewBuilder
    private var machinesSection: some View {
        if !store.liveGroups.isEmpty {
            Section("电脑") {
                ForEach(store.liveGroups) { group in
                    HStack {
                        Label(group.host, systemImage: "desktopcomputer")
                        Spacer()
                        if group.awake {
                            Text("常亮中")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                        Text("\(group.cards.count) 个任务")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var approvalSection: some View {
        if let approval = store.pendingApproval,
           Date().timeIntervalSince(approval.date) < 600 {
            Section("待批准") {
                VStack(alignment: .leading, spacing: 10) {
                    Text(approval.summary.isEmpty ? "Claude 请求授权" : approval.summary)
                        .font(.system(.footnote, design: .monospaced))
                    HStack(spacing: 12) {
                        Button {
                            Task { await store.sendDecision("allow") }
                        } label: {
                            Label("允许", systemImage: "checkmark")
                                .font(.headline)
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.green)

                        Button {
                            Task { await store.sendDecision("deny") }
                        } label: {
                            Label("拒绝", systemImage: "xmark")
                                .font(.headline)
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(.red)
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }

    @ViewBuilder
    private var liveTasksSection: some View {
        if store.liveGroups.isEmpty {
            if !store.groups.isEmpty {
                Section("进行中") {
                    Text("当前没有活跃任务")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        } else {
            ForEach(store.liveGroups) { group in
                Section {
                    MachineControls(group: group)
                    // 每个主任务一张卡,子 agent 嵌在卡内(用量在「用量」Tab)
                    ForEach(group.cards) { card in
                        SessionCard(card: card) { navPath.append($0) }
                    }
                } header: {
                    Text("💻 \(group.host)")
                }
            }
        }
    }

    private var deviceSection: some View {
        Section("本机") {
            HStack {
                Label("通知权限", systemImage: "bell.badge")
                Spacer()
                Text(authLabel).foregroundStyle(.secondary)
            }
            if store.deviceToken.isEmpty {
                Text("等待 APNs 注册…（需要真机运行并允许通知）")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                tokenRow(label: "Device Token", value: store.deviceToken, flag: $copied)
            }
            if !store.pushToStartToken.isEmpty {
                tokenRow(label: "Live Activity 启动 Token", value: store.pushToStartToken, flag: $copiedPTS)
            }
            if #available(iOS 17.2, *) {
                Button {
                    Task {
                        localTestResult = "唤起中…"
                        localTestResult = await LiveActivityManager.shared.reviveDashboard()
                    }
                } label: {
                    Label(localTestResult.isEmpty ? "唤起锁屏面板" : localTestResult,
                          systemImage: "bell.badge.waveform")
                }
            }
        }
    }

    @State private var backendURL = SBBackend.saved?.url ?? ""
    @State private var backendSecret = SBBackend.saved?.secret ?? ""
    @State private var backendSaved = false

    @State private var pairingCode = ""
    @State private var pingResult = ""

    private var backendConfigRow: some View {
        DisclosureGroup {
            TextField("粘贴配对码(自动填充下方)", text: $pairingCode)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.system(.caption, design: .monospaced))
                .onChange(of: pairingCode) { _, code in
                    guard let data = Data(base64Encoded: code.trimmingCharacters(in: .whitespacesAndNewlines)),
                          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String],
                          let u = obj["u"], let s = obj["s"] else { return }
                    backendURL = u
                    backendSecret = s
                    SBBackend.save(url: u, secret: s)
                    backendSaved = true
                    Task { pingResult = await SBBackend.ping() }
                    let token = EventStore.shared.deviceToken
                    if !token.isEmpty {
                        Task { await SBBackend.post("/api/token", body: ["device_token": token],
                                                    to: u, secret: s) }
                    }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { backendSaved = false }
                }
            TextField("https://xxx.vercel.app", text: $backendURL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .font(.system(.caption, design: .monospaced))
            SecureField("共享密钥(SB_SECRET)", text: $backendSecret)
                .font(.system(.caption, design: .monospaced))
            Button {
                SBBackend.save(url: backendURL.trimmingCharacters(in: .whitespacesAndNewlines),
                               secret: backendSecret.trimmingCharacters(in: .whitespacesAndNewlines))
                backendSaved = true
                Task { pingResult = await SBBackend.ping() }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { backendSaved = false }
            } label: {
                Label(backendSaved ? "已保存 ✓" : "保存",
                      systemImage: backendSaved ? "checkmark" : "externaldrive")
            }
            .disabled(backendURL.isEmpty || backendSecret.isEmpty)
            Button {
                pingResult = "⏳ 测试中…"
                Task { pingResult = await SBBackend.ping() }
            } label: {
                Label("测试连接", systemImage: "waveform.path.ecg")
            }
            if !pingResult.isEmpty {
                Text(pingResult)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(pingResult.hasPrefix("✅") ? .green : .red)
                    .textSelection(.enabled)
            }
        } label: {
            Label(SBBackend.saved.map { "后端:\(URL(string: $0.url)?.host ?? $0.url)" }
                    ?? "后端配置(未设置)",
                  systemImage: "server.rack")
                .foregroundStyle(SBBackend.saved == nil ? .orange : .primary)
        }
    }

    private func tokenRow(label: String, value: String, flag: Binding<Bool>) -> some View {
        Button {
            UIPasteboard.general.string = value
            flag.wrappedValue = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { flag.wrappedValue = false }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Label(flag.wrappedValue ? "已拷贝 ✓" : "\(label)（点按拷贝）",
                      systemImage: flag.wrappedValue ? "checkmark" : "doc.on.doc")
                Text(value)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }

    @ViewBuilder
    private var sessionsSection: some View {
        // 已经以活跃卡片露脸的 session 不再在历史里重复一行。
        let liveIds = Set(store.liveTasks.map(\.sessionId))
        let history = store.groups.filter { !liveIds.contains($0.sessionId) }
        if !history.isEmpty {
            Section("通知历史") {
                ForEach(history) { group in
                    NavigationLink(value: group) {
                        SessionRow(group: group)
                    }
                }
            }
        }
    }

    private var authLabel: String {
        switch store.authStatus {
        case .authorized: return "已开启"
        case .denied: return "已拒绝（去设置打开）"
        case .notDetermined: return "未询问"
        default: return "受限"
        }
    }
}

struct MachineControls: View {
    let group: EventStore.HostGroup
    @EnvironmentObject var store: EventStore
    @State private var caffePending = false
    @State private var showSpawn = false

    var body: some View {
        HStack(spacing: 12) {
            Button {
                caffePending = true
                Task {
                    await store.sendMachineCommand("_sys-\(group.canonicalKey)",
                        text: group.awake ? "caffeinate:off" : "caffeinate:on")
                    try? await Task.sleep(for: .seconds(12))
                    await store.fetchLiveTasks()
                    caffePending = false
                }
            } label: {
                Label(caffePending ? "生效中…" : (group.awake ? "常亮中" : "防熄屏"),
                      systemImage: group.awake ? "cup.and.saucer.fill" : "cup.and.saucer")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(group.awake ? .orange : .secondary)
            }
            .buttonStyle(.bordered)
            .disabled(caffePending)

            Spacer()

            Button {
                showSpawn = true
            } label: {
                Label("新建 session", systemImage: "plus.circle.fill")
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.bordered)
            .tint(Color.sbAccentDeep)
        }
        .sheet(isPresented: $showSpawn) {
            SpawnSheet(group: group)
        }
    }
}

struct SpawnSheet: View {
    let group: EventStore.HostGroup
    @EnvironmentObject var store: EventStore
    @Environment(\.dismiss) private var dismiss
    @State private var cwd = ""
    @State private var prompt = ""
    @State private var permMode = "auto"
    @State private var sent = false

    var body: some View {
        NavigationStack {
            Form {
                Section("项目目录") {
                    ForEach(group.spawnDirs, id: \.self) { path in
                        Button {
                            cwd = path
                        } label: {
                            HStack {
                                Text(path).font(.system(.caption, design: .monospaced))
                                    .lineLimit(1).truncationMode(.head)
                                Spacer()
                                if cwd == path {
                                    Image(systemName: "checkmark").foregroundStyle(.green)
                                }
                            }
                        }
                        .foregroundStyle(.primary)
                    }
                    TextField("或手动输入路径", text: $cwd)
                        .font(.system(.caption, design: .monospaced))
                        .autocorrectionDisabled()
                }
                Section("第一条指令") {
                    TextField("让它做什么…", text: $prompt, axis: .vertical)
                        .lineLimit(3...6)
                }
                Section("权限模式") {
                    Picker("权限模式", selection: $permMode) {
                        Text("普通").tag("default")
                        Text("⏵⏵ Auto").tag("auto")
                        Text("Bypass").tag("bypass")
                    }
                    .pickerStyle(.segmented)
                    Text(permMode == "default"
                         ? "所有授权推手机批准,最稳"
                         : permMode == "auto"
                         ? "auto mode:安全操作自动过,存疑的推手机批准(推荐)"
                         : "什么都不问,一路到底;该机器首次需在电脑上接受一次警告")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Section {
                    Button {
                        let body = ["cwd": cwd, "prompt": prompt, "mode": permMode]
                        guard let data = try? JSONSerialization.data(withJSONObject: body),
                              let json = String(data: data, encoding: .utf8) else { return }
                        sent = true
                        Task {
                            await store.sendMachineCommand("_spawn-\(group.canonicalKey)",
                                                           text: json)
                            try? await Task.sleep(for: .seconds(1))
                            dismiss()
                        }
                    } label: {
                        Label(sent ? "已发送,约 10 秒后启动" : "🚀 在 \(group.host) 上启动",
                              systemImage: "paperplane.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .disabled(cwd.isEmpty || prompt.trimmingCharacters(in: .whitespaces).isEmpty || sent)
                } footer: {
                    Text("在那台电脑的 Otty 新窗口里启动交互式 session——回到桌面即可接管;任务出现在面板上,完成推送结果。Otty 未运行时自动改为后台无头执行。")
                }
            }
            .navigationTitle("新建 session")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                if cwd.isEmpty {
                    cwd = group.latestCwd.isEmpty
                        ? (group.spawnDirs.first ?? "") : group.latestCwd
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
    }
}

struct UsageDashboard: View {
    let group: EventStore.HostGroup

    private func statusColor(_ f: Double) -> Color {
        f > 0.85 ? .red : f > 0.6 ? .orange : .green
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // 5 小时窗口最先耗尽,放最上面。
            if let sessionFraction = group.sessionFraction {
                Meter(icon: "clock", label: "5 小时窗口",
                      fraction: sessionFraction, color: statusColor(sessionFraction),
                      detail: group.sessionText)
            }
            if let fraction = group.usageFraction {
                Meter(icon: "gauge.with.needle", label: "本周额度",
                      fraction: fraction, color: statusColor(fraction),
                      detail: group.usage)
            } else if !group.usage.isEmpty {
                Text(group.usage).font(.caption2).foregroundStyle(.secondary)
            }
            if let fableFraction = group.fableFraction {
                Meter(icon: "sparkles", label: "高级模型",
                      fraction: fableFraction, color: .purple,
                      detail: group.fableText)
            } else if !group.fableText.isEmpty {
                HStack(spacing: 5) {
                    Image(systemName: "sparkles").font(.caption2)
                    Text(group.fableText).font(.caption2)
                }
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
    }
}

struct Meter: View {
    let icon: String
    let label: String
    let fraction: Double
    let color: Color
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Label(label, systemImage: icon)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(Int(min(fraction, 9.99) * 100))")
                    .font(.system(.title2, design: .rounded).weight(.bold))
                    .foregroundStyle(color)
                    .monospacedDigit()
                + Text(" %")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(color)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    Capsule()
                        .fill(color.gradient)
                        .frame(width: max(6, geo.size.width * min(fraction, 1.0)))
                }
            }
            .frame(height: 6)
            if !detail.isEmpty {
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

struct SessionCard: View {
    let card: EventStore.TaskCard
    let onTap: (EventStore.LiveTask) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                LiveTaskRow(task: card.root, showHost: false)
                Spacer(minLength: 4)
                Image(systemName: "chevron.right")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
            .onTapGesture { onTap(card.root) }

            if !card.subs.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(card.subs) { sub in
                        Divider().padding(.vertical, 6)
                        HStack {
                            LiveTaskRow(task: sub, showHost: false)
                            Spacer(minLength: 4)
                            Image(systemName: "chevron.right")
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                        .onTapGesture { onTap(sub) }
                    }
                }
                .padding(.leading, 14)
                .overlay(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 1)
                        .fill(Color.blue.opacity(0.25))
                        .frame(width: 2)
                        .padding(.vertical, 6)
                }
            }
        }
        .padding(.vertical, 2)
    }
}

struct LiveTaskRow: View {
    let task: EventStore.LiveTask
    var showHost = true

    private var color: Color {
        switch task.status {
        case "waiting": return .orange
        case "running": return .blue
        default: return .green
        }
    }

    private var symbol: String {
        switch task.status {
        case "waiting": return "hand.raised.fill"
        case "running": return "play.circle.fill"
        default: return "checkmark.circle.fill"
        }
    }

    private var statusLabel: String {
        switch task.status {
        case "waiting": return "等待你"
        case "running": return "运行中"
        default: return "已完成"
        }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: task.isSub ? 7 : 9, style: .continuous)
                    .fill(color.opacity(0.15))
                Image(systemName: symbol)
                    .font(.system(size: task.isSub ? 12 : 15, weight: .semibold))
                    .foregroundStyle(color)
            }
            .frame(width: task.isSub ? 26 : 32, height: task.isSub ? 26 : 32)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 5) {
                    Text(task.project)
                        .font(task.isSub ? .footnote.weight(.medium)
                              : .subheadline.weight(.semibold))
                        .lineLimit(1)
                    if task.isSub {
                        Text("子 agent")
                            .font(.caption2)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.blue.opacity(0.12), in: Capsule())
                            .foregroundStyle(.blue)
                    }
                    if task.engine == "codex" {
                        Text("CODEX")
                            .font(.caption2.bold())
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.teal.opacity(0.14), in: Capsule())
                            .foregroundStyle(.teal)
                    }
                    if let badge = EventStore.modeBadge(task.mode) {
                        let color: Color = badge.tone == "plan" ? .purple
                            : badge.tone == "auto" ? .orange : .red
                        Text(badge.text)
                            .font(.caption2.bold())
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(color.opacity(0.12), in: Capsule())
                            .foregroundStyle(color)
                    }
                    if task.agents > 0 {
                        Label("\(task.agents)", systemImage: "gearshape.2.fill")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.blue)
                            .labelStyle(.titleAndIcon)
                    }
                    if showHost {
                        Text(task.host)
                            .font(.caption2)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(.quaternary, in: Capsule())
                            .foregroundStyle(.secondary)
                    }
                }
                if !task.detail.isEmpty {
                    Text(task.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 6)

            VStack(alignment: .trailing, spacing: 2) {
                Text(statusLabel)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(color)
                Text(task.since, style: .relative)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 3)
    }
}

struct SessionRow: View {
    let group: SessionGroup

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: group.latest.kind.symbol)
                .font(.title2)
                .foregroundStyle(group.latest.kind.color)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(group.project).font(.headline)
                    if let host = group.latest.host {
                        Text(host)
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.quaternary, in: Capsule())
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(group.latest.date, style: .relative)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text(group.latest.body)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 2)
    }
}

/// 全量通知历史 — 从详情页角落进来,不再占详情页半屏。
struct EventHistoryView: View {
    let group: SessionGroup

    var body: some View {
        List {
            ForEach(group.events) { event in
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: event.kind.symbol)
                        .foregroundStyle(event.kind.color)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(event.kind.label).font(.subheadline.bold())
                            Spacer()
                            Text(event.date, format: .dateTime.month().day().hour().minute())
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        MarkdownText(text: event.md ?? event.body)
                    }
                }
                .padding(.vertical, 2)
            }
        }
        .navigationTitle("通知历史")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// 统一详情页 — 通知点进来、任务卡点进来都是它。
/// 只回答两个问题:最新发生了什么(Claude 最新回复 + 终端画面),
/// 你要发什么(底部输入框,自动选通道)。其余收进角落。
struct SessionPage: View {
    let sessionId: String
    let project: String
    var host: String? = nil
    @EnvironmentObject var store: EventStore
    @State private var input = ""
    @State private var sendNote = ""
    @State private var sendNoteOK = true
    @State private var noteTask: Task<Void, Never>?
    @FocusState private var inputFocused: Bool

    private var liveTask: EventStore.LiveTask? {
        store.liveTasks.first { $0.sessionId == sessionId }
    }
    private var group: SessionGroup? {
        store.groups.first { $0.sessionId == sessionId }
    }
    private var resolvedHost: String? {
        liveTask?.host ?? host ?? group?.events.compactMap(\.host).first
    }
    private var isLive: Bool { liveTask != nil }
    private var isCodex: Bool { liveTask?.engine == "codex" }

    private var peekTask: EventStore.LiveTask? {
        guard let h = resolvedHost else { return nil }
        return liveTask ?? EventStore.LiveTask(
            id: sessionId, sessionId: sessionId, project: project, host: h,
            status: "ended", since: group?.latest.date ?? Date(),
            detail: "", agents: 0)
    }

    private var statusInfo: (label: String, color: Color) {
        switch liveTask?.status {
        case "waiting": return ("等待你", .orange)
        case "running": return ("运行中", .blue)
        case "done": return ("已完成", .green)
        default: return ("已结束", .gray)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    headerCard
                    if !isCodex, let task = peekTask {
                        terminalCard(task)
                    }
                    if let g = group, g.events.count > 1 {
                        NavigationLink {
                            EventHistoryView(group: g)
                        } label: {
                            Label("通知历史(\(g.events.count) 条)",
                                  systemImage: "clock.arrow.circlepath")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .scrollDismissesKeyboard(.interactively)
            .onTapGesture { inputFocused = false }
            inputBar
        }
        .navigationTitle(project)
        .navigationBarTitleDisplayMode(.inline)
    }

    /// 状态和最新内容合成一张卡:状态行是标题,Claude 最新回复是正文。
    /// 通知事件的类型/时间与实时状态说的是同一件事 — 不再各说一遍。
    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle().fill(statusInfo.color).frame(width: 8, height: 8)
                Text(statusInfo.label)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(statusInfo.color)
                if let since = liveTask?.since ?? group?.latest.date {
                    Text(since, style: .relative)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.tertiary)
                }
                Spacer()
                if let h = resolvedHost {
                    Text(h)
                        .font(.caption2)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
            }
            if let detail = liveTask?.detail, !detail.isEmpty {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            if let event = group?.latest {
                let content = event.md ?? event.body
                if !content.isEmpty {
                    Divider()
                    MarkdownText(text: content)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    }

    private func terminalCard(_ task: EventStore.LiveTask) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            TerminalPeek(task: task)
            NavigationLink {
                TerminalView(task: task)
            } label: {
                Label("终端模式(实时输出 + 输入)", systemImage: "terminal.fill")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(Color.sbAccentDeep)
            }
        }
    }

    private var canSend: Bool {
        !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var inputBar: some View {
        VStack(alignment: .leading, spacing: 0) {
            Divider().opacity(0.6)
            // 发送回执:彩色药丸,出现 3 秒自动淡出,不常驻占地方。
            if !sendNote.isEmpty {
                HStack(spacing: 5) {
                    Image(systemName: sendNoteOK
                          ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    Text(sendNote)
                }
                .font(.caption.weight(.medium))
                .foregroundStyle(sendNoteOK ? .green : .orange)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background((sendNoteOK ? Color.green : Color.orange).opacity(0.12),
                            in: Capsule())
                .padding(.top, 10)
                .padding(.leading, 16)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
            HStack(alignment: .bottom, spacing: 8) {
                TextField(isLive ? "下一步做什么…" : "直接打进终端…",
                          text: $input, axis: .vertical)
                    .lineLimit(1...5)
                    .font(.subheadline)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(Color(.systemBackground),
                                in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 22, style: .continuous)
                            .strokeBorder(inputFocused ? Color.sbAccentDeep.opacity(0.7)
                                          : Color(.separator),
                                          lineWidth: inputFocused ? 1.5 : 1)
                    )
                    .focused($inputFocused)
                    .onSubmit { if canSend { send() } }
                Button(action: send) {
                    Image(systemName: "paperplane.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 38, height: 38)
                        .background(canSend ? Color.sbAccentDeep : Color(.systemGray3),
                                    in: Circle())
                }
                .disabled(!canSend)
                .scaleEffect(canSend ? 1 : 0.92)
                .animation(.spring(duration: 0.25), value: canSend)
            }
            .padding(.horizontal, 12)
            .padding(.top, 10)
            .padding(.bottom, 10)
        }
        .background(.bar)
        .animation(.easeOut(duration: 0.25), value: sendNote)
    }

    /// 活跃 session 走队列注入(空闲/结束时自动接上);
    /// 已结束的走原始通道直打终端 pane(还开着就能续)。
    private func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        input = ""
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        Task {
            if isLive {
                guard let backend = SBBackend.saved else {
                    showNote("后端未配置", ok: false)
                    return
                }
                await SBBackend.post("/api/command",
                                     body: ["session_id": sessionId, "text": text],
                                     to: backend.url, secret: backend.secret)
                showNote("已发出 · 任务空闲时自动接上")
            } else if let h = resolvedHost,
                      let data = try? JSONSerialization.data(
                          withJSONObject: ["sid": sessionId, "text": text]),
                      let json = String(data: data, encoding: .utf8) {
                await store.sendMachineCommand(
                    "_type-\(EventStore.canonicalHost(h))", text: json)
                showNote("已打进终端")
            } else {
                showNote("找不到那台电脑,没发出去", ok: false)
            }
        }
    }

    private func showNote(_ text: String, ok: Bool = true) {
        sendNote = text
        sendNoteOK = ok
        noteTask?.cancel()
        noteTask = Task {
            try? await Task.sleep(for: .seconds(3))
            if !Task.isCancelled { sendNote = "" }
        }
    }
}

/// 详情页顶部的终端快照:打开就先亮出后端缓存的最后一帧(电脑离线也有),
/// 随后让 watcher 抓新帧替换 — 先看到,再发指令。
struct TerminalPeek: View {
    let task: EventStore.LiveTask
    @EnvironmentObject var store: EventStore
    @State private var output = ""
    @State private var capDate: Date?
    @State private var lastTs: Double = 0
    @State private var running = true
    @State private var refreshing = true

    private var tailText: String {
        terminalPrettify(output)
            .components(separatedBy: "\n")
            .suffix(14)
            .joined(separator: "\n")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Group {
                if output.isEmpty {
                    Text(refreshing ? "⏳ 正在取最后画面…(约 10 秒)"
                         : "暂无画面 — 那台电脑可能不在线,或终端窗口已关")
                        .foregroundStyle(termFG.opacity(0.55))
                } else {
                    Text(tailText).foregroundStyle(termFG)
                }
            }
            .font(.system(size: 11, design: .monospaced))
            .lineSpacing(2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(termBG, in: RoundedRectangle(cornerRadius: 10))

            HStack(spacing: 6) {
                if refreshing {
                    ProgressView().controlSize(.mini)
                    Text("刷新中…")
                } else if let capDate {
                    Text("画面时间:") + Text(capDate, style: .relative) + Text("前")
                }
                Spacer()
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .task { await refreshLoop() }
        .onDisappear { running = false }
    }

    private func refreshLoop() async {
        // 1. 缓存帧立即可见
        if let cap = await fetchCapture(sessionId: task.sessionId) {
            capDate = cap.date
            lastTs = cap.date.timeIntervalSince1970
            output = cap.text
        }
        // 2. 循环请求新帧,页面开着就保持大约十几秒一帧
        while running {
            refreshing = true
            await store.sendMachineCommand(
                "_tail-\(EventStore.canonicalHost(task.host))", text: task.sessionId)
            for _ in 0..<6 {
                try? await Task.sleep(for: .seconds(2))
                guard running else { return }
                if let cap = await fetchCapture(sessionId: task.sessionId),
                   cap.date.timeIntervalSince1970 > lastTs {
                    capDate = cap.date
                    lastTs = cap.date.timeIntervalSince1970
                    output = cap.text
                    break
                }
            }
            refreshing = false
            try? await Task.sleep(for: .seconds(10))
            guard running else { return }
        }
    }
}

let termBG = Color(red: 0.055, green: 0.06, blue: 0.07)
let termFG = Color(red: 0.80, green: 0.87, blue: 0.80)

/// 折叠连续空行、去行尾空白 — 抓屏原文噪音大,读起来更像终端
func terminalPrettify(_ raw: String) -> String {
    var out: [String] = []
    var blanks = 0
    for line in raw.components(separatedBy: "\n") {
        let trimmed = String(line.reversed().drop(while: { $0 == " " }).reversed())
        if trimmed.isEmpty {
            blanks += 1
            if blanks > 1 { continue }
        } else {
            blanks = 0
        }
        out.append(trimmed)
    }
    return out.joined(separator: "\n")
}

/// 后端缓存的最后一帧抓屏(worker /api/capture,ts 为毫秒)
func fetchCapture(sessionId: String) async -> (date: Date, text: String)? {
    guard let obj = await SBBackend.getJSON("/api/capture?id=\(sessionId)") as? [String: Any],
          let cap = obj["capture"] as? [String: Any],
          let ts = cap["ts"] as? Double,
          let text = cap["text"] as? String else { return nil }
    return (Date(timeIntervalSince1970: ts / 1000), text)
}

/// 手机上的迷你终端:实时输出流 + 直接输入。
/// 输出经 watcher 抓屏回传(约 5-10 秒一帧),输入经注入通道打进真终端。
struct TerminalView: View {
    let task: EventStore.LiveTask
    @EnvironmentObject var store: EventStore
    @State private var output = ""
    @State private var input = ""
    @State private var lastTs: Double = 0
    @State private var running = true
    @State private var sending = false
    @FocusState private var inputFocused: Bool

    private func prettify(_ raw: String) -> String { terminalPrettify(raw) }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    Text(output.isEmpty ? "⏳ 正在连接终端…(首帧约 10 秒)"
                         : prettify(output))
                        .font(.system(size: 12, weight: .regular, design: .monospaced))
                        .lineSpacing(3)
                        .foregroundStyle(termFG)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .textSelection(.enabled)
                        .id("bottom")
                }
                .background(termBG)
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: output) { _, _ in
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo("bottom", anchor: .bottom)
                    }
                }
                .onTapGesture { inputFocused = false }
            }
            Divider().overlay(Color.sbAccentDeep.opacity(0.4))
            HStack(spacing: 8) {
                Text("❯")
                    .font(.system(size: 14, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.sbAccentDeep)
                TextField("", text: $input, axis: .vertical)
                    .lineLimit(1...3)
                    .focused($inputFocused)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(termFG)
                    .tint(Color.sbAccentDeep)
                    .autocorrectionDisabled()
                    .onSubmit { send() }
                Button {
                    send()
                } label: {
                    Image(systemName: sending ? "hourglass" : "arrow.up.circle.fill")
                        .font(.title3)
                }
                .foregroundStyle(Color.sbAccentDeep)
                .disabled(input.trimmingCharacters(in: .whitespaces).isEmpty || sending)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(termBG)
        }
        .background(termBG.ignoresSafeArea())
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("收起键盘") { inputFocused = false }
            }
        }
        .navigationTitle("\(task.project) ❯_")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(termBG, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .task { await refreshLoop() }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear {
            running = false
            UIApplication.shared.isIdleTimerDisabled = false
        }
    }

    private func refreshLoop() async {
        // 先把后端缓存的最后一帧亮出来,再等 watcher 抓新帧。
        if output.isEmpty, let cap = await fetchCapture(sessionId: task.sessionId) {
            lastTs = cap.date.timeIntervalSince1970 * 1000
            output = cap.text
        }
        while running {
            await store.sendMachineCommand(
                "_tail-\(EventStore.canonicalHost(task.host))", text: task.sessionId)
            for _ in 0..<5 {
                try? await Task.sleep(for: .seconds(2))
                guard running else { return }
                if let obj = await SBBackend.getJSON("/api/capture?id=\(task.sessionId)")
                    as? [String: Any],
                   let cap = obj["capture"] as? [String: Any],
                   let ts = cap["ts"] as? Double, ts > lastTs,
                   let text = cap["text"] as? String {
                    lastTs = ts
                    output = text
                    break
                }
            }
            try? await Task.sleep(for: .seconds(2))
        }
    }

    private func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        input = ""
        sending = true
        Task {
            // 原始输入通道:直打 pane 而非 claude 进程,session 结束后
            // 照样可用(比如敲 claude -c 从手机上把会话续起来)。
            if let data = try? JSONSerialization.data(
                   withJSONObject: ["sid": task.sessionId, "text": text]),
               let json = String(data: data, encoding: .utf8) {
                await store.sendMachineCommand(
                    "_type-\(EventStore.canonicalHost(task.host))", text: json)
            }
            try? await Task.sleep(for: .seconds(1))
            sending = false
        }
    }
}
