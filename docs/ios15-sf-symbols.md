# iOS 15 SF Symbols compatibility

## Baseline and scope

- Base commit: `f56d0ff4641f4c5e3efa844d936f4d679d1f6afe`, delivered by run `35429647024` (IPA SHA256 `666ecbe6c56c1fed65eb42f32ec645e84607a07d441f3d78d5c19ff0054619c2`). Remote and local branch were compared before changes.
- Work branch: `fix/ios15-sf-symbols`. User reports many missing SF Symbols on iOS 15, including Add Provider's voice templates. This is separate from markdown geometry; that earlier device acceptance remains pending.
- Source of truth: `VoiceProviderTemplate.swift`, `AddProviderView.swift`, native `Image`/`Label`/`UIImage` render calls, and the Xcode project / compile workflow. The older graph was queried for navigation only.
- Do not change provider identifiers, saved icon names, localization keys, actions, layout, markdown sizing, or modern-system artwork. No swizzling, bitmap replacement, new app dependencies, or broad replacement of already-safe literals.

## Feedback loop and evidence

- Offline availability metadata: SFSafeSymbols generated source at `cb2e670a213ff42ae08528ee2c401bfb1d799675`, MIT. Its introduction catalog contains 7,752 names. This is a secondary machine-readable source, **not an iOS 15 runtime**. Runtime `UIImage(systemName:)` remains authoritative.
- Specific deterministic failure: `mic.and.signal.meter` (iOS 16) is used by four voice templates and passed directly to `Image(systemName: template.symbol)`. No fallback exists. Other examples: `waveform.badge.mic` (17), `arrow.trianglehead.2.counterclockwise` (18), `key.circle.fill` (26).
- Red command already executed: `/opt/graphify/venv/bin/python3 scripts/audit_ios15_sf_symbols.py --plan <evidence>/edits.json` => **exit 1, 78 unprotected late/dynamic render calls in 43 files**. This is a coverage gate, not 78 proven blank glyphs: most are dynamic boundaries. Known old literal/ternary icons are exempt.
- The existing device log has no captured missing-symbol warnings. The screenshot/user report and versioned symbol metadata establish the issue; lack of an OS warning is not absence of a bug.

## Ranked causes and predictions

1. Names introduced after the deployment floor: resolving only unsupported names should restore glyphs while preserving originals on newer OS releases. The voice-template case above confirms this mismatch statically.
2. Dynamic names bypassing a literal-only audit: protecting the renderer, rather than mutating templates, must cover template/config/model/file icon strings.
3. Unknown names/typos: an explicit question-mark fallback must render visibly, rather than guessing a semantic mapping or returning an empty image.

## Design decision and success criteria

- One `CompatSystemSymbol` owns semantic fallbacks and actual runtime name availability. Keep a pure resolver seam for offline legacy-catalog tests and a bounded thread-safe cache for UIKit lookups.
- Wrap only late/unknown/dynamic arguments at native render boundaries. Continue using Apple's original Image/Label/UIImage initializers, including configurations, literal/dynamic localization overloads, and symbol rendering modes.
- Existing iOS-15-safe literals/finite ternaries remain unchanged. Existing template values stay unchanged. A source audit prevents new unsafe boundaries bypassing the resolver.
- Success: audit green; fallback targets exist at the 15.0 floor; requested names are unchanged when available; empty/unknown names become a known old visible glyph; native UIKit/SwiftUI call-shapes type-check at iOS 15/16; the production resolver's catalog matrix executes on the pinned macOS runner; full app/package passes.
- Ablations: remove a renderer wrapper => audit red. Remove the microphone fallback => semantic test red. Resolve supported symbols to alternatives => preservation test red. Choose a late fallback => floor test red.
- Independent failure signals: localization overload changes, lost UIImage configuration, helper absent from app target, data/persistence diffs, source audit silently missing dynamic/ternary paths, modern symbols unnecessarily downgraded, all-unknown path returning an unavailable fallback.

## Retirement and limitations

The raw rendering route is retired only for names requiring runtime validation. Safe old names intentionally stay native. There is no new per-OS view fork. iOS 15 hardware is not attached to this host; compile and catalog tests are not device visual acceptance. User should verify the previously blank voice/provider icons after delivery.

## Review/checkpoint

Independent subagent review was unavailable because the tool schema supplied an ACP-only streamTo field to subagent launches; no child review actually ran. Main agent owns review, exact-edit inspection, tests, and delivery. No production edits were made before locking the baseline, writing this plan, and running the red coverage gate.

## Final verification and delivery

- Code commit `31981ccfea891b891c3a7d99cdd16482942d2586`; run `35431955824` completed successfully with pinned Xcode 26.2 / build 17C52 / iphoneos SDK 26.2.
- Red/green: initial real-source coverage gate failed at 78 late/dynamic calls; after only the reviewed symbol-argument edits it passes. Parser/coverage suite 10/10; native Image/Label/UIImage call-shapes type-check at iOS 15.0 and 16.0; actual production resolver executes 78,582 checks across six availability catalogs. Catalog checks remain simulated availability, not an old-OS runtime.
- All 43 existing Swift-file changes match the exact-edit plan byte-for-byte. No provider/config/model storage changes. Full app build succeeded with zero compiler error lines.
- IPA: 84,178,534 bytes, SHA256 `3a0d09f8cad3e95773c81d9738ed9c1c935943bd249a87b8aaba26e61c4c3227`. Manifest/hash, ZIP CRC, bundle minimum 15.0, retained Share extension, compiled resolver in the arm64 product, and absence of the test catalog in the bundle were independently checked. Mach-O minimum is 15.0.0, SDK 26.2.0, UUID `7974ab20-168f-3ce3-a0c5-031275e74ddd`.
- Delivered via Weixin as `Minis-1.13-ios15-SF-icons-31981cc.ipa`. Device visual acceptance remains pending. Workspace evidence: `reports/openminis-ios15/run-35431955824-31981cc-x8aTSB/verification.json` and sibling build/audit/native logs.
