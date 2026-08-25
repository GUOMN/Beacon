import AppIntents
import Foundation
import WidgetKit

enum WidgetCommandWriter {
    static func submit(_ payload: [String: Any]) async throws {
        guard let directory = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: beaconAppGroup
        ) else { throw CocoaError(.fileNoSuchFile) }
        let commands = directory.appendingPathComponent("Commands", isDirectory: true)
        try FileManager.default.createDirectory(at: commands, withIntermediateDirectories: true)
        let identifier = String(format: "%020llu-%@.json", UInt64(Date().timeIntervalSince1970 * 1000), UUID().uuidString)
        let data = try JSONSerialization.data(withJSONObject: ["payload": payload])
        let command = commands.appendingPathComponent(identifier)
        try data.write(to: command, options: .atomic)

        for _ in 0..<12 where FileManager.default.fileExists(atPath: command.path) {
            try await Task.sleep(for: .milliseconds(200))
        }
        WidgetCenter.shared.reloadAllTimelines()
    }
}

struct PinTaskIntent: AppIntent {
    static var title: LocalizedStringResource = "固定任务"
    static var openAppWhenRun = false

    @Parameter(title: "任务") var taskID: String
    @Parameter(title: "固定") var pinned: Bool

    init() {}
    init(taskID: String, pinned: Bool) {
        self.taskID = taskID
        self.pinned = pinned
    }

    func perform() async throws -> some IntentResult {
        try await WidgetCommandWriter.submit(["operation": "pin", "task_id": taskID, "pinned": pinned])
        return .result()
    }
}

struct DeleteTaskIntent: AppIntent {
    static var title: LocalizedStringResource = "删除任务"
    static var openAppWhenRun = false

    @Parameter(title: "任务") var taskID: String

    init() {}
    init(taskID: String) { self.taskID = taskID }

    func perform() async throws -> some IntentResult {
        try await WidgetCommandWriter.submit(["operation": "delete", "task_ids": [taskID]])
        return .result()
    }
}

struct ClearCompletedIntent: AppIntent {
    static var title: LocalizedStringResource = "清理已完成任务"
    static var openAppWhenRun = false

    init() {}

    func perform() async throws -> some IntentResult {
        try await WidgetCommandWriter.submit(["operation": "delete-completed"])
        return .result()
    }
}

struct MoveTaskIntent: AppIntent {
    static var title: LocalizedStringResource = "移动任务"
    static var openAppWhenRun = false

    @Parameter(title: "任务") var taskID: String
    @Parameter(title: "方向") var offset: Int

    init() {}
    init(taskID: String, offset: Int) {
        self.taskID = taskID
        self.offset = offset
    }

    func perform() async throws -> some IntentResult {
        let dashboard = BeaconDashboard.load()
        var ids = dashboard.tasks.map(\.taskID)
        guard let source = ids.firstIndex(of: taskID) else { return .result() }
        let target = min(max(0, source + offset), ids.count - 1)
        guard target != source else { return .result() }
        ids.remove(at: source)
        ids.insert(taskID, at: target)
        try await WidgetCommandWriter.submit([
            "operation": "reorder", "task_ids": ids, "dragged_id": taskID,
            "target_slot": target < dashboard.slotCount ? target : NSNull(),
        ])
        return .result()
    }
}
