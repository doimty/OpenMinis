# iOS 15 native dependency validation

## Baseline and status

- App baseline: `aa21abcbecf2030004e6675dbb8ee6cd2f2e6721`, `doimty/OpenMinis:compat/ios15`.
- Full app compile and nine-file15/16 compatibility smoke passed in run35357606053. Log2,275,679 bytes, SHA256 `df8ddb6377eb3ffca26dfe6139661ea7a61158083b9845da05a2f23192df7f61`, xcodebuild exit0.
- Rclone was rebuilt from source with15.0 device/simulator flags; its higher-minimum linker warnings went11→0. Other existing native build scripts target14.0.
- The one remaining link-floor warning (`RealTimeCutVADCXXLibrary` minimum15.6 at app log17992) now has a validated source-build route: run **35375830915 / `4cc72bc`** produced an APPROVED probe (9 gates ok, exit0). Built framework is `platform ios / arch arm64 / minimum 15.0 / sdk 26.2`, plist `MinimumOSVersion 15.0`, all 7 C entrypoints exported as `T` symbols (incl. 5-arg continuing-PCM `_set_vad_callback`), dependency closure clean (self + CoreFoundation/Foundation/CoreML + libc++/libSystem/libobjc), real upstream `VADWrapper.m` compiles against it at `arm64-apple-ios15.0`. Evidence: `reports/openminis-ios15/run-35375830915-af6ERo/evidence/evidence.json`, SHA256 `42633aadf79cf9592e81f61079fe6ac0e7d3c473e32ab27460f024b1bfd82759`. Binary SHA256 `2fa4125cf5e232609d1e00b1301258724bd0d6ced32c7ae3379375dbeb18d596`.
- The probe uploads evidence only (no framework, no IPA, no app change). **Next gate: integrate the rebuilt 15.0 framework into the app** (swap the SPM prebuilt 15.6 `binaryTarget` for a pinned 15.0-built artifact) and re-verify the app build drops the 17992 warning; then produce an installable IPA and run M3 device acceptance.

## Source of truth and ownership

- Main product floor stays15.0. Do not weaken the product contract in `ios15-port-plan.md`.
- Wrapper package1.0.14 resolves to `b059608836a055dc0ce74ca2d0eeb19fc54cb7d4` in `helloooideeeeea/RealTimeCutVADLibrary`. Its source/resource target depends on a prebuilt native target from distributionv1.0.3.
- C++ source candidate: `helloooideeeeea/RealTimeCutVADCXXLibrary@1648fc13a04a90521a2ef3887885ef2506208605`. Its Xcode target explicitly sets15.6 (Debug/Release), project defaults18.2. It includes the continuing-PCM callback required by the wrapper. Candidate does not mean attested provenance for the original binary.
- Native inputs: upstream ONNX Runtime and WebRTC/APM XCFrameworks. Select exact published checksum pins; do not assume an input is compatible because a source recipe or wrapper manifest advertises13/15.
- Current `VoiceActivityDetector.setupEngineAndVAD` creates VAD before installing the microphone tap. No UI-only availability gate is a sufficient binary or capture fallback. This probe changes no voice behavior.
- Native README declares MIT but its LICENSE link is missing. Keep all applicable notices; logs-only validation may proceed, but a maintained binary redistribution requires a complete notice/provenance record.

## H1: The native core can be rebuilt for15.0 without changing the app voice contract

Success:

1. Exact input/source hashes are recorded and archive checksums match before use.
2. Selected arm64 iOS input object metadata is compatible with15.0 and the correct platform; unknown or higher floors are not silently approved.
3. A genuine C++ source build uses Xcode26.2/17C52 SDK26.2 and explicit `IPHONEOS_DEPLOYMENT_TARGET=15.0`, not a binary/plist patch.
4. The produced framework's Mach-O minimum, platform, architecture, dependency closure and framework plist are recorded and compatible.
5. C exports and the callback ABI match the pinned wrapper. Compiling the real upstream wrapper is stronger evidence than checking only symbol names.

Independent failure signals:

- A downloaded input itself has a higher/unknown minimum, wrong platform or checksum mismatch.
- The C++ build calls an unavailable API or fails to link against the pinned native input headers/binaries.
- The new dylib still imports an unaudited higher-floor native framework, or its own minimum remains15.6/18.2.
- Callback signature mismatch, missing exports, or missing wrapper/model resource contract.
- An implementation merely lowers Info.plist/Mach-O metadata, suppresses the warning, or removes normal dictation to get a green build.

Ablation/evidence plan:

- The existing app link is the positive mismatch control: it still diagnoses VAD15.6.
- Local parser tests must reject >15.0 and wrong-platform/missing metadata; SDK version is not the deployment minimum.
- Build a standalone native probe first. App package references and voice code remain unchanged until its output passes review.
- Initial gate may be device arm64 only if explicitly reported. Do not claim simulator coverage from a device-only build; verify required additional slices before integration/distribution.
- Archive source refs, input hashes, effective build settings, nonempty build logs, read-only Mach-O/dependency/exports inspections and output hashes. Do not publish a framework/IPA from the probe.

## Implementation boundary and retirement track

The initial change is a CI probe plus a small reusable floor-audit helper/tests. It runs only for the probe's paths/manual dispatch on the authorized fork branch. It must use fresh temporary directories, bounded downloads, fixed inputs/toolchain, read-only permissions, and upload evidence on failure as well as success.

The currently linked upstream binary remains the known unresolved baseline while the probe runs. Only after validation should the app switch to a pinned source-built package/artifact. At that point explicitly retire the old binary target and prove the app is not still embedding it. Do not fork or publish new third-party distribution repositories during the probe.

Real iOS15 install/launch and System/cloud dictation, callbacks, long utterance handling, interruptions and native16+ regressions remain M3 acceptance, not results of source inspection or an isolated successful link.
