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


def slice_image(subtype, minimum="15.0", *, signature=True):
    parts = [int(part) for part in minimum.split(".")]
    encoded = (parts[0] << 16) | (parts[1] << 8)
    floor = struct.pack("<6I", 0x32, 24, 2, encoded, encoded, 0)
    name = b"@rpath/MinisAutomation.dylib\0"
    identity_length = ((24 + len(name) + 7) // 8) * 8
    identity = struct.pack("<6I", 0xD, identity_length, 24, 0, 0x10000, 0x10000) + name
    identity += b"\0" * (identity_length - len(identity))
    commands = floor + identity
    if signature:
        commands += struct.pack("<4I", 0x1D, 16, 32 + len(commands) + 16, 8)
    header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, subtype, 6, 3 if signature else 2, len(commands), 0, 0)
    return header + commands + (b"\xfa\xde\x0c\xc0\0\0\0\x08" if signature else b"")


def universal(*, minimum="15.0", signature=True):
    first, second = slice_image(0, minimum, signature=signature), slice_image(2, minimum, signature=signature)
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
        (self.build_dir / companion.DYLIB).write_bytes(universal())
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
                         "architectures": ["arm64", "arm64e"], "native_floors": {"arm64": "15.0", "arm64e": "15.0"}}
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
