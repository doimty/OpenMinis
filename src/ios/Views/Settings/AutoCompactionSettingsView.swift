import SwiftUI
import UIKit

/// Global automatic compaction and soft-budget settings.
///
/// The @AppStorage budget binding is used for writes and observation only:
/// integer-backed wrappers can coerce corrupt defaults values (for example a
/// malformed string) to 0, which has a real meaning here. Always render and
/// seed editors from AutoCompactionPreferences' validated reader instead.
struct AutoCompactionSettingsView: View {
    var contextWindow: Int = 0
    var isUserCap: Bool = false
    var showsDoneButton: Bool = false

    @AppStorage(AutoCompactionPreferences.enabledKey) private var automaticCompactionEnabled = false
    @AppStorage(AutoCompactionPreferences.budgetKey) private var budgetTokensStorage = AutoCompactionPreferences.defaultBudgetTokens
    @State private var presentsBudgetEditor = false
    @Environment(\.dismiss) private var dismiss

    private enum BudgetChoice: Hashable {
        case followModelAndGroup
        case preset(Int)
        case custom
    }

    /// Read the canonical validated value; never display the AppStorage
    /// wrapper's potentially-coerced representation of malformed data.
    private var budgetTokens: Int {
        AutoCompactionPreferences.budgetTokens
    }

    private var selectedBudgetChoice: BudgetChoice {
        switch budgetTokens {
        case 0:
            return .followModelAndGroup
        case 128_000, 256_000, 512_000:
            return .preset(budgetTokens)
        default:
            return .custom
        }
    }

    private var budgetChoiceBinding: Binding<BudgetChoice> {
        Binding(
            get: { selectedBudgetChoice },
            set: { choice in
                switch choice {
                case .followModelAndGroup:
                    budgetTokensStorage = 0
                case .preset(let tokens):
                    budgetTokensStorage = tokens
                case .custom:
                    presentsBudgetEditor = true
                }
            }
        )
    }

    /// A preview of the 85% line only where the soft budget / explicit group
    /// cap actually supplies the proportional policy. A higher soft budget
    /// cannot displace a lower native window's existing safety policy.
    private var triggerReferenceTokens: Int? {
        if contextWindow > 0 {
            let policy = ContextPolicy(contextWindow: contextWindow, isUserCap: isUserCap,
                autoCompactBudgetTokens: AutoCompactionPreferences.activeBudgetTokens)
            return policy.compactThreshold > 0 ? policy.compactThreshold : nil
        }
        return automaticCompactionEnabled && budgetTokens > 0
            ? Int(Double(budgetTokens) * 0.85) : nil
    }

    var body: some View {
        Form {
            Section {
                Toggle(AppLocalized("Enable automatic compaction"), isOn: $automaticCompactionEnabled)
            } footer: {
                Text(AppLocalized("Automatic compaction is a global preference and can be turned off at any time."))
            }

            Section {
                Picker(selection: budgetChoiceBinding) {
                    Text(AppLocalized("Follow model/group limit"))
                        .tag(BudgetChoice.followModelAndGroup)
                    Text(verbatim: budgetOptionLabel(128_000))
                        .tag(BudgetChoice.preset(128_000))
                    Text(verbatim: budgetOptionLabel(256_000))
                        .tag(BudgetChoice.preset(256_000))
                    Text(verbatim: budgetOptionLabel(512_000))
                        .tag(BudgetChoice.preset(512_000))
                    Text(AppLocalized("Custom"))
                        .tag(BudgetChoice.custom)
                } label: {
                    Text(AppLocalized("Budget"))
                }

                if selectedBudgetChoice == .custom {
                    Button(AppLocalized("Edit custom budget")) { presentsBudgetEditor = true }
                    HStack {
                        Text(AppLocalized("Saved custom budget"))
                        Spacer()
                        Text(verbatim: budgetOptionLabel(budgetTokens))
                            .foregroundColor(.secondary)
                    }
                }

                if contextWindow > 0 {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(AppLocalized("Current effective context window"))
                            Text(AppLocalized(isUserCap ? "Limited by the current model-group cap" : "Resolved from the current model"))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Spacer(minLength: 12)
                        Text(verbatim: budgetOptionLabel(contextWindow))
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.trailing)
                    }
                }
            } header: {
                Text(AppLocalized("Budget"))
            } footer: {
                VStack(alignment: .leading, spacing: 6) {
                    if let triggerReferenceTokens {
                        HStack(spacing: 4) {
                            Text(AppLocalized("Compaction trigger (current model or budget reference)"))
                            Text(verbatim: budgetOptionLabel(triggerReferenceTokens))
                        }
                    } else if budgetTokens == 0 {
                        Text(AppLocalized("A zero budget follows the resolved model/group window; the existing model-window safety policy remains in effect."))
                    } else if contextWindow > 0 {
                        Text(AppLocalized("This budget does not lower the current model window, so its existing safety policy remains in effect."))
                    }
                    if !automaticCompactionEnabled {
                        Text(AppLocalized("Automatic compaction is off; the saved budget applies when enabled."))
                    }
                    Text(AppLocalized("A lower model/group context cap always takes precedence; this budget never changes or increases the model context window."))
                }
            }

            Section {
                Text(AppLocalized("This budget is a soft target. Fixed prompts and the current turn may keep a compacted request above it."))
                Text(AppLocalized("Turning automatic compaction off does not disable the existing near-window safety protection."))
                Text(AppLocalized("Compaction keeps a summary of history, which may lose details."))
            } header: {
                Text(AppLocalized("How it works"))
            }
        }
        .navigationTitle(AppLocalized("Auto-Compact"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if showsDoneButton {
                    Button(AppLocalized("Done")) { dismiss() }
                }
            }
        }
        .sheet(isPresented: $presentsBudgetEditor) {
            let initialBudget = budgetTokens > 0 ? budgetTokens : AutoCompactionPreferences.defaultBudgetTokens
            AutoCompactionBudgetEditor(initialBudget: initialBudget) { newBudget in
                budgetTokensStorage = newBudget
            }
        }
    }

    private func budgetOptionLabel(_ tokens: Int) -> String {
        "\(NumberFormatter.localizedString(from: NSNumber(value: tokens), number: .decimal)) \(AppLocalized("Tokens"))"
    }
}

/// Staged custom-budget editor. Only Apply writes the shared UserDefaults key;
/// dismissing with Cancel leaves the existing budget untouched.
private struct AutoCompactionBudgetEditor: View {
    let initialBudget: Int
    let onApply: (Int) -> Void

    @State private var budgetDraft: String
    @Environment(\.dismiss) private var dismiss

    init(initialBudget: Int, onApply: @escaping (Int) -> Void) {
        self.initialBudget = initialBudget
        self.onApply = onApply
        _budgetDraft = State(initialValue: String(initialBudget))
    }

    private var parsedBudget: Int? {
        guard !budgetDraft.isEmpty,
              budgetDraft.utf8.allSatisfy({ (48...57).contains($0) }),
              let value = Int(budgetDraft),
              (AutoCompactionPreferences.minimumBudgetTokens...AutoCompactionPreferences.maximumBudgetTokens).contains(value)
        else { return nil }
        return value
    }

    var body: some View {
        CompatNavigationStack {
            Form {
                Section {
                    TextField(AppLocalized("Budget in tokens"), text: $budgetDraft)
                        .keyboardType(.numberPad)
                        .accessibilityIdentifier("autoCompactBudgetTokens")

                    if parsedBudget == nil {
                        Text(AppLocalized("Enter an ASCII whole number from 32,000 to 4,000,000."))
                            .font(.footnote)
                            .foregroundColor(.red)
                    }
                } footer: {
                    Text(AppLocalized("Custom values are saved only when you tap Apply. Cancel keeps the current budget."))
                }
            }
            .navigationTitle(AppLocalized("Custom Budget"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button(AppLocalized("Cancel")) { dismiss() }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(AppLocalized("Apply")) {
                        guard let parsedBudget else { return }
                        onApply(parsedBudget)
                        dismiss()
                    }
                    .disabled(parsedBudget == nil)
                }
            }
        }
    }
}
