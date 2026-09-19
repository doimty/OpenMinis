#!/usr/bin/env python3
"""Package an unsigned/ad-hoc iOS 15 Minis.app as a TrollStore IPA.

The compile probe already builds with CODE_SIGNING_ALLOWED=NO. This script
copies that .app, drops embedded appex whose MinimumOSVersion is newer than
15.0 (FileProvider 16.0, Agent Widget 16.2), optionally ad-hoc signs on
Darwin, and zips Payload/Minis.app.

It never mutates the source bundle. Linux fixture tests use --skip-sign.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

APP_NAME = "Minis.app"
APP_EXECUTABLE = "Minis"
PRODUCT_FLOOR = (15, 0, 0)
DEFAULT_ENTITLEMENTS = Path(__file__).resolve().parent / "ios15-trollstore.entitlements"
SHARE_ENTITLEMENTS = (
    Path(__file__).resolve().parent.parent
    / "src/ios/ShareExtension/ShareExtension.entitlements"
)
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}


class PackageError(Exception):
    pass


def parse_version(value: str) -> tuple[int, int, int]:
    text = str(value).strip()
    if not text:
        raise PackageError("empty version string")
    parts = text.split(".")
    numbers = []
    for part in parts[:3]:
        if not part.isdigit():
            raise PackageError(f"invalid version {value!r}")
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def load_plist(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except FileNotFoundError as exc:
        raise PackageError(f"missing Info.plist: {path}") from exc
    except Exception as exc:
        raise PackageError(f"unreadable Info.plist: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"Info.plist is not a dictionary: {path}")
    return data


def bundle_minimum_os(info: dict) -> str | None:
    value = info.get("MinimumOSVersion") or info.get("LSMinimumOSVersion")
    if value is None:
        return None
    return str(value)


def is_macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in MACHO_MAGICS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_app(app: Path | None, derived_data: Path | None, configuration: str) -> Path:
    if app is not None:
        path = app.resolve()
        if not path.is_dir():
            raise PackageError(f"app bundle is not a directory: {path}")
        return path
    if derived_data is None:
        raise PackageError("pass --app or --derived-data")
    direct = derived_data / "Build" / "Products" / f"{configuration}-iphoneos" / APP_NAME
    if direct.is_dir():
        return direct.resolve()
    matches = [
        path.resolve()
        for path in derived_data.glob(f"**/Build/Products/{configuration}-iphoneos/{APP_NAME}")
        if path.is_dir() and "iphonesimulator" not in path.parts
    ]
    unique = []
    seen = set()
    for match in matches:
        if match not in seen:
            seen.add(match)
            unique.append(match)
    if len(unique) == 1:
        return unique[0]
    if not unique:
        raise PackageError(
            f"Minis.app not found under {derived_data} "
            f"for {configuration}-iphoneos"
        )
    raise PackageError(f"multiple Minis.app candidates: {unique}")


def validate_app(app: Path) -> dict:
    info_path = app / "Info.plist"
    info = load_plist(info_path)
    minimum = bundle_minimum_os(info)
    if minimum is None:
        raise PackageError(f"{app.name} Info.plist has no MinimumOSVersion")
    if parse_version(minimum) > PRODUCT_FLOOR:
        raise PackageError(
            f"{app.name} MinimumOSVersion {minimum} is newer than 15.0"
        )
    executable = str(info.get("CFBundleExecutable") or APP_EXECUTABLE)
    binary = app / executable
    if not binary.is_file():
        raise PackageError(f"missing executable {binary}")
    if binary.stat().st_size <= 0:
        raise PackageError(f"empty executable {binary}")
    return info


def plugin_bundles(app: Path) -> list[Path]:
    found = []
    for folder in ("PlugIns", "Extensions"):
        root = app / folder
        if not root.is_dir():
            continue
        found.extend(sorted(path for path in root.glob("*.appex") if path.is_dir()))
    return found


def strip_incompatible_plugins(app: Path) -> tuple[list[str], list[dict]]:
    kept: list[str] = []
    stripped: list[dict] = []
    for plugin in plugin_bundles(app):
        info = load_plist(plugin / "Info.plist")
        minimum = bundle_minimum_os(info)
        if minimum is not None and parse_version(minimum) > PRODUCT_FLOOR:
            shutil.rmtree(plugin)
            stripped.append({"name": plugin.name, "minimum_os": minimum})
            continue
        kept.append(plugin.name)
    return kept, stripped


def chmod_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def adhoc_sign(app: Path, entitlements: Path | None) -> None:
    if os.uname().sysname != "Darwin":
        raise PackageError("ad-hoc codesign requires Darwin; pass --skip-sign")
    codesign = shutil.which("codesign")
    if not codesign:
        raise PackageError("codesign not found")
    binaries: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(app):
        directory = Path(dirpath)
        dirnames[:] = [name for name in dirnames if name not in {".DS_Store"}]
        for name in filenames:
            candidate = directory / name
            if is_macho(candidate):
                binaries.append(candidate)
    binaries.sort(key=lambda path: (len(path.parts), str(path)), reverse=True)
    bundles = sorted(
        list(app.rglob("*.framework")) + list(app.rglob("*.appex")) + [app],
        key=lambda path: (len(path.parts), str(path)),
        reverse=True,
    )
    for binary in binaries:
        chmod_executable(binary)
        extra: list[str] = []
        if binary.parent.suffix == ".appex" and SHARE_ENTITLEMENTS.is_file():
            extra = ["--entitlements", str(SHARE_ENTITLEMENTS)]
        run_codesign(codesign, binary, extra)
    for bundle in bundles:
        extra = []
        if bundle == app and entitlements is not None:
            extra = ["--entitlements", str(entitlements)]
        elif bundle.suffix == ".appex" and SHARE_ENTITLEMENTS.is_file():
            extra = ["--entitlements", str(SHARE_ENTITLEMENTS)]
        run_codesign(codesign, bundle, extra)


def run_codesign(codesign: str, target: Path, extra: list[str]) -> None:
    command = [codesign, "--force", "--sign", "-", "--timestamp=none", *extra, str(target)]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise PackageError(
            f"codesign failed for {target}: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def zip_payload(stage: Path, ipa: Path) -> None:
    payload = stage / "Payload"
    if not (payload / APP_NAME).is_dir():
        raise PackageError("staging tree is missing Payload/Minis.app")
    if ipa.exists():
        ipa.unlink()
    with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, arcname=str(path.relative_to(stage).as_posix()))


def default_ipa_name(info: dict) -> str:
    version = str(info.get("CFBundleShortVersionString") or "unknown")
    return f"Minis-{version}-ios15-trollstore.ipa"


def package_app(
    source_app: Path,
    output_dir: Path,
    *,
    skip_sign: bool = False,
    keep_incompatible_plugins: bool = False,
    entitlements: Path | None = None,
    ipa_name: str | None = None,
) -> dict:
    info = validate_app(source_app)
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    staged_app = stage / "Payload" / APP_NAME
    shutil.copytree(source_app, staged_app, symlinks=False)
    if keep_incompatible_plugins:
        kept = [path.name for path in plugin_bundles(staged_app)]
        stripped: list[dict] = []
    else:
        kept, stripped = strip_incompatible_plugins(staged_app)
    validate_app(staged_app)
    signed = False
    if not skip_sign:
        entitlement_path = entitlements or DEFAULT_ENTITLEMENTS
        if not entitlement_path.is_file():
            raise PackageError(f"entitlements file not found: {entitlement_path}")
        adhoc_sign(staged_app, entitlement_path)
        signed = True
    name = ipa_name or default_ipa_name(info)
    ipa = output_dir / name
    zip_payload(stage, ipa)
    if ipa.stat().st_size <= 0:
        raise PackageError("IPA is empty")
    with zipfile.ZipFile(ipa) as archive:
        names = set(archive.namelist())
    executable = str(info.get("CFBundleExecutable") or APP_EXECUTABLE)
    required = {
        f"Payload/{APP_NAME}/Info.plist",
        f"Payload/{APP_NAME}/{executable}",
    }
    missing = sorted(required - names)
    if missing:
        raise PackageError(f"IPA is missing {missing}")
    manifest = {
        "bundle_id": info.get("CFBundleIdentifier"),
        "executable": executable,
        "minimum_os": bundle_minimum_os(info),
        "marketing_version": info.get("CFBundleShortVersionString"),
        "ipa_name": ipa.name,
        "ipa_bytes": ipa.stat().st_size,
        "ipa_sha256": sha256_file(ipa),
        "signed": signed,
        "plugins_kept": kept,
        "plugins_stripped": stripped,
        "purpose": "TrollStore / jailbreak sideload",
        "source_app": str(source_app),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--derived-data", type=Path)
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-sign", action="store_true")
    parser.add_argument("--keep-incompatible-plugins", action="store_true")
    parser.add_argument("--entitlements", type=Path)
    parser.add_argument("--ipa-name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = resolve_app(args.app, args.derived_data, args.configuration)
        package_app(
            app,
            args.output_dir,
            skip_sign=args.skip_sign,
            keep_incompatible_plugins=args.keep_incompatible_plugins,
            entitlements=args.entitlements,
            ipa_name=args.ipa_name,
        )
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
