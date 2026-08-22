import Foundation

// 与 ESP32 固件中的语义状态编号保持一致。
enum TaskState: UInt8, CaseIterable, Identifiable {
    case idle = 0
    case running = 1
    case waiting = 2
    case success = 3
    case warning = 4
    case failure = 5

    var id: UInt8 { rawValue }

    var title: String {
        switch self {
        case .idle: return "无任务"
        case .running: return "进行中"
        case .waiting: return "等待操作"
        case .success: return "已完成"
        case .warning: return "警告"
        case .failure: return "失败"
        }
    }
}

struct TaskSlot: Identifiable {
    let id: Int
    var title: String
    var state: TaskState
    var progress: UInt8

    static func empty(index: Int) -> TaskSlot {
        TaskSlot(id: index, title: "任务 \(index + 1)", state: .idle, progress: 0)
    }
}

enum BluetoothLinkState: Equatable {
    case unavailable
    case scanning
    case connecting
    case connected
    case disconnected

    var title: String {
        switch self {
        case .unavailable: return "蓝牙不可用"
        case .scanning: return "正在搜索灯板"
        case .connecting: return "正在连接"
        case .connected: return "已连接"
        case .disconnected: return "已断开，等待重连"
        }
    }
}
