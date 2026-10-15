// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "apprentice",
    platforms: [.macOS(.v15)],
    targets: [
        // Pure logic (coalescing, AX text extraction, sessions, storage, privacy, schemas): unit tested.
        .target(name: "ApprenticeCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        // AppKit / CGEventTap / ScreenCaptureKit / control socket glue.
        .executableTarget(name: "Apprentice", dependencies: ["ApprenticeCore"], swiftSettings: [.swiftLanguageMode(.v5)]),
        .testTarget(name: "ApprenticeCoreTests", dependencies: ["ApprenticeCore"], swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
