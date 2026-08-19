import Foundation

/// Shared HTTPS client for the SessionBell backend (Vercel).
enum SBBackend {
    static let urlKey = "sb.backendURL"
    static let secretKey = "sb.backendSecret"

    /// 托管服务的注册入口;自托管用户不经过这里(直接粘配对码/填地址)。
    static let hostedBase = "https://sessionbell.westie.ai"

    // No baked-in backend: each user points the app at their own deployment,
    // either in the app's 后端配置 section or automatically from the first
    // push their Mac sends (payloads carry the backend coordinates).
    static var saved: (url: String, secret: String)? {
        guard let url = UserDefaults.standard.string(forKey: urlKey), !url.isEmpty,
              let secret = UserDefaults.standard.string(forKey: secretKey), !secret.isEmpty
        else { return nil }
        return (url, secret)
    }

    static func save(url: String, secret: String) {
        guard !url.isEmpty, !secret.isEmpty else { return }
        UserDefaults.standard.set(url, forKey: urlKey)
        UserDefaults.standard.set(secret, forKey: secretKey)
    }

    static func getJSON(_ path: String) async -> Any? {
        guard let backend = saved, let url = URL(string: backend.url + path) else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        req.setValue(backend.secret, forHTTPHeaderField: "x-sb-secret")
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    static func post(_ path: String, body: [String: String],
                     to backend: String, secret: String) async {
        guard !backend.isEmpty,
              let url = URL(string: backend + path),
              let data = try? JSONSerialization.data(withJSONObject: body)
        else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(secret, forHTTPHeaderField: "x-sb-secret")
        req.httpBody = data
        _ = try? await URLSession.shared.data(for: req)
    }

    /// 由当前保存的配置反推配对码(与 /api/signup 下发的格式一致),
    /// 用于首跑第三屏「拷贝给 Mac」。
    static var pairingCode: String? {
        guard let backend = saved,
              let data = try? JSONSerialization.data(
                  withJSONObject: ["u": backend.url, "s": backend.secret])
        else { return nil }
        return data.base64EncodedString()
    }

    /// 解析配对码(base64 的 {u, s});成功即保存为当前后端。
    @discardableResult
    static func adoptPairingCode(_ code: String) -> Bool {
        guard let data = Data(base64Encoded: code.trimmingCharacters(in: .whitespacesAndNewlines)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let u = obj["u"], let s = obj["s"], !u.isEmpty, !s.isEmpty
        else { return false }
        save(url: u, secret: s)
        return true
    }

    /// 托管注册:邀请码换新租户。成功时已把后端配置保存好。
    static func signup(invite: String) async -> String? {
        guard let url = URL(string: hostedBase + "/api/signup") else { return "地址不合法" }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["invite": invite])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200, let pairing = obj?["pairing_code"] as? String else {
                if let err = obj?["error"] as? String {
                    return err == "bad invite code" ? "邀请码不对,检查一下再试" : err
                }
                return "服务器返回 HTTP \(code)"
            }
            guard adoptPairingCode(pairing) else { return "配对码解析失败,请联系邀请你的人" }
            return nil
        } catch {
            return "网络不通:\(error.localizedDescription)"
        }
    }

    /// 连接自检:把失败原因摊在台面上,不再静默装死。
    static func ping() async -> String {
        guard let backend = saved else { return "❌ 未配置后端" }
        guard let url = URL(string: backend.url + "/api/token") else {
            return "❌ 地址不合法: \(backend.url)"
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.setValue(backend.secret, forHTTPHeaderField: "x-sb-secret")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200 { return "✅ 已连接 \(url.host ?? "")" }
            let body = String(data: data, encoding: .utf8)?.prefix(60) ?? ""
            return "❌ HTTP \(code) \(body)"
        } catch {
            return "❌ \(error.localizedDescription)"
        }
    }
}
