import Foundation

final class AppModel: ObservableObject {
    @Published var remainingPercent: Double = 100
    @Published var periodUsedPercent: Double = 0
    @Published var tasks: [TaskSlot] = (0..<5).map(TaskSlot.empty)

    let bluetooth = BluetoothManager()

    func sendCurrentPanel() {
        bluetooth.sendSnapshot(
            remaining: UInt8(remainingPercent.rounded()),
            periodUsed: UInt8(periodUsedPercent.rounded()),
            tasks: tasks
        )
    }

    func updateTask(id: Int, state: TaskState, progress: Double) {
        guard let index = tasks.firstIndex(where: { $0.id == id }) else { return }
        tasks[index].state = state
        tasks[index].progress = UInt8(max(0, min(100, progress.rounded())))
        sendCurrentPanel()
    }
}
