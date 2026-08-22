import SwiftUI

@main
struct CodexStatusBridgeApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("Codex 六灯蓝牙桥接") {
            ContentView(model: model)
                .frame(minWidth: 680, minHeight: 560)
        }
    }
}
