import Foundation
import SwiftUI

let beaconAppGroup = "group.com.codexstatus.bridge"

struct BeaconTask: Codable, Identifiable {
    let taskID: String
    let title: String
    let source: String
    let state: String
    let progress: Int
    let occurredAtMs: Int64
    let pinned: Int

    var id: String { taskID }
    var isPinned: Bool { pinned != 0 }

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case title, source, state, progress, pinned
        case occurredAtMs = "occurred_at_ms"
    }
}

struct BeaconDashboard: Codable {
    let schemaVersion: Int
    let updatedAtMs: Int64
    let slotCount: Int
    let theme: String
    let tasks: [BeaconTask]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case updatedAtMs = "updated_at_ms"
        case slotCount = "slot_count"
        case theme, tasks
    }

    static let placeholder = BeaconDashboard(
        schemaVersion: 1,
        updatedAtMs: 0,
        slotCount: 5,
        theme: "default",
        tasks: [
            BeaconTask(taskID: "preview-1", title: "同步桌面快捷操作", source: "codex", state: "running", progress: 56, occurredAtMs: 0, pinned: 1),
            BeaconTask(taskID: "preview-2", title: "等待设备确认", source: "beacon", state: "waiting", progress: 20, occurredAtMs: 0, pinned: 0),
            BeaconTask(taskID: "preview-3", title: "状态灯已更新", source: "local", state: "success", progress: 100, occurredAtMs: 0, pinned: 0),
        ]
    )

    static func load() -> BeaconDashboard {
        guard let directory = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: beaconAppGroup
        ) else { return .placeholder }
        let url = directory.appendingPathComponent("dashboard.json")
        guard let data = try? Data(contentsOf: url),
              let dashboard = try? JSONDecoder().decode(BeaconDashboard.self, from: data)
        else { return .placeholder }
        return dashboard
    }
}

struct BeaconPalette {
    let background: Color
    let panel: Color
    let divider: Color
    let primary: Color
    let secondary: Color
    let accent: Color

    static func forTheme(_ theme: String) -> BeaconPalette {
        switch theme {
        case "mecha":
            return BeaconPalette(
                background: Color(hex: 0x080D0A), panel: Color(hex: 0x101813),
                divider: Color(hex: 0x304538), primary: Color(hex: 0xDCE7DF),
                secondary: Color(hex: 0x78A087), accent: Color(hex: 0x72D497)
            )
        case "aldnoah":
            return BeaconPalette(
                background: Color(hex: 0x11172C), panel: Color(hex: 0x252A49),
                divider: Color(hex: 0x534A70), primary: Color(hex: 0xF3F1F8),
                secondary: Color(hex: 0xA9A1BC), accent: Color(hex: 0xD5B966)
            )
        default:
            return BeaconPalette(
                background: Color(hex: 0xF8FAFC), panel: .white,
                divider: Color(hex: 0xE0E5EB), primary: Color(hex: 0x2B333D),
                secondary: Color(hex: 0x7C8997), accent: Color(hex: 0x4AA8F5)
            )
        }
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8) & 0xff) / 255,
            blue: Double(hex & 0xff) / 255
        )
    }
}

func stateAppearance(_ state: String, theme: String) -> (label: String, color: Color) {
    let labels: [String: [String: String]] = [
        "default": ["idle": "无任务", "running": "进行中", "waiting": "等待操作", "success": "已完成", "warning": "警告", "failure": "失败"],
        "mecha": ["idle": "待机", "running": "作战中", "waiting": "待授权", "success": "任务完成", "warning": "异常预警", "failure": "单元失效"],
        "aldnoah": ["idle": "待命", "running": "交战中", "waiting": "等待指令", "success": "任务结束", "warning": "战术预警", "failure": "单元损失"],
    ]
    let colors: [String: Color] = [
        "idle": Color(hex: 0x8B95A7), "running": Color(hex: 0x4AA8F5),
        "waiting": Color(hex: 0xF7C84B), "success": Color(hex: 0x60CAA0),
        "warning": Color(hex: 0xF18D6F), "failure": Color(hex: 0xE26772),
    ]
    return (labels[theme]?[state] ?? labels["default"]?[state] ?? state, colors[state] ?? Color(hex: 0xF18D6F))
}
