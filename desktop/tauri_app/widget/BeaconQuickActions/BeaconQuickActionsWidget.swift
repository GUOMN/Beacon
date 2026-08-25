import AppIntents
import SwiftUI
import WidgetKit

struct BeaconEntry: TimelineEntry {
    let date: Date
    let dashboard: BeaconDashboard
}

struct BeaconProvider: TimelineProvider {
    func placeholder(in context: Context) -> BeaconEntry {
        BeaconEntry(date: .now, dashboard: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (BeaconEntry) -> Void) {
        completion(BeaconEntry(date: .now, dashboard: context.isPreview ? .placeholder : .load()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<BeaconEntry>) -> Void) {
        let entry = BeaconEntry(date: .now, dashboard: .load())
        completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(15))))
    }
}

struct BeaconQuickActionsView: View {
    @Environment(\.widgetFamily) private var family
    let entry: BeaconEntry

    private var dashboard: BeaconDashboard { entry.dashboard }
    private var palette: BeaconPalette { .forTheme(dashboard.theme) }
    private var visibleCount: Int {
        switch family {
        case .systemSmall: 2
        case .systemMedium: 3
        default: min(5, max(dashboard.slotCount, 3))
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(palette.divider)
            taskList
        }
        .foregroundStyle(palette.primary)
        .containerBackground(for: .widget) { palette.background }
        .widgetURL(URL(string: "beacon://quick-actions"))
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "light.beacon.max.fill")
                .font(.system(size: family == .systemSmall ? 15 : 17, weight: .semibold))
                .foregroundStyle(palette.accent)
            VStack(alignment: .leading, spacing: 1) {
                Text(dashboard.theme == "mecha" ? "BEACON // MS" : dashboard.theme == "aldnoah" ? "BEACON // AZ" : "Beacon")
                    .font(.system(size: 13, weight: .semibold, design: dashboard.theme == "default" ? .default : .monospaced))
                    .lineLimit(1)
                if family != .systemSmall {
                    Text("任务与灯位")
                        .font(.system(size: 9))
                        .foregroundStyle(palette.secondary)
                }
            }
            Spacer(minLength: 4)
            Text("\(dashboard.tasks.count) 个任务")
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(palette.secondary)
            if family == .systemLarge {
                Button(intent: ClearCompletedIntent()) {
                    Image(systemName: "trash")
                }
                .buttonStyle(.plain)
                .foregroundStyle(palette.secondary)
                .help("清理已完成任务")
            }
        }
        .padding(.horizontal, 12)
        .frame(height: family == .systemSmall ? 34 : 42)
        .background(palette.panel)
    }

    private var taskList: some View {
        VStack(spacing: 0) {
            ForEach(0..<visibleCount, id: \.self) { index in
                taskRow(index: index, task: dashboard.tasks.indices.contains(index) ? dashboard.tasks[index] : nil)
                if index < visibleCount - 1 { Divider().overlay(palette.divider.opacity(0.7)) }
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private func taskRow(index: Int, task: BeaconTask?) -> some View {
        let state = stateAppearance(task?.state ?? "idle", theme: dashboard.theme)
        HStack(spacing: family == .systemSmall ? 6 : 9) {
            HStack(spacing: 3) {
                Image(systemName: "lightbulb.fill").font(.system(size: 9))
                Text(index < dashboard.slotCount ? "\(index + 1)" : "-")
            }
            .frame(width: family == .systemSmall ? 20 : 28, alignment: .leading)
            .font(.system(size: 9, weight: .medium))
            .foregroundStyle(palette.secondary)

            VStack(alignment: .leading, spacing: 2) {
                Text(task?.title ?? "空闲灯位")
                    .font(.system(size: family == .systemSmall ? 10 : 11, weight: .semibold))
                    .lineLimit(1)
                if family != .systemSmall {
                    Text(task?.source ?? "暂无任务映射")
                        .font(.system(size: 8))
                        .foregroundStyle(palette.secondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 4) {
                Circle().fill(state.color).frame(width: 6, height: 6)
                if family != .systemSmall {
                    Text(index < dashboard.slotCount ? state.label : "未上灯")
                        .font(.system(size: 8, weight: .medium))
                        .lineLimit(1)
                }
            }
            .foregroundStyle(state.color)

            if let task, family == .systemMedium {
                actionButton(task.isPinned ? "pin.slash" : "pin", intent: PinTaskIntent(taskID: task.taskID, pinned: !task.isPinned), color: task.isPinned ? palette.accent : palette.secondary)
            }
            if let task, family == .systemLarge {
                HStack(spacing: 7) {
                    actionButton("arrow.up", intent: MoveTaskIntent(taskID: task.taskID, offset: -1), color: palette.secondary)
                    actionButton("arrow.down", intent: MoveTaskIntent(taskID: task.taskID, offset: 1), color: palette.secondary)
                    actionButton(task.isPinned ? "pin.slash" : "pin", intent: PinTaskIntent(taskID: task.taskID, pinned: !task.isPinned), color: task.isPinned ? palette.accent : palette.secondary)
                    actionButton("trash", intent: DeleteTaskIntent(taskID: task.taskID), color: Color(hex: 0xE26772))
                }
            }
        }
        .padding(.horizontal, 12)
        .frame(height: family == .systemSmall ? 42 : family == .systemMedium ? 43 : 48)
    }

    private func actionButton<I: AppIntent>(_ symbol: String, intent: I, color: Color) -> some View {
        Button(intent: intent) {
            Image(systemName: symbol).font(.system(size: 10, weight: .semibold))
        }
        .buttonStyle(.plain)
        .foregroundStyle(color)
        .frame(width: 16, height: 20)
    }
}

struct BeaconQuickActionsWidget: Widget {
    let kind = "BeaconQuickActions"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: BeaconProvider()) { entry in
            BeaconQuickActionsView(entry: entry)
        }
        .configurationDisplayName("Beacon 快捷操作")
        .description("查看任务与灯位，并直接执行常用操作。")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
        .contentMarginsDisabled()
    }
}
