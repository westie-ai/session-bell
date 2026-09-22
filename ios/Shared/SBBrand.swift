import SwiftUI
import UIKit

/// Missing engine is the legacy Claude schema, not an unknown agent.
enum SBAgent: Equatable {
    case claude, codex, cursor, unknown

    init(engine: String?) {
        switch engine?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? "" {
        case "", "claude": self = .claude
        case "codex": self = .codex
        case "cursor": self = .cursor
        default: self = .unknown
        }
    }

    var name: String {
        switch self { case .claude: return "Claude"; case .codex: return "Codex"; case .cursor: return "Cursor"; case .unknown: return "Agent" }
    }

    var assetName: String? {
        switch self { case .claude: return "AgentClaude"; case .codex: return "AgentCodex"; case .cursor: return "AgentCursor"; case .unknown: return nil }
    }
}

/// Identity and status are independent: never tint the brand mark by task status.
/// Both the app and widget bundle include AgentAssets.xcassets.
struct SBAgentIcon: View {
    let engine: String?
    var size: CGFloat = 34

    private var agent: SBAgent { SBAgent(engine: engine) }

    var body: some View {
        Group {
            if let asset = agent.assetName {
                Image(asset)
                    .renderingMode(.original)
                    .resizable()
                    .scaledToFit()
                    // The Codex and Cursor sources include a wider built-in clear-space margin.
                    .frame(width: agent == .claude ? size * 0.86 : size,
                           height: agent == .claude ? size * 0.86 : size)
                    .clipShape(RoundedRectangle(
                        cornerRadius: agent == .claude ? size * 0.18 : 0,
                        style: .continuous))
            } else {
                Image(systemName: "terminal")
                    .font(.system(size: size * 0.55, weight: .medium))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: size, height: size)
        .accessibilityLabel(Text(agent.name))
    }
}

/// SessionBell 品牌色 — 黄油底 + 铃铛黄 + 炭黑描线(cartoon-2 图标同源)。
extension Color {
    /// 主强调:铃铛黄(浅底上用 sbAccentDeep 保证对比度)
    static let sbAccent = Color(red: 0.996, green: 0.808, blue: 0.137)   // #FECE23
    /// 深一档的琥珀,用于浅色背景上的文字/图标
    static let sbAccentDeep = Color(red: 0.851, green: 0.600, blue: 0.0) // #D99900
    /// 黄油底
    static let sbButter = Color(red: 0.996, green: 0.910, blue: 0.537)   // #FEE889

    // MARK: 设计规范(2026-09 改版):暖调中性色 + 状态色只用于状态

    private static func sbDynamic(_ light: UInt32, _ dark: UInt32) -> Color {
        func c(_ hex: UInt32) -> UIColor {
            UIColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                    blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
        }
        return Color(UIColor { $0.userInterfaceStyle == .dark ? c(dark) : c(light) })
    }

    /// 页面底色(带一点暖,和黄色同温度)
    static let sbBackground  = sbDynamic(0xF4F3EF, 0x121210)
    /// 卡片底
    static let sbCard        = sbDynamic(0xFFFFFF, 0x1C1B18)
    /// 正文 / 次级 / 三级墨色
    static let sbInk         = sbDynamic(0x1C1B18, 0xF2F1EC)
    static let sbInk2        = sbDynamic(0x6B685F, 0xA6A296)
    static let sbInk3        = sbDynamic(0x9C998F, 0x75726A)
    /// 分割线 / 描边
    static let sbLine        = sbDynamic(0xECEAE3, 0x2A2925)
    /// 文字上的品牌强调(浅色用深黄保证对比,深色直接用铃铛黄)
    static let sbAccentText  = sbDynamic(0xD99900, 0xFECE23)
    /// 状态色:只出现在状态色块和状态词上
    static let sbWaiting     = sbDynamic(0xD97757, 0xF0A07C)
    static let sbRunning     = sbDynamic(0x3B7DD8, 0x7FB2F0)
    static let sbDone        = sbDynamic(0x3E9B5F, 0x7CC79A)
    static let sbWaitingSoft = sbDynamic(0xFBEAE3, 0x3A2A22)
    static let sbRunningSoft = sbDynamic(0xE6EFFB, 0x1F2C3F)
    static let sbDoneSoft    = sbDynamic(0xE5F2EA, 0x1F3327)
    static let sbApprovalSoft = sbDynamic(0xFEF3C7, 0x3A3016)

    static func sbStatus(_ status: String) -> Color {
        switch status { case "waiting": return .sbWaiting; case "running": return .sbRunning; case "done": return .sbDone; default: return .sbInk2 }
    }
    static func sbStatusSoft(_ status: String) -> Color {
        switch status { case "waiting": return .sbWaitingSoft; case "running": return .sbRunningSoft; case "done": return .sbDoneSoft; default: return .sbInk2.opacity(0.1) }
    }
    static func sbStatusSymbol(_ status: String) -> String {
        switch status { case "waiting": return "ellipsis.bubble"; case "running": return "arrow.triangle.2.circlepath"; case "done": return "checkmark"; default: return "questionmark" }
    }
}


extension String {
    /// Claude Code 注入的伪 prompt(后台任务完成通知、系统提醒、斜杠命令回显…),不是用户敲的。
    /// 旧版 hook 会把它当任务名上报;界面上一律不显示,退回项目名。
    var sbIsInjectedPrompt: Bool {
        let t = trimmingCharacters(in: .whitespacesAndNewlines)
        return t.hasPrefix("<task-notification") || t.hasPrefix("<system-reminder")
            || t.hasPrefix("<command-name") || t.hasPrefix("<local-command")
            || t.hasPrefix("<user-prompt-submit-hook") || t.hasPrefix("[Request interrupted")
    }
    /// 过滤后的任务名:注入文本 → 空串。
    var sbCleanPrompt: String { sbIsInjectedPrompt ? "" : self }
}

// MARK: 引导页按钮(品牌同源:铃铛黄底 + 炭黑字;次按钮白卡 + 描线)

extension Color {
    /// 黄底上的字,不随深色模式变浅
    static let sbInkOnAccent = Color(red: 0.11, green: 0.106, blue: 0.094)
}

struct SBPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        SBButtonBody(configuration: configuration, primary: true)
    }
}

struct SBSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        SBButtonBody(configuration: configuration, primary: false)
    }
}

private struct SBButtonBody: View {
    let configuration: ButtonStyle.Configuration
    let primary: Bool
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(primary ? Color.sbInkOnAccent : Color.sbInk)
            .frame(maxWidth: .infinity, minHeight: 52)
            .background(primary ? Color.sbAccent : Color.sbCard,
                        in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(primary ? Color.clear : Color.sbLine, lineWidth: 1))
            .opacity(!isEnabled ? 0.45 : configuration.isPressed ? 0.75 : 1)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
