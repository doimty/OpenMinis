#!/usr/bin/env python3
"""Exercise the standard-build audit on filesystem and command-output fixtures."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit_standard_ios_build as audit
from test_package_ios15_ipa import write_plist
from test_package_ios15_root_ipa import make_native_app


def project_fixture():
    objects = {}
    for index, (target, (suffix, minimum, entitlement, _)) in enumerate(audit.TARGETS.items()):
        key = str(index)
        objects[key] = {"isa": "PBXNativeTarget", "name": target, "buildConfigurationList": key + "list"}
        objects[key + "list"] = {"buildConfigurations": [key + "rel"]}
        objects[key + "rel"] = {"name": "Release", "buildSettings": {"PRODUCT_BUNDLE_IDENTIFIER": audit.NORMAL_BUNDLE_ID + suffix,
                                "CODE_SIGN_ENTITLEMENTS": entitlement, "CODE_SIGN_STYLE": "Automatic",
                                "IPHONEOS_DEPLOYMENT_TARGET": minimum}}
    return {"objects": objects}


class AuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dd = Path(temporary.name).resolve()
        self.app = make_native_app(self.dd / "Build/Products/Release-iphoneos")
        self.project = project_fixture()
        info = audit.load_plist(self.app / "Info.plist")
        info["CFBundleURLTypes"] = [{"CFBundleURLSchemes": ["minis", "minis-mcp"]}]
        write_plist(self.app / "Info.plist", info)
        self.objects = self.dd / "Build/Intermediates.noindex/Minis.build/Release-iphoneos/Minis.build/Objects-normal/arm64"
        self.objects.mkdir(parents=True)
        self.swift = self.objects / "Minis.SwiftFileList"
        self.swift.write_text("\n".join("/repo/src/" + name for name in audit.SWIFT_INPUTS))
        self.response = self.objects / "123-common-args.resp"
        self.response.write_text("-target arm64-apple-ios15.0 -DOTHERS=1")
        self.bin_list = self.objects / "Minis.LinkFileList"
        paths = [self.objects / (Path(name).stem + ".o") for name in sorted(audit.C_INPUTS)]
        self.bin_list.write_text("\n".join(path.as_posix() for path in paths))
        swift = f"    builtin-SwiftDriver -- /tools/swiftc -module-name Minis @{self.swift.as_posix()} -target arm64-apple-ios15.0"
        native = []
        for source, output in zip(sorted(audit.C_INPUTS), paths):
            output.write_bytes(b"fixture intermediary")
            native.append(f"    /tools/clang @{self.response.as_posix()} -c /repo/src/{source} -o {output.as_posix()}")
        link = f"    /tools/clang++ -filelist {self.bin_list.as_posix()} -o {(self.app / 'Minis').as_posix()}"
        self.log = self.dd / "xcodebuild.log"
        self.log.write_text("\n".join([swift, *native, link, "** BUILD SUCCEEDED **"]))

    def evidence(self, probe_output="0000000000000000 t ltmp0\n"):
        with patch.object(audit.shutil, "which", return_value="/usr/bin/xcrun"), \
             patch.object(audit.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, probe_output, "")):
            return audit.compile_evidence(self.app, self.dd, self.log)

    def test_all_original_roles_and_entitlements(self):
        roles, floors = audit.roles_and_settings(self.app, self.project)
        self.assertEqual(len(roles), 4)
        self.assertEqual(floors["PlugIns/MinisFileProvider.appex"], (16, 0, 0))
        self.assertEqual(floors["PlugIns/AgentWidgetExtension.appex"], (16, 2, 0))
        main = next(role for role in roles if role["target"] == "Minis")
        self.assertIn("com.apple.developer.icloud-services", main["project_entitlement_keys"])
        self.assertIn("com.apple.developer.healthkit", main["project_entitlement_keys"])

    def test_openstep_project_parser_reads_release_target_settings(self):
        project = audit.parse_project(audit.IOS / "Minis.xcodeproj/project.pbxproj")
        objects = project["objects"]
        targets = {body.get("name"): body for body in objects.values() if body.get("isa") == "PBXNativeTarget"}
        self.assertTrue(set(audit.TARGETS).issubset(targets))
        for target in audit.TARGETS:
            config_list = objects[targets[target]["buildConfigurationList"]]
            release = [objects[key] for key in config_list["buildConfigurations"] if objects[key].get("name") == "Release"]
            self.assertEqual(len(release), 1)
            self.assertEqual(release[0]["buildSettings"]["PRODUCT_BUNDLE_IDENTIFIER"],
                             audit.NORMAL_BUNDLE_ID + audit.TARGETS[target][0])

    def test_refuses_wrong_group_even_when_bundle_id_matches(self):
        path = self.app / "PlugIns/MinisFileProvider.appex/Info.plist"
        info = audit.load_plist(path)
        info["NSExtension"]["NSExtensionFileProviderDocumentGroup"] = "group.com.openminis.app.root"
        write_plist(path, info)
        with self.assertRaisesRegex(audit.PackageError, "different group"):
            audit.roles_and_settings(self.app, self.project)

    def test_standard_product_cannot_silently_drop_widget_or_restore_root_helper(self):
        (self.app / "minis-rootprobe").write_bytes(b"wrong")
        with self.assertRaisesRegex(audit.PackageError, "Root helper"):
            audit.roles_and_settings(self.app, self.project)
        (self.app / "minis-rootprobe").unlink()
        (self.app / "PlugIns/AgentWidgetExtension.appex").rename(self.app / "Widget-withdrawn")
        with self.assertRaisesRegex(audit.PackageError, "Share, File Provider and Widget"):
            audit.roles_and_settings(self.app, self.project)

    def test_rejects_root_signing_template_in_standard_target(self):
        self.project["objects"]["0rel"]["buildSettings"]["CODE_SIGN_ENTITLEMENTS"] = "../../scripts/ios15-root.entitlements"
        with self.assertRaisesRegex(audit.PackageError, "Release settings changed"):
            audit.roles_and_settings(self.app, self.project)

    def test_records_real_compile_and_link_inputs_even_if_final_binary_is_stripped(self):
        result = self.evidence()
        self.assertEqual(set(result["native_object_sha256"]), audit.C_INPUTS)
        self.assertEqual(set(result["compiler_and_link_input_hashes"]), {self.response.name, self.swift.name, self.bin_list.name})
        self.assertFalse(result["root_defines_seen"])
        self.assertFalse(result["device_runtime_verified"])

    def test_rejects_root_define_in_swift_invocation(self):
        self.log.write_text(self.log.read_text().replace("-module-name Minis ", "-module-name Minis -D MINIS_ROOT_MANAGEMENT "))
        with self.assertRaisesRegex(audit.PackageError, "standard compile enables"):
            self.evidence()

    def test_rejects_root_define_in_clang_response(self):
        self.response.write_text("-target arm64-apple-ios15.0 -D MINIS_HOST_FILE_PROBE=1")
        with self.assertRaisesRegex(audit.PackageError, "standard compile enables"):
            self.evidence()

    def test_real_guarded_objects_must_be_linked(self):
        self.bin_list.write_text("\n".join(name for name in self.bin_list.read_text().splitlines()
                                          if not name.endswith("RootManagementClient.o")))
        with self.assertRaisesRegex(audit.PackageError, "not all inputs"):
            self.evidence()

    def test_prestrip_guarded_object_or_symbol_contamination_is_detected(self):
        for probe in ("0000000000000000 T _some_implementation\n", "0000000000000000 T _device_mcp_offload_register\n",
                      "0000000000000000 T ___llvm_profile_runtime_user_extra\n"):
            with self.subTest(probe=probe), self.assertRaisesRegex(audit.PackageError, "emitted executable code|implementation found"):
                self.evidence(probe)

    def test_empty_guarded_unit_can_reference_the_observed_llvm_coverage_runtime(self):
        result = self.evidence("0000000000000000 T ___llvm_profile_runtime_user\n0000000000000000 t ltmp0\n")
        self.assertFalse(result["root_defines_seen"])
        self.assertEqual(set(result["native_object_sha256"]), audit.C_INPUTS)

    def test_missing_swift_and_clang_evidence_is_not_proof_of_absence(self):
        self.swift.write_text("/repo/src/Other.swift")
        self.log.write_text("\n".join(line for line in self.log.read_text().splitlines() if "DeviceMCPOffload.m" not in line))
        with self.assertRaisesRegex(audit.PackageError, "missing clean Release build"):
            self.evidence("")

    def test_build_log_for_different_tree_cannot_attest_this_build(self):
        self.log.write_text(self.log.read_text().replace(self.objects.as_posix(), "/somewhere/unrelated/Minis.build/Release-iphoneos/Minis.build/Objects-normal/arm64"))
        with self.assertRaisesRegex(audit.PackageError, "unrelated to this build"):
            self.evidence()


if __name__ == "__main__":
    unittest.main()
