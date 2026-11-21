#!/usr/bin/env python3
"""Windows-safe focused tests for the downloaded release bundle verifier."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import verify_release_bundle as release


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ios15-m0-baseline.yml"
COMMIT = "a" * 40
RUN_ID = "44908106125"
VERSION = "1.15"
PLUGINS = {
    "15.0": {"MinisShare.appex"},
    "16.0": {"MinisShare.appex", "MinisFileProvider.appex"},
    "16.2": {"MinisShare.appex", "MinisFileProvider.appex", "AgentWidgetExtension.appex"},
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> bytes:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="minis-release-test-")
        self.root = Path(self.temporary.name) / "release"
        self.output = Path(self.temporary.name) / "metadata"
        self._build_valid_bundle()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_valid_bundle(self) -> None:
        main = self.root / "main"
        for floor, directory_name in release.MAIN_ROOTS.items():
            directory = main / directory_name
            directory.mkdir(parents=True, exist_ok=True)
            floor_name = "15" if floor == "15.0" else floor
            ipa_name = f"Minis-{VERSION}-ios{floor_name}-root-{COMMIT[:8]}.ipa"
            ipa_bytes = f"root-ipa-{floor}".encode()
            ipa = directory / ipa_name
            ipa.write_bytes(ipa_bytes)
            write_json(directory / "manifest.json", {
                "bundle_id": release.ROOT_ID,
                "app_group_id": release.ROOT_GROUP,
                "minimum_os": floor,
                "marketing_version": VERSION,
                "file_access_profile": "root-helper",
                "build_configuration": "Release",
                "source_commit": COMMIT,
                "actions_run_id": RUN_ID,
                "root_management_schema": 1,
                "package_floor": floor,
                "host_file_probe_build": {
                    "schema": 1,
                    "source_commit": COMMIT,
                    "actions_run_id": RUN_ID,
                },
                "root_helper_included": True,
                "plugins_kept": sorted(PLUGINS[floor]),
                "plugins_stripped": sorted(set(PLUGINS["16.2"]) - PLUGINS[floor]),
                "ipa_name": ipa_name,
                "ipa_bytes": len(ipa_bytes),
                "ipa_sha256": digest(ipa_bytes),
            })

            source_manifest_path = directory / "manifest.json"
            for scheme in ("rootless", "roothide"):
                architecture = release.ROOT_SCHEMES[scheme][0]
                install_path = release.ROOT_SCHEMES[scheme][1]
                deb_name = f"{release.ROOT_ID}_{VERSION}+ci{RUN_ID}+ios{floor}_{architecture}.deb"
                deb_dir = main / release.ROOT_DEB_DIRS[(floor, scheme)]
                deb_dir.mkdir(parents=True, exist_ok=True)
                deb_bytes = f"root-deb-{floor}-{scheme}".encode()
                deb = deb_dir / deb_name
                deb.write_bytes(deb_bytes)
                write_json(deb_dir / "manifest.json", {
                    "schema": 1,
                    "package": release.ROOT_ID,
                    "bundle_id": release.ROOT_ID,
                    "package_version": f"{VERSION}+ci{RUN_ID}+ios{floor}",
                    "scheme": scheme,
                    "architecture": architecture,
                    "install_path": install_path,
                    "minimum_os": floor,
                    "source_commit": COMMIT,
                    "actions_run_id": RUN_ID,
                    "source_ipa_sha256": digest(ipa_bytes),
                    "source_manifest_sha256": digest(source_manifest_path.read_bytes()),
                    "source_signed_executables_verified": True,
                    "source_signature_evidence_verified": True,
                    "native_floor_readback_verified": True,
                    "deb_archive_readback_verified": True,
                    "root_helper_mode": "0755",
                    "setuid_files": [],
                    "main_app_runs_as_root": False,
                    "persona_support": "unverified",
                    "device_runtime_verified": False,
                    "deb_name": deb_name,
                    "deb_bytes": len(deb_bytes),
                    "deb_sha256": digest(deb_bytes),
                })

        for scheme, directory_name in release.COMPANION_ROOTS.items():
            companion = self.root / directory_name
            architecture = release.AUTOMATION_SCHEMES[scheme][0]
            dylib_sha = digest(f"dylib-{scheme}".encode())
            floors = {"arm64": "15.0", "arm64e": "15.0"}
            build_manifest = {
                "schema": 1,
                "package": release.AUTOMATION_PACKAGE,
                "scheme": scheme,
                "minimum_os": "15.0",
                "source_commit": COMMIT,
                "actions_run_id": RUN_ID,
                "upstream_commit": release.AUTOMATION_UPSTREAM,
                "dylib_name": "MinisAutomation.dylib",
                "dylib_sha256": dylib_sha,
                "dylib_bytes": 123,
                "architectures": ["arm64", "arm64e"],
                "native_floors": floors,
                "signature_verified": True,
                "signature_entitlements": {},
                "filter_name": "MinisAutomation.plist",
                "filter_sha256": digest(b"filter"),
                "source_lock_sha256": digest(b"source-lock"),
                "document_sha256": {},
                "device_runtime_verified": False,
            }
            build_dir = companion / "automation-build"
            build_dir.mkdir(parents=True, exist_ok=True)
            build_bytes = write_json(build_dir / "build.json", build_manifest)
            (build_dir / "build.log").write_text("build complete\n", encoding="utf-8")
            readback = "UNDEFINED SYSTEM IMPORTS\n"
            if scheme == "roothide":
                readback = "@loader_path/.jbroot/usr/lib/libroothide.dylib\n" + readback
            (build_dir / "native-readback.txt").write_text(readback, encoding="utf-8")
            (build_dir / "source-lock.json").write_text("{}\n", encoding="utf-8")

            deb_name = f"{release.AUTOMATION_PACKAGE}_1.0+ci{RUN_ID}_{architecture}.deb"
            deb_bytes = f"companion-{scheme}".encode()
            deb_dir = companion / "automation-deb"
            deb_dir.mkdir(parents=True, exist_ok=True)
            deb = deb_dir / deb_name
            deb.write_bytes(deb_bytes)
            write_json(deb_dir / "manifest.json", {
                "schema": 1,
                "package": release.AUTOMATION_PACKAGE,
                "scheme": scheme,
                "architecture": architecture,
                "minimum_os": "15.0",
                "source_commit": COMMIT,
                "actions_run_id": RUN_ID,
                "upstream_commit": release.AUTOMATION_UPSTREAM,
                "source_build_sha256": digest(build_bytes),
                "dylib_sha256": dylib_sha,
                "native_floors": floors,
                "signature_verified": True,
                "deb_name": deb_name,
                "deb_bytes": len(deb_bytes),
                "deb_sha256": digest(deb_bytes),
                "deb_archive_readback_verified": True,
                "default_enabled": False,
                "bind_address": "127.0.0.1",
                "port": 8091,
                "device_runtime_verified": False,
            })

    def verify(self) -> dict:
        with mock.patch.object(release, "verify_main_ipa_archive", return_value={}), \
             mock.patch.object(release, "verify_main_deb_archive"), \
             mock.patch.object(
                 release,
                 "verify_companion_archive",
                 side_effect=lambda _deb, scheme, *_args: {
                     "dylib_sha256": digest(f"dylib-{scheme}".encode())
                 },
             ):
            return release.verify_bundle(self.root, COMMIT, RUN_ID, self.output)

    def test_valid_bundle_writes_exact_index_and_checksums(self) -> None:
        index = self.verify()
        self.assertEqual(index["expected_counts"]["files"], 30)
        self.assertEqual(len(index["files"]), 30)
        self.assertEqual(len(index["packages"]["main_ipas"]), 3)
        self.assertEqual(len(index["packages"]["main_debs"]), 6)
        self.assertEqual(len(index["packages"]["companion_debs"]), 2)
        self.assertEqual(index["build"]["configuration"], "Release")
        self.assertTrue(index["verification"]["main_release_configuration"])
        self.assertFalse(index["runtime_validation"]["device_runtime_verified"])
        self.assertEqual(index["runtime_validation"]["ios15"]["runtime_status"],
                         "pending_post_download_device_acceptance")
        self.assertEqual(index["runtime_validation"]["ios16_plus"]["runtime_status"], "unverified")
        self.assertEqual(index["runtime_validation"]["arm64e_class_ro"]["runtime_status"],
                         "unverified_on_ios16_plus")
        self.assertTrue((self.output / "release-index.json").is_file())
        sums = (self.output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(sums), 30)
        self.assertEqual({line.rsplit("  ", 1)[1] for line in sums}, {row["path"] for row in index["files"]})

    def test_rejects_extra_file_at_main_artifact_root(self) -> None:
        (self.root / "main" / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(release.BundleError, "directories differ"):
            self.verify()

    def test_rejects_wrong_extension_set_for_floor(self) -> None:
        manifest_path = self.root / "main/root-ipa-ios16.0/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["plugins_kept"] = ["MinisShare.appex"]
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(release.BundleError, "extensions differ"):
            self.verify()

    def test_rejects_debug_root_build(self) -> None:
        manifest_path = self.root / "main/root-ipa/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["build_configuration"] = "Debug"
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(release.BundleError, "Release configuration"):
            self.verify()

    def test_rejects_cross_run_companion_evidence(self) -> None:
        manifest_path = self.root / "companion-rootless/automation-build/build.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["actions_run_id"] = "1"
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(release.BundleError, "Actions run mismatch"):
            self.verify()

    def test_rejects_companion_deb_with_wrong_architectural_name(self) -> None:
        deb_dir = self.root / "companion-roothide/automation-deb"
        actual = next(deb_dir.glob("*.deb"))
        actual.rename(deb_dir / "com.openminis.automation_wrong.deb")
        with self.assertRaisesRegex(release.BundleError, "files differ"):
            self.verify()


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def block(self, start: str, end: str) -> str:
        return self.source.split(start, 1)[1].split(end, 1)[0]

    def test_root_candidate_is_parallel_and_preserves_delivery_commands(self) -> None:
        root = self.block("  root-candidate:", "\n  baseline:")
        self.assertIn("runs-on: macos-26-intel", root)
        self.assertIn("actions/cache/restore@", root)
        self.assertIn("git submodule update --init --recursive deps/ish", root)
        self.assertIn("-destination 'generic/platform=iOS'", root)
        self.assertIn("-configuration Release", root)
        self.assertNotIn("-configuration Debug", root)
        self.assertGreaterEqual(root.count("--configuration Release"), 2)
        self.assertIn('OTHER_SWIFT_FLAGS="-DMINIS_ROOT_MANAGEMENT', root)
        self.assertIn("roothelper/main.c", root)
        self.assertIn("for floor in 16.0 16.2; do", root)
        self.assertIn("for floor in 15.0 16.0 16.2; do", root)
        self.assertIn("for scheme in rootless roothide; do", root)
        self.assertIn("name: minis-ios15-root-candidate", root)
        self.assertNotIn("needs:", root)

    def test_release_gate_waits_for_all_required_jobs(self) -> None:
        gate = self.source.split("  release-gate:", 1)[1]
        for job in (
            "host-file-probe-linux",
            "root-management-linux",
            "root-packaging-windows",
            "automation-companion",
            "baseline",
            "root-candidate",
        ):
            self.assertIn(f"      - {job}", gate)
        self.assertIn("group: ios15-final-${{ github.ref }}", self.source)
        self.assertIn("cancel-in-progress: true", self.source)

    def test_release_gate_downloads_only_the_three_delivery_artifacts(self) -> None:
        gate = self.source.split("  release-gate:", 1)[1]
        self.assertEqual(gate.count("uses: actions/download-artifact@"), 3)
        self.assertIn("name: minis-ios15-root-candidate", gate)
        self.assertIn("name: minis-automation-rootless", gate)
        self.assertIn("name: minis-automation-roothide", gate)
        self.assertIn("scripts/verify_release_bundle.py", gate)
        self.assertIn("name: minis-release-gate", gate)
        self.assertIn("release-index.json", gate)
        self.assertIn("SHA256SUMS", gate)
        windows = self.source.split("  root-packaging-windows:", 1)[1].split(
            "\n  automation-companion:", 1
        )[0]
        self.assertEqual(windows.count("if ($LASTEXITCODE -ne 0)"), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
