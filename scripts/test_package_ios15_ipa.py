#!/usr/bin/env python3
"""Fixture tests for the TrollStore IPA packager. No Apple toolchain required."""
from __future__ import annotations

import json
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from package_ios15_ipa import (
    PackageError,
    package_app,
    parse_version,
    resolve_app,
    validate_app,
)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/ios15-m0-baseline.yml"


def write_plist(path: Path, data: dict, *, binary: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)


def make_app(
    root: Path,
    *,
    minimum_os: str = "15.0",
    version: str = "1.13",
    plugins: dict[str, str] | None = None,
    binary_plist: bool = False,
) -> Path:
    app = root / "Minis.app"
    write_plist(
        app / "Info.plist",
        {
            "CFBundleIdentifier": "com.openminis.app",
            "CFBundleExecutable": "Minis",
            "CFBundleShortVersionString": version,
            "MinimumOSVersion": minimum_os,
        },
        binary=binary_plist,
    )
    executable = app / "Minis"
    executable.write_bytes(b"fake-minis-binary")
    executable.chmod(0o755)
    (app / "embedded.mobileprovision").write_text("not-used\n")
    for name, plugin_minos in (plugins or {}).items():
        plugin = app / "PlugIns" / name
        write_plist(
            plugin / "Info.plist",
            {
                "CFBundleIdentifier": f"com.openminis.app.{name}",
                "CFBundleExecutable": Path(name).stem,
                "MinimumOSVersion": plugin_minos,
            },
        )
        binary = plugin / Path(name).stem
        binary.write_bytes(b"fake-plugin")
        binary.chmod(0o755)
    return app


class VersionTests(unittest.TestCase):
    def test_version_tuple_pads_and_compares(self):
        self.assertEqual(parse_version("15"), (15, 0, 0))
        self.assertEqual(parse_version("15.0"), (15, 0, 0))
        self.assertEqual(parse_version("15.0.1"), (15, 0, 1))
        self.assertGreater(parse_version("15.0.1"), parse_version("15.0"))
        self.assertGreater(parse_version("16.0"), parse_version("15.0"))
        with self.assertRaises(PackageError):
            parse_version("15.x")


class ResolveAndValidateTests(unittest.TestCase):
    def test_missing_app_and_derived_data_fail(self):
        with self.assertRaises(PackageError):
            resolve_app(None, None, "Debug")

    def test_derived_data_uses_iphoneos_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived = Path(tmp) / "DerivedData"
            app = make_app(derived / "Build" / "Products" / "Debug-iphoneos")
            self.assertEqual(resolve_app(None, derived, "Debug"), app)

    def test_rejects_app_newer_than_15(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp), minimum_os="16.0")
            with self.assertRaises(PackageError):
                validate_app(app)

    def test_rejects_missing_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp))
            (app / "Minis").unlink()
            with self.assertRaises(PackageError):
                validate_app(app)


class PackageTests(unittest.TestCase):
    def test_missing_app_directory_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PackageError):
                package_app(Path(tmp) / "missing.app", Path(tmp) / "out", skip_sign=True)

    def test_packages_payload_and_strips_newer_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(
                root / "src",
                plugins={
                    "MinisShare.appex": "15.0",
                    "MinisFileProvider.appex": "16.0",
                    "AgentWidgetExtension.appex": "16.2",
                },
            )
            output = root / "out"
            manifest = package_app(app, output, skip_sign=True)
            ipa = output / manifest["ipa_name"]
            self.assertTrue(ipa.is_file())
            self.assertGreater(manifest["ipa_bytes"], 0)
            self.assertEqual(manifest["signed"], False)
            self.assertEqual(manifest["plugins_kept"], ["MinisShare.appex"])
            stripped = {item["name"]: item["minimum_os"] for item in manifest["plugins_stripped"]}
            self.assertEqual(
                stripped,
                {
                    "MinisFileProvider.appex": "16.0",
                    "AgentWidgetExtension.appex": "16.2",
                },
            )
            with zipfile.ZipFile(ipa) as archive:
                names = set(archive.namelist())
            self.assertIn("Payload/Minis.app/Info.plist", names)
            self.assertIn("Payload/Minis.app/Minis", names)
            self.assertIn("Payload/Minis.app/PlugIns/MinisShare.appex/Info.plist", names)
            self.assertFalse(any("FileProvider" in name for name in names))
            self.assertFalse(any("AgentWidget" in name for name in names))
            self.assertTrue((app / "PlugIns" / "MinisFileProvider.appex").is_dir())
            self.assertEqual(manifest["ipa_sha256"], _sha256(ipa))
            stored = json.loads((output / "manifest.json").read_text())
            self.assertEqual(stored["ipa_sha256"], manifest["ipa_sha256"])

    def test_keep_incompatible_plugins_leaves_16x_appex(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp) / "src", plugins={"MinisFileProvider.appex": "16.0"})
            manifest = package_app(
                app,
                Path(tmp) / "out",
                skip_sign=True,
                keep_incompatible_plugins=True,
            )
            self.assertEqual(manifest["plugins_kept"], ["MinisFileProvider.appex"])
            self.assertEqual(manifest["plugins_stripped"], [])
            with zipfile.ZipFile(Path(tmp) / "out" / manifest["ipa_name"]) as archive:
                self.assertTrue(any("MinisFileProvider.appex" in name for name in archive.namelist()))

    def test_binary_info_plist_and_custom_ipa_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp) / "src", binary_plist=True)
            manifest = package_app(
                app,
                Path(tmp) / "out",
                skip_sign=True,
                ipa_name="custom.ipa",
            )
            self.assertEqual(manifest["ipa_name"], "custom.ipa")
            self.assertEqual(manifest["marketing_version"], "1.13")
            self.assertTrue((Path(tmp) / "out" / "custom.ipa").is_file())


class WorkflowWiringTests(unittest.TestCase):
    def test_baseline_workflow_packages_and_uploads_ipa(self):
        source = WORKFLOW.read_text()
        self.assertIn("python3 scripts/test_package_ios15_ipa.py -v", source)
        self.assertIn("python3 scripts/package_ios15_ipa.py", source)
        self.assertIn("--derived-data \"$RUNNER_TEMP/m1/DerivedData\"", source)
        self.assertIn("name: minis-ios15-trollstore-ipa", source)
        self.assertIn("ios15-trollstore.entitlements", source)
        probe_at = source.index("Probe iOS 15 app target")
        package_at = source.index("Package TrollStore IPA")
        upload_at = source.index("Upload TrollStore IPA")
        self.assertLess(probe_at, package_at)
        self.assertLess(package_at, upload_at)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
