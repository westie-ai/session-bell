import Foundation
import Security

/// Shared HTTPS client for the SessionBell backend (Vercel).
enum SBBackend {
    static let urlKey = "sb.backendURL"
    static let secretKey = "sb.backendSecret"

    /// 托管服务的注册入口;自托管用户不经过这里(直接粘配对码/填地址)。
    /// 启动参数 -sb.hostedBase 可指到本地 worker(UI 测试 / 截图用)。
    static var hostedBase: String {
        UserDefaults.standard.string(forKey: "sb.hostedBase") ?? "https://sessionbell.westie.ai"
    }

    // No baked-in backend: each user points the app at their own deployment,
    // either in the app's 后端配置 section or automatically from the first
    // push their Mac sends (payloads carry the backend coordinates).
    //
    // 租户密钥就是身份,所以放 iCloud 钥匙串(synchronizable):卸载重装不丢,
    // 同一 Apple 账号的新手机自动带过来。读取优先级:
    //   1. 启动参数 -sb.backendURL/-sb.backendSecret(截图测试用,不落盘)
    //   2. 钥匙串
    //   3. 旧版 UserDefaults —— 读到即迁入钥匙串并清掉
    static var saved: (url: String, secret: String)? {
        if let o = argumentOverride { return o.url.isEmpty || o.secret.isEmpty ? nil : o }
        if let c = cache { return c }
        if let k = Keychain.read() { cache = k; return k }
        if let url = UserDefaults.standard.string(forKey: urlKey), !url.isEmpty,
           let secret = UserDefaults.standard.string(forKey: secretKey), !secret.isEmpty {
            if Keychain.write(url: url, secret: secret) {
                UserDefaults.standard.removeObject(forKey: urlKey)
                UserDefaults.standard.removeObject(forKey: secretKey)
            }
            cache = (url, secret)
            return cache
        }
        return nil
    }

    static func save(url: String, secret: String) {
        guard !url.isEmpty, !secret.isEmpty else { return }
        isDemo = false
        if Keychain.write(url: url, secret: secret) {
            UserDefaults.standard.removeObject(forKey: urlKey)
            UserDefaults.standard.removeObject(forKey: secretKey)
        } else {
            // 钥匙串写不进去(极少见)时退回 UserDefaults,至少本机能用。
            UserDefaults.standard.set(url, forKey: urlKey)
            UserDefaults.standard.set(secret, forKey: secretKey)
        }
        cache = (url, secret)
    }

    private static var cache: (url: String, secret: String)?

    /// `xcodebuild test` / 手动 launch 传的 -sb.backendURL 覆盖,只在参数域里存在。
    private static var argumentOverride: (url: String, secret: String)? {
        let args = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        guard args[urlKey] != nil || args[secretKey] != nil else { return nil }
        return (args[urlKey] as? String ?? "", args[secretKey] as? String ?? "")
    }

    /// 一条 generic-password 项,内容是 {"u","s"} 的 JSON,标 synchronizable 走 iCloud 钥匙串。
    /// 只有 App 主进程读它;widget 走 Live Activity 属性里带的坐标,不碰钥匙串。
    private enum Keychain {
        static let service = "dev.yuesun.SessionBell.backend"
        static let account = "tenant"

        private static var base: [String: Any] {
            [kSecClass as String: kSecClassGenericPassword,
             kSecAttrService as String: service,
             kSecAttrAccount as String: account,
             kSecAttrSynchronizable as String: kCFBooleanTrue as Any]
        }

        static func read() -> (url: String, secret: String)? {
            var q = base
            q[kSecReturnData as String] = kCFBooleanTrue
            q[kSecMatchLimit as String] = kSecMatchLimitOne
            var out: CFTypeRef?
            guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
                  let data = out as? Data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String],
                  let u = obj["u"], let s = obj["s"], !u.isEmpty, !s.isEmpty
            else { return nil }
            return (u, s)
        }

        static func write(url: String, secret: String) -> Bool {
            guard let data = try? JSONSerialization.data(withJSONObject: ["u": url, "s": secret])
            else { return false }
            let attrs: [String: Any] = [
                kSecValueData as String: data,
                // 后台收推送时也要能读(AfterFirstUnlock);synchronizable 项不能用 ThisDeviceOnly。
                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
            ]
            let update = SecItemUpdate(base as CFDictionary, attrs as CFDictionary)
            if update == errSecSuccess { return true }
            guard update == errSecItemNotFound else {
                NSLog("SessionBell keychain update failed: %d", update); return false
            }
            var add = base
            attrs.forEach { add[$0.key] = $0.value }
            let status = SecItemAdd(add as CFDictionary, nil)
            if status != errSecSuccess { NSLog("SessionBell keychain add failed: %d", status) }
            return status == errSecSuccess
        }
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

    /// 当前接的是共享的演示租户(数据是模拟的,由后端定时播种)。
    /// 换成真实租户或粘配对码时自动清掉。
    static let demoKey = "sb.demo"
    static var isDemo: Bool {
        get { UserDefaults.standard.bool(forKey: demoKey) }
        set { UserDefaults.standard.set(newValue, forKey: demoKey) }
    }

    /// 托管注册:开放注册,不需要邀请码;demo=true 时接入演示租户。
    /// 成功时已把后端配置保存好,返回 nil;失败返回可展示的错误文案。
    static func signup(demo: Bool = false) async -> String? {
        guard let url = URL(string: hostedBase + "/api/signup") else { return String(localized: "Invalid address") }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: demo ? ["demo": true] : [:])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200, let pairing = obj?["pairing_code"] as? String else {
                switch obj?["error"] as? String {
                case "rate limited":
                    return String(localized: "Too many sign-ups from this network. Try again in an hour.")
                case "demo unavailable":
                    return String(localized: "The demo isn't available right now.")
                case let err?:
                    return err
                default:
                    return String(localized: "Server returned HTTP \(code)")
                }
            }
            // 地址以 App 自己的 hostedBase 为准(服务端回的 origin 在本地/代理环境下不可靠),
            // 只取配对码里的租户密钥。
            guard let pd = Data(base64Encoded: pairing),
                  let po = try? JSONSerialization.jsonObject(with: pd) as? [String: String],
                  let secret = po["s"], !secret.isEmpty
            else { return String(localized: "Couldn't parse the pairing code. Contact the person who invited you.") }
            save(url: hostedBase, secret: secret)
            isDemo = demo
            return nil
        } catch {
            return String(localized: "Network error: \(error.localizedDescription)")
        }
    }

    /// 连接自检:把失败原因摊在台面上,不再静默装死。
    static func ping() async -> String {
        guard let backend = saved else { return String(localized: "❌ No backend configured") }
        guard let url = URL(string: backend.url + "/api/token") else {
            return String(localized: "❌ Invalid address: \(backend.url)")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.setValue(backend.secret, forHTTPHeaderField: "x-sb-secret")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200 { return String(localized: "✅ Connected to \(url.host ?? "")") }
            let body = String(data: data, encoding: .utf8)?.prefix(60) ?? ""
            return "❌ HTTP \(code) \(body)"
        } catch {
            return "❌ \(error.localizedDescription)"
        }
    }
}
