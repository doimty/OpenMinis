#!/usr/bin/env python3
"""Narrow structural guards, not a substitute for the Apple-compiler smoke."""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPAT = ROOT / "src/ios/Shared/SwiftUICompatibility.swift"


class CompatibilityContractTests(unittest.TestCase):
    def test_range_line_limit_adapter_has_one_owner(self):
        source = COMPAT.read_text(encoding="utf-8")
        definitions = re.findall(r"\bfunc\s+compatLineLimit\s*\(", source)
        self.assertEqual(len(definitions), 1, "duplicate Swift declarations are not syntax-tree errors")

    def test_visibility_signature_does_not_leak_newer_sdk_type(self):
        source = COMPAT.read_text(encoding="utf-8")
        signatures = re.findall(r"func\s+compat\w+[^\{]+\{", source)
        self.assertFalse(any("ToolbarPlacement" in signature for signature in signatures),
                         "a body availability guard does not protect the signature")

    def test_legacy_vertical_editor_is_not_a_single_line_text_field(self):
        source = COMPAT.read_text(encoding="utf-8")
        self.assertIn("TextEditor(text:", source)
        self.assertNotIn("func compatTextFieldAxis", source,
                         "retire the helper that silently drops the vertical axis")

    def test_chat_and_voice_timers_do_not_require_duration_clock(self):
        hits = []
        for directory in ("Providers/Voice", "Views/Chat"):
            for path in (ROOT / "src/ios" / directory).rglob("*.swift"):
                if re.search(r"Task\s*\.\s*sleep\s*\(\s*(?:for|until)\s*:", path.read_text(encoding="utf-8")):
                    hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], "use the project's iOS-15-safe nanosecond timers")

    def test_millisecond_conversion_keeps_intervals_and_cancellation_guards(self):
        cases = (
            ("Providers/Voice/VoiceProviderResolver.swift", "200_000_000", 1),
            ("Views/Chat/Voice/SpeechPlayerControl.swift", "250_000_000", 1),
            ("Views/Chat/Voice/SpeechPlayerControl.swift", "UInt64(Self.descentSettleMillis) * 1_000_000", 1),
            ("Views/Chat/Voice/SpeechPlayerControl.swift", "150_000_000", 1),
            ("Views/Chat/AIChatView/AIChatView.swift", "900_000_000", 1),
            ("Views/Chat/AIChatView/AIChatView+InputBar.swift", "380_000_000", 2),
            ("Views/Chat/AIChatView/AIChatView+InputBar.swift", "320_000_000", 1),
        )
        for relative, duration, expected in cases:
            with self.subTest(file=relative, duration=duration):
                source = (ROOT / "src/ios" / relative).read_text(encoding="utf-8")
                call = f"try? await Task.sleep(nanoseconds: {duration})"
                guarded = re.escape(call) + r"\s*guard !Task\.isCancelled"
                self.assertEqual(len(re.findall(guarded, source)), expected)
        player = (ROOT / "src/ios/Views/Chat/Voice/SpeechPlayerControl.swift").read_text(encoding="utf-8")
        self.assertRegex(player, r"descentSettleMillis\s*=\s*1200\b")

    def test_automatic_punctuation_is_optional_on_ios15(self):
        source = (ROOT / "src/ios/Providers/Voice/VoiceProvider+System.swift").read_text(encoding="utf-8")
        self.assertRegex(source, r"if #available\(iOS 16\.0, \*\) \{\s*"
                                 r"recognitionRequest\.addsPunctuation = true\s*\}")
        self.assertIn("recognitionRequest.taskHint = .dictation", source)
        self.assertIn("recognizer.recognitionTask(with: recognitionRequest)", source)

    def test_file_provider_guard_preserves_core_initialization(self):
        source = (ROOT / "src/ios/MinisApp.swift").read_text(encoding="utf-8")
        body = source.split("private static func registerFileProviderDomain() {", 1)[1]
        body = body.split("private static func signalFileProvider()", 1)[0]
        guard = "guard #available(iOS 16.0, *) else { return }"
        self.assertTrue(guard in body, "legacy startup must stop before replicated-domain work")
        boundary = body.index(guard)
        for setup in ("fm.createDirectory", "SoulStore.ensureExists()", "SoulStore.refreshCache()"):
            self.assertLess(body.index(setup), boundary)
        self.assertLess(boundary, body.index("let staleDir"))
        self.assertLess(boundary, body.index("NSFileProviderManager.getDomainsWithCompletionHandler"))
        self.assertNotIn("#if #available", source)
        self.assertTrue(re.search(r"@available\(iOS 16\.0, \*\)\s*private static let fileProviderDomain", source) is not None)
        signal = source.split("private static func signalFileProvider() {", 1)[1]
        self.assertTrue(signal.lstrip().startswith(guard))

    def test_file_provider_watcher_does_not_start_below_ios16(self):
        source = (ROOT / "src/ios/FileProvider/AppGroupChangeWatcher.swift").read_text(encoding="utf-8")
        body = source.split("func start() {", 1)[1]
        guard = "guard #available(iOS 16.0, *) else { return }"
        self.assertTrue(body.lstrip().startswith(guard))
        self.assertLess(body.index(guard), body.index("started = true"))

    def test_badge_fallback_preserves_enabled_policy_and_main_actor(self):
        source = (ROOT / "src/ios/Agent/Background/BackgroundKeepAliveManager.swift").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"@MainActor\s*final class BackgroundKeepAliveManager", source) is not None)
        body = source.split("private func refreshActiveTaskBadge(sessions: Set<String>, enabled: Bool) {", 1)[1]
        body = body.split("// MARK: - Background Task Notifications", 1)[0]
        self.assertTrue(body.lstrip().startswith("guard #available(iOS 16.0, *) else {"))
        self.assertIn("UIApplication.shared.applicationIconBadgeNumber = total", body)
        # 1.15 recomputes from one ledger, so the total (not the raw running count) is set.
        self.assertIn(".setBadgeCount(total)", body)
        # 1.15 dropped the launch-time badge reset from MinisApp: the single
        # ledger in BackgroundKeepAliveManager owns the icon now, so assert the
        # 16+ API is confined there instead of at startup.
        app = (ROOT / "src/ios/MinisApp.swift").read_text(encoding="utf-8")
        self.assertNotIn("setBadgeCount", app)

    def test_markdown_rendering_uses_legacy_safe_media_and_image_scan(self):
        source = (ROOT / "src/ios/Views/Chat/Markdown/Bridge/SelectableMarkdownView.swift").read_text(encoding="utf-8")
        # 1.15 moved the video thumbnail work into its own attachment file.
        source += (ROOT / "src/ios/Views/Chat/Markdown/Attachments/VideoAttachment.swift").read_text(encoding="utf-8")
        self.assertNotIn("markdown.ranges(of:", source)
        self.assertNotIn("try await generator.image(at:", source)
        self.assertIn("MarkdownStripper.imageSyntaxMatches(in: markdown)", source)
        self.assertIn("ThumbnailCache.videoFrame(using: generator, at: .zero)", source)
        self.assertIn("generator.appliesPreferredTrackTransform = true", source)
        self.assertIn("generator.maximumSize = CGSize(width: 400, height: 400)", source)

    def test_sidebar_drag_drop_retires_new_api_calls_without_replacing_actions(self):
        # Upstream 1.15 split ContentView into a component directory; assert over the whole tree.
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "src/ios/Views/ContentView").rglob("*.swift"))
        )
        self.assertNotIn(".draggable(session.id)", source)
        self.assertNotIn(".dropDestination(for: String.self)", source)
        self.assertNotIn(".navigationSplitViewColumnWidth(", source)
        self.assertEqual(source.count(".compatDraggable(session.id)"), 2)
        self.assertEqual(source.count(".compatDropDestination(for: String.self"), 2)
        self.assertIn("await ChatStore.shared.setFolder(nil, forSessions: sessionIds)", source)
        self.assertIn("await ChatStore.shared.setFolder(fid, forSessions: sessionIds)", source)

    def test_os_unfair_lock_is_not_a_swift_stored_var(self):
        # iPhone14,3 / iOS 15.1.1 crash 2026-09-19 10:37: PAC in swift_beginAccess
        # from CrashReporter.appendLog. `&storedLock` is exclusive access on self.
        stored = re.compile(r"\bvar\s+\w+\s*=\s*os_unfair_lock(?:_s)?\s*\(")
        hits = []
        for path in (ROOT / "src/ios").rglob("*.swift"):
            if "MinisTests" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if stored.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{number}:{line.strip()}")
        self.assertEqual(hits, [], "use NSLock or a pointer-backed lock, not a stored os_unfair_lock")

    def test_ios15_legacy_hosting_skips_sync_swiftui_measure(self):
        source = (ROOT / "src/ios/Agent/MessageList/MessageListInfrastructure.swift").read_text(encoding="utf-8")
        branch = source.index("if contentConfiguration is LegacyHostingConfiguration")
        super_measure = source.index("var superAttrs", branch)
        self.assertLess(branch, super_measure)
        self.assertIn("legacyMeasuredSize", source[branch:super_measure])
        self.assertIn("return layoutAttributes", source[branch:super_measure])

        legacy = (ROOT / "src/ios/Shared/LegacyHostingContent.swift").read_text(encoding="utf-8")
        self.assertIn("let onSizeChange: (CGSize) -> Void", legacy)
        self.assertIn("private var pendingSize: CGSize?", legacy)
        self.assertIn("let latest = self.pendingSize ?? size", legacy)
        self.assertIn("self.current.onSizeChange(latest)", legacy)
        self.assertIn("configurationGeneration", legacy)
        self.assertNotIn("self.current.onSizeChange(size)", legacy)
        self.assertNotIn("host.view.bottomAnchor.constraint", legacy,
                         "pin the hosting view top/leading/trailing only so its height is intrinsic")
        # The legacy content view must be reachable from the cell (not private)
        # and expose a hit-region helper that SelfSizingCell consults.
        self.assertIn("final class LegacyHostingContentView", legacy)
        self.assertNotIn("private final class LegacyHostingContentView", legacy)
        self.assertIn("func hitRegionContains(_ point: CGPoint) -> Bool", legacy)
        self.assertIn("return host.view.bounds.contains(hostPoint)", legacy)

    def test_ios15_legacy_hosting_forgets_size_on_reconfigure_and_reuse(self):
        source = (ROOT / "src/ios/Agent/MessageList/MessageListInfrastructure.swift").read_text(encoding="utf-8")
        configure = source.split("func applyContentConfiguration(_ config: any UIContentConfiguration) {", 1)[1]
        configure = configure.split("func applyHostedContent", 1)[0]
        reuse = source.split("override func prepareForReuse() {", 1)[1]
        reuse = reuse.split("// MARK: - NoAnimationCollectionView", 1)[0]
        self.assertIn("legacyMeasuredSize = nil", configure)
        self.assertIn("legacyMeasuredSize = nil", reuse)
        self.assertIn("self.configGeneration == generation", source)

    def test_ios15_self_sizing_cell_extends_hit_region_on_legacy_path(self):
        # The 762a178 content-view-only hitTest forwarding could not fix the
        # footer Retry/Resume capsules on device: UIKit rejects touches at the
        # RECEIVER's edge before consulting subviews, and the receiver is the
        # CELL, not the content view. SelfSizingCell must therefore extend its
        # hit region to the hosting view's real bounds on the legacy path,
        # while leaving iOS 16+'s UIHostingConfiguration untouched.
        source = (ROOT / "src/ios/Agent/MessageList/MessageListInfrastructure.swift").read_text(encoding="utf-8")
        self.assertIn("override func point(inside point: CGPoint, with event: UIEvent?) -> Bool",
                      source)
        self.assertIn("contentConfiguration is LegacyHostingConfiguration", source)
        self.assertIn("contentView as? LegacyHostingContentView", source)
        self.assertIn("hitRegionContains(convert(point, to: legacy))", source)
        self.assertIn("override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView?", source)
        self.assertIn("guard hit === self else { return hit }", source,
                      "super.hitTest returns the cell itself for overflow points; the fallback must only run when super fell back to self")
        self.assertIn("legacyHostingContentView", source)
        # The guard must return false for the native path so iOS 16+ keeps the
        # exact cell-bounds hit region.
        guard = source.index("contentConfiguration is LegacyHostingConfiguration")
        after_guard = source[guard:guard + 600]
        self.assertIn("return false", after_guard)
        self.assertNotIn("hitRegionContains", after_guard.split("return false")[0])

    def test_ios15_session_row_is_tappable_on_legacy_os(self):
        # Upstream 1.15 replaced the hidden NavigationLink (which needed the
        # compatLegacyNavigationTap shim on iOS 15) with a plain Button that
        # sends a menu action. A Button is available on 15, so the invariant is
        # now "the row opens its session through the action channel" rather
        # than "the row carries the shim".
        rows = (ROOT / "src/ios/Views/ContentView/Sidebar/SessionListRows.swift").read_text(encoding="utf-8")
        self.assertIn("menuActions.send(.open(sid))", rows)
        self.assertNotIn(".compatLegacyNavigationTap {", rows)
        channels = (ROOT / "src/ios/Views/ContentView/Sidebar/SidebarActionChannels.swift").read_text(encoding="utf-8")
        self.assertIn("case open(String)", channels)

    def test_ios15_markdown_measure_width_is_guarded(self):
        # The legacy iOS 15 path measured at NSTextContainer's default
        # 10_000_000pt sentinel when bounds.width <= 1, poisoning the height
        # cache and handing CoreAnimation an undrawable layer (device log
        # 2026-09-19: measureW=10000000, 146x "Ignoring bogus layer size").
        source = (ROOT / "src/ios/Views/Chat/Markdown/TextKit/SelectableMarkdownTextView+CellSizing.swift").read_text(encoding="utf-8")
        self.assertIn("T-ios15-measure-width-guard", source)
        self.assertIn("let maxSaneW = max(fallbackW, UIScreen.main.bounds.width)", source)
        self.assertIn("no real width yet; deferring to GeometryReader/next pass", source)

    def test_ios15_fallback_intrinsic_width_does_not_demand_unbounded_width(self):
        # The legacy iOS 15 bridge has no representable sizeThatFits, so
        # SwiftUI asks the view's intrinsicContentSize with an unbounded
        # width proposal. Without this override a non-scrolling
        # NSTextContainer/UITTextView reports its ideal width (653pt for long
        # text, 1e7pt once a width-filling attachment is present) and the
        # default horizontal compression resistance lets that demand win over
        # the host's 396pt proposal — the live bounds grow to 1e7, CoreAnimation
        # refuses the 10M-wide layer, and the chat layout clips and floods
        # (device log 2026-09-19: 144 bogus layers at measureW=10000000;
        # reproduced in the pinned iOS 26.2 simulator matrix in
        # docs/ios15-markdown-width-investigation.md). The override must be
        # plain UIKit so it also works on the iOS 15 runtime.
        source = (ROOT / "src/ios/Views/Chat/Markdown/TextKit/SelectableMarkdownTextView.swift").read_text(encoding="utf-8")
        self.assertIn("T-ios15-fallback-intrinsic-width", source)
        self.assertIn("override var intrinsicContentSize: CGSize", source)
        self.assertIn("noIntrinsicMetric", source)
        self.assertIn("bounds.width.isFinite", source)
        self.assertIn("sizeThatFits(CGSize(width: bounds.width", source)

    def test_sheet_editors_keep_immediate_keyboard_dismissal(self):
        compat = COMPAT.read_text(encoding="utf-8")
        helper = compat.split("func compatScrollDismissesKeyboardImmediately()", 1)[1]
        helper = helper.split("\n    }", 1)[0]
        self.assertIn("if #available(iOS 16.0, *)", helper)
        self.assertIn("self.scrollDismissesKeyboard(.immediately)", helper)
        self.assertNotIn(".scrollDismissesKeyboard(.interactively)", compat)
        for name in ("ProviderInstanceDetailView.swift", "ModelGroupDetailView.swift"):
            with self.subTest(file=name):
                view = (ROOT / "src/ios/Views/Providers" / name).read_text(encoding="utf-8")
                self.assertIn(".compatScrollDismissesKeyboardImmediately()", view)
                self.assertNotIn(".compatScrollDismissesKeyboardInteractively()", view)

    def test_compat_shims_localize_string_titles(self):
        """A String-taking shim must not render its title with bare verbatim.

        A string literal binds to the `StringProtocol` overload rather than to
        the `LocalizedStringKey` one, so `Text(verbatim: String(title))` showed
        the raw English key in a Chinese UI even though the catalog had the
        entry (T-compat-verbatim-drops-localization).
        """
        compat = COMPAT.read_text(encoding="utf-8")
        self.assertNotIn("Text(verbatim: String(title))", compat)
        self.assertIn("AppLocalizedDynamic(String(title))", compat)
        self.assertEqual(compat.count("AppLocalizedDynamic(String(title))"), 3)
        localization = (ROOT / "src/ios/Shared/AppLocalization.swift").read_text(encoding="utf-8")
        helper = localization.split("func AppLocalizedDynamic(", 1)[1].split("\n}", 1)[0]
        self.assertIn("LocalizedStringCache.lookup", helper)
        self.assertIn("String(localized: String.LocalizationValue(key)", helper)

    def test_chat_toolbar_condition_and_popup_indicators_are_ios15_safe(self):
        host = (ROOT / "src/ios/Views/Chat/AIChatView/Toolbar/ChatToolbarHost.swift").read_text(encoding="utf-8")
        toolbar = host.split("private var toolbarHost: some View {", 1)[1]
        toolbar = toolbar.split("private var toolbarAnchor: some View {", 1)[0]
        shown, hidden = toolbar.split("} else {", 1)
        self.assertIn("if showsTrailing {", shown)
        self.assertIn("ToolbarItem(placement: .topBarTrailing) { trailing() }", shown)
        self.assertNotIn(".topBarTrailing", hidden)
        popups = (ROOT / "src/ios/Views/Chat/AIChatView/AIChatView+InputPopups.swift").read_text(encoding="utf-8")
        self.assertEqual(popups.count(".compatVisibleScrollIndicators()"), 2)
        self.assertNotIn(".scrollIndicators(.visible)", popups)

    def test_settings_navigation_uses_compat_stack_and_sectioned_root(self):
        source = (ROOT / "src/ios/Views/Settings/SettingsSheet.swift").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"CompatPathNavigationStack\(path: \$navPath\)\s*\{\s*settingsRoot\s*\}"
            r"\s*destination:\s*\{\s*dest in\s*settingsDestination\(for: dest\)\s*\}",
            "the destination belongs to the navigation stack, not its List",
        )
        self.assertIn("@State private var navPath: [SettingsDestination] = []", source)
        root = source.split("private var settingsRoot: some View {", 1)[1]
        root = root.split("private var providerSection: some View {", 1)[0]
        sections = ("providerSection", "appearanceSection", "agentRuntimeSection",
                    "storageSection", "permissionsSection", "logsSection", "aboutSection")
        positions = [root.index(section) for section in sections]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('.navigationTitle("Settings")', root)
        self.assertIn(".onAppear { handleSettingsAppearance() }", root)
        self.assertIn(".onChange(of: deepLink.pendingSettingsTarget)", root)
        appearance = source.split("private func handleSettingsAppearance() {", 1)[1]
        appearance = appearance.split("private func settingsDestination", 1)[0]
        for value in ("applyPendingDeepLink()", "deepLink.showEnvironmentVariables",
                      "deepLink.showPermissions", '"pendingSettingsReopen"'):
            self.assertIn(value, appearance)

    def test_ios15_chat_geometry_detector_red_and_green(self):
        detector = ROOT / "scripts/check_ios15_chat_geometry.py"
        self.assertTrue(detector.exists(), "geometry replay detector must exist")
        red = """\
[13:42:23] [WatchdogProbe] [cv-bounds] WH old=0x0 new=428x801 visibleCells=0
[13:42:23] [TextContainerGuard] [WARN] short-circuited setSize: size=10000000.0x10000000.0 tick=8738
[13:42:23] [CellSize] [INFO] [invalidateCell] FIRST-MEASURE CORRECTION cellH=1167.0 newH=1020.0
[13:42:23] -[<CALayer> display]: Ignoring bogus layer size (10000000.000000, 1020.000000), contentsScale 3.0
[13:42:23] [CellSize] [INFO] [invalidateCell][SKIP-DEDUPE] storageLen=738 measureW=10000000 tcW=10000000 lastH=1020.0 tableGen=0
"""
        green = """\
[13:42:34] [WatchdogProbe] [cv-bounds] WH old=0x0 new=428x801 visibleCells=0
[13:42:34] [CellSize] [INFO] [invalidateCell][SKIP-DEDUPE] storageLen=2 measureW=396 tcW=396 lastH=28.0 tableGen=0
"""
        with tempfile.TemporaryDirectory() as tmp:
            red_path = Path(tmp) / "red.log"
            green_path = Path(tmp) / "green.log"
            red_path.write_text(red)
            green_path.write_text(green)
            red_run = subprocess.run([sys.executable, str(detector), str(red_path)],
                                     capture_output=True, text=True)
            green_run = subprocess.run([sys.executable, str(detector), str(green_path)],
                                       capture_output=True, text=True)
        self.assertEqual(red_run.returncode, 1,
                         "red fixture must fail: " + red_run.stdout[-400:])
        self.assertEqual(green_run.returncode, 0,
                         "green fixture must pass: " + green_run.stdout[-400:])


if __name__ == "__main__":
    unittest.main()
