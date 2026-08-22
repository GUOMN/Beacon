// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "CodexStatusBridge",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "CodexStatusBridge", targets: ["CodexStatusBridge"]),
    ],
    targets: [
        .executableTarget(
            name: "CodexStatusBridge",
            path: "Sources/CodexStatusBridge"
        ),
    ]
)
