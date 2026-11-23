import SwiftUI
import WidgetKit

/// 桌面 / 锁屏用量 widget:5 小时窗口、周配额、高级模型,口径与 App「用量」页一致。
/// 数据:widget 自己拉 /api/state,拉不到就用 App 最近写进 App Group 的快照。
struct UsageWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: SBUsageShared.widgetKind, provider: UsageProvider()) { entry in
            UsageWidgetView(entry: entry)
                .containerBackground(for: .widget) { Color.sbCard }
        }
        .configurationDisplayName("Claude Code Usage")
        .description("5-hour window and weekly quota at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

struct UsageEntry: TimelineEntry {
    let date: Date
    let snapshot: SBUsageShared.Snapshot?
}

struct UsageProvider: TimelineProvider {
    func placeholder(in context: Context) -> UsageEntry {
        UsageEntry(date: Date(), snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (UsageEntry) -> Void) {
        if context.isPreview {
            completion(UsageEntry(date: Date(), snapshot: SBUsageShared.loadSnapshot() ?? .placeholder))
            return
        }
        Task { completion(UsageEntry(date: Date(), snapshot: await load())) }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<UsageEntry>) -> Void) {
        Task {
            let entry = UsageEntry(date: Date(), snapshot: await load())
            // Mac 端官方用量 15 分钟缓存一次,widget 跟着 15 分钟刷新足够
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(15 * 60))))
        }
    }

    private func load() async -> SBUsageShared.Snapshot? {
        if let fresh = await SBUsageShared.fetch() { return fresh }
        return SBUsageShared.loadSnapshot()
    }
}

// MARK: - Views

private func usageColor(_ f: Double) -> Color { f >= 0.85 ? .sbWaiting : .sbAccentText }
private func pct(_ f: Double) -> String { "\(Int(min(f, 9.99) * 100))%" }

struct UsageWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: UsageEntry

    var body: some View {
        if let snap = entry.snapshot {
            switch family {
            case .accessoryCircular: CircularView(snap: snap)
            case .accessoryRectangular: RectangularView(snap: snap)
            case .accessoryInline: InlineView(snap: snap)
            case .systemMedium: MediumView(snap: snap)
            default: SmallView(snap: snap)
            }
        } else {
            EmptyUsageView(compact: family != .systemSmall && family != .systemMedium)
        }
    }
}

private struct EmptyUsageView: View {
    let compact: Bool
    var body: some View {
        if compact {
            Image(systemName: "gauge.with.needle")
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: "gauge.with.needle").font(.title3).foregroundStyle(Color.sbAccentText)
                Text("No Usage Data").font(.subheadline.weight(.semibold)).foregroundStyle(Color.sbInk)
                Text("Open SessionBell once after pairing a Mac.")
                    .font(.caption2).foregroundStyle(Color.sbInk3)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
}

/// 桌面小号:大环 = 5 小时窗口,下面一行周配额。
private struct SmallView: View {
    let snap: SBUsageShared.Snapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Image(systemName: "bell.fill").foregroundStyle(Color.sbAccentText)
                Text("Usage").foregroundStyle(Color.sbInk2)
                Spacer()
            }
            .font(.caption2.weight(.semibold))
            if let s = snap.sessionPct {
                Ring(fraction: s, lineWidth: 8) {
                    VStack(spacing: 0) {
                        Text(pct(s)).font(.system(size: 20, weight: .semibold).monospacedDigit())
                            .foregroundStyle(s >= 0.85 ? Color.sbWaiting : Color.sbInk)
                        Text("5h").font(.caption2).foregroundStyle(Color.sbInk3)
                    }
                }
                .frame(maxWidth: .infinity)
            }
            if let w = snap.weekPct {
                MiniBar(label: "Week", fraction: w)
            }
            if snap.sessionPct == nil, let p = snap.premiumPct {
                MiniBar(label: LocalizedStringKey(snap.premiumName ?? "Premium"), fraction: p)
            }
        }
    }
}

/// 桌面中号:三条进度条 + 各自重置时间。
private struct MediumView: View {
    let snap: SBUsageShared.Snapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Image(systemName: "bell.fill").foregroundStyle(Color.sbAccentText)
                Text("Claude Code").foregroundStyle(Color.sbInk)
                Spacer()
                Text(snap.reportedAt, style: .relative).foregroundStyle(Color.sbInk3)
            }
            .font(.caption.weight(.semibold))
            Spacer(minLength: 0)
            if let s = snap.sessionPct {
                MeterRow(icon: "clock", label: "5-Hour Window", fraction: s, reset: snap.sessionReset)
            }
            if let w = snap.weekPct {
                MeterRow(icon: "gauge.with.needle", label: "Weekly Quota", fraction: w, reset: snap.weekReset)
            }
            if let p = snap.premiumPct {
                MeterRow(icon: "sparkles", label: LocalizedStringKey(snap.premiumName ?? "Premium Model"),
                         fraction: p, reset: nil)
            }
            Spacer(minLength: 0)
        }
    }
}

private struct MeterRow: View {
    let icon: String
    let label: LocalizedStringKey
    let fraction: Double
    let reset: Date?
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 4) {
                Image(systemName: icon).frame(width: 14)
                Text(label)
                if let reset, reset > Date() {
                    Text("· ") + Text(reset, style: .relative)
                }
                Spacer()
                Text(pct(fraction)).fontWeight(.semibold).monospacedDigit()
                    .foregroundStyle(fraction >= 0.85 ? Color.sbWaiting : Color.sbInk)
            }
            .font(.caption2)
            .foregroundStyle(Color.sbInk2)
            .lineLimit(1)
            Bar(fraction: fraction).frame(height: 5)
        }
    }
}

private struct MiniBar: View {
    let label: LocalizedStringKey
    let fraction: Double
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(label)
                Spacer()
                Text(pct(fraction)).monospacedDigit()
            }
            .font(.caption2.weight(.medium))
            .foregroundStyle(Color.sbInk2)
            Bar(fraction: fraction).frame(height: 4)
        }
    }
}

private struct Bar: View {
    let fraction: Double
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.sbLine)
                Capsule().fill(usageColor(fraction))
                    .frame(width: max(4, geo.size.width * min(fraction, 1)))
            }
        }
    }
}

private struct Ring<Label: View>: View {
    let fraction: Double
    let lineWidth: CGFloat
    @ViewBuilder let label: () -> Label
    var body: some View {
        ZStack {
            Circle().stroke(Color.sbLine, lineWidth: lineWidth)
            Circle().trim(from: 0, to: min(fraction, 1))
                .stroke(usageColor(fraction), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
            label()
        }
        .padding(lineWidth / 2)
    }
}

// MARK: 锁屏

private struct CircularView: View {
    let snap: SBUsageShared.Snapshot
    var body: some View {
        let f = snap.sessionPct ?? snap.weekPct ?? 0
        Gauge(value: min(f, 1)) {
            Text(snap.sessionPct != nil ? "5h" : "wk")
        } currentValueLabel: {
            Text("\(Int(min(f, 9.99) * 100))")
        }
        .gaugeStyle(.accessoryCircularCapacity)
    }
}

private struct RectangularView: View {
    let snap: SBUsageShared.Snapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let s = snap.sessionPct {
                row(label: "5h", fraction: s, reset: snap.sessionReset)
            }
            if let w = snap.weekPct {
                row(label: "Week", fraction: w, reset: snap.weekReset)
            }
            if let p = snap.premiumPct, snap.sessionPct == nil || snap.weekPct == nil {
                row(label: LocalizedStringKey(snap.premiumName ?? "Premium"), fraction: p, reset: nil)
            }
        }
    }

    private func row(label: LocalizedStringKey, fraction: Double, reset: Date?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 4) {
                Text(label).font(.caption2.weight(.semibold))
                Text(pct(fraction)).font(.caption2.monospacedDigit())
                if let reset, reset > Date() {
                    Text(reset, style: .relative).font(.caption2).foregroundStyle(.secondary)
                }
            }
            .lineLimit(1)
            Gauge(value: min(fraction, 1)) { EmptyView() }
                .gaugeStyle(.accessoryLinearCapacity)
        }
    }
}

private struct InlineView: View {
    let snap: SBUsageShared.Snapshot
    var body: some View {
        if let s = snap.sessionPct, let w = snap.weekPct {
            Text("5h \(pct(s)) · wk \(pct(w))")
        } else if let s = snap.sessionPct {
            Text("5h \(pct(s))")
        } else if let w = snap.weekPct {
            Text("wk \(pct(w))")
        }
    }
}
