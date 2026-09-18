import SwiftUI

// Type-check the production compatibility module, not a copied implementation.
// Mirrors a Form label/value row, conditional rows, server navigation, and the
// protected download sheet. This checks availability, not device rendering.
struct CompatibilityCalls: View {
    let deviceName: String
    let count: Int
    let cancelled: Bool

    var body: some View {
        CompatNavigationStack {
            Form {
                CompatLabeledContent("Created", value: "2026-09-18")
                CompatLabeledContent("From", value: "\(deviceName) · iOS 15")
                if count > 0 {
                    CompatLabeledContent("Updated", value: "\(count)")
                }
                NavigationLink {
                    Text("Backup package")
                } label: {
                    Text("Server")
                }
            }
            .compatHiddenScrollBackground()
        }
        .compatPresentationDetentHeight(240)
        .interactiveDismissDisabled(true)
        .disabled(cancelled)
    }
}

struct ExistingSheetAdapters: View {
    var body: some View {
        VStack {
            Text("Medium / large").compatPresentationDetentsMediumLarge()
            Text("Fraction").compatPresentationDetentsFraction75()
        }
    }
}
