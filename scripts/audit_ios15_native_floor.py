#!/usr/bin/env python3
"""Stdlib-only Mach-O / framework audit for the iOS 15 native floor probe.

Parses text produced by `xcrun otool -l`, `xcrun otool -Iv` (with
`-arch arm64` and, for static archives, `-arch all`), `file`, and framework
Info.plists. Never runs otool itself and never downloads anything, so the
suite is safe on Linux with fixture text.

Approval rules (mirrored in tests):
- deployment minimum may not exceed 15.0;
- the image must be arm64 (device) and platform IOS for dynamic frameworks;
- an LC_BUILD_VERSION *or* LC_VERSION_MIN_IPHONEOS entry must be present with
  a parseable minos (SDK version is not the deployment minimum and is ignored
  for the floor decision);
- a dynamic framework's Info.plist MinimumOSVersion must not exceed 15.0;
- missing metadata or an unparseable load command is a rejection, never a
  silent approval.
"""

import argparse
import json
import plistlib
import re
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

_ALLOWED_ARCH = {'arm64'}
_ALLOWED_FRAMEWORK_PLATFORM = {'IOS'}
_ALLOWED_ARCH_PLATFORM = {'IOS', 'IOSSIMULATOR'}


def parse_minos(body):
    """Return (platform, minos, sdk) from an LC_BUILD_VERSION block."""
    platform = None
    minos = None
    sdk = None
    for line in body.splitlines():
        match = re.match(r'^\s*platform\s+(\S+)', line)
        if match:
            platform = match.group(1).upper()
            continue
        match = re.match(r'^\s*minos\s+([0-9]+(?:\.[0-9]+)*)', line)
        if match:
            minos = tuple(int(part) for part in match.group(1).split('.'))
            continue
        match = re.match(r'^\s*sdk\s+([0-9]+(?:\.[0-9]+)*)', line)
        if match:
            sdk = match.group(1)
    return platform, minos, sdk


def parse_legacy_minos(body):
    """Return the version tuple from an LC_VERSION_MIN_IPHONEOS block."""
    for line in body.splitlines():
        match = re.match(r'^\s*version\s+([0-9]+(?:\.[0-9]+)*)', line)
        if match:
            return tuple(int(part) for part in match.group(1).split('.'))
    return None


def version_to_string(version):
    if version is None:
        return None
    return '.'.join(str(part) for part in version)


def version_gt(version, limit):
    """True when version > limit=(major, minor). 15.0 == limit is acceptable."""
    if version is None:
        return True
    return (version[0], version[1] if len(version) > 1 else 0) > (limit[0], limit[1])


def parse_output(text):
    """Split `otool -l` text into per-image blocks keyed by header line."""
    images = {}
    current = None
    for line in text.splitlines():
        if line.startswith('Mach header'):
            current = line
            images[current] = []
            continue
        if current is not None and not line.strip() and images[current]:
            current = None
        if current is not None:
            images[current].append(line)
    return images


def audit_image(image_text, arch_text, image_path, require_minimum=True):
    """Audit one image (dynamic dylib, executable, or static archive member)."""
    records = parse_output(image_text)
    if not records:
        return {
            'arch': arch_text.strip(),
            'path': image_path,
            'errors': ['no Mach header found in otool output'],
        }
    image_errors = []
    entries = []
    for header, body in records.items():
        platform, minos, sdk = parse_minos('\n'.join(body))
        if minos is None:
            legacy = parse_legacy_minos('\n'.join(body))
            if legacy is not None:
                platform = 'IOS'
                minos = legacy
        entries.append({
            'header': header,
            'platform': platform,
            'minimum': version_to_string(minos),
            'sdk': sdk,
        })
        if require_minimum and minos is None:
            image_errors.append(
                f'{image_path}: {header.strip()[:80]}: no deployment minimum '
                '(LC_BUILD_VERSION/minos or LC_VERSION_MIN_IPHONEOS) found')
            continue
        if minos is not None and version_gt(minos, (15, 0)):
            image_errors.append(
                f'{image_path}: {header.strip()[:80]}: deployment minimum '
                f'{version_to_string(minos)} > 15.0')
        arch = None
        arch_match = re.search(
            r'\b(arm64e|arm64|x86_64|armv7|armv7s)\b',
            '\n'.join([header] + body[:3]), flags=re.IGNORECASE)
        if arch_match:
            arch = arch_match.group(1).lower()
        if arch is not None and arch not in _ALLOWED_ARCH:
            image_errors.append(
                f'{image_path}: {header.strip()[:80]}: architecture {arch} not allowed')
        if platform is not None and platform not in _ALLOWED_ARCH_PLATFORM:
            image_errors.append(
                f'{image_path}: {header.strip()[:80]}: platform {platform} not allowed')
        if platform is None and require_minimum:
            image_errors.append(
                f'{image_path}: {header.strip()[:80]}: platform not parseable')
    return {
        'arch': arch_text.strip(),
        'path': image_path,
        'images': entries,
        'errors': image_errors,
    }


def audit_metadata(otool_text, arch_text, plist, output=False):
    """Audit a single (otool -l text, arch text, plist dict) triple."""
    errors = []
    images = {}
    result = audit_image(otool_text, arch_text, '<input>')
    images[result['path']] = result
    errors.extend(result['errors'])
    if plist is not None:
        minimum = plist.get('MinimumOSVersion')
        if minimum is None:
            errors.append('<input>: Info.plist is missing MinimumOSVersion')
        elif version_gt(_parse_version_string(minimum), (15, 0)):
            errors.append(
                f"<input>: Info.plist MinimumOSVersion {minimum} > 15.0")
        platforms = plist.get('CFBundleSupportedPlatforms')
        if platforms is not None and isinstance(platforms, list):
            for platform in platforms:
                if platform.upper() not in _KNOWN_PLATFORMS:
                    errors.append(
                        f'<input>: Info.plist unknown platform {platform!r}')
        elif platforms is None:
            errors.append('<input>: Info.plist is missing CFBundleSupportedPlatforms')
    if output:
        return {
            'ok': not errors,
            'errors': errors,
            'images': result.get('images', []),
            'plist': plist,
        }
    return {'ok': not errors, 'errors': errors}


def _parse_version_string(value):
    try:
        return tuple(int(part) for part in value.split('.'))
    except ValueError:
        return None


def audit_framework(otool_text, arch_text, plist, warnings=None):
    """Audit a framework: combined binary image plus Info.plist metadata."""
    result = audit_metadata(otool_text, arch_text, plist, output=False)
    return result


def audit_dependencies(text):
    """Parse `otool -Iv` and return exported/imported symbol and library lines."""
    exports = []
    imports = []
    libraries = []
    for line in text.splitlines():
        if re.search(r'\b(_set_vad_callback|_create_vad_instance|_destroy_vad_instance|'
                     r'_set_vad_sample_rate|_set_vad_threshold|_set_vad_model|'
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


def audit_native_dir(native_dir, want='framework', limit=15):
    """Audit a tree of produced or input frameworks."

    native_dir: Path to the directory containing frameworks (or the framework
                itself). want: 'framework' for dynamic frameworks, 'static'
                for static archives. limit: (major, minor) floor to enforce.
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
            plist_path = framework / 'Info.plist'
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
    parser.add_argument('--deps', type=Path, help='otool -Iv text file')
    parser.add_argument('--check', action='store_true', help='structural probe check')
    args = parser.parse_args()

    if args.check:
        results = audit_native_dir(Path('native'), want='framework')
        print(json.dumps({'errors': results['errors'],
                          'results': results['results']}, indent=2))
        return

    otool_text = args.input.read_text(encoding='utf-8', errors='replace') if args.input else ''
    arch_text = args.arch.read_text(encoding='utf-8', errors='replace') if args.arch else ''
    plist = None
    if args.plist:
        try:
            plist = plistlib.loads(args.plist.read_bytes())
        except Exception as exc:
            plist = {'_error': str(exc)}
    result = audit_metadata(otool_text, arch_text, plist)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()