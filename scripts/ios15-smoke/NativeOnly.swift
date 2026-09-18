import SwiftUI
import UIKit
import WebKit

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
