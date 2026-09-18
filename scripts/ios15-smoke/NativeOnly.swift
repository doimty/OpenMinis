import SwiftUI

// Negative control: these are the three real API shapes rejected by run
// 35243806282. This fixture must compile at 16 and fail availability at 15.
struct NativeLabelControl: View {
    var body: some View {
        LabeledContent("Created", value: "2026-09-18")
    }
}

struct NativeNavigationControl: View {
    var body: some View {
        NavigationStack { Text("Restore from Server") }
    }
}

struct NativeSheetControl: View {
    var body: some View {
        Text("Downloading Backup")
            .presentationDetents([.height(240)])
            .interactiveDismissDisabled(true)
    }
}
