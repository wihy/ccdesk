// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CCDesk",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "CCDesk", path: "Sources/CCDesk"),
        // 打包产物结构测试是 shell 脚本（在 swift test 里再跑一次 swift build -c release
        // 会撞同一个 .build 锁），SwiftPM 不认识它，显式排除掉免 unhandled-file 警告。
        .testTarget(name: "CCDeskTests", dependencies: ["CCDesk"], path: "Tests/CCDeskTests",
                    exclude: ["bundle_structure_test.sh"]),
    ]
)
