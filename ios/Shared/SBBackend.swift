import Foundation

/// Shared HTTPS client for the SessionBell backend (Vercel).
enum SBBackend {
    static let urlKey = "sb.backendURL"
    static let secretKey = "sb.backendSecret"

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
