#!/usr/bin/env python3
"""Portable Debian packaging tests; these files are deliberately not device binaries."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import plistlib
import stat
import struct
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import package_ios15_root_ipa as root
import package_jailbreak_deb as deb
from test_package_ios15_root_ipa import COMMIT, RUN_ID, make_native_app, native_metadata


def image(minimum="15.0", *, cpu=0x0100000C, platform=2, signed=True):
    major, minor, patch_version = deb.parse_version(minimum)
    deployment = struct.pack("<6I", 0x32, 24, platform, major << 16 | minor << 8 | patch_version, 0x001A0200, 0)
    signature = struct.pack("<4I", 0x1D, 16, 72, 4) if signed else b""
    commands = deployment + signature
    return (struct.pack("<8I", 0xFEEDFACF, cpu, 0, 2, 2 if signed else 1, len(commands), 0, 0)
            + commands + b"test")


def prepare_source(folder: Path, floor="16.2"):
    app = make_native_app(folder / "source")
    for binary in app.rglob("*"):
        if root.is_macho(binary):
            role = next((root.ROOT_PLUGIN_ROLES[parent.name] for parent in binary.parents
                         if parent.name in root.ROOT_PLUGIN_ROLES), "app")
            binary.write_bytes(image(".".join(str(part) for part in root.ROOT_ROLE_FLOORS[role][:2])))
    helper = folder / "helper"
    helper.write_bytes(image())

    def signatures(staged):
        roles = {".": "app", root.HELPER_NAME: "helper"}
        roles.update({plugin.relative_to(staged).as_posix(): root.ROOT_PLUGIN_ROLES[plugin.name]
                      for plugin in root.plugin_bundles(staged)})
        return {relative: root.expected_entitlements(role) for relative, role in roles.items()}

    with patch.object(root.sys, "platform", "darwin"), patch.object(root, "verify_compiled_client"), \
         patch.object(root.shutil, "which", return_value="xcrun"), \
         patch.object(root.subprocess, "run", side_effect=native_metadata), \
         patch.object(root, "sign_and_verify", side_effect=signatures):
        manifest = root.package_root_app(app, helper, folder / "ipa", commit=COMMIT, run_id=RUN_ID, minimum_os=floor)
    return folder / "ipa/manifest.json", folder / "ipa" / manifest["ipa_name"], manifest


def ar_parts(payload: bytes):
    parts = {}
    cursor = 8
    assert payload[:8] == b"!<arch>\n"
    while cursor < len(payload):
        header = payload[cursor:cursor + 60]
        assert len(header) == 60 and header[58:] == b"`\n"
        name = header[:16].decode().strip().removesuffix("/")
        length = int(header[48:58])
        parts[name] = payload[cursor + 60:cursor + 60 + length]
        cursor += 60 + length + (length % 2)
    return parts


def tar_members(payload: bytes):
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(payload)), mode="r:") as archive:
        return {member.name.removeprefix("./"): (member, archive.extractfile(member).read() if member.isreg() else None)
                for member in archive.getmembers()}


class DebTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.manifest_path, self.ipa, self.manifest = prepare_source(self.folder)

    def package(self, scheme="rootless", **options):
        return deb.package_deb(self.manifest_path, self.folder / scheme, scheme,
                               commit=options.get("commit", COMMIT), run_id=options.get("run_id", RUN_ID))

    def save(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def tamper_source(self, relative, *, content=None, mode=None, create_system=None, path=None):
        name = deb.IPA_PREFIX + relative
        with zipfile.ZipFile(self.ipa) as archive:
            members = [(item, archive.read(item)) for item in archive.infolist()]
        with zipfile.ZipFile(self.ipa, "w") as archive:
            for item, original in members:
                if name == item.filename:
                    if mode is not None:
                        item.external_attr = mode << 16
                    if create_system is not None:
                        item.create_system = create_system
                    if path is not None:
                        item.filename = path
                    if content is not None:
                        original = content
                archive.writestr(item, original)
        self.manifest["ipa_bytes"] = self.ipa.stat().st_size
        self.manifest["ipa_sha256"] = root.sha256_file(self.ipa)
        self.save()

    def test_all_schemes_preserve_bundle_and_have_only_fixed_registration_scripts(self):
        layouts = {
            "rootful": ("iphoneos-arm", "Applications/MinisRoot.app", "/usr/bin/uicache"),
            "rootless": ("iphoneos-arm64", "var/jb/Applications/MinisRoot.app", "/var/jb/usr/bin/uicache"),
            "roothide": ("iphoneos-arm64e", "Applications/MinisRoot.app", "/usr/bin/uicache"),
        }
        for scheme, (architecture, app, uicache) in layouts.items():
            with self.subTest(scheme=scheme):
                result = self.package(scheme)
                path = self.folder / scheme / result["deb_name"]
                members = ar_parts(path.read_bytes())
                self.assertEqual(list(members), ["debian-binary", "control.tar.gz", "data.tar.gz"])
                self.assertEqual(members["debian-binary"], b"2.0\n")
                controls = tar_members(members["control.tar.gz"])
                self.assertEqual(set(controls), {"control", "postinst", "postrm"})
                control = dict(line.split(": ", 1) for line in controls["control"][1].decode().splitlines())
                self.assertEqual(control["Package"], "com.openminis.app.root")
                self.assertEqual(control["Architecture"], architecture)
                self.assertEqual(control["Depends"], "firmware (>= 16.2), uikittools")
                self.assertEqual(control["X-Minis-Actions-Run"], RUN_ID)
                self.assertEqual(control["X-Minis-Source-Commit"], COMMIT)
                self.assertEqual(control["X-Minis-Install-Floor"], "16.2")
                self.assertEqual(control["X-Minis-Package-Schema"], "1")
                self.assertEqual(control["X-Minis-Jailbreak-Scheme"], scheme)
                for key, action, flag in (("postinst", "configure", "-p"), ("postrm", "remove", "-u")):
                    entry, body = controls[key]
                    self.assertEqual(entry.mode, 0o755)
                    self.assertEqual(body.decode().splitlines(), ["#!/bin/sh", "set -eu", 'case "${1:-}" in',
                                     f"  {action}) exec {uicache} {flag} /{app} ;;", "esac", "exit 0"])
                records = tar_members(members["data.tar.gz"])
                with zipfile.ZipFile(self.ipa) as source:
                    expected = {app + "/" + member.filename[len(deb.IPA_PREFIX):]: (member, source.read(member))
                                for member in source.infolist()}
                self.assertEqual({name for name, (entry, _) in records.items() if entry.isreg()}, set(expected))
                for name, (entry, data) in records.items():
                    self.assertEqual((entry.uid, entry.gid, entry.uname, entry.gname), (0, 0, "root", "wheel"))
                    if entry.isdir():
                        self.assertEqual(entry.mode, 0o755)
                    else:
                        source_member, content = expected[name]
                        self.assertEqual((entry.mode, data), (stat.S_IMODE(source_member.external_attr >> 16), content))
                self.assertEqual(records[app + "/minis-rootprobe"][0].mode, 0o755)
                self.assertEqual(result["deb_sha256"], root.sha256_file(path))
                self.assertEqual(result["source_ipa_sha256"], root.sha256_file(self.ipa))
                self.assertEqual(result["source_manifest_sha256"], root.sha256_file(self.manifest_path))
                self.assertTrue(result["native_floor_readback_verified"])
                self.assertTrue(result["deb_archive_readback_verified"])
                self.assertFalse(result["main_app_runs_as_root"])
                self.assertFalse(result["device_runtime_verified"])
                self.assertEqual(result["persona_support"], "unverified")
                self.assertEqual(result, json.loads((path.parent / "manifest.json").read_text(encoding="utf-8")))

    def test_15_and_16_packages_have_distinct_versions_and_only_compatible_extensions(self):
        indices = {}
        for minimum, extensions in (("15.0", {"MinisShare.appex"}),
                                    ("16.0", {"MinisShare.appex", "MinisFileProvider.appex"})):
            source_path, _, _ = prepare_source(self.folder / minimum, minimum)
            output = self.folder / minimum / "deb"
            result = deb.package_deb(source_path, output, "rootless", commit=COMMIT, run_id=RUN_ID)
            index = ar_parts((output / result["deb_name"]).read_bytes())
            entries = tar_members(index["data.tar.gz"])
            self.assertEqual({entry.split("/")[-2] for entry in entries if entry.endswith(".appex/Info.plist")}, extensions)
            self.assertIn(f"firmware (>= {minimum})".encode(), tar_members(index["control.tar.gz"])["control"][1])
            indices[minimum] = result["package_version"]
        self.assertNotEqual(indices["15.0"], indices["16.0"])

    def test_refuses_mismatched_run_commit_and_lane(self):
        for options in ({"run_id": "123"}, {"commit": "a" * 40}):
            with self.subTest(options=options), self.assertRaisesRegex(deb.PackageError, "source commit or Actions run"):
                self.package(**options)
        self.manifest["minimum_os"] = "15.0"
        self.save()
        with self.assertRaisesRegex(deb.PackageError, "installation floor"):
            self.package()

    def test_refuses_modified_ipa_even_if_zip_crc_is_valid(self):
        with zipfile.ZipFile(self.ipa, "a") as source:
            source.writestr(deb.IPA_PREFIX + "readme", b"different")
        self.manifest["ipa_bytes"] = self.ipa.stat().st_size
        self.save()
        with self.assertRaisesRegex(deb.PackageError, "IPA hash"):
            self.package()

    def test_refuses_modified_signed_executable_even_if_ipa_hash_is_updated(self):
        self.tamper_source("Minis", content=image()[:-4] + b"fake")
        with self.assertRaisesRegex(deb.PackageError, "signed executable hashes"):
            self.package()

    def test_refuses_package_if_signed_identity_differs_from_receipt(self):
        with zipfile.ZipFile(self.ipa) as source:
            info = plistlib.loads(source.read(deb.IPA_PREFIX + "Info.plist"))
        info["MinisRootActionsRunID"] = "333"
        self.tamper_source("Info.plist", content=plistlib.dumps(info))
        with self.assertRaisesRegex(deb.PackageError, "signed app metadata"):
            self.package()

    def test_refuses_unverified_or_wrong_signature_profile(self):
        self.manifest["signature_entitlements_verified"] = False
        self.save()
        with self.assertRaisesRegex(deb.PackageError, "missing verified packaging evidence"):
            self.package()
        self.manifest["signature_entitlements_verified"] = True
        self.manifest["signature_entitlements"]["PlugIns/MinisFileProvider.appex"] = root.expected_entitlements("app")
        self.save()
        with self.assertRaisesRegex(deb.PackageError, "signature evidence"):
            self.package()

    def test_rechecks_actual_macho_floor_even_if_ipa_and_executable_hashes_are_replaced(self):
        modified = image("16.1")
        self.tamper_source("Minis", content=modified)
        self.manifest["archive_executable_hashes"]["Minis"] = hashlib.sha256(modified).hexdigest()
        self.save()
        with self.assertRaisesRegex(deb.PackageError, "native floor"):
            self.package()

    def test_refuses_missing_native_image_and_signature_metadata(self):
        self.manifest["native_floor_audit"].pop()
        self.save()
        with self.assertRaisesRegex(deb.PackageError, "incomplete native floor evidence"):
            self.package()
        self.tamper_source("Minis", content=image(signed=False))
        with self.assertRaisesRegex(deb.PackageError, "missing iOS floor or code signature"):
            self.package()

    def test_refuses_setuid_or_unix_symlink_modes(self):
        for mode in (stat.S_IFREG | 0o4755, stat.S_IFLNK | 0o755):
            self.tamper_source(root.HELPER_NAME, mode=mode)
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(deb.PackageError, "unsafe IPA member mode"):
                self.package()

    def test_refuses_duplicate_or_escaping_ipa_paths(self):
        self.tamper_source("Minis", path=deb.IPA_PREFIX + "../Minis.app/Minis")
        with self.assertRaisesRegex(deb.PackageError, "invalid or duplicate IPA path"):
            self.package()

    def test_readback_detects_changed_registration_script_payload_or_mode(self):
        files = deb.inspect_ipa(self.ipa, self.manifest, COMMIT, RUN_ID)
        controls = deb.control_members(self.manifest, "rootless", (sum(record[1] for record in files.values()) + 1023) // 1024)
        for name, altered in (("control", (0o644, b"Package: wrong\n")),
                              ("postinst", (0o755, b"#!/bin/sh\nexit 0\n")),
                              ("postrm", (0o644, controls["postrm"][1]))):
            with self.subTest(name=name):
                scratch = self.folder / ("ar-" + name)
                scratch.mkdir()
                (scratch / "debian-binary").write_bytes(b"2.0\n")
                deb.write_tars(scratch, self.ipa, "rootless", files, dict(controls, **{name: altered}))
                archive = scratch / "candidate.deb"
                deb.write_ar(archive, scratch)
                with self.assertRaisesRegex(deb.PackageError, "tar mode or size mismatch|tar data mismatch"):
                    deb.verify_deb(archive, "rootless", files, controls)

    def test_refuses_appended_ar_member_and_reused_output_directory(self):
        result = self.package()
        archive = self.folder / "rootless" / result["deb_name"]
        files = deb.inspect_ipa(self.ipa, self.manifest, COMMIT, RUN_ID)
        controls = deb.control_members(self.manifest, "rootless", (sum(record[1] for record in files.values()) + 1023) // 1024)
        with archive.open("ab") as stream:
            stream.write(b"junk")
        with self.assertRaisesRegex(deb.PackageError, "extra Debian ar member"):
            deb.verify_deb(archive, "rootless", files, controls)
        with self.assertRaisesRegex(deb.PackageError, "fresh output directory"):
            self.package()


class MachOReadbackTests(unittest.TestCase):
    def test_checks_device_architecture_platform_and_embedded_signature(self):
        examples = ((image("16.2", cpu=0x01000007), "architecture"),
                    (image("16.2", platform=7), "device platform"),
                    (image("16.2", signed=False), "code signature"),
                    (image("16.2")[:-1], "signature bounds"))
        for payload, diagnostic in examples:
            with self.subTest(diagnostic=diagnostic), self.assertRaisesRegex(deb.PackageError, diagnostic):
                deb.macho_minimum(io.BytesIO(payload), len(payload))

    def test_fat_arm64_and_legacy_load_command(self):
        binary = image("16.2")
        payload = struct.pack(">7I", 0xCAFEBABE, 1, 0x0100000C, 0, 32, len(binary), 0) + bytes(4) + binary
        self.assertEqual(deb.macho_minimum(io.BytesIO(payload), len(payload)), (16, 2, 0))
        header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 2, 32, 0, 0)
        payload = header + struct.pack("<8I", 0x25, 16, 0x000E0100, 0x001A0200, 0x1D, 16, 64, 4) + b"test"
        self.assertEqual(deb.macho_minimum(io.BytesIO(payload), len(payload)), (14, 1, 0))


if __name__ == "__main__":
    unittest.main()
