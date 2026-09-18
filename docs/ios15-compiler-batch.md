# iOS 15 compiler batch after f644f41

## Locked baseline and red feedback

- Work branch `compat/ios15`, local and fork head `f644f412169487643a8e78623364ea73add21bf6`, initially clean. Only `fork` (`doimty/OpenMinis`) may receive the work branch; never upstream `origin` or main.
- CI `35316524791` failed with xcodebuild exit 65. Its 810383-byte log and versions.json agree on that SHA, Xcode 26.2/17C52, iOS SDK 26.2 and pinned iSH. Cache hit is explicitly true.
- Five diagnosed calls: WebPreviewSheet's WebKit fullscreen preference (15.4) and system overlays (16), ToolLiveSheet's uneven corners (16), and two message-list context menus with previews (16).
- Replayed the real log with `python3 scripts/ios15_build_log.py <artifact>/m1/xcodebuild.log --output-dir <report>`: the same five diagnostics. This replays evidence, NOT compilation of local changes.
- There is no local Xcode/Swift. The real feedback loop is the pinned Apple compiler in Actions. The existing standalone Swift smoke was never wired into that workflow and only checks one Shared file; fix that gap. Do not call tree-sitter parsing type-checking.

## Read set and ranked explanations

Read docs/ios15-port-plan.md, progress.md, workflow, the five diagnostic sites, all six Shared adapters, ContentView shape/field sites, ChatMessageViews context menus, and remaining multiline-field users. No repository AGENTS.md/CONTEXT.md or local ADR exists. Existing graph vocabulary query [web, preview, sheet, message, context, menu] finds the expected WebViewHolder/MessageContextMenuPreview boundaries; source is authoritative because the graph predates this batch.

1. Unguarded newer APIs are still reached at target 15.0. Prediction: preserving native branches and replacing old-platform call shapes removes the named availability diagnostics.
2. Compatibility code itself was insufficiently checked: duplicate compatLineLimit and ToolbarPlacement in a legacy-visible signature. Prediction: standalone type-checking the production Shared files catches these before a full app build.
3. A stopped/failed full build has not diagnosed every compilation batch. Prediction: other exact call shapes (four multiline fields, AnyShape/uneven corners, two additional preview menus) also need migration; a smaller diagnostic count is not global completion.
4. Toolchain drift is not supported: artifact versions match the pin. Do not change deployment target or SDK to hide the errors.

Random bisection/instrumentation is intentionally skipped: these are deterministic, source-located compiler diagnostics. Minimise through a real SwiftUI fixture, not a fake local runtime. Independent subagent audit could not start: the exposed payload supplies ACP-only streamTo to subagent runtime. No independent review is claimed.

## Scope and compatibility decisions

- One production adapter owner per API. Remove duplicate declarations and retire the broken generic toolbar/field helpers when replacing them.
- Navigation-bar visibility: native toolbar API on 16+, navigationBarHidden on 15; public adapter takes Bool, not a newer SDK type.
- Context menus: keep all actions on 15 through the older menu API, omit only the optional custom preview; preserve native previews on 16+. Keep existing zero-size overlays and sizing ownership unchanged.
- Shapes: retain native uneven shape paths on 16+; bounded per-corner paths and local shape erasure on 15. Do not round away square joining edges in folder segments.
- WebKit fullscreen preference: gate at 15.4. System-overlay hiding remains a cosmetic capability on 16+; older OS retains its system home indicator and explicit exit controls.
- Multiline fields: retain native vertical TextField on 16+. Use a real TextEditor on 15, not a single-line field falsely described as multiline. Form fields get bounded line-height sizing; transcript editing retains its existing bounded parent/scroll/focus ownership. Preserve localized labels versus already-localized strings. Browser custom-UA changes must actually save on the legacy editor path.
- No persistence/provider/sandbox/schema changes. iOS15 native/weather/framework loading still requires device/package validation after compile.

## Tests and success/failure contracts

1. Before production changes, add structural regression checks for the observed duplicate declaration and unavailable adapter-signature type; demonstrate they fail on this baseline. These checks do not prove Swift semantics.
2. Extend the existing negative-control fixture with the actual failing API shapes. It must compile at 16 and fail availability at 15. Type-check all six production Shared files plus call fixtures at both targets, including forms/paths/photo picker/hosting/geometry/shapes/menu variants.
3. Run that real compiler smoke in Actions and retain its log even on failure. Still collect full-app errors; make either smoke or full-app failure keep the workflow red.
4. Local checks: Python regression tests, shell syntax, whitespace, source inventory, new-file target membership, syntax deltas with nonzero status on new parser errors. Inspect staged files explicitly; no generated cache/config secrets.
5. Success: fresh artifact matches pushed SHA; smoke passes both targets and original full xcodebuild exits zero with BUILD SUCCEEDED. No claim of device success from that alone.

Independent failure signals: native menu actions/preview removed on 16+, folder joins rounded incorrectly, legacy multiline text hidden/unreachable or unsaved, stale route/cell ownership changed, source scan described as build proof, or failing smoke allowed to produce a green run.

## Resume and drift guard

Before each push recheck branch, local changes, fork SHA, and scope. Use explicit staged paths and fork branch. Download CI evidence into a new directory; inspect all logs rather than only the first error count. Update progress.md with actual result. No IPA/device acceptance is claimed until the product contract is exercised.
