# Progress

## 2026-09-17 21:39 — M0 native fail: iSH VDSO missing lld

- Run `35225046741` failed in 5 minutes. Xcode 26.2 pin and iSH SHA passed. LAME and FFmpeg 6.1.2 packaged successfully.
- Fail: `deps/build_ish.sh` ninja `[1/90] vdso/arm64/libvdso.so.elf` → Homebrew clang 23.1.0 `invalid linker name in argument '-fuse-ld=lld'`.
- Not an iOS 15 Swift issue. App compile never started. Evidence step then failed because `$RUNNER_TEMP/m0` did not exist.
- Fix: install Homebrew `lld`, put `ld.lld` on PATH, mkdir evidence dir. Re-run on `compat/ios15`.

## 2026-09-17 evening — M0 fork + baseline CI

### Baseline

- Upstream remains `OpenMinis/OpenMinis` `4ef29002e88db1e20e462ec2ff46916e8a7dcb45`.
- Disk ordinary availability recovered to about 1.2 GiB after cancelling the unrelated OpenClaw upgrade cleanup. Still no local Xcode.
- User asked to continue OpenMinis, then said start, then asked why no new GitHub repository existed. 21:04: “我全部授权你，后续的话不用我同意.” Subsequent fork/commit/push to `doimty/OpenMinis` non-default branches, Actions, and compatibility code do not need another ask. Still do not push upstream `OpenMinis/OpenMinis` or `main`.

### This checkpoint

- Added `.github/workflows/ios15-m0-baseline.yml`: pin macOS 26 / Xcode 26.2 / iOS SDK 26.2, init iSH `3f6384c70eefd1a370f121d3492a5f21f7767df9`, build lame/ffmpeg/ish/rootfs/rclone, compile `Minis` for `generic/platform=iOS` with signing disabled.
- No Swift compatibility edits and no deployment-target change in this checkpoint.
- Resume: after the fork exists and the workflow run finishes, treat only a zero `xcodebuild` with nonempty log + `versions.json` as M0 pass. Then M1.

## 2026-09-17 — iOS 15 port started, M0 authorization gate

### Baseline

- Upstream: `OpenMinis/OpenMinis`, `main` at `4ef29002e88db1e20e462ec2ff46916e8a7dcb45`.
- Work branch created: `compat/ios15`.
- Initial working tree was clean. No user modifications were overwritten.
- Product scope: iOS 15 core edition; preserve iOS 16+ native paths where practical.
- Authoritative implementation plan: `docs/ios15-port-plan.md`.

### Completed

- Rechecked source SHA, target settings, workspace cleanliness and native-tool availability.
- Created a task-specific branch, without a commit or push.
- Recorded hypotheses, failure signals, ownership boundaries, native/legacy ablations and runtime acceptance gates in the plan.
- Ran `bash -n` on `deps/build_ish.sh`, `deps/build_ffmpeg.sh`, `deps/build_lame.sh` and `deps/prepare_alpine_rootfs.sh`: all passed shell syntax checks. None of these scripts was executed.

### Current blockers / limitations

- No local `xcodebuild` or Swift compiler. Xcode compilation has not been attempted and no IPA exists.
- Filesystem ordinary available space is 0 bytes; remaining free reserved blocks are approximately 1.3 GiB. Do not install/download a local toolchain or large native artifacts.
- Need explicit approval for GitHub fork/repository creation, commit/push of a non-default work branch and macOS Actions execution. No GitHub write has occurred.
- Independent child plan review did not run: runtime rejected the incompatible `streamTo` argument on a subagent spawn. Do not count failed launch attempts as a review or claim background implementation is running.

### Resume

1. Obtain user authorization for the GitHub write/build operations above.
2. Check authenticated repository ownership and current macOS runner/Xcode availability; pin actual versions.
3. Produce an unchanged-source baseline build and dependency lock before interpreting iOS 15 compiler errors.
4. Follow milestones M1–M3 in `docs/ios15-port-plan.md`; update this file after each verification gate.

### Not done

No Swift compatibility code has been changed yet. No targets lowered, no dependency scripts run, no fork created, no commits or pushes, no build or device validation. This checkpoint records the start and a genuine build-environment/authorization gate, not completion of the port.
