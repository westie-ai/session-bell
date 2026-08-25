import ActivityKit
import SwiftUI
import WidgetKit

@main
struct SessionBellWidgetBundle: WidgetBundle {
    var body: some Widget {
        SessionLiveActivity()
    }
}

private let coral = Color.sbAccentDeep  // #D97757

private typealias TaskItem = SessionActivityAttributes.TaskItem
private typealias DashState = SessionActivityAttributes.ContentState

struct SessionLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: SessionActivityAttributes.self) { context in
            LockScreenView(state: context.state, attrs: context.attributes)
                .activityBackgroundTint(Color(.systemBackground).opacity(0.85))
                .activitySystemActionForegroundColor(coral)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    SummaryIcon(state: context.state).font(.title2)
                }
                DynamicIslandExpandedRegion(.center) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(summaryLine(context.state))
                            .font(.caption.bold())
                        ForEach(Array(context.state.tasks.prefix(2)), id: \.self) { task in
                            TaskRow(task: task, compact: true)
                        }
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("\(context.state.activeCount)")
                        .font(.title2.monospacedDigit().bold())
                        .foregroundStyle(context.state.waitingCount > 0 ? coral : .secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    if context.state.hasApproval {
                        ApprovalButtons(state: context.state, attrs: context.attributes)
                    }
                }
            } compactLeading: {
                SummaryIcon(state: context.state)
            } compactTrailing: {
                Text("\(context.state.activeCount)")
                    .font(.caption.monospacedDigit().bold())
                    .foregroundStyle(context.state.waitingCount > 0 ? coral : .secondary)
            } minimal: {
                SummaryIcon(state: context.state)
            }
            .keylineTint(coral)
        }
    }
}

private func summaryLine(_ state: DashState) -> String {
    if state.waitingCount > 0 {
        return "\(state.waitingCount) 个任务在等你"
    }
    if state.runningCount > 0 {
        return "\(state.runningCount) 个任务运行中"
    }
    return "全部完成"
}

/// 锁屏空间金贵:机器名去掉主人前缀、机型缩写。只影响显示,不动数据里的 host。
private func shortHost(_ host: String) -> String {
    var s = host
    if let r = s.range(of: "的 ") { s = String(s[r.upperBound...]) }
    if let r = s.range(of: "的") { s = String(s[r.upperBound...]) }
    s = s.replacingOccurrences(of: "MacBook Pro", with: "MBP")
    s = s.replacingOccurrences(of: "MacBook Air", with: "MBA")
    s = s.replacingOccurrences(of: "Mac mini", with: "mini")
    return s.trimmingCharacters(in: .whitespaces)
}

private func statusColor(_ status: String) -> Color {
    switch status {
    case "waiting": return coral
    case "running": return .blue
    default: return .green
    }
}

private func statusSymbol(_ status: String) -> String {
    switch status {
    case "waiting": return "hand.raised.fill"
    case "running": return "play.circle.fill"
    default: return "checkmark.circle.fill"
    }
}

private struct SummaryIcon: View {
    let state: DashState
    var body: some View {
        let status = state.waitingCount > 0 ? "waiting" : (state.runningCount > 0 ? "running" : "done")
        Image(systemName: statusSymbol(status))
            .foregroundStyle(statusColor(status))
    }
}

private struct ElapsedText: View {
    let task: TaskItem
    var body: some View {
        if task.status == "done" {
            Text("完成")
        } else {
            Text(timerInterval: task.sinceDate...task.sinceDate.addingTimeInterval(86400),
                 countsDown: false)
                .multilineTextAlignment(.trailing)
        }
    }
}

private struct TaskRow: View {
    let task: TaskItem
    var compact = false
    /// 密集模式:任务多时行内不放详情行(第一行除外,由父视图决定)
    var showDetail = true
    /// 全部任务同一台 Mac 时,host 收进标题行,行内不再重复
    var showHost = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: compact ? 5 : 8) {
                if task.sub == true {
                    Image(systemName: "arrow.turn.down.right")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                Image(systemName: statusSymbol(task.status))
                    .font(compact ? .caption2 : .subheadline)
                    .foregroundStyle(statusColor(task.status))
                Text(task.project)
                    .font(compact ? .caption2 : .subheadline.weight(.medium))
                    .lineLimit(1)
                if showHost {
                    Text(shortHost(task.host))
                        .font(.caption2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
                if let agents = task.agents, agents > 0 {
                    Text("⚙︎\(agents)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.blue)
                }
                Spacer(minLength: 4)
                ElapsedText(task: task)
                    .font((compact ? Font.caption2 : .caption).monospacedDigit())
                    .foregroundStyle(task.status == "waiting" ? coral : .secondary)
                    .frame(maxWidth: 64, alignment: .trailing)
            }
            if !compact, showDetail, let detail = task.detail, !detail.isEmpty {
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .padding(.leading, 26)
            }
        }
    }
}

private struct LockScreenView: View {
    let state: DashState
    let attrs: SessionActivityAttributes

    var body: some View {
        // 任务一多就切密集模式:行内只留一行,详情只保第一条,能多放一行任务
        let dense = state.tasks.count > 2
        let hosts = Set(state.tasks.map(\.host))
        let sharedHost = hosts.count == 1 ? hosts.first : nil
        let shown = dense ? 4 : 3

        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                SummaryIcon(state: state).font(.subheadline)
                Text(summaryLine(state)).font(.headline)
                if let sharedHost {
                    // 全在同一台 Mac:host 只在这里出现一次
                    Text(shortHost(sharedHost))
                        .font(.caption2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "bell.fill")
                    .font(.caption)
                    .foregroundStyle(coral.opacity(0.6))
            }
            VStack(alignment: .leading, spacing: dense ? 4 : 6) {
                ForEach(Array(state.tasks.prefix(shown).enumerated()), id: \.element) { i, task in
                    TaskRow(task: task,
                            showDetail: !dense || i == 0,
                            showHost: sharedHost == nil)
                }
                if state.tasks.count > shown {
                    Text("还有 \(state.tasks.count - shown) 个（App 内查看全部）")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            if state.hasApproval {
                ApprovalButtons(state: state, attrs: attrs)
            }
        }
        .padding(14)
    }
}

#Preview("锁屏·任务多(密集)", as: .content,
         using: SessionActivityAttributes(backend: "https://example.test", secret: "x")) {
    SessionLiveActivity()
} contentStates: {
    let now = Date().timeIntervalSince1970
    SessionActivityAttributes.ContentState(
        tasks: [
            .init(project: "reply", host: "Piper的 MacBook Pro (2)", status: "waiting",
                  since: now - 81000, detail: "投放的方式也不对 就是在 ins 或者 meta 买手机广告"),
            .init(project: "reply", host: "Piper的 MacBook Pro (2)", status: "waiting",
                  since: now - 26000, detail: "ok 开 ultracode mode 实施"),
            .init(project: "reply", host: "Piper的 MacBook Pro (2)", status: "waiting",
                  since: now - 10000, detail: "开 pr 修复 457"),
            .init(project: "health-app", host: "Piper的 MacBook Pro (2)", status: "running",
                  since: now - 600, detail: nil, agents: 2),
            .init(project: "kairos", host: "Piper的 MacBook Pro (2)", status: "waiting",
                  since: now - 300, detail: "migration 待确认"),
            .init(project: "wesdget", host: "Piper的 MacBook Pro (2)", status: "done",
                  since: now - 100, detail: nil),
        ],
        updatedAt: now)
    SessionActivityAttributes.ContentState(
        tasks: [
            .init(project: "reply", host: "Piper的 MacBook Pro (2)", status: "waiting",
                  since: now - 5000, detail: "开 pr 修复 457"),
            .init(project: "kairos", host: "工作室 Mac mini", status: "running",
                  since: now - 900, detail: nil),
        ],
        updatedAt: now)
}

private struct ApprovalButtons: View {
    let state: DashState
    let attrs: SessionActivityAttributes

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let summary = state.approvalSummary, !summary.isEmpty {
                Text(summary)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            HStack(spacing: 10) {
                Button(intent: ApprovalIntent(
                    backend: attrs.backend, secret: attrs.secret,
                    requestId: state.approvalId ?? "", decision: "allow")
                ) {
                    Label("允许", systemImage: "checkmark")
                        .font(.subheadline.bold())
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)

                Button(intent: ApprovalIntent(
                    backend: attrs.backend, secret: attrs.secret,
                    requestId: state.approvalId ?? "", decision: "deny")
                ) {
                    Label("拒绝", systemImage: "xmark")
                        .font(.subheadline.bold())
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }
        }
        .padding(.top, 2)
    }
}
