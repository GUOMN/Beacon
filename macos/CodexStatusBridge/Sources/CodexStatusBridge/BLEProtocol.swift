import Foundation

enum BLEProtocol {
    static let deviceName = "Codex-Status-6"

    // ESP32 NimBLE 固件中的 128 位服务和控制特征 UUID。
    static let serviceUUID = "0100C310-7625-819E-934C-32B8E4177D6A"
    static let controlUUID = "0200C310-7625-819E-934C-32B8E4177D6A"

    private static let magic: UInt8 = 0xC3
    private static let version: UInt8 = 0x01
    private static let heartbeatType: UInt8 = 0x01
    private static let snapshotType: UInt8 = 0x02

    // 四字节心跳用于防止 ESP32 将连接判定为数据超时。
    static func heartbeat(sequence: UInt8) -> Data {
        Data([magic, version, heartbeatType, sequence])
    }

    /*
     * 十六字节完整快照：
     * 0 魔数，1 版本，2 类型，3 序号，4 剩余量，5 周期已用量，
     * 6~10 五个任务状态，11~15 五个任务进度。
     */
    static func snapshot(sequence: UInt8,
                         remaining: UInt8,
                         periodUsed: UInt8,
                         tasks: [TaskSlot]) -> Data {
        precondition(tasks.count == 5)
        var bytes: [UInt8] = [
            magic, version, snapshotType, sequence,
            min(remaining, 100), min(periodUsed, 100),
        ]
        bytes.append(contentsOf: tasks.map { $0.state.rawValue })
        bytes.append(contentsOf: tasks.map { min($0.progress, 100) })
        return Data(bytes)
    }
}
