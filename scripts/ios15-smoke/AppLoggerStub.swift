import Foundation

// Smoke-only stand-in for the app's AppLogger (Shared/AppLogger.swift).
//
// CompatNavigationPath logs through AppLogger, but the real module calls
// CrashReporter.shared.appendLog(...), and CrashReporter in turn pulls in
// SessionActivityTracker / BackgroundKeepAliveManager / BrowserTabPoolRegistry /
// MetricKit — i.e. half the app, far beyond what an iOS 15/16 type-check of the
// shared compatibility modules should compile. Mirror the MinisTests shim
// (TestSupport_AppLogger.swift): same call surface, NSLog only.
struct AppLogger {
    let category: String

    init(category: String) {
        self.category = category
    }

    func debug(_ message: String) { log("DEBUG", message) }
    func info(_ message: String) { log("INFO", message) }
    func warning(_ message: String) { log("WARN", message) }
    func error(_ message: String) { log("ERROR", message) }
    func critical(_ message: String) { log("CRIT", message) }
    func fault(_ message: String) { log("FAULT", message) }

    private func log(_ level: String, _ message: String) {
        NSLog("[%@] [%@] %@", category, level, message)
    }
}
