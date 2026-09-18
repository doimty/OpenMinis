// swift-tools-version:5.7
import PackageDescription

// Vendored local copy of helloooideeeeea/RealTimeCutVADLibrary @
// b059608836a055dc0ce74ca2d0eeb19fc54cb7d4 (tag 1.0.14), MIT (LICENSE in
// this directory). The only change from upstream: the RealTimeCutVADCXXLibrary
// binaryTarget is now a LOCAL xcframework that the iOS 15 build pipeline
// produces from C++ source (deps/build_vad_framework.sh) instead of the
// upstream prebuilt zip, whose Mach-O minimum is 15.6 and would be refused by
// dyld on iOS 15.0-15.5. The framework must exist at Frameworks/ before
// package resolution.

let package = Package(
    name: "RealTimeCutVADLibrary",
    platforms: [
        .iOS(.v15),
        .macOS(.v11)
    ],
    products: [
        .library(
            name: "RealTimeCutVADLibrary",
            targets: ["RealTimeCutVADLibrary"]
        ),
    ],
    dependencies: [],
    targets: [
        .target(
            name: "RealTimeCutVADLibrary",
            dependencies: [
                .target(name: "RealTimeCutVADCXXLibrary"),
            ],
            path: "RealTimeCutVADLibrary/src",
            sources: nil,
            resources: [
                .process("Resources")
            ],
            publicHeadersPath: "include"
        ),
        .binaryTarget(
            name: "RealTimeCutVADCXXLibrary",
            path: "Frameworks/RealTimeCutVADCXXLibrary.xcframework"
        ),
    ]
)