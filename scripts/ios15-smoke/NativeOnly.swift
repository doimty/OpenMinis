import SwiftUI
import UIKit
import WebKit
import Speech
import FileProvider
import UserNotifications
import AVFoundation

// Negative controls use the actual rejected API shapes. This entire file must
// type-check at 16, and diagnose availability (not bad syntax) at 15.
struct NativeLabelControl: View {
    var body: some View { LabeledContent("Created", value: "2026-09-18") }
}

struct NativeNavigationControl: View {
    var body: some View { NavigationStack { Text("Restore from Server") } }
}

struct NativeSheetControl: View {
    var body: some View {
        Text("Downloading Backup")
            .presentationDetents([.height(240)])
            .interactiveDismissDisabled(true)
    }
}

struct NativeToolbarControl: View {
    var body: some View {
        Text("Browser")
            .toolbar(.hidden, for: .navigationBar)
            .persistentSystemOverlays(.hidden)
    }
}

struct NativeMenuControl: View {
    var body: some View {
        Color.clear.frame(width: 0, height: 0)
            .contextMenu { Button("Copy") {} } preview: { Text("Message") }
    }
}

struct NativeShapeControl: View {
    var body: some View {
        AnyShape(UnevenRoundedRectangle(bottomLeadingRadius: 8, bottomTrailingRadius: 8))
            .fill(Color.blue)
    }
}

struct NativeTextControl: View {
    var body: some View {
        TextField("Description", text: .constant(""), axis: .vertical)
            .lineLimit(1...4)
    }
}

@MainActor
func nativeWebKitControl() {
    let configuration = WKWebViewConfiguration()
    configuration.preferences.isElementFullscreenEnabled = true
    _ = UITextView(usingTextLayoutManager: true)
}

@MainActor
func nativeSpeechControl(url: URL) {
    let request = SFSpeechURLRecognitionRequest(url: url)
    request.addsPunctuation = true
}

func nativeTimerControl() async throws {
    try await Task.sleep(for: .milliseconds(200))
}

func nativeFileProviderControl() {
    let domain = NSFileProviderDomain(identifier: NSFileProviderDomainIdentifier("smoke"), displayName: "Smoke")
    NSFileProviderManager.remove(domain, mode: .removeAll) { _, _ in }
    UNUserNotificationCenter.current().setBadgeCount(0) { _ in }
}

struct NativeDecorationControl: View {
    var body: some View {
        Circle().fill(Color.orange.gradient).toolbar {
            ToolbarItem(placement: .secondaryAction) { Button("Copy") {} }
        }
    }
}

func nativeVideoFrameControl(generator: AVAssetImageGenerator) async throws {
    _ = try await generator.image(at: .zero)
}

struct NativeDragDropControl: View {
    var body: some View {
        Text("Sidebar")
            .draggable("session-a")
            .dropDestination(for: String.self,
                action: { values, _ in !values.isEmpty },
                isTargeted: { _ in }
            )
    }
}

func nativeSplitColumnControl() -> some View {
    Text("Sidebar").navigationSplitViewColumnWidth(min: 140, ideal: 160, max: 240)
}

func nativeImageRegexControl(_ text: String) {
    let pattern = try! Regex(#"!\[([^\]]*)\]\(([^)]+)\)"#)
    _ = text.ranges(of: pattern)
}

struct NativeOptionalToolbarControl: View {
    let dirty: Bool
    var body: some View {
        Text("Editor").toolbar {
            if dirty {
                ToolbarItem(placement: .navigationBarTrailing) { Button("Save") {} }
            }
        }
    }
}

struct NativeBranchToolbarControl: View {
    let multi: Bool
    var body: some View {
        Text("Models").toolbar {
            if multi {
                ToolbarItem(placement: .navigationBarLeading) { Button("Cancel") {} }
                ToolbarItem(placement: .navigationBarTrailing) { Button("Add") {} }
            } else {
                ToolbarItem(placement: .navigationBarTrailing) { Button("Done") {} }
            }
        }
    }
}
