#!/usr/bin/env python3
"""Package the explicitly compiled, isolated Minis Root candidate.

This is intentionally separate from the standard/file-only packager. It embeds
one fixed-operation helper and emits iOS 15.0, 16.0 or 16.2 installation tiers.
Core images stay at iOS 15.0; each retained extension has its own native floor.
Signatures and archive contents are verified; device privileges remain UNVERIFIED.
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
from pathlib import Path, PurePosixPath

from package_ios15_ipa import (
    APP_NAME, MACHO_MAGICS, NORMAL_BUNDLE_ID, PackageError, audit_staged_native_floor,
    bundle_minimum_os,
    chmod_executable, entitlement_values_equal, is_macho, load_plist,
    parse_version, plugin_bundles, resolve_app, run_codesign, sha256_file, signed_entitlements,
    strip_incompatible_plugins, validate_app, write_bundle_info,
)

SCRIPTS = Path(__file__).resolve().parent
ROOT_ID = "com.openminis.app.root"
ROOT_GROUP = "group.com.openminis.app.root"
HELPER_NAME = "minis-rootprobe"
SCHEMA = 1
ROOT_ENTITLEMENTS = SCRIPTS / "ios15-root.entitlements"
SHARE_ENTITLEMENTS = SCRIPTS / "ios15-root-share.entitlements"
FILEPROVIDER_ENTITLEMENTS = SCRIPTS / "ios15-root-fileprovider.entitlements"
WIDGET_ENTITLEMENTS = SCRIPTS / "ios15-root-widget.entitlements"
HELPER_ENTITLEMENTS = SCRIPTS / "ios15-root-helper.entitlements"
ROOT_PLUGIN_ROLES = {
    "MinisShare.appex": "share",
    "MinisFileProvider.appex": "fileprovider",
    "AgentWidgetExtension.appex": "widget",
}
ROOT_PRODUCT_FLOORS = {
    "15.0": (15, 0, 0),
    "16.0": (16, 0, 0),
    "16.2": (16, 2, 0),
}
ROOT_ROLE_FLOORS = {
    "app": (15, 0, 0),
    "helper": (15, 0, 0),
    "share": (15, 0, 0),
    "fileprovider": (16, 0, 0),
    "widget": (16, 2, 0),
}
ROOT_ROLE_SUFFIXES = {
    "app": "",
    "share": ".ShareExtension",
    "fileprovider": ".FileProvider",
    "widget": ".AgentWidget",
}
ROOT_EXTENSION_POINTS = {
    "share": "com.apple.share-services",
    "fileprovider": "com.apple.fileprovider-nonui",
    "widget": "com.apple.widgetkit-extension",
}


def validated_plugins(app: Path, minimum_os: str, *, complete: bool = False) -> dict[Path, str]:
    if minimum_os not in ROOT_PRODUCT_FLOORS:
        raise PackageError(f"unsupported Root package floor: {minimum_os}")
    roles = {}
    for plugin in plugin_bundles(app):
        role = ROOT_PLUGIN_ROLES.get(plugin.name)
        if role is None:
            raise PackageError(f"unrecognized app extension in Root IPA: {plugin.name}")
        if role in roles.values():
            raise PackageError(f"duplicate Root extension role: {role}")
        plugin_info = load_plist(plugin / "Info.plist")
        minimum = bundle_minimum_os(plugin_info)
        if minimum is None or parse_version(minimum) != ROOT_ROLE_FLOORS[role]:
            raise PackageError(f"{role} extension must preserve its declared deployment floor")
        if plugin_info.get("CFBundleIdentifier") != NORMAL_BUNDLE_ID + ROOT_ROLE_SUFFIXES[role]:
            raise PackageError(f"unrecognized {role} extension source identity")
        extension = plugin_info.get("NSExtension")
        if not isinstance(extension, dict) or extension.get("NSExtensionPointIdentifier") != ROOT_EXTENSION_POINTS[role]:
            raise PackageError(f"invalid {role} extension metadata")
        roles[plugin] = role
    if complete:
        required = {role for role in ROOT_PLUGIN_ROLES.values()
                    if ROOT_ROLE_FLOORS[role] <= ROOT_PRODUCT_FLOORS[minimum_os]}
        missing = required - set(roles.values())
        if missing:
            raise PackageError(f"Root package is missing compatible extensions: {', '.join(sorted(missing))}")
    return roles


def expected_entitlements(role: str) -> dict:
    if role == "helper":
        return {"get-task-allow": False,
                "com.apple.private.security.no-sandbox": True,
                "platform-application": True}
    if role not in {"app", "share", "fileprovider", "widget"}:
        raise PackageError(f"unknown root signature role: {role}")
    bundle_suffix = ROOT_ROLE_SUFFIXES[role]
    values = {
        "application-identifier": ROOT_ID + bundle_suffix,
        "com.apple.security.application-groups": [ROOT_GROUP],
        "get-task-allow": False,
    }
    if role in {"app", "share"}:
        values["keychain-access-groups"] = [ROOT_ID]
    if role == "app":
        values.update({"com.apple.private.security.no-sandbox": True,
                       "platform-application": True,
                       "com.apple.private.security.storage.AppDataContainers": True,
                       "com.apple.private.persona-mgmt": True})
    return values


def validate_templates() -> None:
    for role, path in (("app", ROOT_ENTITLEMENTS), ("share", SHARE_ENTITLEMENTS),
                       ("fileprovider", FILEPROVIDER_ENTITLEMENTS),
                       ("widget", WIDGET_ENTITLEMENTS),
                       ("helper", HELPER_ENTITLEMENTS)):
        if not entitlement_values_equal(load_plist(path), expected_entitlements(role)):
            raise PackageError(f"{role} entitlements differ from the explicit root profile")


def validate_build_identity(commit: str, run_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PackageError("source commit must be a full lowercase Git SHA")
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise PackageError("Actions run ID must be a positive ASCII decimal string")


def prepare_root_identity(app: Path, commit: str, run_id: str, minimum_os: str = "15.0") -> dict:
    validate_build_identity(commit, run_id)
    roles = validated_plugins(app, minimum_os)
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
        "MinimumOSVersion": minimum_os,
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
    for plugin, role in roles.items():
        plugin_info = load_plist(plugin / "Info.plist")
        original_suffix = ROOT_ROLE_SUFFIXES[role]
        plugin_info["CFBundleIdentifier"] = ROOT_ID + original_suffix
        if role == "share":
            plugin_info["MinisFileAccessProfile"] = "root-helper"
        if role == "fileprovider":
            extension = dict(plugin_info["NSExtension"])
            extension["NSExtensionFileProviderDocumentGroup"] = ROOT_GROUP
            plugin_info["NSExtension"] = extension
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
    plugin_roles = {plugin: ROOT_PLUGIN_ROLES[plugin.name] for plugin in plugin_bundles(app)}
    entitlement_paths = {
        "app": ROOT_ENTITLEMENTS,
        "share": SHARE_ENTITLEMENTS,
        "fileprovider": FILEPROVIDER_ENTITLEMENTS,
        "widget": WIDGET_ENTITLEMENTS,
        "helper": HELPER_ENTITLEMENTS,
    }

    def role_for_path(path: Path) -> str | None:
        if path == app:
            return "app"
        if path == helper:
            return "helper"
        if path in plugin_roles:
            return plugin_roles[path]
        for plugin, role in plugin_roles.items():
            plugin_info = load_plist(plugin / "Info.plist")
            if path == plugin / str(plugin_info.get("CFBundleExecutable", "")):
                return role
            if path == plugin / (str(plugin_info.get("CFBundleExecutable", "")) + ".debug.dylib"):
                return role
        return None

    binaries = sorted((p for p in app.rglob("*") if is_macho(p)),
                      key=lambda p: (len(p.parts), str(p)), reverse=True)
    for binary in binaries:
        chmod_executable(binary)
        role = role_for_path(binary)
        extra = ["--entitlements", str(entitlement_paths[role])] if role else []
        run_codesign(codesign, binary, extra)
    bundles = sorted([*app.rglob("*.framework"), *app.rglob("*.appex"), app],
                     key=lambda p: (len(p.parts), str(p)), reverse=True)
    for bundle in bundles:
        role = role_for_path(bundle)
        extra = ["--entitlements", str(entitlement_paths[role])] if role else []
        run_codesign(codesign, bundle, extra)
    verified: dict = {}
    targets = [("app", app), ("helper", helper)] + [
        (ROOT_PLUGIN_ROLES[p.name], p) for p in plugin_bundles(app)
    ]
    for role, target in targets:
        result = subprocess.run([codesign, "--verify", "--strict", str(target)],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise PackageError(f"{role} signature verification failed: {result.stderr.strip()}")
        values = signed_entitlements(codesign, target)
        if not entitlement_values_equal(values, expected_entitlements(role)):
            raise PackageError(f"signed {role} entitlements differ from the root profile")
        verified[target.relative_to(app).as_posix()] = values
    result = subprocess.run([codesign, "--verify", "--deep", "--strict", str(app)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise PackageError(f"root bundle deep verification failed: {result.stderr.strip()}")
    return verified


def declared_executables(app: Path) -> set[Path]:
    paths = {app / HELPER_NAME}
    for info_path in app.rglob("Info.plist"):
        bundle = info_path.parent
        if bundle != app and bundle.suffix not in {".appex", ".framework", ".bundle"}:
            continue
        info = load_plist(info_path)
        name = info.get("CFBundleExecutable")
        if name is None:
            continue
        if not isinstance(name, str) or not name or PurePosixPath(name).name != name or "\\" in name:
            raise PackageError(f"invalid bundle executable name: {info_path}")
        executable = bundle / name
        if not executable.is_file() or executable.is_symlink():
            raise PackageError(f"missing regular bundle executable: {executable}")
        paths.add(executable)
    return paths


def executable_hashes(app: Path) -> dict[str, str]:
    declared = declared_executables(app)
    return {path.relative_to(app).as_posix(): sha256_file(path)
            for path in sorted(app.rglob("*"))
            if path.is_file() and (path in declared or is_macho(path) or path.stat().st_mode & 0o111)}


def zip_root_payload(stage: Path, ipa: Path) -> None:
    """Record all native and declared executables as regular Unix 0755 files."""
    payload = stage / "Payload"
    app = payload / APP_NAME
    if not (app / HELPER_NAME).is_file():
        raise PackageError("root staging tree is missing its helper")
    executables = set(executable_hashes(app))
    with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_symlink():
                raise PackageError(f"Root staging tree contains a symlink: {path}")
            if not path.is_file():
                continue
            name = path.relative_to(stage).as_posix()
            mode = 0o755 if path.relative_to(app).as_posix() in executables else 0o644
            info = zipfile.ZipInfo.from_file(path, name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as source, archive.open(info, "w") as destination:
                shutil.copyfileobj(source, destination)


def verify_root_archive(
    ipa: Path, info: dict, helper_sha256: str, *,
    expected_executables: dict[str, str] | None = None,
    expected_plugins: dict[str, dict] | None = None,
) -> None:
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
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise PackageError("Root IPA contains duplicate archive members")
        declared = {f"Payload/{APP_NAME}/{info['CFBundleExecutable']}", helper_path}
        plugin_infos = {}
        prefix = f"Payload/{APP_NAME}/"
        for name in names:
            path = PurePosixPath(name)
            if not name.startswith(prefix) or ".." in path.parts or "\\" in name:
                raise PackageError(f"invalid Root archive path: {name}")
            if path.name != "Info.plist":
                continue
            bundle = path.parent
            if bundle.suffix not in {".appex", ".framework", ".bundle"}:
                continue
            bundle_info = plistlib.loads(archive.read(name))
            executable = bundle_info.get("CFBundleExecutable")
            if executable:
                if not isinstance(executable, str) or PurePosixPath(executable).name != executable or "\\" in executable:
                    raise PackageError(f"invalid archived executable name: {name}")
                declared.add(f"{bundle.as_posix()}/{executable}")
            if bundle.suffix == ".appex":
                plugin_infos[bundle.relative_to(PurePosixPath(prefix)).as_posix()] = bundle_info
        if not declared <= set(names):
            raise PackageError("Root IPA is missing a declared executable")
        observed = {}
        for member in archive.infolist():
            with archive.open(member) as source:
                head = source.read(4)
                digest = hashlib.sha256(head)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            native = head in MACHO_MAGICS
            executable = member.filename in declared or native or member.external_attr >> 16 & 0o111
            if not executable:
                continue
            mode = member.external_attr >> 16
            if member.create_system != 3 or not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o755:
                raise PackageError(f"Root executable mode was not preserved in the IPA: {member.filename}")
            observed[member.filename[len(prefix):]] = digest.hexdigest()
        if expected_executables is not None and observed != expected_executables:
            raise PackageError("Root IPA executables differ from the signed staging binaries")
        if expected_plugins is not None and plugin_infos != expected_plugins:
            raise PackageError("Root IPA extension metadata differs from the signed staging bundles")


def package_root_app(source_app: Path, helper: Path, output_dir: Path, *,
                     commit: str, run_id: str, minimum_os: str = "15.0") -> dict:
    validate_build_identity(commit, run_id)
    validate_templates()
    if minimum_os not in ROOT_PRODUCT_FLOORS:
        raise PackageError(f"unsupported Root package floor: {minimum_os}")
    product_floor = ROOT_PRODUCT_FLOORS[minimum_os]
    if sys.platform != "darwin":
        raise PackageError("Root IPA packaging requires macOS signing and native audits")
    validate_app(source_app)
    validated_plugins(source_app, minimum_os, complete=True)
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
    kept, stripped = strip_incompatible_plugins(app, product_floor)
    info = prepare_root_identity(app, commit, run_id, minimum_os)
    source_helper_sha = sha256_file(helper)
    staged_helper = app / HELPER_NAME
    if staged_helper.exists():
        raise PackageError("source App unexpectedly already contains a root helper")
    shutil.copy2(helper, staged_helper)
    staged_helper.chmod(0o755)  # Persona spawning, deliberately no setuid bit.
    extension_floors = {plugin.relative_to(app).as_posix(): ROOT_ROLE_FLOORS[ROOT_PLUGIN_ROLES[plugin.name]]
                        for plugin in plugin_bundles(app)}
    native_floor = audit_staged_native_floor(app, extension_floors=extension_floors)
    if not any(item["path"] == HELPER_NAME for item in native_floor):
        raise PackageError("native-floor audit did not cover the helper")
    signatures = sign_and_verify(app)
    signed_helper_sha = sha256_file(staged_helper)
    archive_executables = executable_hashes(app)
    archive_plugins = {plugin.relative_to(app).as_posix(): load_plist(plugin / "Info.plist")
                       for plugin in plugin_bundles(app)}
    floor_name = "15" if minimum_os == "15.0" else minimum_os
    name = f"Minis-{info.get('CFBundleShortVersionString', 'unknown')}-ios{floor_name}-root-{commit[:8]}.ipa"
    ipa = output_dir / name
    zip_root_payload(stage, ipa)
    verify_root_archive(ipa, info, signed_helper_sha,
                        expected_executables=archive_executables,
                        expected_plugins=archive_plugins)
    manifest = {
        "bundle_id": ROOT_ID, "app_group_id": ROOT_GROUP,
        "minimum_os": info.get("MinimumOSVersion"),
        "marketing_version": info.get("CFBundleShortVersionString"),
        "file_access_profile": "root-helper", "purpose": "TrollStore root-helper candidate; device acceptance required",
        "source_commit": commit, "actions_run_id": run_id, "root_management_schema": SCHEMA,
        "package_floor": minimum_os,
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
        "archive_executable_hashes": archive_executables,
        "archive_executable_modes_verified": True,
        "extension_metadata_verified": True,
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
    parser.add_argument("--minimum-os", choices=tuple(ROOT_PRODUCT_FLOORS), default="15.0")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--actions-run-id", required=True)
    args = parser.parse_args()
    try:
        source = resolve_app(args.app, args.derived_data, args.configuration)
        result = package_root_app(source, args.helper, args.output_dir,
                                  commit=args.source_commit, run_id=args.actions_run_id,
                                  minimum_os=args.minimum_os)
    except (PackageError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
