import SwiftUI

/// SessionBell 品牌色 — 黄油底 + 铃铛黄 + 炭黑描线(cartoon-2 图标同源)。
extension Color {
    /// 主强调:铃铛黄(浅底上用 sbAccentDeep 保证对比度)
    static let sbAccent = Color(red: 0.996, green: 0.808, blue: 0.137)   // #FECE23
    /// 深一档的琥珀,用于浅色背景上的文字/图标
    static let sbAccentDeep = Color(red: 0.851, green: 0.600, blue: 0.0) // #D99900
    /// 黄油底
    static let sbButter = Color(red: 0.996, green: 0.910, blue: 0.537)   // #FEE889
}
