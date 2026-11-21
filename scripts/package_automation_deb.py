#!/usr/bin/env python3
"""Package the Actions-built SpringBoard companion for Rootless or RootHide.

This does not compile, install, respring, or infer device compatibility. It
checks the signed native build evidence and reads the final Debian archive back.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import plistlib
import struct
import sys
import tarfile
import tempfile
from pathlib import Path

from package_ios15_ipa import PackageError, sha256_file
from package_ios15_root_ipa import validate_build_identity
from package_jailbreak_deb import AR_MEMBERS, relative_name, write_ar


PACKAGE = "com.openminis.automation"
UPSTREAM = "62c4464f688c8f269419924b1a19c996de75f175"
SCHEMES = {"rootless": ("iphoneos-arm64", "var/jb/"), "roothide": ("iphoneos-arm64e", "")}
DYLIB = "MinisAutomation.dylib"
FILTER = "MinisAutomation.plist"
DOCUMENTS = ("LICENSE.ios-mcp", "NOTICE.ios-mcp", "THIRD_PARTY_NOTICES.ios-mcp.md",
             "LICENSE.RootHide", "source-lock.json", "build.json")
MAX_DYLIB_BYTES = 64 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise PackageError(message)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON field: {key}")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(PackageError("non-finite JSON number")))


def native_slices(data):
    """Check both real signed device slices; Debian architecture is separate."""
    require(0 < len(data) <= MAX_DYLIB_BYTES, "invalid companion dylib size")
    require(data[:4] == b"\xca\xfe\xba\xbe" and len(data) >= 48, "expected universal arm64/arm64e dylib")
    count = struct.unpack_from(">I", data, 4)[0]
    require(count == 2, "expected exactly two native slices")
    records, ranges = {}, []
    for index in range(count):
        cpu, subtype, offset, size, alignment = struct.unpack_from(">5I", data, 8 + index * 20)
        arch = {0: "arm64", 2: "arm64e"}.get(subtype & 0x00FFFFFF)
        require(cpu == 0x0100000C and arch is not None and arch not in records,
                "unexpected or duplicate native architecture")
        require(alignment <= 30 and offset >= 48 and offset % (1 << alignment) == 0
                and size >= 32 and offset + size <= len(data), "invalid native slice bounds")
        require(not any(offset < end and start < offset + size for start, end in ranges), "overlapping native slices")
        ranges.append((offset, offset + size))
        image = data[offset:offset + size]
        require(image[:4] == b"\xcf\xfa\xed\xfe", "expected little-endian device Mach-O")
        _, real_cpu, real_subtype, kind, command_count, command_bytes = struct.unpack_from("<6I", image)
        require(real_cpu == cpu and (real_subtype & 0x00FFFFFF) == (subtype & 0x00FFFFFF) and kind == 6,
                "slice is not the declared device dynamic library")
        require(0 < command_count <= 4096 and command_count * 8 <= command_bytes <= size - 32,
                "invalid native load command table")
        position, minimum, identity, signature = 32, None, None, False
        for _ in range(command_count):
            require(position + 8 <= 32 + command_bytes, "truncated native load command")
            command, length = struct.unpack_from("<2I", image, position)
            require(8 <= length <= 32 + command_bytes - position, "invalid native load command length")
            if command == 0x32:
                require(minimum is None and length >= 24, "invalid or duplicate native floor")
                platform, encoded = struct.unpack_from("<2I", image, position + 8)
                require(platform == 2, "companion must target iOS devices")
                minimum = (encoded >> 16, (encoded >> 8) & 255, encoded & 255)
            elif command == 0x25:
                require(minimum is None and length >= 16, "invalid or duplicate native floor")
                encoded = struct.unpack_from("<I", image, position + 8)[0]
                minimum = (encoded >> 16, (encoded >> 8) & 255, encoded & 255)
            elif command == 0xD:
                require(identity is None and length >= 24, "invalid or duplicate dylib identity")
                name_offset = struct.unpack_from("<I", image, position + 8)[0]
                require(24 <= name_offset < length, "invalid dylib identity offset")
                name = image[position + name_offset:position + length].split(b"\0", 1)[0]
                identity = name.decode("utf-8")
                require(identity.endswith("/" + DYLIB), "unexpected companion install name")
            elif command == 0x1D:
                require(not signature and length >= 16, "invalid or duplicate native signature")
                sig_offset, sig_bytes = struct.unpack_from("<2I", image, position + 8)
                require(sig_offset >= 32 + command_bytes and sig_bytes >= 8 and sig_offset + sig_bytes <= size,
                        "native signature out of bounds")
                require(image[sig_offset:sig_offset + 4] == b"\xfa\xde\x0c\xc0", "missing embedded signature blob")
                signature = True
            position += length
        require(position == 32 + command_bytes and minimum == (15, 0, 0) and identity is not None and signature,
                "missing signed iOS 15.0 companion evidence")
        records[arch] = "15.0"
    require(set(records) == {"arm64", "arm64e"}, "missing native architecture")
    return records


def validate_build(folder, scheme, commit, run_id):
    validate_build_identity(commit, run_id)
    require(scheme in SCHEMES, "unsupported companion package scheme")
    manifest = strict_json((folder / "build.json").read_text(encoding="utf-8"))
    require(manifest.get("schema") == 1 and manifest.get("package") == PACKAGE
            and manifest.get("scheme") == scheme and manifest.get("minimum_os") == "15.0"
            and manifest.get("source_commit") == commit and manifest.get("actions_run_id") == run_id
            and manifest.get("upstream_commit") == UPSTREAM, "companion build identity mismatch")
    require(manifest.get("dylib_name") == DYLIB and manifest.get("filter_name") == FILTER
            and manifest.get("signature_verified") is True and manifest.get("signature_entitlements") == {},
            "missing signed companion build evidence")
    required = {DYLIB, FILTER, *DOCUMENTS}
    for name in required:
        path = folder / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1,
                f"missing or unsafe companion output: {name}")
    data = (folder / DYLIB).read_bytes()
    slices = native_slices(data)
    require(set(manifest.get("architectures", [])) == set(slices) and manifest.get("native_floors") == slices
            and manifest.get("dylib_bytes") == len(data) and manifest.get("dylib_sha256") == hashlib.sha256(data).hexdigest(),
            "native companion bytes or floor differ from Actions evidence")
    require(sha256_file(folder / FILTER) == manifest.get("filter_sha256")
            and plistlib.loads((folder / FILTER).read_bytes()) == {"Filter": {"Bundles": ["com.apple.springboard"]}},
            "companion may only inject into SpringBoard")
    require(sha256_file(folder / "source-lock.json") == manifest.get("source_lock_sha256"), "source lock mismatch")
    source_lock = strict_json((folder / "source-lock.json").read_text(encoding="utf-8"))
    require(type(source_lock) is dict and source_lock.get("schema") == 1
            and source_lock.get("upstream_commit") == UPSTREAM
            and source_lock.get("external_binaries_included") is False
            and type(source_lock.get("generated_source_hashes")) is dict
            and source_lock["generated_source_hashes"], "incomplete companion source provenance")
    for name in DOCUMENTS:
        require(0 < (folder / name).stat().st_size <= 2 * 1024 * 1024, f"invalid source notice: {name}")
        if name != "build.json":
            require(manifest.get("document_sha256", {}).get(name) == sha256_file(folder / name),
                    f"source notice differs from Actions build: {name}")
    return manifest


def controls(scheme, run_id, installed_kib):
    architecture, _ = SCHEMES[scheme]
    dependencies = "firmware (>= 15.0), mobilesubstrate" + (", roothide" if scheme == "roothide" else "")
    text = (f"Package: {PACKAGE}\nName: Minis Automation\nVersion: 1.0+ci{run_id}\nArchitecture: {architecture}\n"
            f"Depends: {dependencies}\nMaintainer: OpenMinis\nSection: Tweaks\nInstalled-Size: {installed_kib}\n"
            "Description: Explicitly authorized local device automation for Minis Root\n")
    # Loading a newly installed tweak needs a user-directed SpringBoard restart.
    # Maintainer scripts never terminate or restart a system process.
    return {"control": (0o644, text.encode()), "postinst": (0o755, b"#!/bin/sh\nexit 0\n"),
            "postrm": (0o755, b"#!/bin/sh\nexit 0\n")}


def write_tar(path, members):
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, \
         tarfile.open(fileobj=zipped, mode="w|", format=tarfile.GNU_FORMAT) as archive:
        for name, (mode, data) in sorted(members.items()):
            require(relative_name(name), "unsafe package member")
            entry = tarfile.TarInfo("./" + name)
            entry.mode, entry.uid, entry.gid, entry.mtime = mode, 0, 0, 0
            entry.uname, entry.gname, entry.size = "root", "wheel", len(data)
            archive.addfile(entry, io.BytesIO(data))


def verify_archive(path, control, payload):
    expected = {"debian-binary": None, "control.tar.gz": control, "data.tar.gz": payload}
    with path.open("rb") as stream:
        require(stream.read(8) == b"!<arch>\n", "invalid Debian archive")
        for name in AR_MEMBERS:
            header = stream.read(60)
            require(len(header) == 60 and header[:16].rstrip().rstrip(b"/") == name.encode()
                    and header[58:] == b"`\n" and header[48:58].strip().isdigit(), "unexpected Debian member")
            size = int(header[48:58])
            require(0 < size <= path.stat().st_size - stream.tell(), "invalid Debian member bounds")
            end = stream.tell() + size
            if name == "debian-binary":
                require(size == 4 and stream.read(size) == b"2.0\n", "invalid Debian version")
            else:
                seen = set()
                with tarfile.open(fileobj=stream, mode="r|gz") as archive:
                    for entry in archive:
                        key = entry.name.removeprefix("./")
                        require(relative_name(key) and entry.isreg() and key in expected[name] and key not in seen,
                                "unexpected companion payload or symlink")
                        require(entry.uid == entry.gid == 0 and entry.uname == "root" and entry.gname == "wheel",
                                "unexpected companion ownership")
                        mode, data = expected[name][key]
                        source = archive.extractfile(entry)
                        require(entry.mode == mode and entry.size == len(data) and source is not None
                                and source.read() == data, "companion archive bytes or permissions changed")
                        seen.add(key)
                require(seen == set(expected[name]), "incomplete companion payload")
            stream.seek(end)
            if size % 2:
                require(stream.read(1) == b"\n", "invalid ar padding")
        require(stream.read(1) == b"", "trailing Debian members")


def package(folder, output_dir, scheme, commit, run_id):
    build = validate_build(folder, scheme, commit, run_id)
    architecture, prefix = SCHEMES[scheme]
    payload = {prefix + "Library/MobileSubstrate/DynamicLibraries/" + name:
               (0o755 if name == DYLIB else 0o644, (folder / name).read_bytes()) for name in (DYLIB, FILTER)}
    payload.update({prefix + "usr/share/doc/minis-automation/" + name: (0o644, (folder / name).read_bytes())
                    for name in DOCUMENTS})
    control = controls(scheme, run_id, (sum(len(data) for _, data in payload.values()) + 1023) // 1024)
    output_dir.mkdir(parents=True, exist_ok=True)
    require(not any(output_dir.iterdir()), "use a fresh companion package output directory")
    filename = f"{PACKAGE}_1.0+ci{run_id}_{architecture}.deb"
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        (staging / "debian-binary").write_bytes(b"2.0\n")
        write_tar(staging / "control.tar.gz", control)
        write_tar(staging / "data.tar.gz", payload)
        path = staging / filename
        write_ar(path, staging)
        verify_archive(path, control, payload)
        result = {"schema": 1, "package": PACKAGE, "scheme": scheme, "architecture": architecture,
                  "minimum_os": "15.0", "source_commit": commit, "actions_run_id": run_id, "upstream_commit": UPSTREAM,
                  "source_build_sha256": sha256_file(folder / "build.json"), "dylib_sha256": build["dylib_sha256"],
                  "native_floors": build["native_floors"], "signature_verified": True,
                  "deb_name": filename, "deb_bytes": path.stat().st_size, "deb_sha256": sha256_file(path),
                  "deb_archive_readback_verified": True, "default_enabled": False, "bind_address": "127.0.0.1",
                  "port": 8091, "authorization": "explicit-expiring-bearer-session", "device_runtime_verified": False}
        path.rename(output_dir / filename)
        (output_dir / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scheme", choices=SCHEMES, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--actions-run-id", required=True)
    args = parser.parse_args()
    try:
        result = package(args.build_dir, args.output_dir, args.scheme, args.source_commit, args.actions_run_id)
    except (OSError, ValueError, KeyError, TypeError, struct.error, PackageError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
