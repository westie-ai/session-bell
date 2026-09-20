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
    @AppStorage(SBBackend.demoKey) private var isDemo = false
    @State private var showOnboarding = false
    @State private var onboardingStart: OnboardingView.Step = .welcome
    @State private var creatingSpace = false
    @State private var createError = ""
    @State private var showFeedback = false
    @State private var confirmReset = false
    @State private var showAddMac = false
    @State private var showAddDevice = false
    /// 这台手机是否见过任何一台 Mac 的心跳:没见过 → 任务页空态引导去连 Mac,而不是"给 agent 派活"。
    @AppStorage("sb.macSeen") private var macSeen = false

    var body: some View {
        TabView(selection: $selectedTab) {
            tasksTab
                .tabItem { Label("Tasks", systemImage: "bolt.horizontal.circle") }
                .badge(approvalBadge)
                .tag(0)
            usageTab
                .tabItem { Label("Usage", systemImage: "gauge.with.needle") }
                .tag(1)
            settingsTab
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(2)
        }
        .tint(Color.sbAccentText)
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
        .onReceive(store.$liveGroups) { groups in
            if !groups.isEmpty && !isDemo { macSeen = true }
        }
        .onReceive(store.$pendingPairCode) { code in
            guard let code else { return }
            store.pendingPairCode = nil
            // 已经配好真实空间的手机再扫码 = 想加第二台 Mac 或换空间;也走同一页,由用户决定。
            onboardingStart = .code(prefill: code)
            showOnboarding = true
        }
        .onAppear {
            if !onboarded && SBBackend.saved == nil { showOnboarding = true }
        }
        .fullScreenCover(isPresented: $showOnboarding, onDismiss: { onboardingStart = .welcome }) {
            // .id:引导页已经打开时扫码进来(onboardingStart 变成 .code),要重建视图才能落到输码页。
            OnboardingView(initialStep: onboardingStart) {
                showOnboarding = false
                onboarded = true
                Task { await store.refresh() }
            }
            .id(onboardingStart)
        }
    }

    /// 「重置并重新开始」:手机侧全部忘掉,回到第一屏。真机上反复走引导用。
    private func resetEverything() {
        Task {
            if #available(iOS 17.2, *) { await LiveActivityManager.shared.endAll() }
            if let backend = SBBackend.saved {
                await SBBackend.post("/api/token", body: ["reset_dashboard": "1"],
                                     to: backend.url, secret: backend.secret)
            }
            SBBackend.reset()
            store.clearAll()
            store.pendingApproval = nil
            UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [OnboardingView.reminderId])
            isDemo = false
            onboarded = false
            macSeen = false
            selectedTab = 0
            navPath = NavigationPath()
            onboardingStart = .welcome
            showOnboarding = true
        }
    }

    /// 演示租户 → 真实空间:开一个新租户,清空演示数据,直接跳到「连接 Mac」。
    private func createOwnSpace() {
        guard !creatingSpace else { return }
        creatingSpace = true
        createError = ""
        Task {
            if let err = await SBBackend.signup() {
                createError = err
                creatingSpace = false
                return
            }
            store.clearAll()
            await OnboardingView.registerTokens()
            creatingSpace = false
            onboardingStart = .atMac
            showOnboarding = true
        }
    }

    @ViewBuilder
    private var demoBanner: some View {
        if isDemo {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Not connected to a Mac yet", systemImage: "laptopcomputer.slash")
                        .font(.headline)
                    Text("These tasks are a demo. When you're back at your Mac, tap below — it's one line in Terminal, about 30 seconds.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if !createError.isEmpty {
                        Text(createError).font(.footnote).foregroundStyle(.red)
                    }
                    Button {
                        createOwnSpace()
                    } label: {
                        Group {
                            if creatingSpace { ProgressView() }
                            else { Label("Connect my Mac", systemImage: "laptopcomputer") }
                        }
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(creatingSpace)
                }
                .padding(.vertical, 4)
            }
            .listRowBackground(Color.sbAccent.opacity(0.12))
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
                demoBanner
                approvalSection
                liveTasksSection
                sessionsSection
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.sbBackground)
            .overlay {
                if !isDemo && store.pendingApproval == nil && store.liveGroups.isEmpty && store.groups.isEmpty {
                    ContentUnavailableView {
                        Label(macSeen ? "No Tasks Yet" : "Not connected to a Mac yet", systemImage: macSeen ? "bell" : "laptopcomputer.slash")
                    } description: {
                        Text(SBBackend.saved == nil
                             ? "Finish setup first. Tasks will show up here once a Mac connects."
                             : macSeen
                             ? "Give an agent a job on your Mac. Tasks appear here and on the Lock Screen in real time, and your phone rings when they finish."
                             : "One line in Terminal on your Mac, about 30 seconds. Tap below when you're in front of it.")
                    } actions: {
                        if SBBackend.saved == nil {
                            Button("Start Setup") { showOnboarding = true }
                                .buttonStyle(.borderedProminent)
                        } else if !macSeen {
                            Button {
                                onboardingStart = .atMac
                                showOnboarding = true
                            } label: {
                                Label("Connect my Mac", systemImage: "laptopcomputer")
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }
                }
            }
            .navigationTitle("Tasks")
            .navigationDestination(for: SessionGroup.self) {
                SessionPage(sessionId: $0.sessionId, project: $0.project)
            }
            .navigationDestination(for: EventStore.LiveTask.self) {
                SessionPage(sessionId: $0.sessionId, project: $0.project, host: $0.host)
            }
            .refreshable { await store.refresh() }
            .toolbar {
                if !store.events.isEmpty {
                    Button("Clear history", role: .destructive) { store.clearAll() }
                }
            }
        }
    }

    // MARK: 用量 Tab

    private var hasUsage: Bool {
        store.liveGroups.contains {
            !$0.usage.isEmpty || $0.usageFraction != nil || $0.sessionFraction != nil || $0.codexUsage != nil
        }
    }

    private var usageTab: some View {
        NavigationStack {
            List {
                ForEach(store.liveGroups.filter {
                    !$0.usage.isEmpty || $0.usageFraction != nil || $0.sessionFraction != nil || $0.codexUsage != nil
                }) { group in
                    Section {
                        UsageDashboard(group: group)
                        if let usage = group.codexUsage {
                            CodexUsageDashboard(usage: usage)
                        }
                    } header: {
                        HStack(spacing: 8) {
                            Circle().fill(Color.sbDone).frame(width: 7, height: 7)
                            Text(group.host)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Color.sbInk)
                                .lineLimit(1)
                        }
                        .textCase(nil)
                        .padding(.horizontal, 4)
                    }
                    .listRowBackground(Color.sbCard)
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.sbBackground)
            .overlay {
                if !hasUsage {
                    ContentUnavailableView {
                        Label("No Usage Data", systemImage: "gauge.with.needle")
                    } description: {
                        Text("After your Mac connects, this shows official Claude Code and Codex quota windows.")
                    }
                }
            }
            .navigationTitle("Usage")
            .refreshable { await store.refresh() }
        }
    }

    // MARK: 设置 Tab

    private var settingsTab: some View {
        NavigationStack {
            List {
                // MAC:已连接的 + 再加一台
                Section {
                    ForEach(store.liveGroups) { group in
                        HStack(spacing: 12) {
                            Circle().fill(Color.sbDone).frame(width: 7, height: 7)
                            Text(group.host)
                                .font(.subheadline)
                                .foregroundStyle(Color.sbInk)
                                .lineLimit(2)
                            Spacer(minLength: 8)
                            if group.awake {
                                Image(systemName: "sun.max.fill")
                                    .font(.caption)
                                    .foregroundStyle(Color.sbAccentText)
                            }
                        }
                        .frame(minHeight: 28)
                    }
                    if isDemo {
                        Button {
                            createOwnSpace()
                        } label: {
                            Label(creatingSpace ? String(localized: "Creating your space…")
                                                : String(localized: "Connect my Mac"),
                                  systemImage: "plus")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Color.sbAccentText)
                        }
                        .disabled(creatingSpace)
                    } else if SBBackend.pairingCode != nil {
                        Button {
                            showAddMac = true
                        } label: {
                            Label("Add another Mac", systemImage: "plus")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Color.sbAccentText)
                        }
                        Button {
                            showAddDevice = true
                        } label: {
                            Label("Add another phone or iPad", systemImage: "iphone.badge.plus")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Color.sbAccentText)
                        }
                    }
                } header: {
                    SectionLabel("Mac")
                } footer: {
                    Text("One line in Terminal on the new Mac. This page waits and shows the Mac as soon as it connects.")
                        .font(.caption).foregroundStyle(Color.sbInk3)
                }
                .listRowBackground(Color.sbCard)

                // 这台 iPhone
                Section {
                    HStack {
                        Label("Notifications", systemImage: "bell")
                            .font(.subheadline).foregroundStyle(Color.sbInk)
                        Spacer()
                        Text(authLabel)
                            .font(.footnote)
                            .foregroundStyle(store.authStatus == .authorized ? Color.sbDone : Color.sbInk3)
                    }
                    if store.deviceToken.isEmpty {
                        Text("Waiting for APNs registration… (requires a real device with notifications allowed)")
                            .font(.footnote)
                            .foregroundStyle(Color.sbInk3)
                    }
                    if #available(iOS 17.2, *) {
                        Button {
                            Task {
                                localTestResult = String(localized: "Reviving…")
                                localTestResult = await LiveActivityManager.shared.reviveDashboard()
                            }
                        } label: {
                            HStack {
                                Label(localTestResult.isEmpty ? String(localized: "Revive Lock Screen Panel") : localTestResult,
                                      systemImage: "iphone")
                                    .font(.subheadline).foregroundStyle(Color.sbInk)
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.caption2.weight(.semibold)).foregroundStyle(Color.sbInk3)
                            }
                        }
                    }
                } header: {
                    SectionLabel("This iPhone")
                } footer: {
                    Text("Use this if the panel disappeared, for example after you swiped it away.")
                        .font(.caption).foregroundStyle(Color.sbInk3)
                }
                .listRowBackground(Color.sbCard)

                Section {
                    Button {
                        showFeedback = true
                    } label: {
                        SettingsRow(title: "Send Feedback", symbol: "ellipsis.bubble")
                    }
                    Button {
                        showOnboarding = true
                    } label: {
                        SettingsRow(title: "Show Setup Guide Again", symbol: "book")
                    }
                    Link(destination: URL(string: "https://github.com/westie-ai/session-bell")!) {
                        HStack {
                            Label("Source Code", systemImage: "chevron.left.forwardslash.chevron.right")
                                .font(.subheadline).foregroundStyle(Color.sbInk)
                            Spacer()
                            Text("westie-ai/session-bell")
                                .font(.footnote).foregroundStyle(Color.sbInk3)
                        }
                    }
                    HStack {
                        Label("Version", systemImage: "info.circle")
                            .font(.subheadline).foregroundStyle(Color.sbInk)
                        Spacer()
                        Text("\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?") (\(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"))")
                            .font(.footnote).foregroundStyle(Color.sbInk3)
                    }
                }
                .listRowBackground(Color.sbCard)

                Section {
                    backendConfigRow
                } header: {
                    SectionLabel("Advanced · Self-hosted")
                }
                .listRowBackground(Color.sbCard)

                Section {
                    Button(role: .destructive) {
                        confirmReset = true
                    } label: {
                        Text("Reset and Start Over")
                            .font(.subheadline)
                            .frame(maxWidth: .infinity)
                    }
                } footer: {
                    Text("Forgets the pairing on this phone and returns to the first screen, as if freshly installed. Your Macs keep their setup; pair again to reconnect them.")
                        .font(.caption).foregroundStyle(Color.sbInk3)
                }
                .listRowBackground(Color.sbCard)
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.sbBackground)
            .navigationTitle("Settings")
            .sheet(isPresented: $showFeedback) { FeedbackView() }
            .sheet(isPresented: $showAddDevice) { NavigationStack { AddDeviceView() } }
            .sheet(isPresented: $showAddMac) {
                NavigationStack {
                    ConnectMacStep(onDone: { showAddMac = false }, onEnterCode: {}, firstTime: false)
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) {
                                Button("Done") { showAddMac = false }
                            }
                        }
                }
            }
            .confirmationDialog("Reset SessionBell on this phone?", isPresented: $confirmReset, titleVisibility: .visible) {
                Button("Reset and Start Over", role: .destructive) { resetEverything() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Pairing, history and Lock Screen cards on this phone are removed. Nothing is deleted on your Macs or the server.")
            }
        }
    }

    @ViewBuilder
    private var approvalSection: some View {
        if let approval = store.pendingApproval,
           Date().timeIntervalSince(approval.date) < 600 {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 12) {
                        StatusTile(symbol: "checkmark.shield", color: .sbAccentText, soft: .sbApprovalSoft)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(approval.engine == "codex" ? "Codex requests permission" : "Claude is asking for permission")
                                .font(.callout.weight(.medium))
                                .foregroundStyle(Color.sbInk)
                            Text(approval.date, style: .relative)
                                .font(.footnote)
                                .foregroundStyle(Color.sbInk3)
                        }
                    }
                    if !approval.summary.isEmpty {
                        Text(approval.summary)
                            .font(.system(.footnote, design: .monospaced))
                            .foregroundStyle(Color.sbInk)
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.sbBackground, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    HStack(spacing: 10) {
                        Button {
                            Task { await store.sendDecision("deny") }
                        } label: {
                            Text("Deny")
                                .font(.callout.weight(.semibold))
                                .foregroundStyle(Color.sbInk2)
                                .frame(maxWidth: .infinity, minHeight: 44)
                        }
                        .buttonStyle(.plain)
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Color.sbLine, lineWidth: 1))

                        Button {
                            Task { await store.sendDecision("allow") }
                        } label: {
                            Text("Allow")
                                .font(.callout.weight(.semibold))
                                .foregroundStyle(Color(red: 0.11, green: 0.106, blue: 0.094))
                                .frame(maxWidth: .infinity, minHeight: 44)
                                .background(Color.sbAccent, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 4)
            } header: {
                SectionLabel("Needs your approval")
            }
            .listRowBackground(Color.sbCard)
        }
    }

    @ViewBuilder
    private var liveTasksSection: some View {
        if store.liveGroups.isEmpty {
            if !store.groups.isEmpty {
                Section("Active") {
                    Text("No active tasks right now")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        } else {
            ForEach(store.liveGroups) { group in
                Section {
                    AwakeToggleRow(group: group)
                    // 每个主任务一张卡,子 agent 嵌在卡内(用量在「用量」Tab)
                    ForEach(group.cards) { card in
                        SessionCard(card: card) { navPath.append($0) }
                    }
                    if group.cards.isEmpty {
                        HStack(spacing: 10) {
                            Image(systemName: "moon.zzz")
                                .foregroundStyle(Color.sbInk3)
                                .frame(width: 20)
                            Text("Idle — nothing running right now")
                                .font(.footnote)
                                .foregroundStyle(Color.sbInk3)
                        }
                        .frame(minHeight: 28)
                    }
                } header: {
                    MachineHeader(group: group)
                }
                .listRowBackground(Color.sbCard)
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
            TextField("Paste pairing code (fills in below)", text: $pairingCode)
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
            SecureField("Shared secret (SB_SECRET)", text: $backendSecret)
                .font(.system(.caption, design: .monospaced))
            Button {
                SBBackend.save(url: backendURL.trimmingCharacters(in: .whitespacesAndNewlines),
                               secret: backendSecret.trimmingCharacters(in: .whitespacesAndNewlines))
                backendSaved = true
                Task { pingResult = await SBBackend.ping() }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { backendSaved = false }
            } label: {
                Label(backendSaved ? "Saved ✓" : "Save",
                      systemImage: backendSaved ? "checkmark" : "externaldrive")
            }
            .disabled(backendURL.isEmpty || backendSecret.isEmpty)
            Button {
                pingResult = String(localized: "⏳ Testing…")
                Task { pingResult = await SBBackend.ping() }
            } label: {
                Label("Test Connection", systemImage: "waveform.path.ecg")
            }
            if !pingResult.isEmpty {
                Text(pingResult)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(pingResult.hasPrefix("✅") ? .green : .red)
                    .textSelection(.enabled)
            }
        } label: {
            Label(SBBackend.saved.map { String(localized: "Backend: \(URL(string: $0.url)?.host ?? $0.url)") }
                    ?? String(localized: "Backend (not set)"),
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
                Label(flag.wrappedValue ? "Copied ✓" : "\(label) (tap to copy)",
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
            Section {
                ForEach(history) { group in
                    NavigationLink(value: group) {
                        SessionRow(group: group)
                    }
                }
            } header: {
                SectionLabel("Notification History")
            }
            .listRowBackground(Color.sbCard)
        }
    }

    private var authLabel: String {
        switch store.authStatus {
        case .authorized: return String(localized: "Allowed")
        case .denied: return String(localized: "Denied (enable in Settings)")
        case .notDetermined: return String(localized: "Not asked yet")
        default: return String(localized: "Restricted")
        }
    }
}

/// 分组头:在线点 + 主机名 + 「开新任务」。
struct MachineHeader: View {
    let group: EventStore.HostGroup
    @State private var showSpawn = false

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(Color.sbDone).frame(width: 7, height: 7)
            Text(group.host)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Color.sbInk)
                .lineLimit(1)
            Spacer(minLength: 8)
            Button {
                showSpawn = true
            } label: {
                Label("New task", systemImage: "plus")
                    .labelStyle(.titleAndIcon)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Color.sbAccentText)
            }
            .buttonStyle(.plain)
        }
        .textCase(nil)
        .padding(.horizontal, 4)
        .padding(.bottom, 2)
        .sheet(isPresented: $showSpawn) {
            SpawnSheet(group: group)
        }
    }
}

/// 卡片第一行:保持 Mac 常亮(caffeinate)开关。切换后等 Mac 回报,期间禁用。
struct AwakeToggleRow: View {
    let group: EventStore.HostGroup
    @EnvironmentObject var store: EventStore
    @State private var caffePending = false

    private var awakeBinding: Binding<Bool> {
        Binding(get: { group.awake }, set: { on in
            caffePending = true
            Task {
                await store.sendMachineCommand("_sys-\(group.canonicalKey)",
                    text: on ? "caffeinate:on" : "caffeinate:off")
                try? await Task.sleep(for: .seconds(12))
                await store.fetchLiveTasks()
                caffePending = false
            }
        })
    }

    var body: some View {
        Toggle(isOn: awakeBinding) {
            Label(caffePending ? "Applying…" : "Keep Mac awake", systemImage: "sun.max")
                .font(.subheadline)
                .foregroundStyle(Color.sbInk2)
                .lineLimit(1)
        }
        .toggleStyle(.switch)
        .tint(Color.sbAccentText)
        .disabled(caffePending)
    }
}

/// 设置页普通行:图标 + 标题 + 右箭头。
struct SettingsRow: View {
    let title: LocalizedStringKey
    let symbol: String
    var body: some View {
        HStack {
            Label(title, systemImage: symbol)
                .font(.subheadline).foregroundStyle(Color.sbInk)
            Spacer()
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold)).foregroundStyle(Color.sbInk3)
        }
    }
}

/// 区块标签:12pt 半粗 + 字距,三级墨色。
struct SectionLabel: View {
    let key: LocalizedStringKey
    init(_ key: LocalizedStringKey) { self.key = key }
    var body: some View {
        Text(key)
            .font(.caption.weight(.semibold))
            .kerning(0.8)
            .foregroundStyle(Color.sbInk3)
            .textCase(nil)
            .padding(.horizontal, 4)
    }
}

/// 30pt 状态色块 + 线性图标。
struct StatusTile: View {
    let symbol: String
    let color: Color
    let soft: Color
    var size: CGFloat = 30
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size / 3, style: .continuous).fill(soft)
            Image(systemName: symbol)
                .font(.system(size: size * 0.5, weight: .semibold))
                .foregroundStyle(color)
        }
        .frame(width: size, height: size)
    }
}

struct SpawnSheet: View {
    let group: EventStore.HostGroup
    @EnvironmentObject var store: EventStore
    @Environment(\.dismiss) private var dismiss
    @State private var cwd = ""
    @State private var prompt = ""
    @State private var permMode = "auto"
    @State private var engine = "claude"
    @State private var sendResult = ""
    @State private var sent = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Agent") {
                    Picker("Agent", selection: $engine) {
                        Text("Claude Code").tag("claude")
                        Text("Codex").tag("codex")
                    }
                    .pickerStyle(.segmented)
                    if engine == "codex" && !group.codexConnected {
                        Text("Codex is offline. This task will wait for your Mac to reconnect.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("Project Folder") {
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
                    TextField("Or type a path", text: $cwd)
                        .font(.system(.caption, design: .monospaced))
                        .autocorrectionDisabled()
                }
                Section("First Instruction") {
                    TextField("What should it do…", text: $prompt, axis: .vertical)
                        .lineLimit(3...6)
                }
                if engine == "claude" { Section("Permission Mode") {
                    Picker("Permission Mode", selection: $permMode) {
                        Text("Default").tag("default")
                        Text("⏵⏵ Auto").tag("auto")
                        Text("Bypass").tag("bypass")
                    }
                    .pickerStyle(.segmented)
                    Text(permMode == "default"
                         ? "Every permission goes to your phone for approval. Safest."
                         : permMode == "auto"
                         ? "Auto mode: safe actions pass automatically, doubtful ones go to your phone (recommended)"
                         : "Asks nothing and runs straight through. The first time on a machine you must accept a warning on the Mac.")
                        .font(.caption2).foregroundStyle(.secondary)
                } } else {
                    Section {
                        Text("Codex works in this project folder. Requests for additional permissions go to your phone or Mac.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section {
                    Button {
                        let body = ["cwd": cwd, "prompt": prompt, "mode": permMode]
                        guard let data = try? JSONSerialization.data(withJSONObject: body),
                              let json = String(data: data, encoding: .utf8) else { return }
                        sent = true
                        Task {
                            if engine == "codex" {
                                let result = await store.sendCodexCommand(host: group.host, action: "spawn", text: prompt, cwd: cwd)
                                sendResult = result.message
                                if result.ok { dismiss() } else { sent = false }
                                return
                            }
                            await store.sendMachineCommand("_spawn-\(group.canonicalKey)",
                                                           text: json)
                            try? await Task.sleep(for: .seconds(1))
                            dismiss()
                        }
                    } label: {
                        Label(sent ? (engine == "codex" ? "Sending to Codex…" : "Sent. Starting in about 10 seconds") : "🚀 Start on \(group.host)",
                              systemImage: "paperplane.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .disabled(cwd.isEmpty || prompt.trimmingCharacters(in: .whitespaces).isEmpty || sent)
                } footer: {
                    Text(engine == "codex"
                         ? "Starts a Codex task on your Mac. The same conversation is available in Codex on the desktop and CLI, and its result is pushed to your phone."
                         : "Starts an interactive session in a new Otty window on that Mac, so you can take over when you're back at the desk. The task shows up on the panel and the result is pushed when it finishes. If Otty isn't running, it falls back to headless execution in the background.")
                }
                if !sendResult.isEmpty { Text(sendResult).foregroundStyle(Color.sbWaiting) }
            }
            .navigationTitle("New Session")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                if cwd.isEmpty {
                    cwd = group.latestCwd.isEmpty
                        ? (group.spawnDirs.first ?? "") : group.latestCwd
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}

struct UsageDashboard: View {
    let group: EventStore.HostGroup

    /// 用量条平时是品牌深黄;只有快用完(≥ 85%)才换成"等待"那档珊瑚色提醒。
    private func statusColor(_ f: Double) -> Color {
        f >= 0.85 ? .sbWaiting : .sbAccentText
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !group.usage.isEmpty || group.sessionFraction != nil {
                Text("Claude Code").font(.headline)
            }
            // 5 小时窗口最先耗尽,放最上面。
            if let sessionFraction = group.sessionFraction {
                Meter(icon: "clock", label: "5-Hour Window",
                      fraction: sessionFraction, color: statusColor(sessionFraction),
                      detail: group.sessionText)
            }
            if let fraction = group.usageFraction {
                Meter(icon: "gauge.with.needle", label: "Weekly Quota",
                      fraction: fraction, color: statusColor(fraction),
                      detail: group.usage)
            } else if !group.usage.isEmpty {
                Text(group.usage).font(.footnote).foregroundStyle(Color.sbInk3)
            }
            if let fableFraction = group.fableFraction {
                Meter(icon: "sparkles", label: "Premium Model",
                      fraction: fableFraction, color: statusColor(fableFraction),
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

struct CodexUsageDashboard: View {
    let usage: CodexUsage

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Codex").font(.headline)
            if usage.available {
                ForEach(usage.limits) { limit in
                    if usage.limits.count > 1 {
                        Text(limit.name).font(.subheadline.weight(.medium))
                    }
                    ForEach(limit.windows) { window in
                        Meter(icon: "gauge.with.needle", label: LocalizedStringKey(window.label),
                              fraction: window.used_pct / 100,
                              color: window.used_pct >= 85 ? .sbWaiting : .sbAccentText,
                              detail: String(localized: "official figures"))
                        if let reset = window.resets_at {
                            HStack {
                                Text("Resets")
                                Text(Date(timeIntervalSince1970: reset), style: .relative)
                            }
                            .font(.caption).foregroundStyle(Color.sbInk3)
                        }
                    }
                }
            } else {
                Text(usage.reason == "subscription_required"
                     ? "Subscription quota is only available when Codex is signed in with ChatGPT."
                     : "Codex usage is currently unavailable.")
                    .font(.footnote).foregroundStyle(Color.sbInk3)
            }
            if usage.ordinary_usage_allowed == false {
                Text("Included usage is currently unavailable for this account.")
                    .font(.caption).foregroundStyle(Color.sbWaiting)
            }
            if let updated = usage.updated_at {
                HStack {
                    Text(usage.stale == true ? "Last known usage" : "Updated")
                    Text(Date(timeIntervalSince1970: updated), style: .relative)
                }
                .font(.caption2).foregroundStyle(Color.sbInk3)
            }
        }
        .padding(.vertical, 6)
    }
}

struct Meter: View {
    let icon: String
    let label: LocalizedStringKey
    let fraction: Double
    let color: Color
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Label(label, systemImage: icon)
                    .font(.footnote)
                    .foregroundStyle(Color.sbInk2)
                Spacer()
                Text("\(Int(min(fraction, 9.99) * 100))")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(fraction >= 0.85 ? Color.sbWaiting : Color.sbInk)
                    .monospacedDigit()
                + Text(" %")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(Color.sbInk3)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.sbLine)
                    Capsule()
                        .fill(color)
                        .frame(width: max(6, geo.size.width * min(fraction, 1.0)))
                }
            }
            .frame(height: 6)
            if !detail.isEmpty {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(Color.sbInk3)
            }
        }
        .padding(.vertical, 4)
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
                    .font(.caption2.weight(.semibold)).foregroundStyle(Color.sbInk3)
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
                        .fill(Color.sbLine)
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

    private var color: Color { Color.sbStatus(task.status) }

    private var statusLabel: LocalizedStringKey {
        switch task.status {
        case "waiting": return "Waiting for you"
        case "running": return "Running"
        default: return "Done"
        }
    }

    /// 标题是 prompt 摘录;没有就退回项目名。
    private var title: String {
        let d = task.detail.sbCleanPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        return d.isEmpty ? task.project : d
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            StatusTile(symbol: Color.sbStatusSymbol(task.status), color: color,
                       soft: Color.sbStatusSoft(task.status), size: task.isSub ? 24 : 30)

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(task.isSub ? .subheadline.weight(.medium) : .callout.weight(.medium))
                    .foregroundStyle(task.status == "done" ? Color.sbInk2 : Color.sbInk)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    if title != task.project {
                        Text(task.project).lineLimit(1)
                        Dot()
                    }
                    if task.isSub {
                        Text("sub-agent")
                        Dot()
                    }
                    if task.engine == "codex" {
                        Tag("CODEX")
                        Dot()
                    }
                    if let badge = EventStore.modeBadge(task.mode) {
                        Tag(badge.text)
                        Dot()
                    }
                    Text(statusLabel)
                        .fontWeight(.medium)
                        .foregroundStyle(color)
                    Dot()
                    Text(task.since, style: .relative)
                        .monospacedDigit()
                    if task.agents > 0 {
                        Dot()
                        Text("\(task.agents) sub-agents")
                    }
                    if showHost {
                        Dot()
                        Text(task.host).lineLimit(1)
                    }
                }
                .font(.footnote)
                .foregroundStyle(Color.sbInk3)
                .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 3)
    }

    private struct Dot: View {
        var body: some View { Text("·") }
    }
    private struct Tag: View {
        let text: String
        init(_ text: String) { self.text = text }
        var body: some View {
            Text(text)
                .font(.system(size: 10, weight: .semibold))
                .kerning(0.6)
                .foregroundStyle(Color.sbInk2)
                .padding(.horizontal, 4).padding(.vertical, 1)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.sbLine, lineWidth: 1))
        }
    }
}

struct SessionRow: View {
    let group: SessionGroup

    /// 推送标题形如「✅ reply · 完成「prompt 摘录」」:把摘录抠出来当标题,没有就用项目名。
    private var title: String {
        let t = group.latest.title
        if let open = t.range(of: "「"), let close = t.range(of: "」", range: open.upperBound..<t.endIndex) {
            let inner = String(t[open.upperBound..<close.lowerBound]).trimmingCharacters(in: .whitespaces)
            if !inner.isEmpty, !inner.sbIsInjectedPrompt { return inner }
        }
        return group.project
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: group.latest.kind.symbol)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(group.latest.kind.color)
                .frame(width: 20)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Color.sbInk)
                    .lineLimit(1)
                if !group.latest.body.isEmpty {
                    Text(group.latest.body)
                        .font(.footnote)
                        .foregroundStyle(Color.sbInk2)
                        .lineLimit(2)
                }
                HStack(spacing: 6) {
                    if title != group.project { Text(group.project); Text("·") }
                    if let host = group.latest.host { Text(host); Text("·") }
                    Text(group.latest.date, style: .relative)
                }
                .font(.caption)
                .foregroundStyle(Color.sbInk3)
                .lineLimit(1)
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
        .navigationTitle("Notification History")
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
    private var isCodex: Bool { (liveTask?.engine ?? group?.latest.engine) == "codex" }

    private var peekTask: EventStore.LiveTask? {
        guard let h = resolvedHost else { return nil }
        return liveTask ?? EventStore.LiveTask(
            id: sessionId, sessionId: sessionId, project: project, host: h,
            status: "ended", since: group?.latest.date ?? Date(),
            detail: "", agents: 0)
    }

    private var statusInfo: (label: LocalizedStringKey, color: Color) {
        switch liveTask?.status {
        case "waiting": return ("Waiting for you", .orange)
        case "running": return ("Running", .blue)
        case "done": return ("Done", .green)
        default: return ("Ended", .gray)
        }
    }

    enum Tab { case progress, terminal }
    @State private var tab: Tab = .progress
    @State private var showHistory = false
    // 终端帧由页面统一拉取,两个视图共用 — 切换不重连、不重等。
    @State private var termOutput = ""
    @State private var termDate: Date?
    @State private var termLastTs: Double = 0
    @State private var termRefreshing = false
    // 进展帧:Mac 从本地会话记录整理出的 markdown(提示 / 回复 / 工具调用)。
    @State private var mdText = ""
    @State private var mdDate: Date?
    @State private var mdLastTs: Double = 0
    @State private var mdRefreshing = false

    private var hasTerminal: Bool { !isCodex && peekTask != nil }

    var body: some View {
        VStack(spacing: 0) {
            if hasTerminal {
                Picker("View", selection: $tab) {
                    Text("Progress").tag(Tab.progress)
                    Text("Terminal").tag(Tab.terminal)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.top, 6)
                .padding(.bottom, 8)
            }
            if tab == .terminal, hasTerminal {
                terminalPane
            } else {
                progressPane
            }
            inputBar
        }
        .navigationTitle(project)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let g = group, g.events.count > 1 {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showHistory = true } label: {
                        Label("Notification History", systemImage: "clock.arrow.circlepath")
                    }
                }
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Hide Keyboard") { inputFocused = false }
            }
        }
        .sheet(isPresented: $showHistory) {
            if let g = group {
                NavigationStack { EventHistoryView(group: g) }
            }
        }
        .task(id: "\(peekTask?.sessionId ?? "")/\(tab == .terminal)") {
            if tab == .terminal { await terminalLoop() } else { await progressLoop() }
        }
        .onChange(of: tab) { _, t in UIApplication.shared.isIdleTimerDisabled = (t == .terminal) }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
    }

    // MARK: 终端

    /// 进展视图:状态卡 + 整段会话的整理版(你的提示、Claude 的回复、工具调用),
    /// 内容和终端画面一一对应,只是排好了版。Mac 不在线时退回最近一条推送的回复。
    private var progressPane: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    headerCard
                    if isCodex, let host = resolvedHost {
                        ForEach(liveTask?.requests ?? []) { request in
                            CodexRequestCard(request: request, sessionId: sessionId, host: host)
                        }
                    }
                    if !mdText.isEmpty {
                        MarkdownText(text: mdText)
                            .padding(.horizontal, 2)
                        HStack(spacing: 4) {
                            if mdRefreshing { ProgressView().controlSize(.mini) }
                            if let mdDate { Text("\(Text(mdDate, style: .relative)) ago") }
                            Spacer()
                        }
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    } else if hasTerminal, mdRefreshing {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.mini)
                            Text("Fetching the full conversation from the Mac…")
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                    Color.clear.frame(height: 1).id("pbottom")
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
            }
            .scrollDismissesKeyboard(.interactively)
            .onTapGesture { inputFocused = false }
            .onChange(of: mdText) { _, _ in
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo("pbottom", anchor: .bottom)
                }
            }
        }
    }

    /// 进展帧循环:先亮后端缓存的整理版,再让 Mac 重新整理;4 秒一轮。
    private func progressLoop() async {
        guard hasTerminal, let task = peekTask else { return }
        if mdText.isEmpty, let cap = await fetchCapture(sessionId: task.sessionId, kind: "md") {
            mdLastTs = cap.date.timeIntervalSince1970
            mdDate = cap.date
            mdText = cap.text
        }
        while !Task.isCancelled {
            mdRefreshing = true
            await store.sendMachineCommand(
                "_md-\(EventStore.canonicalHost(task.host))", text: task.sessionId)
            for _ in 0..<5 {
                try? await Task.sleep(for: .seconds(2))
                if Task.isCancelled { return }
                if let cap = await fetchCapture(sessionId: task.sessionId, kind: "md"),
                   cap.date.timeIntervalSince1970 > mdLastTs {
                    mdLastTs = cap.date.timeIntervalSince1970
                    mdDate = cap.date
                    mdText = cap.text
                    break
                }
            }
            mdRefreshing = false
            try? await Task.sleep(for: .seconds(4))
        }
    }

    /// 终端视图:原始画面,底部还是同一个输入栏。
    private var terminalPane: some View {
        VStack(spacing: 0) {
            // 顶部一条细状态栏:帧龄 / 正在抓新帧。不叠在正文上。
            HStack(spacing: 4) {
                Spacer()
                if termRefreshing { ProgressView().controlSize(.mini).tint(termFG) }
                if let termDate, !termOutput.isEmpty {
                    Text("\(Text(termDate, style: .relative)) ago")
                } else if termRefreshing {
                    Text("Fetching…")
                }
            }
            .font(.caption2)
            .foregroundStyle(termFG.opacity(0.6))
            .padding(.horizontal, 14)
            .padding(.top, 8)
            .frame(height: 22)
            ScrollViewReader { proxy in
                ScrollView {
                    Group {
                        if termOutput.isEmpty {
                            if termRefreshing {
                                Text("⏳ Connecting to terminal… (first frame in about 5 s)")
                            } else {
                                Text("No frame yet — that Mac may be offline, or the terminal window was closed")
                            }
                        } else {
                            Text(terminalPrettify(termOutput))
                        }
                    }
                    .font(.system(size: 12, weight: .regular, design: .monospaced))
                    .lineSpacing(3)
                    .foregroundStyle(termOutput.isEmpty ? termFG.opacity(0.55) : termFG)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .textSelection(.enabled)
                    .id("bottom")
                }
                .scrollDismissesKeyboard(.interactively)
                .onAppear { proxy.scrollTo("bottom", anchor: .bottom) }
                .onChange(of: termOutput) { _, _ in
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo("bottom", anchor: .bottom)
                    }
                }
                .onTapGesture { inputFocused = false }
            }
        }
        .background(termBG)
    }

    /// 只在终端视图打开时跑:先亮后端缓存的最后一帧,再让 watcher 持续抓新帧。
    /// 切回进展视图就停,别让 Mac 白抓屏;再切回来接着上次的帧继续。
    private func terminalLoop() async {
        guard let task = peekTask else { return }
        if termOutput.isEmpty, let cap = await fetchCapture(sessionId: task.sessionId) {
            termLastTs = cap.date.timeIntervalSince1970
            termDate = cap.date
            termOutput = cap.text
        }
        while !Task.isCancelled {
            termRefreshing = true
            await store.sendMachineCommand(
                "_tail-\(EventStore.canonicalHost(task.host))", text: task.sessionId)
            for _ in 0..<5 {
                try? await Task.sleep(for: .seconds(2))
                if Task.isCancelled { return }
                if let cap = await fetchCapture(sessionId: task.sessionId),
                   cap.date.timeIntervalSince1970 > termLastTs {
                    termLastTs = cap.date.timeIntervalSince1970
                    termDate = cap.date
                    termOutput = cap.text
                    break
                }
            }
            termRefreshing = false
            try? await Task.sleep(for: .seconds(2))
        }
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
            if let reply = liveTask?.latestReply, isCodex, !reply.isEmpty {
                Divider()
                MarkdownText(text: reply)
            } else if mdText.isEmpty, let event = group?.latest {
                let content = event.md ?? event.body
                if !content.isEmpty {
                    Divider()
                    MarkdownText(text: content)
                }
            }
            if let error = liveTask?.deliveryError, !error.isEmpty {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(Color.sbWaiting)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
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
                TextField(!isCodex && (tab == .terminal || !isLive) ? "Type straight into the terminal…" : "What's next…",
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
            if isCodex, let host = resolvedHost {
                showNote(String(localized: "Sending to Codex…"))
                let result = await store.sendCodexCommand(host: host, action: "send", sessionId: sessionId, text: text)
                showNote(result.message, ok: result.ok)
            } else if isLive {
                guard let backend = SBBackend.saved else {
                    showNote(String(localized: "Backend not configured"), ok: false)
                    return
                }
                await SBBackend.post("/api/command",
                                     body: ["session_id": sessionId, "text": text],
                                     to: backend.url, secret: backend.secret)
                showNote(String(localized: "Sent · picked up when the task is idle"))
            } else if let h = resolvedHost,
                      let data = try? JSONSerialization.data(
                          withJSONObject: ["sid": sessionId, "text": text]),
                      let json = String(data: data, encoding: .utf8) {
                await store.sendMachineCommand(
                    "_type-\(EventStore.canonicalHost(h))", text: json)
                showNote(String(localized: "Typed into terminal"))
            } else {
                showNote(String(localized: "Couldn't find that Mac. Not sent."), ok: false)
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

struct CodexRequestCard: View {
    let request: CodexPendingRequest
    let sessionId: String
    let host: String
    @EnvironmentObject var store: EventStore
    @State private var answers: [String: String] = [:]
    @State private var sending = false
    @State private var result = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(request.kind == "question" ? "Codex needs your input" : "Codex requests permission",
                  systemImage: request.kind == "question" ? "questionmark.bubble" : "lock.shield")
                .font(.headline)
            if request.kind == "question" {
                ForEach(request.questions) { question in
                    Text(question.question).font(.subheadline)
                    if let options = question.options, !options.isEmpty {
                        ForEach(options, id: \.label) { option in
                            Button {
                                answers[question.id] = option.label
                            } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Label(option.label, systemImage: answers[question.id] == option.label ? "checkmark.circle.fill" : "circle")
                                    Text(option.description).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                    let value = Binding(get: { answers[question.id] ?? "" }, set: { answers[question.id] = $0 })
                    if question.isSecret == true {
                        SecureField("Your answer", text: value).textFieldStyle(.roundedBorder)
                    } else {
                        TextField("Your answer", text: value, axis: .vertical).textFieldStyle(.roundedBorder)
                    }
                }
                Button("Send answers") {
                    sending = true
                    Task {
                        let response = await store.sendCodexCommand(host: host, action: "answer", sessionId: sessionId,
                                                                   requestId: request.id, answers: answers)
                        result = response.message
                        sending = response.ok
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(sending || request.questions.contains { (answers[$0.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
            } else {
                Text(request.summary).font(.system(.footnote, design: .monospaced)).textSelection(.enabled)
                HStack {
                    Button("Allow") { decide("allow") }.buttonStyle(.borderedProminent)
                    Button("Deny", role: .destructive) { decide("deny") }.buttonStyle(.bordered)
                }
                .disabled(sending)
            }
            if !result.isEmpty { Text(result).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sbCard, in: RoundedRectangle(cornerRadius: 12))
    }

    private func decide(_ decision: String) {
        sending = true
        Task {
            let ok = await SBBackend.postChecked("/api/decision", body: ["request_id": request.id, "decision": decision])
            result = ok ? String(localized: "Decision sent to Codex") : String(localized: "Couldn't send the decision. Try again.")
            sending = ok
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
func fetchCapture(sessionId: String, kind: String = "capture") async -> (date: Date, text: String)? {
    guard let obj = await SBBackend.getJSON("/api/capture?id=\(sessionId)&kind=\(kind)") as? [String: Any],
          let cap = obj["capture"] as? [String: Any],
          let ts = cap["ts"] as? Double,
          let text = cap["text"] as? String else { return nil }
    return (Date(timeIntervalSince1970: ts / 1000), text)
}
