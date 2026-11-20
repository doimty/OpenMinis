# iOS 15 GPT-6.2, attachment layout and isolated file-access build

## Verified baseline / scope

- Repository: `doimty/OpenMinis`, latest named iOS15 branch `ios15/1.15`.
- GitHub API checked on 2026-11-20: `d592dc6b42ab1e655b4fbf917a082d298bad692b`; existing MinisShare iOS15 fix stays intact.
- Working branch: `fix/ios15-gpt62-attachments-broadfiles-20261120` (local only until explicit push authorization).
- No `AGENTS.md` found in the repository or parent locations checked. Read README, BUILDING, current iOS15 build/packaging docs before changes.
- User added an AzTuT 1.14 static security report and three screenshots. The report is reference evidence, not proof of runtime privilege. First screenshot is the subagent-detail steer composer; the other two show the main-chat rounded surface.

## Assumptions

1. GPT-6.2 support is capability adaptation for endpoint-returned/custom model IDs, not proof that every OpenAI/relay account serves it. Endpoint/user declarations outrank name heuristics. Do not invent context limits, release dates or account tiers.
2. Attachment tiles should pack horizontally at their existing fixed size and wrap by available width. Preserve original order, drag/remove, preview/gallery starting index, mixed-media/file tiles and queued messages.
3. Subagent steer composer should use a continuous rounded surface matching main-chat styling on iOS15, without changing steer/cancel semantics, height bounds or keyboard placement.
4. A broad-file TrollStore IPA must be explicitly selected and distinguishable. Ordinary packaging keeps its entitlements/identity. No auto-mount of `/`, no root helper, no process injection, no TCC blanket bypass, no device modification.
5. Native host access and Linux guest access remain different; use existing explicit mount/native file bridge, never claim an entitlement makes guest `/` the iOS root.

## Success criteria

- GPT-6.2 name recognition handles dotted/dash spellings and vendor-prefixed relay IDs without matching unrelated IDs such as GPT-6.20. Unknown capabilities get a conservative, documented fallback; explicit false/no-tiers and narrower endpoint tiers stay authoritative. Chat Completions and Responses use the correct envelope and never send literal `ultra`.
- Both composer and sent/queued attachment grids share width/height behavior with tile-height estimation. On first render and rotation, two or three fitting tiles stay in a row; no zero-height initial composer region or vertical-only fallback.
- iOS15 steer editor's inner white rectangle cannot escape its rounded boundary; remains accessible, supports multiline text and bounded scroll.
- Keep the original global auto-compaction preference, budget and user-facing manual compaction. Restore the missing iOS 15 UIKit overflow-menu toggle; it must survive stream-menu refreshes and never change external/model-summary settings.
- Broad-files package is opt-in, has separate manifest/output/identity and intentionally minimal file access entitlements. Normal IPA behavior and MinisShare minOS filtering remain unchanged. Signing and identity checks fail closed.
- Add behavior/negative/boundary tests where executable here; wire Swift native smoke checks into existing macOS workflow if needed. Report static tests, mocked packager checks, native compile, simulator and device evidence separately.

## Independent failure signals / verification

- Capability negative fixtures: explicit disabled reasoning; endpoint low/medium/high-only list; no effort tiers; unrelated near-prefix ID; legacy persisted model with missing fields. Compare source-path wire decisions, not just a matching model-name string.
- Layout negative fixtures: fitting tiles rendered vertically, retained wrong width after rotation, grid measured as zero, preview keyed to wrong attachment. Test pure packing math and source integration; existing LegacyFlowLayout tests protect unrelated uses.
- Packaging negatives: normal mode receives broad entitlements; broad identity collides with ordinary App Group/keychain; incompatible appex retained; missing/extra high-risk entitlement; malformed profile or manifest accepted. Use synthetic app fixtures; no signed IPA claim on Linux.
- Run relevant Python unittest suites, shell syntax/compatibility checks, `git diff --check`, final diff/worktree review. Actual SwiftUI rendering requires macOS/iOS; no screenshot claim from a static preview.
- Cloud commit/push/build and device installation require explicit authorization. Do not install TrollStore or jailbreak payloads.

## Work boundaries

- Main agent: GPT capability adaptation, isolated packaging/permissions design, integration and delivery.
- Code implementation subagent: attachment layout plus steer input rounding only.
- Technical research subagent: read-only entitlement/source checks.
- Independent final review: read-only patch correctness/permission boundaries.


## Verification log (2026-11-20)

- `python3 tests/test_gpt62_reasoning.py --static`: passed. `--native`: skipped with exit 77 because this iSH host has no Swift/Xcode toolchain; it does not prove an iOS build.
- `python3 scripts/test_ios15_attachment_grid.py`: 16/16 passed. Pure packing/source-contract checks, not SwiftUI rendering.
- `python3 scripts/test_ios15_auto_compaction_menu.py`: passed source/state/negative checks; UIKit was not executed here.
- `python3 scripts/test_ios15_file_access_profile.py`: passed profile isolation, forbidden-privilege and fail-closed checks; codesign/runtime file access were not available.
- `python3 scripts/test_package_ios15_ipa.py -v`: packaging fixture suite passed. `git diff --check`: passed. Apple native compile, simulator, TrollStore install, jailbreak/root-helper install and device permission probes remain unverified.
- Apelin and `openminis-jailbreak` are references only. Apelin is an iOS 16+/rootless package with a separate privileged worker architecture; the jailbreak repository is a loopback MCP bridge and does not make the ordinary OpenMinis process root. Neither is copied into this patch.
