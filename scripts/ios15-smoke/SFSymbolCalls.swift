import SwiftUI
import UIKit

// Compile actual native overloads on both deployment targets. Labels keep
// literal keys vs already-localized String values; UIImage keeps its config.
struct SFSymbolCalls: View {
    let dynamicTitle: String
    let dynamicSymbol: String

    var body: some View {
        VStack {
            Image(systemName: CompatSystemSymbol.name(dynamicSymbol))
                .symbolRenderingMode(.hierarchical)
                .font(.title)
            Label("Literal localization key", systemImage: CompatSystemSymbol.name("mic.and.signal.meter"))
            Label(dynamicTitle, systemImage: CompatSystemSymbol.name(dynamicSymbol))
            Label(dynamicTitle.prefix(3), systemImage: CompatSystemSymbol.name(dynamicSymbol))
        }
    }

    func configuredUIKitImage() -> UIImage? {
        UIImage(systemName: CompatSystemSymbol.name(dynamicSymbol),
                withConfiguration: UIImage.SymbolConfiguration(pointSize: 16, weight: .semibold))
    }
}
