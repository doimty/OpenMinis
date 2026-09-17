//
//  AppLocalization.swift
//  MinisApp
//
//  [T-ios-inapp-language-string-localized] Makes the in-app language picker
//  actually apply to every localized string, not just some of them.
//
//  The problem
//  -----------
//  `Bundle.enableLanguageOverride()` (MinisApp.swift) swizzles
//  `Bundle.localizedString(forKey:value:table:)` so the in-app language choice
//  is honoured without an app restart. That works for `Text("…")` and UIKit,
//  which route through that ObjC method — but `String(localized:)` does NOT.
//
//  Measured, not assumed. With the swizzle installed:
//
//      NSLocalizedString("k")   -> Bundle.localizedString hits: 1
//      String(localized: "k")   -> Bundle.localizedString hits: 0
//
//  Three interception strategies were tried against a real bundle and all
//  three failed to redirect `String(localized:)`:
//    1. method swizzling `localizedString(forKey:value:table:)`   — bypassed
//    2. `object_setClass` onto a Bundle subclass overriding it    — bypassed
//    3. overriding `preferredLocalizations` / `localizations`     — bypassed
//  `String(localized:)` resolves the table natively inside Foundation and
//  never calls back into the ObjC entry point, so nothing installed on
//  `Bundle.main` can steer it. Setting `AppleLanguages` does work, but only
//  from the NEXT process launch — verified: `preferredLocalizations` does not
//  change within the running process.
//
//  What DOES work is the one documented seam: `String(localized:bundle:)`
//  honours an explicit bundle. Verified against `es.lproj` / `de.lproj` /
//  `en.lproj` — each returns that language's value.
//
//  The fix
//  -------
//  Route every call through `AppBundle.current`, which is the overridden
//  `.lproj` bundle when the user has picked a language in-app, and
//  `Bundle.main` otherwise. This is deliberately generic: it is keyed off
//  whatever language is selected, so de / fr / ja / ko / ru / zh-Hans /
//  zh-Hant / es all behave the same. Nothing here is Spanish-specific.
//

import Foundation
import SwiftUI

/// The bundle localized lookups should read from.
///
/// Mirrors `Bundle.main.languageBundle` (set by `Bundle.setLanguage(_:)`), so
/// the in-app picker and this helper can never disagree about which language is
/// active. Falls back to `Bundle.main` when no in-app override is set, which is
/// the normal case — the system language then applies exactly as before.
enum AppBundle {
    static var current: Bundle {
        Bundle.main.languageBundle ?? Bundle.main
    }
}

/// Localized string that follows the in-app language override.
///
/// Drop-in replacement for `String(localized:)`. Prefer this over
/// `String(localized:)` anywhere the result is shown to the user: the bare
/// form silently ignores the in-app language setting (see the file comment),
/// which is what produced mixed-language UI for users whose system language
/// differs from their in-app choice.
///
/// `Text("…")` does not need this — SwiftUI's `Text` routes through
/// `NSLocalizedString`, which the existing swizzle already covers.
///
/// - Parameters:
///   - key: the localization key, i.e. the English source string.
///   - comment: translator context, kept so `genstrings`-style extraction and
///     the String Catalog continue to see it.
func AppLocalized(_ key: String, comment: StaticString? = nil) -> String {
    // Bundle.localizedString honours the in-app language swizzle and is
    // available on iOS 15. Do not take String.LocalizationValue /
    // LocalizedStringResource here: interpolated literals pick the iOS 16
    // resource type and fail availability checks.
    _ = comment
    return AppBundle.current.localizedString(forKey: key, value: key, table: nil)
}

/// `LocalizedStringResource` overload, for App Intents call sites.
@available(iOS 16.0, *)
func AppLocalized(_ resource: LocalizedStringResource) -> String {
    String(localized: resource)
}

/// Navigation stack that keeps the iOS 16+ API on 16+ and uses NavigationView
/// on iOS 15. Do not globally shadow `NavigationStack`.
struct CompatNavigationStack<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        if #available(iOS 16.0, *) {
            NavigationStack { content }
        } else {
            NavigationView { content }
                .navigationViewStyle(StackNavigationViewStyle())
        }
    }
}

extension View {
    @ViewBuilder
    func compatHiddenScrollBackground() -> some View {
        if #available(iOS 16.0, *) {
            self.scrollContentBackground(.hidden)
        } else {
            self
        }
    }

    @ViewBuilder
    func compatPresentationDetentsMediumLarge() -> some View {
        if #available(iOS 16.0, *) {
            self.presentationDetents([.medium, .large])
        } else {
            self
        }
    }

    @ViewBuilder
    func compatPresentationDetentsFraction75() -> some View {
        if #available(iOS 16.0, *) {
            self.presentationDetents([.fraction(0.75)])
        } else {
            self
        }
    }
}
