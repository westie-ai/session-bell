import Foundation
import UserNotifications

@MainActor
final class EventStore: ObservableObject {
    static let shared = EventStore()

    @Published private(set) var events: [SessionEvent] = []
    @Published var deviceToken: String = ""
    @Published var pushToStartToken: String = ""
    @Published var authStatus: UNAuthorizationStatus = .notDetermined

    struct PendingApproval: Equatable {
        let id: String
        let summary: String
        let backend: String
        let secret: String
        let date: Date
    }
    @Published var pendingApproval: PendingApproval?
    @Published var openSessionId: String?

    struct LiveTask: Identifiable, Equatable, Hashable {
        let id: String
        let sessionId: String
        let project: String
        let host: String
        let status: String
        let since: Date
        let detail: String
        let agents: Int
        var isSub: Bool = false
        var mode: String = ""
        var engine: String = ""   // "" = claude; "codex" = OpenAI Codex
        var cwd: String = ""
        var rootDir: String = ""
    }

    /// 与 Claude Code 官方模式一一对应
    static func modeBadge(_ mode: String) -> (text: String, tone: String)? {
        switch mode {
        case "plan": return ("PLAN", "plan")
        case "auto": return ("AUTO", "auto")
        case "acceptEdits": return ("EDITS", "auto")
        case "bypassPermissions": return ("BYPASS", "bypass")
        case "dontAsk": return ("DONTASK", "bypass")
        default: return nil
        }
    }
    struct TaskCard: Identifiable, Equatable {
        let root: LiveTask
        let subs: [LiveTask]
        var id: String { root.id }
    }
    struct HostGroup: Identifiable, Equatable {
        let host: String
        let cards: [TaskCard]
        var usage: String = ""
        var usageFraction: Double? = nil  // week_out / week_budget
        var fableText: String = ""
        var fableFraction: Double? = nil
        var sessionText: String = ""      // 5 小时窗口(官方口径)
        var sessionFraction: Double? = nil
        var awake: Bool = false
        var canonicalKey: String = ""
        var projects: [String] = []
        var id: String { host }

        /// 新建 session 的目录候选:电脑发布的最近项目 + 活跃 session 目录
        var spawnDirs: [String] {
            var seen = Set<String>()
            return (projects + recentCwds).filter { !$0.isEmpty && seen.insert($0).inserted }
        }

        var recentCwds: [String] {
            var seen = Set<String>()
            return cards.flatMap { [$0.root] + $0.subs }
                .map { $0.rootDir.isEmpty ? $0.cwd : $0.rootDir }
                .filter { !$0.isEmpty && seen.insert($0).inserted }
        }

        /// 最近活跃 session 的主仓库目录(worktree 已归位)— 新建时的默认值
        var latestCwd: String {
            cards.flatMap { [$0.root] + $0.subs }
                .map { (dir: $0.rootDir.isEmpty ? $0.cwd : $0.rootDir, since: $0.since) }
                .filter { !$0.dir.isEmpty }
                .max { $0.since < $1.since }?.dir ?? ""
        }
    }

    static func canonicalHost(_ host: String) -> String {
        String(host.lowercased().unicodeScalars.filter {
            CharacterSet.alphanumerics.contains($0) && $0.isASCII
        })
    }

    func sendMachineCommand(_ key: String, text: String) async {
        guard let backend = SBBackend.saved else { return }
        await SBBackend.post("/api/command",
                             body: ["session_id": key, "text": text],
                             to: backend.url, secret: backend.secret)
    }
    @Published var liveTasks: [LiveTask] = []
    @Published var liveGroups: [HostGroup] = []

    /// Same source of truth as the lock-screen card: the backend state table.
    func fetchLiveTasks() async {
        guard let obj = await SBBackend.getJSON("/api/state") as? [String: [String: Any]]
        else { return }
        let now = Date().timeIntervalSince1970
        let limits: [String: Double] = ["done": 600, "waiting": 1800, "running": 21600]

        // "Piper的 MacBook Pro (2)" and "Piper-MacBook-Pro-2" are the same
        // machine under two spellings — canonicalize for grouping, and show
        // the label from the freshest report.
        func canonical(_ host: String) -> String {
            String(host.lowercased().unicodeScalars.filter {
                CharacterSet.alphanumerics.contains($0) && $0.isASCII
            })
        }
        var labelFor: [String: (label: String, ts: Double)] = [:]
        var awakeFor: [String: Bool] = [:]
        var projectsFor: [String: [String]] = [:]
        var usageFor: [String: String] = [:]
        var fractionFor: [String: Double] = [:]
        var fableTextFor: [String: String] = [:]
        var fableFractionFor: [String: Double] = [:]
        var sessionTextFor: [String: String] = [:]
        var sessionFractionFor: [String: Double] = [:]
        func fmtTokens(_ n: Double) -> String {
            n >= 1e6 ? String(format: "%.1fM", n / 1e6)
                : n >= 1000 ? String(format: "%.0fk", n / 1000) : String(Int(n))
        }

        // Dedupe by session id; resolve sub-agent parentage via pid links.
        var bySession: [String: LiveTask] = [:]
        var parentOf: [String: String] = [:]  // sid -> parent sid
        for (host, blob) in obj {
            guard let ts = blob["ts"] as? Double, now - ts < 3600,
                  let sessions = blob["sessions"] as? [String: [String: Any]] else { continue }
            let key = canonical(host)
            if (labelFor[key]?.ts ?? 0) < ts {
                labelFor[key] = (host, ts)
                awakeFor[key] = blob["awake"] as? Bool ?? false
                projectsFor[key] = blob["projects"] as? [String] ?? []
                if let u = blob["usage"] as? [String: Any],
                   let todayOut = u["today_out"] as? Double,
                   let weekOut = u["week_out"] as? Double {
                    let budget = u["week_budget"] as? Double ?? 0
                    let officialTotal = u["official_total_pct"] as? Double
                    var text = String(localized: "Today \(fmtTokens(todayOut)) · This cycle \(fmtTokens(weekOut))")
                        + (officialTotal == nil && budget > 0 ? "/\(fmtTokens(budget))" : "")
                    if let resetTs = u["reset_ts"] as? Double {
                        let remain = resetTs - now
                        if remain > 0 {
                            let d = Int(remain / 86400)
                            let h = Int(remain.truncatingRemainder(dividingBy: 86400) / 3600)
                            let left = d > 0 ? String(localized: "\(d)d \(h)h") : String(localized: "\(h)h")
                            text += " · " + String(localized: "resets in \(left)")
                        }
                    }
                    if let t = officialTotal {
                        // Ground truth straight from the OAuth usage endpoint.
                        text += " · " + String(localized: "official figures")
                        fractionFor[key] = t / 100
                    } else if budget > 0 {
                        fractionFor[key] = weekOut / budget
                    }
                    usageFor[key] = text
                    if let sessionPct = u["official_session_pct"] as? Double {
                        sessionFractionFor[key] = sessionPct / 100
                        var stext = String(localized: "official figures")
                        if let sReset = u["session_reset_ts"] as? Double {
                            let remain = sReset - now
                            if remain > 0 {
                                let h = Int(remain / 3600)
                                let m = Int(remain.truncatingRemainder(dividingBy: 3600) / 60)
                                let left = h > 0 ? String(localized: "\(h)h \(m)m") : String(localized: "\(m)m")
                                stext = String(localized: "resets in \(left)") + " · "
                                    + String(localized: "official figures")
                            }
                        }
                        sessionTextFor[key] = stext
                    }
                    if let weekFable = u["week_fable"] as? Double, weekFable > 0 {
                        let name = u["premium_name"] as? String ?? "Fable"
                        if let officialPct = u["official_pct"] as? Double {
                            // Ground truth from the OAuth usage endpoint.
                            fableTextFor[key] = "\(name) \(fmtTokens(weekFable)) · " + String(localized: "official figures")
                            fableFractionFor[key] = officialPct / 100
                        } else if let fBudget = u["fable_budget"] as? Double, fBudget > 0 {
                            fableTextFor[key] = "\(name) \(fmtTokens(weekFable))/\(fmtTokens(fBudget))"
                            fableFractionFor[key] = weekFable / fBudget
                        } else {
                            fableTextFor[key] = "\(name) \(fmtTokens(weekFable))"
                        }
                    }
                }
            }
            var pidToSid: [Int: String] = [:]
            for (sid, e) in sessions {
                if let pid = e["pid"] as? Int { pidToSid[pid] = sid }
            }
            for (sid, e) in sessions {
                guard let status = e["status"] as? String,
                      let since = e["since"] as? Double,
                      now - since <= (limits[status] ?? 86400),
                      let project = e["project"] as? String else { continue }
                if let existing = bySession[sid],
                   existing.since.timeIntervalSince1970 >= since { continue }
                bySession[sid] = LiveTask(
                    id: sid, sessionId: sid, project: project, host: host,
                    status: status,
                    since: Date(timeIntervalSince1970: since),
                    detail: e["detail"] as? String ?? "",
                    agents: e["agents"] as? Int ?? 0,
                    mode: e["mode"] as? String ?? "",
                    engine: e["engine"] as? String ?? "",
                    cwd: e["cwd"] as? String ?? "",
                    rootDir: e["root"] as? String ?? "")
                if let ppid = e["parent_pid"] as? Int,
                   let parentSid = pidToSid[ppid], parentSid != sid {
                    parentOf[sid] = parentSid
                }
            }
        }

        // 电脑 → 任务 → 子 agent: group by canonical host, nest children.
        let order = ["waiting": 0, "running": 1, "done": 2]
        var groups: [HostGroup] = []
        let byHost = Dictionary(grouping: bySession.values) { canonical($0.host) }
        for (hostKey, tasks) in byHost {
            let displayHost = labelFor[hostKey]?.label ?? tasks[0].host
            var children: [String: [LiveTask]] = [:]
            var roots: [LiveTask] = []
            for t in tasks {
                if let p = parentOf[t.sessionId], let parent = bySession[p],
                   canonical(parent.host) == hostKey {
                    var sub = t; sub.isSub = true
                    children[p, default: []].append(sub)
                } else {
                    roots.append(t)
                }
            }
            roots.sort { (order[$0.status] ?? 3, $0.since) < (order[$1.status] ?? 3, $1.since) }
            let cards = roots.map { r in
                TaskCard(root: r,
                         subs: (children[r.sessionId] ?? []).sorted { $0.since < $1.since })
            }
            groups.append(HostGroup(host: displayHost, cards: cards,
                                    usage: usageFor[hostKey] ?? "",
                                    usageFraction: fractionFor[hostKey],
                                    fableText: fableTextFor[hostKey] ?? "",
                                    fableFraction: fableFractionFor[hostKey],
                                    sessionText: sessionTextFor[hostKey] ?? "",
                                    sessionFraction: sessionFractionFor[hostKey],
                                    awake: awakeFor[hostKey] ?? false,
                                    canonicalKey: hostKey,
                                    projects: projectsFor[hostKey] ?? []))
        }
        groups.sort { $0.host < $1.host }
        liveGroups = groups
        liveTasks = groups.flatMap { $0.cards.flatMap { [$0.root] + $0.subs } }
    }

    private var knownIDs = Set<String>()
    private let fileURL: URL = {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return dir.appendingPathComponent("events.json")
    }()

    init() {
        load()
    }

    var groups: [SessionGroup] {
        let bySession = Dictionary(grouping: events, by: \.sessionId)
        return bySession
            .map { SessionGroup(sessionId: $0.key, events: $0.value.sorted { $0.date > $1.date }) }
            .sorted { $0.latest.date > $1.latest.date }
    }

    func ingest(userInfo: [AnyHashable: Any]) {
        guard let sb = userInfo["sb"] as? [String: Any],
              let kindRaw = sb["event"] as? String,
              let kind = EventKind(rawValue: kindRaw),
              let ts = sb["ts"] as? TimeInterval
        else { return }

        // Every push carries the backend coordinates — remember the latest.
        if let backend = sb["backend"] as? [String: String],
           let url = backend["url"], let secret = backend["secret"] {
            SBBackend.save(url: url, secret: secret)
        }

        if kind == .permission,
           let requestId = sb["request_id"] as? String,
           let backend = sb["backend"] as? [String: String],
           let url = backend["url"], let secret = backend["secret"] {
            SBBackend.save(url: url, secret: secret)
            var summary = ""
            if let aps = userInfo["aps"] as? [String: Any],
               let alert = aps["alert"] as? [String: Any] {
                summary = Self.alertText(alert, "body")
            }
            let date = Date(timeIntervalSince1970: ts)
            // Only surface approvals the Mac is still waiting on.
            if Date().timeIntervalSince(date) < 600 {
                pendingApproval = PendingApproval(
                    id: requestId, summary: summary, backend: url, secret: secret, date: date)
            }
        }

        let sessionId = sb["session_id"] as? String ?? "unknown"
        let id = "\(sessionId)-\(Int(ts))-\(kindRaw)"
        guard !knownIDs.contains(id) else { return }

        var title = "", body = ""
        if let aps = userInfo["aps"] as? [String: Any],
           let alert = aps["alert"] as? [String: Any] {
            title = Self.alertText(alert, "title")
            body = Self.alertText(alert, "body")
        }

        let event = SessionEvent(
            id: id,
            kind: kind,
            sessionId: sessionId,
            project: sb["project"] as? String ?? String(localized: "Unknown project"),
            cwd: sb["cwd"] as? String ?? "",
            host: sb["host"] as? String,
            title: title,
            body: body,
            md: sb["md"] as? String,
            date: Date(timeIntervalSince1970: ts)
        )
        knownIDs.insert(id)
        events.append(event)
        save()
    }

    /// Pick up pushes that arrived while the app wasn't running.
    func refresh() async {
        let center = UNUserNotificationCenter.current()
        authStatus = await center.notificationSettings().authorizationStatus
        for delivered in await center.deliveredNotifications() {
            ingest(userInfo: delivered.request.content.userInfo)
        }
        await fetchLiveTasks()
    }

    func sendDecision(_ decision: String) async {
        guard let approval = pendingApproval else { return }
        pendingApproval = nil
        await SBBackend.post(
            "/api/decision",
            body: ["request_id": approval.id, "decision": decision],
            to: approval.backend, secret: approval.secret)
    }

    func clearAll() {
        events.removeAll()
        knownIDs.removeAll()
        save()
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([SessionEvent].self, from: data)
        else { return }
        events = decoded
        knownIDs = Set(decoded.map(\.id))
    }

    private func save() {
        if let data = try? JSONEncoder().encode(events) {
            try? data.write(to: fileURL, options: .atomic)
        }
    }
}

extension EventStore {
    /// 按系统通知的规则解析 alert 文案:优先 `title-loc-key`/`loc-key`(hook 发的中文原文
    /// 作 key,catalog 里有英文译文),查不到就用 key 本身;没有 loc-key 才退回明文字段。
    static func alertText(_ alert: [String: Any], _ field: String) -> String {
        let keyName = field == "title" ? "title-loc-key" : "loc-key"
        let argsName = field == "title" ? "title-loc-args" : "loc-args"
        if let key = alert[keyName] as? String {
            let format = NSLocalizedString(key, comment: "")
            let args = (alert[argsName] as? [Any] ?? []).map { "\($0)" }
            return String(format: format, arguments: args.map { $0 as NSString })
        }
        return alert[field] as? String ?? ""
    }
}
