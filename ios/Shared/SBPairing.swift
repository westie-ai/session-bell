import Foundation

/// 6 位短码配对(替代 150 字符的 base64 配对码在手机和 Mac 之间搬运):
///   手机先来:mintShortCode() → 用户在 Mac 上跑 `curl …/i | bash -s 483920`
///   Mac 先来:Mac 脚本自己注册并弹出二维码 → redeemShortCode() 把手机挂进去
extension SBBackend {
    struct ShortCode {
        let code: String
        let expires: Date
        var pretty: String { code.prefix(3) + " " + code.suffix(3) }
        var isExpired: Bool { Date() >= expires }
    }

    /// 给 Mac 端看的那一行。域名跟着 hostedBase 走,自托管也成立。
    static func oneLiner(code: String) -> String {
        let host = hostedBase.replacingOccurrences(of: "https://", with: "")
            .replacingOccurrences(of: "http://", with: "")
        let scheme = hostedBase.hasPrefix("http://") ? "http://" : ""
        return "curl -fsSL \(scheme)\(host)/i | bash -s \(code)"
    }

    /// 用当前租户的配对码换一个新的 6 位短码(15 分钟有效,单次使用)。
    static func mintShortCode() async -> ShortCode? {
        guard let backend = saved, let pairing = pairingCode,
              let url = URL(string: backend.url + "/api/pair-code") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(backend.secret, forHTTPHeaderField: "x-sb-secret")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["pairing_code": pairing])
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let code = obj["short_code"] as? String else { return nil }
        let ttl = obj["expires_in"] as? Double ?? 900
        return ShortCode(code: code, expires: Date().addingTimeInterval(ttl - 30))
    }

    /// Mac 先来:把 Mac 屏幕上的 6 位数字换成配对码并采用。返回 nil 表示成功,否则是给用户看的错误。
    static func redeemShortCode(_ raw: String) async -> String? {
        let code = raw.filter(\.isNumber)
        guard code.count == 6, let url = URL(string: hostedBase + "/api/pair/" + code) else {
            return String(localized: "Enter the 6 digits shown on the Mac.")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 15
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            switch status {
            case 200:
                // 地址以 App 自己的 hostedBase 为准(码就是从这里兑换的),只取配对码里的租户密钥;
                // 与 signup() 同一口径,本地 / 代理环境下服务端回的 origin 不可靠。
                guard let pairing = obj?["pairing_code"] as? String,
                      let pd = Data(base64Encoded: pairing),
                      let po = try? JSONSerialization.jsonObject(with: pd) as? [String: String],
                      let secret = po["s"], !secret.isEmpty else {
                    return String(localized: "The Mac sent something unexpected. Run the command on the Mac again.")
                }
                save(url: hostedBase, secret: secret)
                isDemo = false
                return nil
            case 410:
                return String(localized: "This code was already used. Run the command on the Mac again for a new one.")
            case 404:
                return String(localized: "This code expired or isn't right. Check the Mac screen and try again.")
            case 429:
                return String(localized: "Too many tries. Wait a minute and try again.")
            default:
                return String(localized: "Server returned HTTP \(status)")
            }
        } catch {
            return String(localized: "Network error: \(error.localizedDescription)")
        }
    }

    /// 接入漏斗埋点,发后不管。名字与后端 allow-list 一致。
    static func event(_ name: String) {
        guard let backend = saved else { return }
        Task { await post("/api/event", body: ["name": name], to: backend.url, secret: backend.secret) }
    }

    /// universal link `https://sessionbell.westie.ai/p/483920` → "483920"
    static func shortCode(from url: URL) -> String? {
        let parts = url.pathComponents
        guard parts.count >= 3, parts[1] == "p", parts[2].count == 6,
              parts[2].allSatisfy(\.isNumber) else { return nil }
        return parts[2]
    }
}
