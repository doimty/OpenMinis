#!/usr/bin/env python3
"""Stdlib-only Mach-O / framework audit for the iOS 15 native floor probe.

Parses text produced by `xcrun otool -l`, `xcrun otool -Iv` (with
`-arch arm64` and, for static archives, `-arch all`), `file`, and framework
Info.plists. Never runs otool itself and never downloads anything, so the
suite is safe on Linux with fixture text.

Approval rules (mirrored in tests):
- deployment minimum may not exceed 15.0 (a 15.0.x patch is also a rejection);
- the (only) allowed architecture is arm64; an image without a parseable
  architecture is rejected, never approved;
- the only allowed platform is IOS; simulator/other platforms are rejected;
- an LC_BUILD_VERSION *or* LC_VERSION_MIN_IPHONEOS entry must be present with
  a parseable minos (SDK version is not the deployment minimum and is ignored
  for the floor decision);
- a dynamic framework's Info.plist MinimumOSVersion must not exceed 15.0;
- duplicate load-command minimums, missing metadata, or an unparseable load
  command are rejections, never a silent approval;
- every static archive member and every image block is audited: an earlier
  bad member must not be hidden by a later same-named member.
"""

import argparse
import json
import plistlib
import re
import sys
from pathlib import Path


_TRIPLE = {
    'IOS': 'ios',
    'MACOS': 'macos',
    'TVOS': 'tvos',
    'WATCHOS': 'watchos',
    'BRIDGEOS': 'bridgeos',
    'MACCATALYST': 'maccatalyst',
    'IOSSIMULATOR': 'iossimulator',
    'TVOSSIMULATOR': 'tvossimulator',
    'WATCHOSSIMULATOR': 'watchossimulator',
    'DRIVERKIT': 'driverkit',
    'VISIONOS': 'visionos',
    'VISIONOSSIMULATOR': 'visionossimulator',
    'XROS': 'xros',
    'XROSSIMULATOR': 'xrossimulator',
}

_KNOWN_PLATFORMS = frozenset(_TRIPLE) | {'IPHONEOS'}

# Device-arm64 probe: simulator and every non-iOS platform are rejections.
_ALLOWED_ARCH = {'arm64'}
_ALLOWED_PLATFORM = {'IOS', 'IPHONEOS'}

_FLOOR = (15, 0)

_ARCH_PATTERN = re.compile(r'\b(arm64e|arm64|x86_64|armv7|armv7s)\b',
                           flags=re.IGNORECASE)
_MINOS_PATTERN = re.compile(r'^\s*minos\s+([0-9]+(?:\.[0-9]+)*)', flags=re.MULTILINE)
_VERSION_PATTERN = re.compile(r'^\s*version\s+([0-9]+(?:\.[0-9]+)*)', flags=re.MULTILINE)
_PLATFORM_PATTERN = re.compile(r'^\s*platform\s+(\S+)', flags=re.MULTILINE)
_SDK_PATTERN = re.compile(r'^\s*sdk\s+([0-9]+(?:\.[0-9]+)*)', flags=re.MULTILINE)


def _parse_version(value):
    try:
        return tuple(int(part) for part in value.split('.'))
    except ValueError:
        return None


def version_to_string(version):
    if version is None:
        return None
    return '.'.join(str(part) for part in version)


def version_gt(version, limit=_FLOOR):
    """True when version > limit (tuple compare; 15.0.1 > 15.0 rejects)."""
    if version is None:
        return True
    return version > limit


def parse_output(text):
    """Split `otool -l` text into image blocks.

    Real `otool -l` (without -h) prints load commands with NO Mach header and,
    for a single image, no leading path line either. Static archives print one
    block per member, each headed by a `path(member):` line. Support all three
    shapes:

    - a line that ends with ':' whose next non-empty line is a `Load command`
      or `Mach header` starts a new image block (this covers archive members
      and the `otool -h`/`otool -lv` path prefixes);
    - a `Mach header` line NEVER starts a block on its own: it belongs to the
      block whose path line preceded it (or to the single anonymous image);
    - if no block start is found at all, the whole text is one image.

    Blocks are accumulated in order; same-named archive members each get their
    own entry (an earlier bad member must not be hidden by a later one).
    """
    lines = text.splitlines()
    starts = []
    for index, line in enumerate(lines):
        if line.endswith(':') and not line[:1].isspace():
            cursor = index + 1
            while cursor < len(lines) and not lines[cursor].strip():
                cursor += 1
            if cursor < len(lines) and (
                    lines[cursor].startswith('Load command')
                    or lines[cursor].startswith('Mach header')):
                starts.append(index)
    if not starts:
        return [['<image>', lines]]
    blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        header = lines[start]
        body = lines[start + 1:end]
        blocks.append([header, body])
    return blocks


def _audit_block(header, body, image_path, require_minimum=True):
    """Audit one image block; returns (entry, errors)."""
    block = '\n'.join([header] + body)
    has_legacy_command = 'LC_VERSION_MIN_IPHONEOS' in block
    platform_match = _PLATFORM_PATTERN.search(block)
    # LC_VERSION_MIN_IPHONEOS carries no platform line; treat it as IOS.
    platform = None if platform_match is None else platform_match.group(1).upper()
    if platform is None and has_legacy_command:
        platform = 'IOS'

    minos_matches = _MINOS_PATTERN.findall(block)
    if minos_matches:
        minos = _parse_version(minos_matches[0])
    elif has_legacy_command:
        legacy = _VERSION_PATTERN.search(block)
        minos = _parse_version(legacy.group(1)) if legacy is not None else None
    else:
        minos = None

    sdk_match = _SDK_PATTERN.search(block)
    sdk = sdk_match.group(1) if sdk_match else None

    arch = None
    arch_match = _ARCH_PATTERN.search(block)
    if arch_match:
        arch = arch_match.group(1).lower()

    entry = {
        'header': header.strip()[:120],
        'platform': ('ios' if platform == 'IOS' or platform == 'IPHONEOS'
                     else platform.lower() if platform else None),
        'minimum': version_to_string(minos),
        'sdk': sdk,
        'arch': arch,
    }
    errors = []
    if require_minimum and minos is None:
        errors.append(f'{image_path}: {entry["header"]}: no deployment '
                      'minimum (LC_BUILD_VERSION/minos or '
                      'LC_VERSION_MIN_IPHONEOS) found')
    elif minos is not None and version_gt(minos):
        errors.append(f'{image_path}: {entry["header"]}: deployment minimum '
                      f'{version_to_string(minos)} > 15.0')
    if len(minos_matches) > 1:
        errors.append(f'{image_path}: {entry["header"]}: duplicate minos '
                      f'fields ({", ".join(minos_matches)})')
    if platform is not None and platform not in _ALLOWED_PLATFORM:
        errors.append(f'{image_path}: {entry["header"]}: platform '
                      f'{platform} not allowed')
    if platform is None and require_minimum:
        errors.append(f'{image_path}: {entry["header"]}: platform not parseable')
    return entry, errors


def audit_image(image_text, arch_text, image_path, require_minimum=True):
    """Audit one image (dynamic dylib, executable, or static archive)."""
    arch_from_file = None
    arch_match = _ARCH_PATTERN.search(arch_text)
    if arch_match:
        arch_from_file = arch_match.group(1).lower()

    blocks = parse_output(image_text)
    entries = []
    errors = []
    for header, body in blocks:
        entry, block_errors = _audit_block(header, body, image_path,
                                           require_minimum)
        if entry['arch'] is None and arch_from_file is not None:
            entry['arch'] = arch_from_file
        if entry['arch'] is None:
            block_errors.append(
                f'{image_path}: {entry["header"]}: architecture not '
                'found (file(1) and load commands)')
        elif entry['arch'] not in _ALLOWED_ARCH:
            block_errors.append(
                f'{image_path}: {entry["header"]}: architecture '
                f'{entry["arch"]} not allowed')
        entries.append(entry)
        errors.extend(block_errors)
    if not blocks:
        errors.append(f'{image_path}: no image blocks found in otool output')
    return {
        'arch': arch_text.strip()[:300],
        'path': image_path,
        'images': entries,
        'errors': errors,
    }


def audit_metadata(otool_text, arch_text, plist, output=False):
    """Audit a single (otool -l text, file(1) text, plist dict) triple."""
    errors = []
    result = audit_image(otool_text, arch_text, '<input>')
    errors.extend(result['errors'])
    if plist is not None:
        minimum = plist.get('MinimumOSVersion')
        version = _parse_version(minimum) if isinstance(minimum, str) else None
        if minimum is None:
            errors.append('<input>: Info.plist is missing MinimumOSVersion')
        elif version is None:
            errors.append(f'<input>: Info.plist MinimumOSVersion '
                          f'{minimum!r} is not parseable')
        elif version_gt(version):
            errors.append(f'<input>: Info.plist MinimumOSVersion '
                          f'{minimum} > 15.0')
        platforms = plist.get('CFBundleSupportedPlatforms')
        if platforms is None or not isinstance(platforms, list) or not platforms:
            errors.append('<input>: Info.plist missing/empty '
                          'CFBundleSupportedPlatforms')
        else:
            for platform in platforms:
                if platform.upper() not in _KNOWN_PLATFORMS:
                    errors.append(
                        f'<input>: Info.plist unknown platform {platform!r}')
                if platform.upper() not in _ALLOWED_PLATFORM:
                    errors.append(
                        f'<input>: Info.plist platform {platform} not allowed')
    if output:
        return {
            'ok': not errors,
            'errors': errors,
            'images': result.get('images', []),
            'plist': plist,
        }
    return {'ok': not errors, 'errors': errors}


def audit_dependencies(text):
    """Parse `otool -Iv` and return exported/imported symbol and library lines."""
    exports = []
    imports = []
    libraries = []
    for line in text.splitlines():
        if re.search(r'\b(_set_vad_callback|_create_vad_instance|'
                     r'_destroy_vad_instance|_set_vad_sample_rate|'
                     r'_set_vad_threshold|_set_vad_model|'
                     r'_process_vad_audio)\b', line):
            exports.append(line.strip())
        if re.search(r'\b(_?dyld_stub_binder)\b', line):
            imports.append(line.strip())
        if '.framework/' in line or '.dylib' in line or '/usr/lib/' in line:
            libraries.append(line.strip())
    return {
        'export_lines': exports,
        'import_lines': imports,
        'library_lines': libraries,
    }


def audit_native_dir(native_dir, want='framework', limit=_FLOOR):
    """Audit a tree of produced or input frameworks.

    native_dir: Path to the directory containing frameworks (or the framework
                itself). want: 'framework' for dynamic frameworks, 'static'
                for static archives. limit: (major, minor) floor to enforce.

    NOTE: current 'static' branch only lists archives; member-level otool
    auditing is done by the caller on the actual archive text.
    """
    native_dir = Path(native_dir)
    errors = []
    results = []
    if want == 'static':
        archives = sorted(native_dir.glob('*.a'))
        if not archives:
            errors.append(f'{native_dir}: no static archives found')
        for archive in archives:
            entry = {'path': str(archive), 'kind': 'static'}
            results.append(entry)
    else:
        frameworks = sorted(native_dir.glob('*.framework'))
        if not frameworks:
            errors.append(f'{native_dir}: no frameworks found')
        for framework in frameworks:
            binary = framework / framework.stem
            entry = {'path': str(framework), 'kind': 'framework'}
            if not binary.exists():
                errors.append(f'{framework}: framework binary missing')
                results.append(entry)
                continue
            results.append(entry)
    return {'errors': errors, 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='otool -l text file')
    parser.add_argument('--arch', type=Path, help='file(1) arch text file')
    parser.add_argument('--plist', type=Path, help='Info.plist binary or XML')
    parser.add_argument('--check', action='store_true',
                        help='structural probe check')
    args = parser.parse_args()

    if args.check:
        result = audit_native_dir(Path('native'), want='framework')
        print(json.dumps({'errors': result['errors'],
                          'results': result['results']}, indent=2))
        sys.exit(0 if not result['errors'] else 1)

    otool_text = (args.input.read_text(encoding='utf-8', errors='replace')
                  if args.input else '')
    arch_text = (args.arch.read_text(encoding='utf-8', errors='replace')
                 if args.arch else '')
    plist = None
    if args.plist:
        try:
            plist = plistlib.loads(args.plist.read_bytes())
        except Exception as exc:
            plist = {'_error': str(exc)}
    result = audit_metadata(otool_text, arch_text, plist)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()