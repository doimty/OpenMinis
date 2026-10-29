// This historical standalone entry point now runs the actual production
// ISHCommandLifecycle regression instead of copying its policy into a model.
// It compiles the unchanged production Swift file with the native macOS SDK.
// Linux returns 77 (SKIP), never a fabricated native pass.
import Foundation

var dir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
var script: URL?
for _ in 0..<8 {
    let candidate = dir.appendingPathComponent("scripts/test_ish_command_lifecycle.sh")
    if FileManager.default.fileExists(atPath: candidate.path) { script = candidate; break }
    dir = dir.deletingLastPathComponent()
}
guard let script = script else {
    fputs("error: cannot locate scripts/test_ish_command_lifecycle.sh\n", stderr)
    exit(2)
}
let child = Process()
child.executableURL = URL(fileURLWithPath: "/usr/bin/env")
child.arguments = ["bash", script.path]
child.standardOutput = FileHandle.standardOutput
child.standardError = FileHandle.standardError
do {
    try child.run()
    child.waitUntilExit()
    exit(child.terminationStatus)
} catch {
    fputs("error: cannot start production lifecycle regression: \(error)\n", stderr)
    exit(2)
}
