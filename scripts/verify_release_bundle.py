#!/usr/bin/env python3
"""Verify a downloaded Minis release bundle and write release metadata."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import plistlib
import re
import struct
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import package_automation_deb as automation
from package_automation_deb import (
    PACKAGE as AUTOMATION_PACKAGE,
    SCHEMES as AUTOMATION_SCHEMES,
    UPSTREAM as AUTOMATION_UPSTREAM,
)
from package_ios15_ipa import PackageError
from package_ios15_root_ipa import (
    ROOT_GROUP,
    ROOT_ID,
    ROOT_PLUGIN_ROLES,
    ROOT_PRODUCT_FLOORS,
    ROOT_ROLE_FLOORS,
)
from package_jailbreak_deb import (
    SCHEMES as ROOT_SCHEMES,
    control_members,
    inspect_ipa,
    verify_deb,
)


MAIN_ARTIFACT = "minis-ios15-root-candidate"
COMPANION_ARTIFACTS = {
    "rootless": "minis-automation-rootless",
    "roothide": "minis-automation-roothide",
}
MAIN_ROOTS = {
    "15.0": "root-ipa",
    "16.0": "root-ipa-ios16.0",
    "16.2": "root-ipa-ios16.2",
}
ROOT_DEB_DIRS = {
    (floor, scheme): f"deb-{floor}-{scheme}"
    for floor in ROOT_PRODUCT_FLOORS
    for scheme in ("rootless", "roothide")
}
COMPANION_ROOTS = {
    "rootless": "companion-rootless",
    "roothide": "companion-roothide",
}
COMPANION_BUILD_FILES = {
    "automation-build/build.json",
    "automation-build/build.log",
    "automation-build/native-readback.txt",
    "automation-build/source-lock.json",
}
MAX_DEB_BYTES = 128 * 1024 * 1024
MAX_TAR_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TAR_TOTAL_BYTES = 128 * 1024 * 1024


class BundleError(Exception):
    """A release artifact does not match the required delivery contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_json_bytes(data: bytes) -> dict:
    try:
        value = automation.strict_json(data.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as error:
        raise BundleError(f"invalid JSON manifest: {error}") from error
    require(type(value) is dict, "manifest root must be a JSON object")
    return value


def strict_json_file(path: Path) -> dict:
    return strict_json_bytes(path.read_bytes())


def expected_plugins(floor: str) -> set[str]:
    return {
        name
        for name, role in ROOT_PLUGIN_ROLES.items()
        if ROOT_ROLE_FLOORS[role] <= ROOT_PRODUCT_FLOORS[floor]
    }


def all_plugins() -> set[str]:
    return set(ROOT_PLUGIN_ROLES)


def require_identity(manifest: dict, commit: str, run_id: str, label: str) -> None:
    require(manifest.get("source_commit") == commit, f"{label} source commit mismatch")
    require(manifest.get("actions_run_id") == run_id, f"{label} Actions run mismatch")


def require_plain_directory(path: Path, label: str) -> None:
    require(path.is_dir() and not path.is_symlink(), f"{label} is not a plain directory")


def directory_entries(path: Path, label: str) -> list[Path]:
    require_plain_directory(path, label)
    entries = sorted(path.iterdir(), key=lambda entry: entry.name)
    for entry in entries:
        require(not entry.is_symlink(), f"{label} contains a symlink: {entry.name}")
    return entries


def exact_files(path: Path, expected_names: set[str], label: str) -> dict[str, Path]:
    entries = directory_entries(path, label)
    actual = {entry.name for entry in entries if entry.is_file()}
    expected_names = set(expected_names)
    require(actual == expected_names, f"{label} files differ: expected {sorted(expected_names)}, got {sorted(actual)}")
    require(len(entries) == len(expected_names), f"{label} contains a non-file entry")
    return {entry.name: entry for entry in entries}


def tree_files(root: Path) -> list[Path]:
    require_plain_directory(root, "release bundle")
    files = []
    seen = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), f"release bundle contains a symlink: {path.relative_to(root)}")
        if path.is_dir():
            continue
        require(path.is_file(), f"release bundle contains a special file: {path.relative_to(root)}")
        relative = path.relative_to(root).as_posix()
        folded = relative.casefold()
        require(folded not in seen, f"release bundle contains a case-insensitive path collision: {relative}")
        seen.add(folded)
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def file_record(root: Path, path: Path, kind: str, **details) -> dict:
    return {
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **details,
    }


def verify_main_ipa_archive(ipa: Path, manifest: dict, commit: str, run_id: str) -> dict:
    return inspect_ipa(ipa, manifest, commit, run_id)


def verify_main_deb_archive(deb: Path, scheme: str, files: dict, controls: dict) -> None:
    verify_deb(deb, scheme, files, controls)


def _read_ar(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    require(len(data) <= MAX_DEB_BYTES, f"Debian artifact is too large: {path.name}")
    require(data.startswith(b"!<arch>\n"), f"invalid Debian ar signature: {path.name}")
    members = {}
    position = 8
    while position < len(data):
        header = data[position:position + 60]
        require(len(header) == 60 and header[58:] == b"`\n", f"truncated Debian ar member: {path.name}")
        name = header[:16].decode("ascii", "strict").rstrip().rstrip("/")
        require(name not in members, f"duplicate Debian ar member: {name}")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError as error:
            raise BundleError(f"invalid Debian ar member size: {name}") from error
        start = position + 60
        end = start + size
        require(size > 0 and end <= len(data), f"truncated Debian ar member: {name}")
        members[name] = data[start:end]
        position = end
        if size % 2:
            require(position < len(data) and data[position:position + 1] == b"\n",
                    f"invalid Debian ar padding after {name}")
            position += 1
    require(position == len(data), f"trailing bytes in Debian archive: {path.name}")
    return members


def _tar_entries(raw: bytes, label: str) -> dict[str, tuple[int, bytes]]:
    entries = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                path = PurePosixPath(name)
                require(
                    bool(name)
                    and not path.is_absolute()
                    and path.as_posix() == name
                    and ".." not in path.parts
                    and "\\" not in name,
                    f"unsafe path in {label}: {name}",
                )
                if member.isdir():
                    continue
                require(member.isreg(), f"non-regular entry in {label}: {name}")
                require(name.casefold() not in {key.casefold() for key in entries},
                        f"duplicate entry in {label}: {name}")
                require(0 <= member.size <= MAX_TAR_MEMBER_BYTES,
                        f"oversized entry in {label}: {name}")
                source = archive.extractfile(member)
                require(source is not None, f"unreadable entry in {label}: {name}")
                data = source.read()
                require(len(data) == member.size, f"truncated entry in {label}: {name}")
                total += len(data)
                require(total <= MAX_TAR_TOTAL_BYTES, f"{label} expands beyond the artifact limit")
                entries[name] = (member.mode, data)
    except (tarfile.TarError, OSError) as error:
        raise BundleError(f"invalid {label}: {error}") from error
    return entries


def verify_companion_archive(
    deb: Path,
    scheme: str,
    outer_build: bytes,
    outer_source_lock: bytes,
    commit: str,
    run_id: str,
) -> dict:
    members = _read_ar(deb)
    require(set(members) == {"debian-binary", "control.tar.gz", "data.tar.gz"},
            f"unexpected Debian members in {deb.name}")
    require(members["debian-binary"] == b"2.0\n", f"invalid Debian version in {deb.name}")
    payload = _tar_entries(members["data.tar.gz"], f"{scheme} companion payload")
    prefix = AUTOMATION_SCHEMES[scheme][1]
    dynamic = prefix + "Library/MobileSubstrate/DynamicLibraries/"
    documents = prefix + "usr/share/doc/minis-automation/"
    paths = {
        automation.DYLIB: dynamic + automation.DYLIB,
        automation.FILTER: dynamic + automation.FILTER,
        **{name: documents + name for name in automation.DOCUMENTS},
    }
    require(set(payload) == set(paths.values()), f"{scheme} companion payload file set differs")
    require(payload[paths["build.json"]][1] == outer_build,
            f"{scheme} embedded build.json differs from the uploaded build evidence")
    require(payload[paths["source-lock.json"]][1] == outer_source_lock,
            f"{scheme} embedded source-lock.json differs from the uploaded source lock")
    with tempfile.TemporaryDirectory(prefix=f"minis-{scheme}-release-") as temporary:
        folder = Path(temporary)
        for name, archive_name in paths.items():
            (folder / name).write_bytes(payload[archive_name][1])
        build = automation.validate_build(folder, scheme, commit, run_id)
        expected_payload = {archive_name: payload[archive_name] for archive_name in paths.values()}
        installed_kib = (sum(len(data) for _, data in expected_payload.values()) + 1023) // 1024
        expected_control = automation.controls(scheme, run_id, installed_kib)
        automation.verify_archive(deb, expected_control, expected_payload)
    return build


def verify_main_ips(
    main_root: Path,
    commit: str,
    run_id: str,
) -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    expected_dirs = set(MAIN_ROOTS.values()) | {ROOT_DEB_DIRS[key] for key in ROOT_DEB_DIRS}
    actual_dirs = {entry.name for entry in directory_entries(main_root, MAIN_ARTIFACT)}
    require(actual_dirs == expected_dirs,
            f"{MAIN_ARTIFACT} directories differ: expected {sorted(expected_dirs)}, got {sorted(actual_dirs)}")

    rows = []
    manifests = {}
    ipa_files = {}
    for floor, directory_name in MAIN_ROOTS.items():
        directory = main_root / directory_name
        entries = directory_entries(directory, directory_name)
        ipa_paths = [entry for entry in entries if entry.is_file() and entry.suffix == ".ipa"]
        require(len(ipa_paths) == 1, f"{directory_name} must contain exactly one IPA")
        manifest_paths = [entry for entry in entries if entry.is_file() and entry.name == "manifest.json"]
        require(len(manifest_paths) == 1, f"{directory_name} must contain exactly one manifest.json")
        require(len(entries) == 2 and all(entry.is_file() for entry in entries),
                f"{directory_name} must contain only its IPA and manifest.json")
        ipa = ipa_paths[0]
        manifest = strict_json_file(manifest_paths[0])
        require_identity(manifest, commit, run_id, f"{floor} root IPA")
        require(manifest.get("bundle_id") == ROOT_ID, f"{floor} root bundle identity mismatch")
        require(manifest.get("app_group_id") == ROOT_GROUP, f"{floor} root app group mismatch")
        require(manifest.get("file_access_profile") == "root-helper", f"{floor} root profile mismatch")
        require(manifest.get("package_floor") == floor and manifest.get("minimum_os") == floor,
                f"{floor} root package floor mismatch")
        version = manifest.get("marketing_version")
        require(isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) is not None,
                f"{floor} root marketing version is invalid")
        floor_name = "15" if floor == "15.0" else floor
        expected_ipa_name = f"Minis-{version}-ios{floor_name}-root-{commit[:8]}.ipa"
        kept = set(manifest.get("plugins_kept", []))
        stripped = {
            item.get("name") if isinstance(item, dict) else item
            for item in manifest.get("plugins_stripped", [])
            if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        require(kept == expected_plugins(floor), f"{floor} root extensions differ from the delivery floor")
        require(not kept.intersection(stripped) and kept.union(stripped) == all_plugins(),
                f"{floor} root extension accounting is incomplete")
        require(manifest.get("ipa_name") == expected_ipa_name == ipa.name,
                f"{floor} root IPA name differs from the delivery contract")
        require(manifest.get("ipa_bytes") == ipa.stat().st_size, f"{floor} root IPA size differs")
        require(manifest.get("ipa_sha256") == sha256_file(ipa), f"{floor} root IPA hash differs")
        ipa_files[floor] = verify_main_ipa_archive(ipa, manifest, commit, run_id)
        manifests[floor] = manifest
        rows.append(file_record(
            main_root.parent,
            ipa,
            "main_ipa",
            floor=floor,
            bundle_id=ROOT_ID,
            plugins=sorted(kept),
        ))
        rows.append(file_record(
            main_root.parent,
            manifest_paths[0],
            "main_ipa_manifest",
            floor=floor,
            package_id=ROOT_ID,
        ))
    require(len({manifest["marketing_version"] for manifest in manifests.values()}) == 1,
            "the three root IPAs must use one marketing version")
    return rows, manifests, ipa_files


def verify_main_debs(
    main_root: Path,
    commit: str,
    run_id: str,
    ipa_manifests: dict[str, dict],
    ipa_files: dict[str, dict],
) -> list[dict]:
    rows = []
    for floor in ROOT_PRODUCT_FLOORS:
        manifest = ipa_manifests[floor]
        version = manifest["marketing_version"]
        source_ipa_sha = manifest["ipa_sha256"]
        source_manifest_sha = sha256_file(main_root / MAIN_ROOTS[floor] / "manifest.json")
        for scheme in ("rootless", "roothide"):
            directory_name = ROOT_DEB_DIRS[(floor, scheme)]
            directory = main_root / directory_name
            architecture, install_path, _ = ROOT_SCHEMES[scheme]
            expected_name = f"{ROOT_ID}_{version}+ci{run_id}+ios{floor}_{architecture}.deb"
            files = exact_files(directory, {expected_name, "manifest.json"}, directory_name)
            deb = files[expected_name]
            package_manifest = strict_json_file(files["manifest.json"])
            require_identity(package_manifest, commit, run_id, f"{floor} {scheme} main DEB")
            require(package_manifest.get("schema") == 1, f"{floor} {scheme} DEB schema mismatch")
            require(package_manifest.get("package") == ROOT_ID and package_manifest.get("bundle_id") == ROOT_ID,
                    f"{floor} {scheme} DEB package identity mismatch")
            require(package_manifest.get("package_version") == f"{version}+ci{run_id}+ios{floor}",
                    f"{floor} {scheme} DEB version mismatch")
            require(package_manifest.get("scheme") == scheme, f"{floor} {scheme} DEB scheme mismatch")
            require(package_manifest.get("architecture") == architecture,
                    f"{floor} {scheme} DEB architecture mismatch")
            require(package_manifest.get("install_path") == install_path,
                    f"{floor} {scheme} DEB install path mismatch")
            require(package_manifest.get("minimum_os") == floor, f"{floor} {scheme} DEB floor mismatch")
            require(package_manifest.get("source_ipa_sha256") == source_ipa_sha,
                    f"{floor} {scheme} DEB source IPA hash mismatch")
            require(package_manifest.get("source_manifest_sha256") == source_manifest_sha,
                    f"{floor} {scheme} DEB source manifest hash mismatch")
            require(package_manifest.get("source_signed_executables_verified") is True
                    and package_manifest.get("source_signature_evidence_verified") is True
                    and package_manifest.get("native_floor_readback_verified") is True
                    and package_manifest.get("deb_archive_readback_verified") is True,
                    f"{floor} {scheme} DEB omits required verification evidence")
            require(package_manifest.get("root_helper_mode") == "0755"
                    and package_manifest.get("setuid_files") == []
                    and package_manifest.get("main_app_runs_as_root") is False,
                    f"{floor} {scheme} DEB helper policy mismatch")
            require(package_manifest.get("persona_support") == "unverified"
                    and package_manifest.get("device_runtime_verified") is False,
                    f"{floor} {scheme} DEB overstates device verification")
            require(package_manifest.get("deb_name") == expected_name,
                    f"{floor} {scheme} DEB name differs from the manifest")
            require(package_manifest.get("deb_bytes") == deb.stat().st_size,
                    f"{floor} {scheme} DEB size differs")
            require(package_manifest.get("deb_sha256") == sha256_file(deb),
                    f"{floor} {scheme} DEB hash differs")
            controls = control_members(
                ipa_manifests[floor],
                scheme,
                (sum(length for _, length, _ in ipa_files[floor].values()) + 1023) // 1024,
            )
            verify_main_deb_archive(deb, scheme, ipa_files[floor], controls)
            rows.append(file_record(
                main_root.parent,
                deb,
                "main_deb",
                floor=floor,
                scheme=scheme,
                architecture=architecture,
                package_id=ROOT_ID,
            ))
            rows.append(file_record(
                main_root.parent,
                files["manifest.json"],
                "main_deb_manifest",
                floor=floor,
                scheme=scheme,
                package_id=ROOT_ID,
            ))
    return rows


def verify_companion(
    companion_root: Path,
    scheme: str,
    commit: str,
    run_id: str,
) -> list[dict]:
    artifact = COMPANION_ARTIFACTS[scheme]
    expected_dirs = {"automation-build", "automation-deb"}
    actual_dirs = {entry.name for entry in directory_entries(companion_root, artifact)}
    require(actual_dirs == expected_dirs,
            f"{artifact} directories differ: expected {sorted(expected_dirs)}, got {sorted(actual_dirs)}")

    build_dir = companion_root / "automation-build"
    build_paths = exact_files(
        build_dir,
        {"build.json", "build.log", "native-readback.txt", "source-lock.json"},
        f"{artifact} automation-build",
    )
    build_bytes = build_paths["build.json"].read_bytes()
    source_lock_bytes = build_paths["source-lock.json"].read_bytes()
    build_manifest = strict_json_bytes(build_bytes)
    require_identity(build_manifest, commit, run_id, f"{scheme} companion build")
    require(build_manifest.get("schema") == 1
            and build_manifest.get("package") == AUTOMATION_PACKAGE
            and build_manifest.get("scheme") == scheme
            and build_manifest.get("minimum_os") == "15.0"
            and build_manifest.get("upstream_commit") == AUTOMATION_UPSTREAM,
            f"{scheme} companion build identity mismatch")
    require(build_paths["build.log"].stat().st_size > 0,
            f"{scheme} companion build log is empty")
    readback = build_paths["native-readback.txt"].read_text(encoding="utf-8")
    root_hide_link = "@loader_path/.jbroot/usr/lib/libroothide.dylib"
    require("UNDEFINED SYSTEM IMPORTS" in readback, f"{scheme} companion readback is incomplete")
    require((root_hide_link in readback) == (scheme == "roothide"),
            f"{scheme} companion RootHide linkage is wrong")

    architecture = AUTOMATION_SCHEMES[scheme][0]
    expected_name = f"{AUTOMATION_PACKAGE}_1.0+ci{run_id}_{architecture}.deb"
    deb_paths = exact_files(
        companion_root / "automation-deb",
        {expected_name, "manifest.json"},
        f"{artifact} automation-deb",
    )
    deb = deb_paths[expected_name]
    package_manifest = strict_json_file(deb_paths["manifest.json"])
    require_identity(package_manifest, commit, run_id, f"{scheme} companion DEB")
    require(package_manifest.get("schema") == 1
            and package_manifest.get("package") == AUTOMATION_PACKAGE
            and package_manifest.get("scheme") == scheme
            and package_manifest.get("architecture") == architecture
            and package_manifest.get("minimum_os") == "15.0"
            and package_manifest.get("upstream_commit") == AUTOMATION_UPSTREAM,
            f"{scheme} companion package identity mismatch")
    require(package_manifest.get("source_build_sha256") == sha256_bytes(build_bytes),
            f"{scheme} companion source build hash mismatch")
    require(package_manifest.get("dylib_sha256") == build_manifest.get("dylib_sha256"),
            f"{scheme} companion dylib hash differs between manifests")
    require(package_manifest.get("native_floors") == build_manifest.get("native_floors"),
            f"{scheme} companion native floors differ between manifests")
    require(package_manifest.get("deb_name") == expected_name,
            f"{scheme} companion DEB name differs from the manifest")
    require(package_manifest.get("deb_bytes") == deb.stat().st_size,
            f"{scheme} companion DEB size differs")
    require(package_manifest.get("deb_sha256") == sha256_file(deb),
            f"{scheme} companion DEB hash differs")
    require(package_manifest.get("deb_archive_readback_verified") is True
            and package_manifest.get("signature_verified") is True,
            f"{scheme} companion DEB omits required verification evidence")
    require(package_manifest.get("default_enabled") is False
            and package_manifest.get("bind_address") == "127.0.0.1"
            and package_manifest.get("port") == 8091
            and package_manifest.get("device_runtime_verified") is False,
            f"{scheme} companion runtime policy mismatch")
    embedded_build = verify_companion_archive(
        deb, scheme, build_bytes, source_lock_bytes, commit, run_id
    )
    require(embedded_build.get("dylib_sha256") == package_manifest.get("dylib_sha256"),
            f"{scheme} embedded companion build differs from the package manifest")

    all_paths = list(build_paths.values()) + list(deb_paths.values())
    return [
        file_record(
            companion_root.parent,
            path,
            "companion_deb" if path.suffix == ".deb" else "companion_evidence",
            scheme=scheme,
            package_id=AUTOMATION_PACKAGE,
        )
        for path in sorted(all_paths, key=lambda item: item.relative_to(companion_root.parent).as_posix())
    ]


def verify_bundle(bundle_root: Path, commit: str, run_id: str, output_dir: Path) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "source commit must be a full lowercase SHA")
    require(re.fullmatch(r"[1-9][0-9]*", run_id) is not None, "Actions run ID must be numeric")
    require_plain_directory(bundle_root, "release bundle")
    actual_roots = {entry.name for entry in directory_entries(bundle_root, "release bundle")}
    expected_roots = {"main", *COMPANION_ROOTS.values()}
    require(actual_roots == expected_roots,
            f"release bundle roots differ: expected {sorted(expected_roots)}, got {sorted(actual_roots)}")

    main_root = bundle_root / "main"
    ipa_rows, ipa_manifests, ipa_files = verify_main_ips(main_root, commit, run_id)
    deb_rows = verify_main_debs(main_root, commit, run_id, ipa_manifests, ipa_files)
    companion_rows = []
    for scheme, directory_name in COMPANION_ROOTS.items():
        companion_rows.extend(verify_companion(bundle_root / directory_name, scheme, commit, run_id))

    rows = ipa_rows + deb_rows + companion_rows
    main_ipa_rows = [row for row in ipa_rows if row["kind"] == "main_ipa"]
    main_deb_rows = [row for row in deb_rows if row["kind"] == "main_deb"]
    companion_deb_rows = [row for row in companion_rows if row["kind"] == "companion_deb"]
    actual_files = tree_files(bundle_root)
    recorded = {row["path"]: row for row in rows}
    actual_relative = {path.relative_to(bundle_root).as_posix(): path for path in actual_files}
    require(set(recorded) == set(actual_relative),
            f"release file inventory differs: missing records {sorted(set(actual_relative) - set(recorded))}, "
            f"stale records {sorted(set(recorded) - set(actual_relative))}")
    require(len(actual_files) == 30, f"release bundle must contain exactly 30 files, got {len(actual_files)}")
    require(len(main_ipa_rows) == 3, f"release bundle must contain exactly 3 main IPAs, got {len(main_ipa_rows)}")
    require(len(main_deb_rows) == 6, f"release bundle must contain exactly 6 main DEBs, got {len(main_deb_rows)}")
    require(len(companion_deb_rows) == 2,
            "release bundle must contain exactly 2 companion DEBs")

    index = {
        "schema": 1,
        "source_commit": commit,
        "actions_run_id": run_id,
        "artifact_names": {
            "main": MAIN_ARTIFACT,
            **COMPANION_ARTIFACTS,
        },
        "expected_counts": {
            "main_ipas": 3,
            "main_debs": 6,
            "companion_debs": 2,
            "files": 30,
        },
        "packages": {
            "main_ipas": main_ipa_rows,
            "main_debs": main_deb_rows,
            "companion_debs": companion_deb_rows,
        },
        "files": [recorded[relative] for relative in sorted(recorded)],
        "verification": {
            "exact_file_names": True,
            "manifest_hashes": True,
            "main_package_identities_and_floors": True,
            "main_deb_payload_readback": True,
            "companion_build_and_archive_readback": True,
            "source_commit_and_actions_run_matched": True,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    require(not any(output_dir.iterdir()), "release metadata output directory must be empty")
    (output_dir / "release-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sums = "".join(
        f"{recorded[relative]['sha256']}  {relative}\n"
        for relative in sorted(recorded)
    )
    (output_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--actions-run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        index = verify_bundle(
            args.bundle_root.resolve(),
            args.source_commit,
            args.actions_run_id,
            args.output_dir.resolve(),
        )
    except (
        BundleError,
        PackageError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        struct.error,
        tarfile.TarError,
        plistlib.InvalidFileException,
        zipfile.BadZipFile,
    ) as error:
        print(f"release bundle rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "source_commit": index["source_commit"],
        "actions_run_id": index["actions_run_id"],
        "files": len(index["files"]),
        "main_ipas": len(index["packages"]["main_ipas"]),
        "main_debs": len(index["packages"]["main_debs"]),
        "companion_debs": len(index["packages"]["companion_debs"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
