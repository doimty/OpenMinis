#!/usr/bin/env python3
"""Wrap a verified Minis Root IPA in a package for an already jailbroken device.

Input is the IPA and manifest from the same trusted Actions build. Preserve the
signed bundle bytes and check the Debian archive independently after writing it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import plistlib
import re
import shutil
import stat
import struct
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from package_ios15_ipa import MACHO_MAGICS, PackageError, entitlement_values_equal, parse_version, sha256_file
from package_ios15_root_ipa import (
    HELPER_NAME, ROOT_EXTENSION_POINTS, ROOT_GROUP, ROOT_ID, ROOT_PLUGIN_ROLES,
    ROOT_PRODUCT_FLOORS, ROOT_ROLE_FLOORS, ROOT_ROLE_SUFFIXES, expected_entitlements,
    validate_build_identity,
)

SCHEMES = {
    "rootful": ("iphoneos-arm", "/Applications/MinisRoot.app", "/usr/bin/uicache"),
    "rootless": ("iphoneos-arm64", "/var/jb/Applications/MinisRoot.app", "/var/jb/usr/bin/uicache"),
    "roothide": ("iphoneos-arm64e", "/Applications/MinisRoot.app", "/usr/bin/uicache"),
}
IPA_PREFIX = "Payload/Minis.app/"
CHUNK = 1024 * 1024
AR_MEMBERS = ("debian-binary", "control.tar.gz", "data.tar.gz")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def relative_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name and not path.is_absolute() and path.as_posix() == name and ".." not in path.parts
                and "\\" not in name and not any(ord(char) < 32 for char in name))


def digest_stream(source) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: source.read(CHUNK), b""):
        digest.update(block)
    return digest.hexdigest()


def macho_minimum(source, size: int) -> tuple[int, int, int] | None:
    header = source.read(32)
    if header[:4] not in MACHO_MAGICS:
        return None
    require(len(header) == 32, "truncated Mach-O header")
    fat_formats = {b"\xca\xfe\xba\xbe": (">", "IIIII"), b"\xbe\xba\xfe\xca": ("<", "IIIII"),
                   b"\xca\xfe\xba\xbf": (">", "IIQQII"), b"\xbf\xba\xfe\xca": ("<", "IIQQII")}
    if header[:4] in fat_formats:
        endian, layout = fat_formats[header[:4]]
        require(struct.unpack_from(endian + "I", header, 4)[0] == 1, "expected a single arm64 slice")
        source.seek(8)
        cpu, _, offset, slice_size, *_ = struct.unpack(endian + layout, source.read(struct.calcsize(endian + layout)))
        require(cpu == 0x0100000C and offset + slice_size <= size and slice_size >= 32, "invalid arm64 slice")
        source.seek(offset)
        header = source.read(32)
        size = slice_size
    require(header[:4] == b"\xcf\xfa\xed\xfe", "expected a device arm64 Mach-O")
    _, cpu, subtype, kind, count, length = struct.unpack_from("<6I", header)
    require(cpu == 0x0100000C and subtype & 0x00FFFFFF == 0, "expected the audited arm64 architecture")
    if kind == 1:
        return None
    require(0 < count <= 65536 and count * 8 <= length <= min(size - 32, 16 * CHUNK), "invalid Mach-O load commands")
    commands = source.read(length)
    require(len(commands) == length, "truncated Mach-O load commands")
    position = 0
    minimum = None
    signed = False
    for _ in range(count):
        require(position + 8 <= length, "missing Mach-O load command")
        command, command_size = struct.unpack_from("<2I", commands, position)
        require(8 <= command_size <= length - position, "invalid Mach-O command size")
        if command in (0x32, 0x25):
            require(minimum is None and command_size >= (24 if command == 0x32 else 16), "invalid or duplicate iOS deployment floor")
            if command == 0x32:
                require(struct.unpack_from("<I", commands, position + 8)[0] == 2, "expected iOS device platform")
            encoded = struct.unpack_from("<I", commands, position + (12 if command == 0x32 else 8))[0]
            minimum = (encoded >> 16, (encoded >> 8) & 255, encoded & 255)
        if command == 0x1D:
            require(not signed and command_size >= 16, "invalid or duplicate code-signature command")
            offset, byte_count = struct.unpack_from("<2I", commands, position + 8)
            require(offset >= 32 + length and byte_count > 0 and offset + byte_count <= size,
                    "invalid code-signature bounds")
            signed = True
        position += command_size
    require(position == length and minimum is not None and signed, "missing iOS floor or code signature")
    return minimum


def inspect_ipa(ipa: Path, manifest: dict, commit: str, run_id: str) -> dict:
    validate_build_identity(commit, run_id)
    floor = manifest.get("package_floor")
    require(floor in ROOT_PRODUCT_FLOORS and manifest.get("minimum_os") == floor, "unexpected installation floor")
    for name in ("signed", "signature_entitlements_verified", "archive_executable_modes_verified",
                 "extension_metadata_verified", "root_helper_included"):
        require(manifest.get(name) is True, f"missing verified packaging evidence: {name}")
    require(manifest.get("main_app_runs_as_root") is False and manifest.get("root_helper_mode") == "0755"
            and manifest.get("root_helper_path") == HELPER_NAME, "unexpected helper policy")
    require(manifest.get("bundle_id") == ROOT_ID and manifest.get("app_group_id") == ROOT_GROUP
            and manifest.get("file_access_profile") == "root-helper", "unexpected IPA identity")
    require(manifest.get("source_commit") == commit and manifest.get("actions_run_id") == run_id,
            "source commit or Actions run mismatch")
    require(ipa.name == manifest.get("ipa_name") and ipa.stat().st_size == manifest.get("ipa_bytes")
            and sha256_file(ipa) == manifest.get("ipa_sha256"), "source IPA hash, name or size mismatch")
    version = manifest.get("marketing_version")
    require(isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) is not None,
            "unsupported marketing version")
    identity = {"schema": 1, "source_commit": commit, "actions_run_id": run_id}
    require(manifest.get("host_file_probe_build") == identity and manifest.get("root_management_schema") == 1,
            "source build evidence mismatch")
    expected_plugins = {name for name, role in ROOT_PLUGIN_ROLES.items()
                        if ROOT_ROLE_FLOORS[role] <= ROOT_PRODUCT_FLOORS[floor]}
    require(set(manifest.get("plugins_kept", [])) == expected_plugins, "incomplete extension manifest")
    files, plists, executable_hashes, native = {}, {}, {}, {}
    with zipfile.ZipFile(ipa) as archive:
        seen = set()
        for member in archive.infolist():
            require(member.filename.startswith(IPA_PREFIX), "unexpected file outside signed Minis bundle")
            relative = member.filename[len(IPA_PREFIX):]
            require(relative_name(relative) and not member.is_dir() and relative.casefold() not in seen,
                    f"invalid or duplicate IPA path: {relative}")
            seen.add(relative.casefold())
            unix_mode = member.external_attr >> 16
            require(member.create_system == 3 and stat.S_ISREG(unix_mode)
                    and stat.S_IMODE(unix_mode) in (0o644, 0o755), f"unsafe IPA member mode: {relative}")
            mode = stat.S_IMODE(unix_mode)
            with archive.open(member) as stream:
                minimum = macho_minimum(stream, member.file_size)
            with archive.open(member) as stream:
                digest = digest_stream(stream)
            files[relative] = (mode, member.file_size, digest)
            if mode == 0o755:
                executable_hashes[relative] = digest
            if minimum is not None:
                require(mode == 0o755, f"native executable mode changed: {relative}")
                native[relative] = minimum
            if relative == "Info.plist" or (relative.endswith("/Info.plist") and PurePosixPath(relative).parent.suffix == ".appex"):
                plists[relative] = plistlib.loads(archive.read(member))
    main = plists.pop("Info.plist", {})
    require(main.get("CFBundleIdentifier") == ROOT_ID and main.get("CFBundleExecutable") == "Minis"
            and main.get("MinimumOSVersion") == floor and main.get("CFBundleShortVersionString") == version
            and main.get("MinisRootBuildCommit") == commit and main.get("MinisRootActionsRunID") == run_id
            and main.get("MinisHostFileProbeBuild") == identity and main.get("MinisRootManagementEnabled") is True
            and main.get("MinisRootManagementSchema") == 1, "signed app metadata mismatch")
    wanted_entitlements = {".": expected_entitlements("app"), HELPER_NAME: expected_entitlements("helper")}
    plugin_roots = {}
    required_native = {"Minis", HELPER_NAME}
    for path, info in plists.items():
        bundle = PurePosixPath(path).parent
        role = ROOT_PLUGIN_ROLES.get(bundle.name)
        require(role is not None and bundle.parent.as_posix() in {"PlugIns", "Extensions"}
                and bundle.name in expected_plugins and role not in plugin_roots.values(), "unexpected or duplicate extension")
        require(info.get("CFBundleIdentifier") == ROOT_ID + ROOT_ROLE_SUFFIXES[role]
                and parse_version(info.get("MinimumOSVersion")) == ROOT_ROLE_FLOORS[role], "extension metadata mismatch")
        extension = info.get("NSExtension", {})
        require(extension.get("NSExtensionPointIdentifier") == ROOT_EXTENSION_POINTS[role], "incorrect extension point")
        if role == "fileprovider":
            require(extension.get("NSExtensionFileProviderDocumentGroup") == ROOT_GROUP, "wrong File Provider group")
        executable = info.get("CFBundleExecutable")
        require(isinstance(executable, str) and relative_name(executable) and "/" not in executable,
                "invalid extension executable name")
        required_native.add((bundle / executable).as_posix())
        plugin_roots[bundle] = role
        wanted_entitlements[bundle.as_posix()] = expected_entitlements(role)
    require({path.name for path in plugin_roots} == expected_plugins and required_native <= set(native),
            "archive is missing required app, helper or extensions")
    require(entitlement_values_equal(manifest.get("signature_entitlements"), wanted_entitlements),
            "signature evidence does not match the expected app and extension profiles")
    require(manifest.get("archive_executable_hashes") == executable_hashes and
            executable_hashes[HELPER_NAME] == manifest.get("root_helper_signed_sha256") == manifest.get("root_helper_archive_sha256"),
            "signed executable hashes do not match IPA")
    rows = manifest.get("native_floor_audit", [])
    require(len(rows) == len(native) and {row["path"] for row in rows} == set(native), "incomplete native floor evidence")
    for row in rows:
        path = PurePosixPath(row["path"])
        role = next((role for bundle, role in plugin_roots.items() if bundle in path.parents), "app")
        limit = ROOT_ROLE_FLOORS[role]
        require(row["arch"] == "arm64" and parse_version(row["allowed_minimum_os"]) == limit
                and parse_version(row["minimum_os"]) == native[row["path"]] <= limit,
                f"native floor differs from signed IPA or role floor: {path}")
    return files


def control_members(manifest: dict, scheme: str, installed_kib: int) -> dict:
    arch, app, uicache = SCHEMES[scheme]
    floor, run = manifest["package_floor"], manifest["actions_run_id"]
    version = f"{manifest['marketing_version']}+ci{run}+ios{floor}"
    control = (f"Package: {ROOT_ID}\nName: Minis Root\nVersion: {version}\nArchitecture: {arch}\n"
               f"Depends: firmware (>= {floor}), uikittools\nMaintainer: OpenMinis\nSection: Applications\n"
               f"Installed-Size: {installed_kib}\nDescription: Minis with device tools on jailbroken iOS\n"
               f"X-Minis-Source-Commit: {manifest['source_commit']}\nX-Minis-Actions-Run: {run}\n"
               f"X-Minis-Install-Floor: {floor}\nX-Minis-Package-Schema: 1\nX-Minis-Jailbreak-Scheme: {scheme}\n")
    postinst = f'#!/bin/sh\nset -eu\ncase "${{1:-}}" in\n  configure) exec {uicache} -p {app} ;;\nesac\nexit 0\n'
    postrm = f'#!/bin/sh\nset -eu\ncase "${{1:-}}" in\n  remove) exec {uicache} -u {app} ;;\nesac\nexit 0\n'
    return {"control": (0o644, control.encode()), "postinst": (0o755, postinst.encode()),
            "postrm": (0o755, postrm.encode())}


def tar_member(name: str, mode: int, size: int = 0, *, directory: bool = False) -> tarfile.TarInfo:
    member = tarfile.TarInfo("./" + name)
    member.size, member.mode = size, mode
    member.uid = member.gid = member.mtime = 0
    member.uname, member.gname = "root", "wheel"
    member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    return member


def write_tars(folder: Path, ipa: Path, scheme: str, files: dict, controls: dict) -> None:
    app = PurePosixPath(SCHEMES[scheme][1].lstrip("/"))
    directories = {app}
    for relative in files:
        directories.update(parent for parent in (app / relative).parents if app in parent.parents)
    with (folder / "control.tar.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed, \
         tarfile.open(fileobj=compressed, mode="w|", format=tarfile.GNU_FORMAT) as archive:
        for name, (mode, content) in controls.items():
            archive.addfile(tar_member(name, mode, len(content)), io.BytesIO(content))
    with zipfile.ZipFile(ipa) as source, (folder / "data.tar.gz").open("wb") as raw, \
         gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=3) as compressed, \
         tarfile.open(fileobj=compressed, mode="w|", format=tarfile.GNU_FORMAT) as archive:
        for directory in sorted(directories):
            archive.addfile(tar_member(directory.as_posix(), 0o755, directory=True))
        for relative, (mode, size, _) in sorted(files.items()):
            with source.open(IPA_PREFIX + relative) as reader:
                archive.addfile(tar_member((app / relative).as_posix(), mode, size), reader)


def write_ar(deb: Path, folder: Path) -> None:
    with deb.open("xb") as output:
        output.write(b"!<arch>\n")
        for name in AR_MEMBERS:
            member = folder / name
            size = member.stat().st_size
            header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{size:<10}`\n".encode("ascii")
            require(len(header) == 60, "ar member is too large")
            output.write(header)
            with member.open("rb") as source:
                shutil.copyfileobj(source, output, CHUNK)
            if size % 2:
                output.write(b"\n")


def verify_deb(deb: Path, scheme: str, files: dict, controls: dict) -> None:
    app = SCHEMES[scheme][1].lstrip("/")
    with deb.open("rb") as source:
        require(source.read(8) == b"!<arch>\n", "invalid Debian ar signature")
        for wanted in AR_MEMBERS:
            header = source.read(60)
            require(len(header) == 60 and header[:16].rstrip().rstrip(b"/") == wanted.encode()
                    and header[58:] == b"`\n" and header[48:58].strip().isdigit(), "unexpected Debian ar member")
            size = int(header[48:58])
            require(0 < size <= deb.stat().st_size - source.tell(), "truncated Debian ar member")
            end = source.tell() + size
            if wanted == "debian-binary":
                require(size == 4 and source.read(4) == b"2.0\n", "unexpected Debian format version")
            else:
                expected = ({name: (mode, len(data), hashlib.sha256(data).hexdigest()) for name, (mode, data) in controls.items()}
                            if wanted == "control.tar.gz" else {f"{app}/{name}": record for name, record in files.items()})
                seen, directories = set(), set()
                with tarfile.open(fileobj=source, mode="r|gz") as archive:
                    for member in archive:
                        name = member.name.removeprefix("./")
                        require(relative_name(name) and member.uid == 0 and member.gid == 0
                                and member.uname == "root" and member.gname == "wheel", "unsafe tar path or ownership")
                        if member.isdir():
                            require(wanted == "data.tar.gz" and member.mode == 0o755 and name not in directories
                                    and (name == app or name.startswith(app + "/")), "unexpected tar directory")
                            directories.add(name)
                            continue
                        require(member.isreg() and name in expected and name not in seen, f"unexpected tar entry: {name}")
                        seen.add(name)
                        mode, length, digest = expected[name]
                        require(member.mode == mode and member.size == length, f"tar mode or size mismatch: {name}")
                        reader = archive.extractfile(member)
                        require(reader is not None and digest_stream(reader) == digest, f"tar data mismatch: {name}")
                require(seen == set(expected), f"incomplete {wanted}")
            source.seek(end)
            if size % 2:
                require(source.read(1) == b"\n", "invalid Debian ar member padding")
        require(not source.read(1), "unexpected extra Debian ar member")


def package_deb(manifest_path: Path, output_dir: Path, scheme: str, *, commit: str, run_id: str, ipa: Path | None = None) -> dict:
    require(scheme in SCHEMES, "unknown jailbreak scheme")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ipa_name = manifest.get("ipa_name", "")
    require(isinstance(ipa_name, str) and relative_name(ipa_name) and "/" not in ipa_name and ipa_name.endswith(".ipa"), "invalid IPA name")
    ipa = ipa or manifest_path.parent / ipa_name
    files = inspect_ipa(ipa, manifest, commit, run_id)
    architecture, app, _ = SCHEMES[scheme]
    floor = manifest["package_floor"]
    version = f"{manifest['marketing_version']}+ci{run_id}+ios{floor}"
    name = f"{ROOT_ID}_{version}_{architecture}.deb"
    controls = control_members(manifest, scheme, (sum(length for _, length, _ in files.values()) + 1023) // 1024)
    output_dir.mkdir(parents=True, exist_ok=True)
    require(not any(output_dir.iterdir()), "use a fresh output directory for each Debian package")
    with tempfile.TemporaryDirectory(dir=output_dir) as temp:
        folder = Path(temp)
        (folder / "debian-binary").write_bytes(b"2.0\n")
        write_tars(folder, ipa, scheme, files, controls)
        deb = folder / name
        write_ar(deb, folder)
        verify_deb(deb, scheme, files, controls)
        result = {"schema": 1, "package": ROOT_ID, "bundle_id": ROOT_ID, "package_version": version,
                  "scheme": scheme, "architecture": architecture, "install_path": app, "minimum_os": floor,
                  "source_commit": commit, "actions_run_id": run_id, "source_ipa_sha256": manifest["ipa_sha256"],
                  "source_manifest_sha256": sha256_file(manifest_path), "source_signed_executables_verified": True,
                  "source_signature_evidence_verified": True, "native_floor_readback_verified": True,
                  "deb_archive_readback_verified": True, "root_helper_mode": "0755", "setuid_files": [],
                  "main_app_runs_as_root": False, "persona_support": "unverified", "device_runtime_verified": False,
                  "deb_name": name, "deb_bytes": deb.stat().st_size, "deb_sha256": sha256_file(deb)}
        deb.rename(output_dir / name)
        (output_dir / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ipa", type=Path)
    parser.add_argument("--scheme", choices=SCHEMES, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--actions-run-id", required=True)
    args = parser.parse_args()
    try:
        result = package_deb(args.manifest, args.output_dir, args.scheme, commit=args.source_commit,
                             run_id=args.actions_run_id, ipa=args.ipa)
    except (OSError, ValueError, TypeError, KeyError, PackageError, zipfile.BadZipFile, plistlib.InvalidFileException,
            struct.error, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
