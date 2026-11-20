#!/usr/bin/env python3
"""Source-only regression for the iOS 15 UIKit auto-compaction menu.

No UIKit execution, Swift compilation, device/defaults access or network.
Exit 0 means source assertions passed, NOT native or on-device validation.
Use --toolbar-source with `git show d592dc6:<toolbar path>` for a baseline
negative control. --mutants checks named in-memory source regressions.
"""
import argparse
import re
from pathlib import Path

TOOLBAR = "src/ios/Views/Chat/AIChatView/Toolbar/ChatToolbarHost.swift"
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/|[{}]')


def uncomment(text):
    return TOKENS.sub(lambda m: "" if m[0].startswith(("//", "/*")) else m[0], text)


def block(text, marker):
    start = text.find(marker)
    if start < 0:
        return ""
    opening = text.find("{", start + len(marker))
    if opening < 0:
        return ""
    depth = 0
    for match in TOKENS.finditer(text, opening):
        if match[0] == "{":
            depth += 1
        elif match[0] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1:match.start()]
    return ""


def compact(text):
    return re.sub(r"\s+", "", text)


def audit(text):
    source = uncomment(text)
    button = block(source, "struct ChatTrailingMenuButton:")
    key = block(button, "struct Key:")
    getter = block(button, "fileprivate var key:")
    coordinator = block(button, "final class Coordinator")
    toggle = compact(block(coordinator, "func toggleAutoCompaction("))
    refresh = compact(block(coordinator, "func refreshMenuIfNeeded("))
    build = block(button, "fileprivate static func buildMenu(")
    groups = re.findall(
        r"groups\.append\(UIMenu\(options:\s*\.displayInline,\s*children:\s*\[([\s\S]*?)\]\)\)", build)
    compaction = [g for g in groups if 'AppLocalized("Compact Messages")' in g]
    group = compaction[0] if len(compaction) == 1 else ""
    action_match = re.search(
        r'UIAction\(title:\s*AppLocalized\("Auto Compaction"\)[\s\S]*?\},', group)
    action = action_match[0] if action_match else ""
    button_update = compact(block(button, "func updateUIView("))
    installer = block(source, "struct ChatNavMenuBarItemInstaller:")
    bar_update = compact(block(installer, "func updateUIView("))
    snapshot = compact(block(source, "struct ChatToolbarKey:"))
    fields = set(re.findall(r"let\s+(\w+)\s*:", key))
    expected_fields = {
        "messagesEmpty", "hasSession", "isForcePulling", "iCloudSyncEnabled",
        "memoryEnabled", "speakEnabled", "showEnhancedCacheToggle",
        "enhancedCacheEnabled", "showFastModeToggle", "speedTier",
        "showUltrafast", "keepAwakeEnabled", "screenSaverNowEnabled",
        "autoCompactionEnabled",
    }
    titles = [group.find('AppLocalized("%s")' % t) for t in
              ("Compact Messages", "Auto Compaction", "Clear Chat")]
    return {
        "uikit.direct_compaction_group": (
            len(compaction) == 1 and -1 not in titles and titles == sorted(titles)
            and build.count('AppLocalized("Auto Compaction")') == 1
            and "attributes:" not in action
            and build.find(group) < build.find("if #available")),
        "uikit.checkmark_and_coordinator_action": (
            "state:key.autoCompactionEnabled?.on:.off" in compact(action)
            and "coordinator.toggleAutoCompaction()" in action),
        "key.live_preference_snapshot_no_stream_fields": (
            fields == expected_fields
            and "autoCompactionEnabled:AutoCompactionPreferences.enabled" in compact(getter)),
        "toolbar.equatable_preference_snapshot": (
            "letautoCompactionEnabled:Bool=AutoCompactionPreferences.enabled" in snapshot),
        "toggle.latest_coordinator_state_enabled_key_only": (
            "letenabled=!parent.key.autoCompactionEnabled" in toggle
            and "UserDefaults.standard.set(enabled,forKey:AutoCompactionPreferences.enabledKey)" in toggle
            and toggle.count("UserDefaults.standard.set(") == 1
            and not any(t in toggle for t in ("budget", "onCompact", "compactAll", "sheet"))),
        "toggle.immediate_checkmark_and_menu_refresh": (
            "refreshMenuIfNeeded()" in toggle
            and ".state=" not in toggle
            and "UserDefaults.standard.set(" in toggle
            and toggle.index("UserDefaults.standard.set(") < toggle.index("refreshMenuIfNeeded()")
            and not any(t in toggle for t in ("Task{", ".async", "DispatchQueue"))),
        "coordinator.guarded_refresh_both_anchors": (
            "letkey=parent.key" in refresh
            and "guardlastKey!=keyelse{return}" in refresh
            and "lastKey=key" in refresh
            and "ChatTrailingMenuButton.buildMenu(key:key,coordinator:self)" in refresh
            and "button?.menu=menu" in refresh and "barItem?.menu=menu" in refresh
            and refresh.index("guardlastKey") < refresh.index("buildMenu")
            and "context.coordinator.button = button" in button
            and "inner.barItem = item" in installer),
        "coordinator.external_preferences_weak_subscription": (
            "private var defaultsObserver: AnyCancellable?" in coordinator
            and "UserDefaults.didChangeNotification" in coordinator
            and ".receive(on: DispatchQueue.main)" in coordinator
            and ".sink { [weak self] _ in self?.refreshMenuIfNeeded() }" in coordinator
            and "weak var button: UIButton?" in coordinator
            and "weak var barItem: UIBarButtonItem?" in coordinator),
        "streaming.existing_update_guards_latest_closures": (
            "context.coordinator.parent=selfguardcontext.coordinator.lastKey!=keyelse{return}" in button_update
            and "coordinator.inner.parent=menuifcoordinator.inner.lastKey!=menu.key{" in bar_update
            and button_update.count("buildMenu(") == 1
            and bar_update.count("buildMenu(") == 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--toolbar-source", type=Path)
    parser.add_argument("--mutants", action="store_true")
    args = parser.parse_args()
    path = args.toolbar_source or args.source_root / TOOLBAR
    text = path.read_text(encoding="utf-8")
    checks = audit(text)
    for name, passed in checks.items():
        print("[source] %s %s" % ("PASS" if passed else "FAIL", name))
    passed = all(checks.values())
    if args.mutants and passed:
        mutants = [
            ("missing UIKit row", 'AppLocalized("Auto Compaction")', 'AppLocalized("Removed")',
             "uikit.direct_compaction_group", 1),
            ("stale captured toggle", "!parent.key.autoCompactionEnabled", "!key.autoCompactionEnabled",
             "toggle.latest_coordinator_state_enabled_key_only", 1),
            ("missing immediate refresh", "        refreshMenuIfNeeded()\n", "",
             "toggle.immediate_checkmark_and_menu_refresh", 1),
            ("unconditional refresh", "guard lastKey != key else { return }", "",
             "coordinator.guarded_refresh_both_anchors", 1),
        ]
        # The first label occurrence is in the unused SwiftUI menu: restrict
        # the row mutant to the UIKit struct so a SwiftUI-only hit cannot pass.
        split = text.index("struct ChatTrailingMenuButton:")
        for label, old, new, expected, count in mutants:
            prefix, tail = text[:split], text[split:]
            valid = tail.count(old) == count
            mutant = prefix + tail.replace(old, new, count)
            rejected = valid and not audit(mutant)[expected]
            print("[source-mutant] %s %s (%s)" %
                  ("REJECTED" if rejected else "FAIL", label, expected))
            passed = passed and rejected
    print("[native/UIKit/device] NOT RUN: source assertions only")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
