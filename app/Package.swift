// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "PhotosLocalKeywordIndexer",
    platforms: [.macOS(.v14)],
    products: [
        .executable(
            name: "PhotosLocalKeywordIndexer",
            targets: ["PhotosLocalKeywordIndexer"]
        )
    ],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle.git", exact: "2.9.2"),
    ],
    targets: [
        .executableTarget(
            name: "PhotosLocalKeywordIndexer",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "PhotosLocalKeywordIndexer"
        ),
        .testTarget(
            name: "PhotosLocalKeywordIndexerTests",
            dependencies: ["PhotosLocalKeywordIndexer"],
            path: "Tests/PhotosLocalKeywordIndexerTests"
        ),
    ],
    swiftLanguageVersions: [.v5]
)
