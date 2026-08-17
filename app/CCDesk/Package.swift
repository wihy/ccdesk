// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CCDesk",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "CCDesk", path: "Sources/CCDesk"),
        .testTarget(name: "CCDeskTests", dependencies: ["CCDesk"], path: "Tests/CCDeskTests"),
    ]
)
