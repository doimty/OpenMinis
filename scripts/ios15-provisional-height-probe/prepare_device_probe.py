#!/usr/bin/env python3
"""Build-only full-app probe overlay and isolated-bundle preparation.

No renderer, text view, host, cell or layout is replaced or truncated. The
normal app entry point is disabled only in a disposable CI checkout. A DEBUG
probe entry point is appended to an already compiled debug file. Source hashes
and inverse restoration are required before building.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

BASELINE = '2f21e242df71d63576682f8ac61c50a097f3a5ae'
MAIN_PATH = 'src/ios/MinisApp.swift'
DEBUG_PATH = 'src/ios/Debug/MessageListTestView.swift'
LOGGER_PATH = 'src/ios/Shared/AppLogger.swift'
RENDER_PATHS = (
    'src/ios/Views/Chat/SelectableMarkdownView.swift',
    'src/ios/Agent/Markdown/MinisMarkdownParser.swift',
    'src/ios/Shared/LegacyHostingContent.swift',
    'src/ios/Agent/MessageList/MessageListInfrastructure.swift',
    'src/ios/Agent/MessageList/MessageListLayout.swift',
    'src/ios/Agent/MessageList/CollectionViewMessageListV3.swift',
    'src/ios/Views/Chat/AssistantBlockView.swift',
    'src/ios/Agent/Chat/AIChatViewModel.swift',
    'src/ios/Agent/Chat/AIChatViewModel+Persistence.swift',
    'src/ios/Agent/Chat/ChatStore.swift',
    'src/ios/Agent/Chat/ChatModels.swift',
)
BASELINE_PATHS = (MAIN_PATH, DEBUG_PATH, LOGGER_PATH, *RENDER_PATHS)
START = '\n// NATIVE-DEVICE-PROBE-OVERLAY-BEGIN\n'
END = '// NATIVE-DEVICE-PROBE-OVERLAY-END\n'
ENTRY = '\n@main\nstruct MinisApp: App {'
NO_ENTRY = '\nstruct MinisApp: App {'

READONLY_COLLECTOR = '''#if DEBUG
extension ReentryDiagnostics {
    var nativeProbeRunID: String { runID }

    /// UI-controlled one-shot window, never called from rendering callbacks.
    /// Pausing does not reset identities, sequence, dedup state or the hard cap.
    func nativeProbePauseCapture() {
        lock.lock()
        closed = true
        lock.unlock()
    }

    func nativeProbeBeginCapture() throws {
        lock.lock()
        defer { lock.unlock() }
        guard enabledForInstance, sequence == 0 else {
            throw NSError(domain: "NativeProbeCapture", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Capture is disabled or already consumed"])
        }
        closed = false
    }

    /// Test report finalization only; never invoked by a rendering callback.
    func nativeProbeReadEvents() throws -> [[String: Any]] {
        drainForTesting()
        guard !queue.sync(execute: { fileFailed }), let url = logURL else {
            throw CocoaError(.fileReadUnknown)
        }
        let contents = try String(contentsOf: url, encoding: .utf8)
        return try contents.split(separator: "\\n").map { line in
            guard let marker = line.range(of: "[REENTRYDIAG] ") else {
                throw CocoaError(.fileReadCorruptFile)
            }
            let json = String(line[marker.upperBound...])
            guard let object = try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] else {
                throw CocoaError(.fileReadCorruptFile)
            }
            return object
        }
    }
}
#endif
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def baseline_sources(repository: Path) -> dict[str, bytes]:
    return {name: subprocess.check_output(['git', '-C', str(repository), 'show', BASELINE + ':' + name])
            for name in BASELINE_PATHS}


def remove_overlay(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError('missing or repeated build overlay marker')
    a = text.index(START)
    b = text.index(END, a) + len(END)
    return text[:a] + text[b:]


def restore_sources(build_root: Path) -> dict[str, bytes]:
    restored = {}
    for name in BASELINE_PATHS:
        text = (build_root / name).read_bytes().decode('utf8')
        if name == MAIN_PATH:
            if text.count(NO_ENTRY) != 1 or ENTRY in text:
                raise ValueError('unexpected generated app entrypoint')
            text = text.replace(NO_ENTRY, ENTRY)
        elif name in (DEBUG_PATH, LOGGER_PATH):
            text = remove_overlay(text)
        restored[name] = text.encode('utf8')
    return restored


def prepare_sources(build_root: Path, repository: Path, driver: Path) -> dict:
    build_root, repository, driver = Path(build_root), Path(repository), Path(driver)
    original = baseline_sources(repository)
    for name, data in original.items():
        if (build_root / name).read_bytes() != data:
            raise ValueError('source differs from the immutable baseline: ' + name)
    main = original[MAIN_PATH].decode('utf8')
    if main.count(ENTRY) != 1:
        raise ValueError('app entrypoint is not uniquely identified')
    driver_text = driver.read_text()
    if driver_text.count('@main') != 1 or 'final class NativeProvisionalHeightApp' not in driver_text:
        raise ValueError('missing unique probe entrypoint')
    for name in ('SelectableMarkdownTextView', 'LegacyHostingContentView', 'SelfSizingCell', 'MessageListLayout'):
        if re.search(r'\bclass\s+' + name + r'\b', driver_text):
            raise ValueError('probe must not replace a production type: ' + name)
    generated = {
        MAIN_PATH: main.replace(ENTRY, NO_ENTRY),
        DEBUG_PATH: original[DEBUG_PATH].decode('utf8') + START + driver_text + '\n' + END,
        LOGGER_PATH: original[LOGGER_PATH].decode('utf8') + START + READONLY_COLLECTOR + END,
    }
    for name, text in generated.items():
        (build_root / name).write_text(text)
    if restore_sources(build_root) != original:
        raise ValueError('generated source cannot restore exact baseline bytes')
    for name in RENDER_PATHS:
        if (build_root / name).read_bytes() != original[name]:
            raise ValueError('renderer/measurement path changed: ' + name)
    return {
        'kind': 'full-app-device-native-probe-overlay', 'baselineCommit': BASELINE,
        'buildCommit': subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip(),
        'driverSHA256': digest(driver.read_bytes()),
        'sourceSHA256Before': {name: digest(data) for name, data in original.items()},
        'sourceSHA256After': {name: digest((build_root / name).read_bytes()) for name in BASELINE_PATHS},
        'modifiedSources': list(generated), 'renderingSourcesUnchanged': True,
        'exactInverseVerified': True, 'nativeExecution': 'NOT_RUN',
        'limits': 'Complete app components; temporary DEBUG entry/trace-read overlay; device run still required.',
    }


def prepare_bundle(source_app: Path, output: Path, commit: str) -> Path:
    source_app, output = Path(source_app).resolve(), Path(output).resolve()
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('full source commit required')
    if not source_app.is_dir() or not (source_app / 'Info.plist').is_file():
        raise ValueError('source app missing')
    if output == source_app or output.is_relative_to(source_app):
        raise ValueError('probe output must not modify or be inside source app')
    if output.exists() and any(output.iterdir()):
        raise ValueError('probe output directory must be empty')
    before = (source_app / 'Info.plist').read_bytes()
    output.mkdir(parents=True, exist_ok=True)
    app = output / 'Minis.app'
    shutil.copytree(source_app, app, symlinks=False)
    info = plistlib.loads(before)
    info.update(CFBundleIdentifier='com.openminis.layoutprobe',
                CFBundleName='Minis Layout Probe', CFBundleDisplayName='Minis Layout Probe',
                MinisNativeHeightProbe=True, MinisReentryDiagnostics=True,
                MinisDiagnosticCommit=commit, MinisProbeBaselineCommit=BASELINE,
                UISupportedInterfaceOrientations=['UIInterfaceOrientationPortrait'])
    for key in ('UIApplicationSceneManifest', 'CFBundleURLTypes', 'CFBundleDocumentTypes',
                'UTExportedTypeDeclarations', 'UTImportedTypeDeclarations'):
        info.pop(key, None)
    (app / 'Info.plist').write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    # These are copies inside the new staging directory, never the source app.
    for name in ('PlugIns', 'Extensions'):
        folder = app / name
        if folder.exists():
            shutil.rmtree(folder)
    if (source_app / 'Info.plist').read_bytes() != before:
        raise ValueError('source bundle was unexpectedly mutated')
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sources = sub.add_parser('sources')
    sources.add_argument('--root', type=Path, required=True)
    sources.add_argument('--repository', type=Path)
    sources.add_argument('--manifest', type=Path, required=True)
    bundle = sub.add_parser('bundle')
    bundle.add_argument('--app', type=Path, required=True)
    bundle.add_argument('--output', type=Path, required=True)
    bundle.add_argument('--commit', required=True)
    args = parser.parse_args()
    if args.action == 'sources':
        result = prepare_sources(args.root, args.repository or args.root, Path(__file__).with_name('DeviceProbe.swift'))
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(result, indent=2) + '\n')
        print('Full production rendering sources unchanged; exact inverse verified; native execution NOT_RUN.')
    else:
        print(prepare_bundle(args.app, args.output, args.commit))


if __name__ == '__main__':
    main()
