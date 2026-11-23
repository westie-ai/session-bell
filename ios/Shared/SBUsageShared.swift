import Foundation

/// 用量 widget 与 App 之间的共享层(App Group)。
///
/// - App 每次拉到 /api/state 就把解析好的快照写进 App Group,并顺手镜像后端坐标;
/// - widget 刷新时间线时先自己拉一次 /api/state(用镜像的坐标),拉不到就用快照。
///
/// 钥匙串里的租户密钥只有 App 主进程读得到,所以坐标要镜像一份到 App Group。
enum SBUsageShared {
    static let appGroup = "group.dev.yuesun.SessionBell"
    static let widgetKind = "SessionBellUsage"

    private static let snapshotKey = "usage.snapshot"
    private static let urlKey = "usage.backendURL"
    private static let secretKey = "usage.backendSecret"

    private static var defaults: UserDefaults? { UserDefaults(suiteName: appGroup) }

    struct Snapshot: Codable, Equatable {
        var host: String
        /// 0...1+,nil = 这台 Mac 没上报该项
        var sessionPct: Double?
        var sessionReset: Date?
        var weekPct: Double?
        var weekReset: Date?
        var premiumName: String?
        var premiumPct: Double?
        /// Mac 上报这份用量的时间
        var reportedAt: Date

        static let placeholder = Snapshot(
            host: "MacBook Pro", sessionPct: 0.42, sessionReset: Date().addingTimeInterval(2 * 3600),
            weekPct: 0.27, weekReset: Date().addingTimeInterval(3 * 86400),
            premiumName: "Opus", premiumPct: 0.18, reportedAt: Date())
    }

    // MARK: 坐标镜像

    static func mirrorBackend(url: String, secret: String) {
        guard let d = defaults, !url.isEmpty, !secret.isEmpty else { return }
        if d.string(forKey: urlKey) != url { d.set(url, forKey: urlKey) }
        if d.string(forKey: secretKey) != secret { d.set(secret, forKey: secretKey) }
    }

    static func clearBackend() {
        defaults?.removeObject(forKey: urlKey)
        defaults?.removeObject(forKey: secretKey)
        defaults?.removeObject(forKey: snapshotKey)
    }

    // MARK: 快照

    static func loadSnapshot() -> Snapshot? {
        guard let data = defaults?.data(forKey: snapshotKey) else { return nil }
        return try? JSONDecoder().decode(Snapshot.self, from: data)
    }

    /// 写入快照;内容没变返回 false(App 据此决定要不要叫 widget 重载)。
    @discardableResult
    static func saveSnapshot(_ snap: Snapshot) -> Bool {
        guard let d = defaults else { return false }
        if loadSnapshot() == snap { return false }
        guard let data = try? JSONEncoder().encode(snap) else { return false }
        d.set(data, forKey: snapshotKey)
        return true
    }

    // MARK: 解析 /api/state

    /// 从 /api/state 的原始 JSON 挑出最近一次带用量上报的那台 Mac。
    /// 用量是账号级的,多台 Mac 登同一账号时数字一样,取最新那份即可。
    static func parse(state obj: [String: [String: Any]], now: Date = Date()) -> Snapshot? {
        var best: Snapshot?
        for (host, blob) in obj {
            guard let ts = blob["ts"] as? Double,
                  now.timeIntervalSince1970 - ts < 6 * 3600,
                  let u = blob["usage"] as? [String: Any] else { continue }
            func date(_ k: String) -> Date? {
                (u[k] as? Double).map { Date(timeIntervalSince1970: $0) }
            }
            var snap = Snapshot(host: host, reportedAt: Date(timeIntervalSince1970: ts))
            snap.sessionPct = (u["official_session_pct"] as? Double).map { $0 / 100 }
            snap.sessionReset = date("session_reset_ts")
            if let t = u["official_total_pct"] as? Double {
                snap.weekPct = t / 100
            } else if let out = u["week_out"] as? Double, let budget = u["week_budget"] as? Double, budget > 0 {
                snap.weekPct = out / budget
            }
            snap.weekReset = date("reset_ts")
            if let p = u["official_pct"] as? Double {
                snap.premiumPct = p / 100
            } else if let f = u["week_fable"] as? Double, let b = u["fable_budget"] as? Double, b > 0 {
                snap.premiumPct = f / b
            }
            if snap.premiumPct != nil {
                snap.premiumName = u["premium_name"] as? String
            }
            guard snap.sessionPct != nil || snap.weekPct != nil else { continue }
            if best == nil || snap.reportedAt > best!.reportedAt { best = snap }
        }
        return best
    }

    // MARK: widget 侧拉取

    /// widget 进程自己拉 /api/state;没坐标或网络失败返回 nil。
    static func fetch() async -> Snapshot? {
        guard let d = defaults,
              let base = d.string(forKey: urlKey), let secret = d.string(forKey: secretKey),
              let url = URL(string: base + "/api/state") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.setValue(secret, forHTTPHeaderField: "x-sb-secret")
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]]
        else { return nil }
        guard let snap = parse(state: obj) else { return nil }
        saveSnapshot(snap)
        return snap
    }
}
