#!/usr/bin/env python3
"""Read the actual signed universal companion product after the Actions build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess


def run(*command):
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(folder, scheme):
    dylib = folder / "MinisAutomation.dylib"
    assert sorted(run("xcrun", "lipo", "-archs", str(dylib)).split()) == ["arm64", "arm64e"]
    floors = {}
    for arch in ("arm64", "arm64e"):
        load = run("xcrun", "vtool", "-arch", arch, "-show-build", str(dylib))
        assert re.search(r"\bplatform\s+IOS\b", load), load
        minimum = re.findall(r"\bminos\s+([0-9.]+)", load)
        assert len(minimum) == 1 and minimum[0] in ("15.0", "15.0.0"), load
        floors[arch] = "15.0"
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
        "architectures": ["arm64", "arm64e"], "native_floors": floors,
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
