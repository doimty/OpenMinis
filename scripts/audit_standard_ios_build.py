#!/usr/bin/env python3
"""Audit an unmodified, non-Root Xcode iPhoneOS Release product and compiler log."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from package_ios15_ipa import (
    BROAD_FILE_KEYS, FORBIDDEN_PRIVILEGE_KEYS, NORMAL_BUNDLE_ID, PackageError, audit_staged_native_floor,
    is_macho, load_plist, parse_version, plugin_bundles, resolve_app, sha256_file,
)

IOS = Path(__file__).resolve().parents[1] / "src/ios"
TARGETS = {
    "Minis": ("", "15.0", "Minis.entitlements", None),
    "MinisShare": (".ShareExtension", "15.0", "ShareExtension/ShareExtension.entitlements", "com.apple.share-services"),
    "MinisFileProvider": (".FileProvider", "16.0", "FileProvider/FileProvider.entitlements", "com.apple.fileprovider-nonui"),
    "AgentWidgetExtension": (".AgentWidget", "16.2", "AgentWidget/AgentWidget.entitlements", "com.apple.widgetkit-extension"),
}
C_INPUTS = {"RootManagementClient.c", "DeviceMCPOffload.m", "AutomationMCPOffload.m", "HostFileAccessProbe.c", "ISHKernel.m"}
SWIFT_INPUTS = {"RootManagementAuthorization.swift", "RootManagementRunner.swift", "DeviceMCPNativeBridge.swift",
                "AutomationMCPController.swift"}
FEATURES = {"MINIS_ROOT_MANAGEMENT", "MINIS_HOST_FILE_PROBE"}
SYMBOLS = {"_OMRootManagementRun", "_OMRootManagementClientVersion", "_device_mcp_offload_register",
           "_automation_mcp_offload_register", "_OMHostFileProbeReadSample", "_OMHostFileProbeSamplePath",
           "_OBJC_CLASS_$_DeviceMCPNativeBridge", "_OBJC_CLASS_$_AutomationMCPBridge"}
# Xcode's scheme enables coverage even for Release. Clang emits this one
# runtime reference function in an otherwise empty translation unit. The
# Actions native guard probe verifies coverage-on/off objects and a real
# Root-enabled negative control against this same classifier.
COVERAGE_RUNTIME_SYMBOLS = {"___llvm_profile_runtime_user"}
MARKERS = {"MinisFileAccessProfile", "MinisRootManagementEnabled", "MinisRootManagementSchema",
           "MinisRootBuildCommit", "MinisRootActionsRunID", "MinisHostFileProbeBuild"}


def require(condition, message):
    if not condition:
        raise PackageError(message)


def _block(text: str, start: int) -> str:
    opening = text.find("{", start)
    require(opening >= 0, "malformed Xcode project object")
    depth, quoted, escaped, index = 0, False, False, opening
    while index < len(text):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening:index + 1]
        index += 1
    raise PackageError("unterminated Xcode project object")


def parse_project(path: Path) -> dict:
    """Parse only target/configuration objects from Xcode's OpenStep project."""
    text = path.read_text(encoding="utf-8")
    objects = {}
    target_lists = {}
    for match in re.finditer(r"([A-Za-z0-9]+) /\* Build configuration list for PBXNativeTarget \"([^\"]+)\" \*/ =", text):
        target_lists[match.group(2)] = match.group(1)
    for match in re.finditer(r"([A-Za-z0-9]+) /\* ([^*]+) \*/ =", text):
        identifier, comment = match.group(1), match.group(2).strip()
        if "Build configuration list for PBXNativeTarget" in comment:
            body = _block(text, match.end())
            ids = re.search(r"buildConfigurations = \((.*?)\);", body, re.S)
            objects[identifier] = {"buildConfigurations": re.findall(r"([A-Za-z0-9]+) /\*", ids.group(1) if ids else "")}
        elif "/* Debug */" in match.group(0) or "/* Release */" in match.group(0):
            body = _block(text, match.end())
            name_match = re.search(r"\bname = (Debug|Release);", body)
            settings_match = re.search(r"buildSettings = \{(.*?)\n\s*\};", body, re.S)
            settings = {}
            for setting in (settings_match.group(1).splitlines() if settings_match else []):
                setting_match = re.match(r"\s*([A-Za-z0-9_\[\]=.-]+) = (.*?);\s*$", setting)
                if setting_match:
                    value = setting_match.group(2).strip().strip('"')
                    settings[setting_match.group(1)] = value
            objects[identifier] = {"name": name_match.group(1) if name_match else comment,
                                   "buildSettings": settings}
    for target, config_list in target_lists.items():
        objects[target] = {"isa": "PBXNativeTarget", "name": target, "buildConfigurationList": config_list}
    return {"objects": objects}


def roles_and_settings(app: Path, project: dict) -> tuple[list[dict], dict]:
    bundles = {bundle.stem: bundle for bundle in plugin_bundles(app)}
    require(len(bundles) == len(plugin_bundles(app)) and set(bundles) == set(TARGETS) - {"Minis"},
            "standard product must contain exactly Share, File Provider and Widget")
    bundles["Minis"] = app
    require(not any(app.rglob("minis-rootprobe")), "standard product contains a Root helper")
    objects = project["objects"]
    evidence, extension_floors = [], {}
    for name, (suffix, minimum, entitlements_path, point) in TARGETS.items():
        bundle = bundles[name]
        info = load_plist(bundle / "Info.plist")
        require(info.get("CFBundleIdentifier") == NORMAL_BUNDLE_ID + suffix and
                info.get("CFBundleExecutable") == name and is_macho(bundle / name) and
                parse_version(info.get("MinimumOSVersion")) == parse_version(minimum), f"wrong {name} identity or floor")
        require(not MARKERS.intersection(info), f"Root or diagnostic marker found on {name}")
        if point:
            extension = info.get("NSExtension", {})
            require(extension.get("NSExtensionPointIdentifier") == point, f"incorrect {name} extension point")
            if name == "MinisFileProvider":
                require(extension.get("NSExtensionFileProviderDocumentGroup") == "group." + NORMAL_BUNDLE_ID,
                        "standard File Provider uses a different group")
            extension_floors[bundle.relative_to(app).as_posix()] = parse_version(minimum)
        else:
            schemes = [scheme.lower() for entry in info.get("CFBundleURLTypes", []) for scheme in entry.get("CFBundleURLSchemes", [])]
            require("minis" in schemes and not {"minis-root", "minis-broadfiles"}.intersection(schemes),
                    "standard app must keep the ordinary share URL scheme")
        targets = [item for item in objects.values() if item.get("isa") == "PBXNativeTarget" and item.get("name") == name]
        require(len(targets) == 1, f"project lacks unique target {name}")
        configs = objects[targets[0]["buildConfigurationList"]]["buildConfigurations"]
        releases = [objects[key]["buildSettings"] for key in configs if objects[key].get("name") == "Release"]
        require(len(releases) == 1, f"project lacks Release settings for {name}")
        settings = releases[0]
        require(settings.get("PRODUCT_BUNDLE_IDENTIFIER") == info["CFBundleIdentifier"] and
                settings.get("CODE_SIGN_ENTITLEMENTS") == entitlements_path and
                settings.get("CODE_SIGN_STYLE") == "Automatic" and
                parse_version(settings.get("IPHONEOS_DEPLOYMENT_TARGET")) == parse_version(minimum),
                f"standard Release settings changed for {name}")
        path = IOS / entitlements_path
        entitlements = load_plist(path)
        require(entitlements.get("com.apple.security.application-groups") == ["group." + NORMAL_BUNDLE_ID]
                and not (FORBIDDEN_PRIVILEGE_KEYS | BROAD_FILE_KEYS).intersection(entitlements),
                f"unexpected privileged entitlements or shared group on {name}")
        evidence.append({"target": name, "bundle_id": info["CFBundleIdentifier"], "minimum_os": minimum,
                         "executable_sha256": sha256_file(bundle / name), "entitlements_path": entitlements_path,
                         "entitlements_source_sha256": sha256_file(path), "project_entitlement_keys": sorted(entitlements)})
    return evidence, extension_floors


def option(tokens, key):
    return tokens[tokens.index(key) + 1] if key in tokens and tokens.index(key) + 1 < len(tokens) else None


def response_file_belongs_to_build(path: Path, objects: Path, derived: Path, *, link_command: bool) -> bool:
    """Accept target inputs plus Swift package linker response files from this build."""
    if path.parent == objects:
        return True
    if not link_command:
        return False
    intermediates = (derived / "Build/Intermediates.noindex").resolve()
    try:
        relative = path.relative_to(intermediates)
    except ValueError:
        return False
    parts = relative.parts
    if len(parts) != 6 or not parts[0].endswith(".build"):
        return False
    module = parts[0][:-len(".build")]
    return (parts[1] == "Release-iphoneos" and parts[2] == parts[0]
            and parts[3] == "Objects-normal" and parts[4] == "arm64"
            and parts[5] == f"{module}-linker-args.resp")


def reject_features(tokens):
    compact = [token for token in tokens if token not in {"-Xcc", "-Xfrontend"}]
    for index, token in enumerate(compact):
        value = (compact[index + 1] if token == "-D" and index + 1 < len(compact) else token[2:]
                 if token.startswith("-D") else "")
        require(value.split("=", 1)[0] not in FEATURES, f"standard compile enables {value}")


def prestrip_symbols(path: Path, *, reject_all=False) -> str:
    xcrun = shutil.which("xcrun")
    require(xcrun is not None, "xcrun is required")
    result = subprocess.run([xcrun, "nm", "-U", str(path)], capture_output=True, text=True)
    require(result.returncode == 0, f"cannot read defined symbols from {path.name}")
    names = {fields[-1] for line in result.stdout.splitlines() if len(fields := line.split()) >= 3}
    require(not names.intersection(SYMBOLS), f"Root/MCP implementation found in {path.name}")
    if reject_all:
        unexpected = [fields[-1] for line in result.stdout.splitlines()
                      if len(fields := line.split()) >= 3 and fields[-2] in {"T", "t"}
                      and not re.fullmatch(r"ltmp[0-9]+", fields[-1])
                      and fields[-1] not in COVERAGE_RUNTIME_SYMBOLS]
        require(not unexpected,
                f"guarded translation unit emitted executable code: {path.name}: {', '.join(unexpected)}")
    return sha256_file(path)


def compile_evidence(app: Path, derived: Path, log: Path) -> dict:
    objects = (derived / "Build/Intermediates.noindex/Minis.build/Release-iphoneos/Minis.build/Objects-normal/arm64").resolve()
    require(objects.is_dir(), "standard audit needs the original Release target intermediates")
    responses, compiled, swift, linked = {}, {}, False, False
    sources, link_inputs, build_success = set(), set(), False
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip() == "** BUILD SUCCEEDED **":
            build_success = True
        if not (" -module-name Minis " in line or " -c " in line or " -filelist " in line):
            continue
        if "Release-iphoneos/Minis.build/Objects-normal/arm64/" not in line:
            continue
        if not ("/swiftc " in line or "/clang " in line or "/clang++ " in line):
            continue
        tokens = shlex.split(line)
        module = option(tokens, "-module-name")
        source = option(tokens, "-c")
        outfile = option(tokens, "-o")
        filelist = option(tokens, "-filelist")
        compiler = Path(tokens[2] if tokens[:2] == ["builtin-SwiftDriver", "--"] else tokens[0]).name
        swift_command = compiler == "swiftc" and module == "Minis"
        c_command = compiler == "clang" and source and outfile and Path(outfile).resolve().parent == objects
        link_command = compiler in ("clang", "clang++") and filelist and outfile and Path(outfile).resolve() == app / "Minis"
        if not (swift_command or c_command or link_command):
            continue
        reject_features(tokens)
        for token in tokens:
            if not token.startswith("@") or not token.endswith((".resp", ".SwiftFileList")):
                continue
            path = Path(token[1:]).resolve()
            require(path.is_file() and response_file_belongs_to_build(
                path, objects, derived, link_command=link_command),
                "compiler response file is missing or unrelated to this build")
            if path.suffix == ".SwiftFileList":
                require(path.name == "Minis.SwiftFileList" and swift_command, "unrelated Swift input file list")
                sources.update(Path(name).name for name in shlex.split(path.read_text(encoding="utf-8")))
            else:
                reject_features(shlex.split(path.read_text(encoding="utf-8")))
            responses[path.name] = sha256_file(path)
        if swift_command:
            require(option(tokens, "-target") == "arm64-apple-ios15.0", "incorrect Swift target for standard product")
            swift = True
        if c_command and Path(source).name in C_INPUTS:
            native_input = Path(source).name
            object_file = Path(outfile).resolve()
            require(object_file.is_file() and object_file.stem == Path(native_input).stem, "missing original native object")
            compiled[native_input] = prestrip_symbols(object_file, reject_all=native_input != "ISHKernel.m")
        if link_command:
            path = Path(filelist).resolve()
            require(path == objects / "Minis.LinkFileList" and path.is_file(), "unrelated standard link file list")
            link_inputs.update(Path(name).resolve() for name in shlex.split(path.read_text(encoding="utf-8")))
            responses[path.name] = sha256_file(path)
            linked = True
    require(build_success and swift and linked and SWIFT_INPUTS <= sources and C_INPUTS == set(compiled),
            "missing clean Release build, Swift input list, native compiler commands or final link")
    require({objects / (Path(name).stem + ".o") for name in C_INPUTS} <= link_inputs,
            "verified objects are not all inputs to the standard product link")
    return {"build_log_sha256": sha256_file(log), "native_object_sha256": compiled,
            "compiler_and_link_input_hashes": responses, "root_defines_seen": False,
            "linked_executable_sha256": prestrip_symbols(app / "Minis"),
            "device_runtime_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    product = parser.add_mutually_exclusive_group(required=True)
    product.add_argument("--app", type=Path)
    product.add_argument("--derived-data", type=Path)
    parser.add_argument("--configuration", choices=["Release"], default="Release")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-log", type=Path)
    args = parser.parse_args()
    try:
        app = resolve_app(args.app, args.derived_data, args.configuration).resolve()
        require(app.name == "Minis.app" and app.parent.name == "Release-iphoneos" and app.parents[2].name == "Build",
                "audit the original Release-iphoneos/Minis.app inside DerivedData")
        derived = app.parents[3]
        log = args.build_log or derived / "xcodebuild.log"
        require(log.is_file(), "supply the successful Release xcodebuild log using --build-log")
        require(app not in args.output.resolve().parents, "audit output must not modify the built app")
        roles, floors = roles_and_settings(app, parse_project(IOS / "Minis.xcodeproj/project.pbxproj"))
        native = audit_staged_native_floor(app, extension_floors=floors)
        declared = {(bundle / load_plist(bundle / "Info.plist")["CFBundleExecutable"]).relative_to(app).as_posix()
                    for bundle in [app, *plugin_bundles(app)]}
        require(declared <= {row["path"] for row in native}, "not all four bundle executables were audited")
        result = {"schema": 1, "configuration": "Release", "platform": "iphoneos", "scheme": "Minis",
                  "targets": roles, "native_floors": native, "compilation": compile_evidence(app, derived, log),
                  "root_helper_included": False, "apple_signing_or_provisioning_verified": False,
                  "device_runtime_verified": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (PackageError, OSError, subprocess.CalledProcessError, KeyError, IndexError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Audited standard Release and {len(native)} native images: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
