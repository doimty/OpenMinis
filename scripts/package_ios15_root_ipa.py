#!/usr/bin/env python3
"""Package the explicitly compiled, isolated TrollStore Minis Root candidate.

This is intentionally separate from the standard/file-only packager. It embeds
one fixed-operation helper, verifies each signature and the iOS 15 native floor,
and records device privileges as UNVERIFIED. It does not install anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

from package_ios15_ipa import (
    APP_NAME, NORMAL_BUNDLE_ID, PackageError, audit_staged_native_floor,
    chmod_executable, entitlement_values_equal, is_macho, load_plist,
    plugin_bundles, resolve_app, run_codesign, sha256_file, signed_entitlements,
    strip_incompatible_plugins, validate_app, write_bundle_info,
)

SCRIPTS = Path(__file__).resolve().parent
ROOT_ID = "com.openminis.app.root"
ROOT_GROUP = "group.com.openminis.app.root"
HELPER_NAME = "minis-rootprobe"
SCHEMA = 1
ROOT_ENTITLEMENTS = SCRIPTS / "ios15-root.entitlements"
SHARE_ENTITLEMENTS = SCRIPTS / "ios15-root-share.entitlements"
HELPER_ENTITLEMENTS = SCRIPTS / "ios15-root-helper.entitlements"


def expected_entitlements(role: str) -> dict:
    if role == "helper":
        return {"get-task-allow": False,
                "com.apple.private.security.no-sandbox": True,
                "platform-application": True}
    if role not in {"app", "share"}:
        raise PackageError(f"unknown root signature role: {role}")
    values = {
        "application-identifier": ROOT_ID + (".ShareExtension" if role == "share" else ""),
        "com.apple.security.application-groups": [ROOT_GROUP],
        "keychain-access-groups": [ROOT_ID],
        "get-task-allow": False,
    }
    if role == "app":
        values.update({"com.apple.private.security.no-sandbox": True,
                       "platform-application": True,
                       "com.apple.private.security.storage.AppDataContainers": True,
                       "com.apple.private.persona-mgmt": True})
    return values


def validate_templates() -> None:
    for role, path in (("app", ROOT_ENTITLEMENTS), ("share", SHARE_ENTITLEMENTS),
                       ("helper", HELPER_ENTITLEMENTS)):
        if not entitlement_values_equal(load_plist(path), expected_entitlements(role)):
            raise PackageError(f"{role} entitlements differ from the explicit root profile")


def validate_build_identity(commit: str, run_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PackageError("source commit must be a full lowercase Git SHA")
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise PackageError("Actions run ID must be a positive ASCII decimal string")


def prepare_root_identity(app: Path, commit: str, run_id: str) -> dict:
    info = validate_app(app)
    if info.get("CFBundleIdentifier") != NORMAL_BUNDLE_ID:
        raise PackageError("root packaging requires the unchanged build identity com.openminis.app")
    info = dict(info)
    for key in ("MinisReentryDiagnostics", "MinisDiagnosticCommit", "MinisHostFileProbeBuild"):
        info.pop(key, None)
    info.update({
        "CFBundleIdentifier": ROOT_ID,
        "CFBundleName": "Minis Root",
        "CFBundleDisplayName": "Minis Root",
        "MinisFileAccessProfile": "root-helper",
        "MinisRootManagementEnabled": True,
        "MinisRootManagementSchema": SCHEMA,
        "MinisRootBuildCommit": commit,
        "MinisRootActionsRunID": run_id,
        "MinisHostFileProbeBuild": {"schema": 1, "source_commit": commit,
                                   "actions_run_id": run_id},
    })
    # Keep provider callbacks while removing other variants' share handoffs.
    urls = []
    for original in info.get("CFBundleURLTypes", []):
        entry = dict(original)
        schemes = [scheme for scheme in entry.get("CFBundleURLSchemes", [])
                   if str(scheme).lower() not in {"minis", "minis-broadfiles", "minis-root"}]
        if schemes:
            entry["CFBundleURLSchemes"] = schemes
            urls.append(entry)
    urls.append({"CFBundleURLName": "Minis Root", "CFBundleURLSchemes": ["minis-root"]})
    info["CFBundleURLTypes"] = urls
    write_bundle_info(app, info)
    for plugin in plugin_bundles(app):
        if plugin.name != "MinisShare.appex":
            raise PackageError(f"root IPA only retains MinisShare.appex, not {plugin.name}")
        plugin_info = load_plist(plugin / "Info.plist")
        if not str(plugin_info.get("CFBundleIdentifier", "")).startswith(NORMAL_BUNDLE_ID + "."):
            raise PackageError("unrecognized root share source identity")
        plugin_info.update({"CFBundleIdentifier": ROOT_ID + ".ShareExtension",
                            "MinisFileAccessProfile": "root-helper"})
        write_bundle_info(plugin, plugin_info)
    return info


def verify_compiled_client(app: Path) -> None:
    xcrun = shutil.which("xcrun")
    if not xcrun:
        raise PackageError("xcrun is required to verify the compiled root client")
    # Debug Xcode puts App code in Minis.debug.dylib; Release puts it in Minis.
    for binary in (app / "Minis", app / "Minis.debug.dylib"):
        if not is_macho(binary):
            continue
        result = subprocess.run([xcrun, "nm", "-gU", str(binary)],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise PackageError(f"cannot inspect root client symbols: {binary.name}")
        if re.search(r"\bT\s+_OMRootManagementClientVersion\s*$", result.stdout, re.MULTILINE):
            return
    raise PackageError("App was not compiled with the native root management client")


def sign_and_verify(app: Path) -> dict:
    codesign = shutil.which("codesign")
    if not codesign:
        raise PackageError("codesign is required for the Root candidate")
    helper = app / HELPER_NAME
    binaries = sorted((p for p in app.rglob("*") if is_macho(p)),
                      key=lambda p: (len(p.parts), str(p)), reverse=True)
    for binary in binaries:
        chmod_executable(binary)
        extra: list[str] = []
        if binary == helper:
            extra = ["--entitlements", str(HELPER_ENTITLEMENTS)]
        elif binary.parent.suffix == ".appex":
            extra = ["--entitlements", str(SHARE_ENTITLEMENTS)]
        run_codesign(codesign, binary, extra)
    bundles = sorted([*app.rglob("*.framework"), *app.rglob("*.appex"), app],
                     key=lambda p: (len(p.parts), str(p)), reverse=True)
    for bundle in bundles:
        extra = (["--entitlements", str(ROOT_ENTITLEMENTS)] if bundle == app else
                 ["--entitlements", str(SHARE_ENTITLEMENTS)] if bundle.suffix == ".appex" else [])
        run_codesign(codesign, bundle, extra)
    verified: dict = {}
    targets = [("app", app), ("helper", helper)] + [("share", p) for p in plugin_bundles(app)]
    for role, target in targets:
        result = subprocess.run([codesign, "--verify", "--strict", str(target)],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise PackageError(f"{role} signature verification failed: {result.stderr.strip()}")
        values = signed_entitlements(codesign, target)
        if not entitlement_values_equal(values, expected_entitlements(role)):
            raise PackageError(f"signed {role} entitlements differ from the root profile")
        verified[str(target.relative_to(app))] = values
    result = subprocess.run([codesign, "--verify", "--deep", "--strict", str(app)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise PackageError(f"root bundle deep verification failed: {result.stderr.strip()}")
    return verified


def zip_root_payload(stage: Path, ipa: Path) -> None:
    """Record the helper as a Unix executable, including in portable fixtures."""
    payload = stage / "Payload"
    if not (payload / APP_NAME / HELPER_NAME).is_file():
        raise PackageError("root staging tree is missing its helper")
    with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(payload.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(stage).as_posix()
            if name == f"Payload/{APP_NAME}/{HELPER_NAME}":
                info = zipfile.ZipInfo.from_file(path, name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
            else:
                archive.write(path, arcname=name)


def verify_root_archive(ipa: Path, info: dict, helper_sha256: str) -> None:
    with zipfile.ZipFile(ipa) as archive:
        if archive.testzip() is not None:
            raise PackageError("Root IPA ZIP CRC failed")
        stored_info = plistlib.loads(archive.read(f"Payload/{APP_NAME}/Info.plist"))
        if stored_info != info:
            raise PackageError("Root IPA Info.plist differs from the signed staging bundle")
        helper_path = f"Payload/{APP_NAME}/{HELPER_NAME}"
        members = [member for member in archive.infolist() if member.filename == helper_path]
        if len(members) != 1:
            raise PackageError("Root IPA must contain exactly one helper member")
        stored_helper = members[0]
        mode = stored_helper.external_attr >> 16
        if stored_helper.create_system != 3 or not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o755:
            raise PackageError("Root helper executable mode was not preserved in the IPA")
        if hashlib.sha256(archive.read(stored_helper)).hexdigest() != helper_sha256:
            raise PackageError("Root IPA helper differs from the signed staging binary")


def package_root_app(source_app: Path, helper: Path, output_dir: Path, *,
                     commit: str, run_id: str) -> dict:
    validate_build_identity(commit, run_id)
    validate_templates()
    if sys.platform != "darwin":
        raise PackageError("Root IPA packaging requires macOS signing and native audits")
    validate_app(source_app)
    if not is_macho(helper) or helper.is_symlink():
        raise PackageError("root helper must be a regular Mach-O executable")
    verify_compiled_client(source_app)
    output_dir = output_dir.resolve()
    if output_dir == source_app.resolve() or source_app.resolve() in output_dir.parents:
        raise PackageError("output directory must be outside the source App")
    if (output_dir / "manifest.json").exists():
        raise PackageError("use a fresh output directory for each Root build")
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / "stage"
    if stage.exists():
        raise PackageError("root staging directory already exists")
    app = stage / "Payload" / APP_NAME
    shutil.copytree(source_app, app, symlinks=False)
    kept, stripped = strip_incompatible_plugins(app)
    info = prepare_root_identity(app, commit, run_id)
    source_helper_sha = sha256_file(helper)
    staged_helper = app / HELPER_NAME
    if staged_helper.exists():
        raise PackageError("source App unexpectedly already contains a root helper")
    shutil.copy2(helper, staged_helper)
    staged_helper.chmod(0o755)  # Persona spawning, deliberately no setuid bit.
    native_floor = audit_staged_native_floor(app)
    if not any(item["path"] == HELPER_NAME for item in native_floor):
        raise PackageError("native-floor audit did not cover the helper")
    signatures = sign_and_verify(app)
    signed_helper_sha = sha256_file(staged_helper)
    name = f"Minis-{info.get('CFBundleShortVersionString', 'unknown')}-ios15-root-{commit[:8]}.ipa"
    ipa = output_dir / name
    zip_root_payload(stage, ipa)
    verify_root_archive(ipa, info, signed_helper_sha)
    manifest = {
        "bundle_id": ROOT_ID, "app_group_id": ROOT_GROUP,
        "minimum_os": info.get("MinimumOSVersion"),
        "marketing_version": info.get("CFBundleShortVersionString"),
        "file_access_profile": "root-helper", "purpose": "TrollStore root-helper candidate; device acceptance required",
        "source_commit": commit, "actions_run_id": run_id, "root_management_schema": SCHEMA,
        "host_file_probe_build": info["MinisHostFileProbeBuild"],
        "root_helper_included": True, "root_helper_path": HELPER_NAME,
        "root_helper_source_binary_sha256": source_helper_sha,
        "root_helper_signed_sha256": signed_helper_sha,
        "root_helper_archive_sha256": signed_helper_sha,
        "root_helper_operations": ["identity", "prepare", "check", "cleanup"],
        "root_helper_mode": "0755", "privilege_mechanism": "TrollStore persona 99 uid/gid 0",
        "main_app_runs_as_root": False, "root_runtime_verified": False,
        "runtime_file_access_verified": False, "device_runtime_verified": False,
        "signed": True, "signature_entitlements_verified": True,
        "signature_entitlements": signatures, "native_floor_audit": native_floor,
        "plugins_kept": kept, "plugins_stripped": stripped,
        "ipa_name": name, "ipa_bytes": ipa.stat().st_size, "ipa_sha256": sha256_file(ipa),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--derived-data", type=Path)
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--actions-run-id", required=True)
    args = parser.parse_args()
    try:
        source = resolve_app(args.app, args.derived_data, args.configuration)
        result = package_root_app(source, args.helper, args.output_dir,
                                  commit=args.source_commit, run_id=args.actions_run_id)
    except (PackageError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
