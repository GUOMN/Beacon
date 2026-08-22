import SwiftUI

struct ContentView: View {
    @ObservedObject var model: AppModel
    @ObservedObject private var bluetooth: BluetoothManager

    init(model: AppModel) {
        self.model = model
        self.bluetooth = model.bluetooth
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            connectionHeader
            usagePanel
            Divider()
            taskPanel
            Divider()
            logPanel
        }
        .padding(22)
    }

    private var connectionHeader: some View {
        HStack {
            Circle()
                .fill(bluetooth.linkState == .connected ? Color.green : Color.orange)
                .frame(width: 12, height: 12)
            Text(bluetooth.linkState.title)
                .font(.headline)
            Spacer()
            Button("重新搜索") { bluetooth.startScanning() }
            Button("断开") { bluetooth.disconnect() }
                .disabled(bluetooth.linkState != .connected)
        }
    }

    private var usagePanel: some View {
        GroupBox("第一颗灯 · 用量") {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("剩余额度")
                    Slider(value: $model.remainingPercent, in: 0...100, step: 1)
                    Text("\(Int(model.remainingPercent))%")
                        .monospacedDigit().frame(width: 48)
                }
                HStack {
                    Text("周期已用")
                    Slider(value: $model.periodUsedPercent, in: 0...100, step: 1)
                    Text("\(Int(model.periodUsedPercent))%")
                        .monospacedDigit().frame(width: 48)
                }
                HStack {
                    Text("颜色表示剩余量，闪烁速度表示短周期用量")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("发送用量") { model.sendCurrentPanel() }
                }
            }
            .padding(8)
        }
    }

    private var taskPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("第二至第六颗灯 · 五个任务").font(.headline)
            ForEach(Array(model.tasks.enumerated()), id: \.element.id) { index, task in
                HStack {
                    Text(task.title).frame(width: 70, alignment: .leading)
                    Picker("状态", selection: $model.tasks[index].state) {
                        ForEach(TaskState.allCases) { state in
                            Text(state.title).tag(state)
                        }
                    }
                    .labelsHidden().frame(width: 130)
                    Slider(
                        value: Binding(
                            get: { Double(model.tasks[index].progress) },
                            set: { model.tasks[index].progress = UInt8($0.rounded()) }
                        ),
                        in: 0...100, step: 1
                    )
                    Text("\(model.tasks[index].progress)%")
                        .monospacedDigit().frame(width: 44)
                    Button("发送") { model.sendCurrentPanel() }
                }
            }
        }
    }

    private var logPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("连接日志").font(.headline)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(bluetooth.logLines.enumerated()), id: \.offset) { _, line in
                        Text(line).font(.system(.caption, design: .monospaced))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minHeight: 90)
            .padding(8)
            .background(Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}
