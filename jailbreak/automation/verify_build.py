#!/usr/bin/env python3
"""Read the actual signed universal companion product after the Actions build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import uuid


CPU_TYPE_ARM64 = 0x0100000C
LC_SEGMENT_64 = 0x19
LC_UUID = 0x1B
LC_DYLD_CHAINED_FIXUPS = 0x80000034
OBJC_IMAGEINFO_SIGNED_CLASS_RO = 0x10


def run(*command):
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _arch_name(cpu_type, cpu_subtype):
    require(cpu_type == CPU_TYPE_ARM64, f"unexpected Mach-O CPU type: {cpu_type:#x}")
    return {0: "arm64", 2: "arm64e"}.get(
        cpu_subtype & 0x00FFFFFF, f"arm64-subtype-{cpu_subtype:#x}")


def _parse_thin_shape(data, base, size):
    end = base + size
    require(base >= 0 and size >= 32 and end <= len(data), "Mach-O slice is out of bounds")
    require(data[base:base + 4] == b"\xcf\xfa\xed\xfe", "expected a little-endian 64-bit Mach-O")
    _, cpu_type, cpu_subtype, file_type, command_count, command_bytes = struct.unpack_from(
        "<6I", data, base)
    require(file_type in (1, 6), "Mach-O is neither an object nor a dynamic library")
    require(0 < command_count <= 4096 and command_count * 8 <= command_bytes <= size - 32,
            "invalid Mach-O load-command table")

    arch = _arch_name(cpu_type, cpu_subtype)
    command_offset = base + 32
    command_end = command_offset + command_bytes
    metadata = {
        "uuid": None,
        "chained_fixups": False,
        "objc_imageinfo_version": None,
        "objc_imageinfo_flags": None,
        "signed_class_ro": None,
    }
    for _ in range(command_count):
        require(command_offset + 8 <= command_end, "truncated Mach-O load command")
        command, command_size = struct.unpack_from("<II", data, command_offset)
        require(8 <= command_size <= command_end - command_offset,
                "invalid Mach-O load-command size")
        if command == LC_UUID:
            require(metadata["uuid"] is None and command_size >= 24, "invalid or duplicate LC_UUID")
            metadata["uuid"] = str(uuid.UUID(
                bytes=bytes(data[command_offset + 8:command_offset + 24])))
        elif command == LC_DYLD_CHAINED_FIXUPS:
            require(not metadata["chained_fixups"], "duplicate LC_DYLD_CHAINED_FIXUPS")
            metadata["chained_fixups"] = True
        elif command == LC_SEGMENT_64:
            require(command_size >= 72, "truncated LC_SEGMENT_64")
            section_count = struct.unpack_from("<I", data, command_offset + 64)[0]
            require(section_count <= (command_size - 72) // 80,
                    "invalid Mach-O section table")
            for index in range(section_count):
                section_offset = command_offset + 72 + index * 80
                section_name = data[section_offset:section_offset + 16].split(b"\0", 1)[0]
                if section_name != b"__objc_imageinfo":
                    continue
                require(metadata["objc_imageinfo_flags"] is None,
                        "duplicate __objc_imageinfo sections")
                section_size = struct.unpack_from("<Q", data, section_offset + 40)[0]
                section_file_offset = struct.unpack_from("<I", data, section_offset + 48)[0]
                require(section_size >= 8 and section_file_offset + 8 <= size,
                        "__objc_imageinfo is out of bounds")
                version, flags = struct.unpack_from("<II", data, base + section_file_offset)
                metadata["objc_imageinfo_version"] = version
                metadata["objc_imageinfo_flags"] = flags
                metadata["signed_class_ro"] = bool(flags & OBJC_IMAGEINFO_SIGNED_CLASS_RO)
        command_offset += command_size
    require(command_offset == command_end, "Mach-O load-command table length mismatch")
    require(metadata["objc_imageinfo_flags"] is not None, "missing __objc_imageinfo metadata")
    return arch, metadata


def macho_metadata(path):
    data = Path(path).read_bytes()
    if data[:4] == b"\xca\xfe\xba\xbe":
        require(len(data) >= 8, "truncated universal Mach-O")
        count = struct.unpack_from(">I", data, 4)[0]
        require(0 < count <= 16 and 8 + count * 20 <= len(data),
                "invalid universal Mach-O table")
        slices = [
            struct.unpack_from(">5I", data, 8 + index * 20)[2:4]
            for index in range(count)
        ]
    elif data[:4] == b"\xcf\xfa\xed\xfe":
        slices = [(0, len(data))]
    else:
        raise RuntimeError("unsupported Mach-O container")

    result = {}
    ranges = []
    for base, size in slices:
        require(not any(base < end and start < base + size for start, end in ranges),
                "overlapping Mach-O slices")
        ranges.append((base, base + size))
        arch, metadata = _parse_thin_shape(data, base, size)
        require(arch not in result, f"duplicate Mach-O architecture: {arch}")
        result[arch] = metadata
    return result


def verify(folder, scheme):
    dylib = folder / "MinisAutomation.dylib"
    assert sorted(run("xcrun", "lipo", "-archs", str(dylib)).split()) == ["arm64", "arm64e"]
    macho = macho_metadata(dylib)
    assert set(macho) == {"arm64", "arm64e"}, macho
    floors = {}
    for arch in ("arm64", "arm64e"):
        load = run("xcrun", "vtool", "-arch", arch, "-show-build", str(dylib))
        assert re.search(r"\bplatform\s+IOS\b", load), load
        minimum = re.findall(r"\bminos\s+([0-9.]+)", load)
        assert len(minimum) == 1 and minimum[0] in ("15.0", "15.0.0"), load
        floors[arch] = "15.0"
        assert macho[arch]["uuid"], f"missing UUID for {arch}"
        assert macho[arch]["signed_class_ro"] is False, (
            f"{arch} emits SignedClassRO metadata that iOS 15 objc4 cannot consume")
    assert macho["arm64e"]["chained_fixups"], "arm64e lost LC_DYLD_CHAINED_FIXUPS"
    subprocess.run(["codesign", "--verify", "--strict", str(dylib)], check=True)
    entitlement_output = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(dylib)],
                                         check=True, capture_output=True).stdout
    entitlements = plistlib.loads(entitlement_output) if entitlement_output.strip() else {}
    assert entitlements == {}, "the tweak inherits its host; do not sign it as a privileged App"
    symbols = run("xcrun", "nm", "-U", str(dylib))
    undefined = run("xcrun", "nm", "-u", str(dylib))
    links = run("xcrun", "otool", "-L", str(dylib))
    (folder / "native-readback.txt").write_text(links + "\n" + symbols + "\nUNDEFINED SYSTEM IMPORTS\n" + undefined, encoding="utf-8")
    defined_classes = re.findall(r"_OBJC_CLASS_\$_(\w+)", symbols)
    assert defined_classes and all(name.startswith("OMMAV_") for name in defined_classes), defined_classes
    defined_names = {line.split()[-1] for line in symbols.splitlines() if len(line.split()) >= 3}
    assert not any(name.startswith(("_MCP", "_IOSMCP")) for name in defined_names), "upstream C symbols must be namespaced"
    assert not re.search(r"\b_OMMA\w*\b", undefined), "unresolved project-owned symbol: " + undefined
    if scheme == "roothide":
        assert "@loader_path/.jbroot/usr/lib/libroothide.dylib" in links
    else:
        assert "libroothide" not in links
    info = plistlib.loads((folder / "MinisAutomation.plist").read_bytes())
    assert info == {"Filter": {"Bundles": ["com.apple.springboard"]}}
    docs = ("LICENSE.ios-mcp", "NOTICE.ios-mcp", "THIRD_PARTY_NOTICES.ios-mcp.md", "LICENSE.RootHide", "source-lock.json")
    manifest = {"schema": 1, "package": "com.openminis.automation", "scheme": scheme, "minimum_os": "15.0",
        "source_commit": os.environ["GITHUB_SHA"], "actions_run_id": os.environ["GITHUB_RUN_ID"],
        "upstream_commit": "62c4464f688c8f269419924b1a19c996de75f175", "dylib_name": dylib.name,
        "dylib_sha256": digest(dylib), "dylib_bytes": dylib.stat().st_size,
        "architectures": ["arm64", "arm64e"], "native_floors": floors, "macho_slices": macho,
        "signature_verified": True, "signature_entitlements": {}, "filter_name": "MinisAutomation.plist",
        "filter_sha256": digest(folder / "MinisAutomation.plist"), "source_lock_sha256": digest(folder / "source-lock.json"),
        "document_sha256": {name: digest(folder / name) for name in docs}, "device_runtime_verified": False}
    (folder / "build.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--scheme", choices=("rootless", "roothide"), required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.folder, args.scheme), indent=2))
