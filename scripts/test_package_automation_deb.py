#!/usr/bin/env python3
"""Companion packaging fixtures; no native compiler, signer or device needed."""
import hashlib
import json
import plistlib
import struct
import tempfile
import unittest
from pathlib import Path

import package_automation_deb as companion


COMMIT = "a" * 40
RUN_ID = "44902887891"


def slice_image(subtype, minimum="15.0", *, signature=True, imageinfo_version=0, imageinfo_flags=0x40,
                include_imageinfo=True, include_uuid=True, chained_fixups=True):
    parts = [int(part) for part in minimum.split(".")]
    encoded = (parts[0] << 16) | (parts[1] << 8)
    floor = struct.pack("<6I", 0x32, 24, 2, encoded, encoded, 0)
    name = b"@rpath/MinisAutomation.dylib\0"
    identity_length = ((24 + len(name) + 7) // 8) * 8
    identity = struct.pack("<6I", 0xD, identity_length, 24, 0, 0x10000, 0x10000) + name
    identity += b"\0" * (identity_length - len(identity))
    uuid = bytes.fromhex("00112233445566778899aabbccddee") + bytes([subtype])
    uuid_command = struct.pack("<2I", companion.LC_UUID, 24) + uuid
    section_count = 1 if include_imageinfo else 0
    segment_length = 72 + section_count * 80
    has_chained_fixups = (subtype & 0x00FFFFFF) == 2 and chained_fixups
    command_bytes = 24 + (24 if include_uuid else 0) + identity_length + segment_length
    command_bytes += 16 if has_chained_fixups else 0
    command_bytes += 16 if signature else 0
    imageinfo_offset = 32 + command_bytes
    payload = struct.pack("<2I", imageinfo_version, imageinfo_flags) if include_imageinfo else b""
    fixups_offset = imageinfo_offset + len(payload)
    if has_chained_fixups:
        payload += b"\x01\0\0\0\x08\0\0\0"
    signature_offset = imageinfo_offset + len(payload)
    if signature:
        payload += b"\xfa\xde\x0c\xc0\0\0\0\x08"
    segment = struct.pack("<II16sQQQQIIII", companion.LC_SEGMENT_64, segment_length, b"__DATA",
                          0x1000, 0x1000, 0, 32 + command_bytes + len(payload), 7, 3, section_count, 0)
    if include_imageinfo:
        segment += struct.pack("<16s16sQQIIIIIIII", b"__objc_imageinfo", b"__DATA",
                               0x1000, 8, imageinfo_offset, 3, 0, 0, 0, 0, 0, 0)
    commands = floor
    if include_uuid:
        commands += uuid_command
    commands += identity + segment
    if has_chained_fixups:
        commands += struct.pack("<4I", companion.LC_DYLD_CHAINED_FIXUPS, 16, fixups_offset, 8)
    if signature:
        commands += struct.pack("<4I", 0x1D, 16, signature_offset, 8)
    command_count = 3 + (1 if include_uuid else 0) + (1 if has_chained_fixups else 0) + (1 if signature else 0)
    header = struct.pack("<8I", 0xFEEDFACF, companion.CPU_TYPE_ARM64, subtype, 6,
                         command_count, len(commands), 0, 0)
    return header + commands + payload


def universal(*, minimum="15.0", signature=True, imageinfo_version=0, imageinfo_flags=0x40,
              include_imageinfo=True, include_uuid=True, chained_fixups=True):
    first = slice_image(0, minimum, signature=signature, imageinfo_version=imageinfo_version,
                        imageinfo_flags=imageinfo_flags, include_imageinfo=include_imageinfo,
                        include_uuid=include_uuid, chained_fixups=chained_fixups)
    second = slice_image(2, minimum, signature=signature, imageinfo_version=imageinfo_version,
                         imageinfo_flags=imageinfo_flags, include_imageinfo=include_imageinfo,
                         include_uuid=include_uuid, chained_fixups=chained_fixups)
    start, next_start = 64, 64 + ((len(first) + 15) // 16) * 16
    table = (struct.pack(">2I", 0xCAFEBABE, 2)
             + struct.pack(">5I", 0x0100000C, 0, start, len(first), 4)
             + struct.pack(">5I", 0x0100000C, 2, next_start, len(second), 4))
    return table.ljust(start, b"\0") + first + b"\0" * (next_start - start - len(first)) + second


class CompanionPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.build_dir = self.root / "build"
        self.build_dir.mkdir()
        dylib = universal()
        (self.build_dir / companion.DYLIB).write_bytes(dylib)
        (self.build_dir / companion.FILTER).write_bytes(
            plistlib.dumps({"Filter": {"Bundles": ["com.apple.springboard"]}}))
        for name in companion.DOCUMENTS:
            if name != "build.json":
                (self.build_dir / name).write_text("fixture-not-a-production-license\n", encoding="utf-8")
        (self.build_dir / "source-lock.json").write_text(json.dumps({"schema": 1,
            "upstream_commit": companion.UPSTREAM, "external_binaries_included": False,
            "generated_source_hashes": {"Fixture.m": "a" * 64}}), encoding="utf-8")
        self.manifest = {"schema": 1, "package": companion.PACKAGE, "scheme": "rootless", "minimum_os": "15.0",
                         "source_commit": COMMIT, "actions_run_id": RUN_ID, "upstream_commit": companion.UPSTREAM,
                         "dylib_name": companion.DYLIB, "filter_name": companion.FILTER,
                         "signature_verified": True, "signature_entitlements": {},
                         "architectures": ["arm64", "arm64e"], "native_floors": {"arm64": "15.0", "arm64e": "15.0"},
                         "macho_slices": companion.macho_metadata(dylib)}
        self.save()

    def save(self):
        self.manifest.update({"dylib_sha256": companion.sha256_file(self.build_dir / companion.DYLIB),
                              "dylib_bytes": (self.build_dir / companion.DYLIB).stat().st_size,
                              "filter_sha256": companion.sha256_file(self.build_dir / companion.FILTER),
                              "source_lock_sha256": companion.sha256_file(self.build_dir / "source-lock.json"),
                              "document_sha256": {name: companion.sha256_file(self.build_dir / name)
                                                  for name in companion.DOCUMENTS if name != "build.json"}})
        (self.build_dir / "build.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_both_requested_schemes_have_exact_payloads_and_no_restart(self):
        for scheme in companion.SCHEMES:
            with self.subTest(scheme=scheme):
                self.manifest["scheme"] = scheme
                self.save()
                result = companion.package(self.build_dir, self.root / scheme, scheme, COMMIT, RUN_ID)
                path = self.root / scheme / result["deb_name"]
                self.assertEqual(result["deb_sha256"], companion.sha256_file(path))
                self.assertEqual(result["deb_bytes"], path.stat().st_size)
                self.assertFalse(result["device_runtime_verified"])
                self.assertFalse(result["default_enabled"])
                controls = companion.controls(scheme, RUN_ID, 1)
                self.assertEqual(controls["postinst"][1], b"#!/bin/sh\nexit 0\n")
                self.assertEqual(controls["postrm"][1], b"#!/bin/sh\nexit 0\n")
                self.assertIn(b"mobilesubstrate", controls["control"][1])
                self.assertEqual(b", roothide\n" in controls["control"][1], scheme == "roothide")

    def test_fails_closed_on_wrong_build_or_scheme(self):
        for field, value in (("source_commit", "b" * 40), ("actions_run_id", "1"),
                             ("upstream_commit", "b" * 40), ("scheme", "roothide"),
                             ("signature_verified", False), ("signature_entitlements", {"get-task-allow": True})):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                self.save()
                with self.assertRaises(companion.PackageError):
                    companion.package(self.build_dir, self.root / field, "rootless", COMMIT, RUN_ID)
                self.manifest[field] = original

    def test_source_hash_is_checked_before_delivery(self):
        (self.build_dir / companion.DYLIB).write_bytes(universal() + b"changed")
        with self.assertRaisesRegex(companion.PackageError, "native companion bytes"):
            companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_actual_native_floor_and_signatures_cannot_be_hidden_by_rehashing(self):
        for data in (universal(minimum="16.0"), universal(signature=False)):
            with self.subTest(sha256=hashlib.sha256(data).hexdigest()):
                (self.build_dir / companion.DYLIB).write_bytes(data)
                self.save()
                with self.assertRaisesRegex(companion.PackageError, "signed iOS 15.0"):
                    companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_rehashed_signed_class_ro_is_rejected_from_actual_bytes(self):
        data = universal(imageinfo_flags=0x50)
        (self.build_dir / companion.DYLIB).write_bytes(data)
        self.save()
        with self.assertRaisesRegex(companion.PackageError, "SignedClassRO"):
            companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_required_macho_metadata_cannot_be_hidden_by_rehashing(self):
        cases = (
            ({"include_imageinfo": False}, "__objc_imageinfo"),
            ({"imageinfo_version": 1}, "version 0"),
            ({"include_uuid": False}, "LC_UUID"),
            ({"chained_fixups": False}, "LC_DYLD_CHAINED_FIXUPS"),
        )
        for options, message in cases:
            with self.subTest(options=options):
                (self.build_dir / companion.DYLIB).write_bytes(universal(**options))
                self.save()
                with self.assertRaisesRegex(companion.PackageError, message):
                    companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_manifest_macho_metadata_must_match_actual_bytes(self):
        self.manifest.pop("macho_slices")
        self.save()
        with self.assertRaisesRegex(companion.PackageError, "macho_slices"):
            companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_filter_cannot_inject_into_other_app_even_with_updated_hash(self):
        (self.build_dir / companion.FILTER).write_bytes(plistlib.dumps({"Filter": {"Bundles": ["com.apple.Preferences"]}}))
        self.save()
        with self.assertRaisesRegex(companion.PackageError, "only inject into SpringBoard"):
            companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_modified_license_and_incomplete_source_provenance_are_rejected(self):
        (self.build_dir / "LICENSE.ios-mcp").write_text("replaced notice", encoding="utf-8")
        with self.assertRaisesRegex(companion.PackageError, "source notice differs"):
            companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)
        self.save()
        (self.build_dir / "source-lock.json").write_text("{}", encoding="utf-8")
        self.save()
        with self.assertRaisesRegex(companion.PackageError, "incomplete companion source provenance"):
            companion.validate_build(self.build_dir, "rootless", COMMIT, RUN_ID)

    def test_refuses_duplicate_architecture_and_overlapping_slices(self):
        for offset, value in ((32, 0), (36, 64)):
            data = bytearray(universal())
            struct.pack_into(">I", data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(companion.PackageError):
                companion.native_slices(data)

    def test_archive_readback_rejects_unknown_payload_and_changed_script(self):
        payload = {"var/jb/Library/MobileSubstrate/DynamicLibraries/MinisAutomation.dylib": (0o755, universal())}
        controls = companion.controls("rootless", RUN_ID, 1)
        staging = self.root / "archive"
        staging.mkdir()
        (staging / "debian-binary").write_bytes(b"2.0\n")
        companion.write_tar(staging / "control.tar.gz", controls)
        companion.write_tar(staging / "data.tar.gz", dict(payload, **{"usr/bin/unexpected-helper": (0o755, b"wrong")}))
        path = self.root / "unexpected.deb"
        companion.write_ar(path, staging)
        with self.assertRaisesRegex(companion.PackageError, "unexpected companion payload"):
            companion.verify_archive(path, controls, payload)

    def test_cannot_overwrite_existing_output(self):
        output = self.root / "out"
        companion.package(self.build_dir, output, "rootless", COMMIT, RUN_ID)
        with self.assertRaisesRegex(companion.PackageError, "fresh companion"):
            companion.package(self.build_dir, output, "rootless", COMMIT, RUN_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
