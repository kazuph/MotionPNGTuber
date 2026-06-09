// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DokochanRuntime",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "DokochanRuntime", targets: ["DokochanRuntime"])
    ],
    targets: [
        .executableTarget(name: "DokochanRuntime")
    ]
)
