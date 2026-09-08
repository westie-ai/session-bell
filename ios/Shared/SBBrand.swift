import SwiftUI
import UIKit

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
        switch status { case "waiting": return .sbWaiting; case "running": return .sbRunning; default: return .sbDone }
    }
    static func sbStatusSoft(_ status: String) -> Color {
        switch status { case "waiting": return .sbWaitingSoft; case "running": return .sbRunningSoft; default: return .sbDoneSoft }
    }
    static func sbStatusSymbol(_ status: String) -> String {
        switch status { case "waiting": return "ellipsis.bubble"; case "running": return "arrow.triangle.2.circlepath"; default: return "checkmark" }
    }
}
