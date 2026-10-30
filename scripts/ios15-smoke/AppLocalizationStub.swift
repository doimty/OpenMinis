import Foundation

// Smoke-only stand-in for Shared/AppLocalization.swift.
//
// The real helper resolves through AppBundle.current, which reads
// Bundle.languageBundle — an associated-object extension declared in
// MinisApp.swift, i.e. the app module. Compiling AppLocalization.swift here
// would drag in the app. The compat shims only need the call surface so their
// titles can be type-checked at iOS 15/16, so mirror the signature and resolve
// against Bundle.main (the smoke never runs, it only type-checks).
func AppLocalizedDynamic(_ key: String) -> String {
    String(localized: String.LocalizationValue(key), bundle: .main)
}
