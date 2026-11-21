#!/usr/bin/env python3
"""Pinned-source checks and Actions-only production Foundation gate tests."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from prepare_sources import prepare
from verify_build import OBJC_IMAGEINFO_SIGNED_CLASS_RO, macho_metadata

ROOT = Path(__file__).resolve().parent


def static():
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    assert 'test "${GITHUB_ACTIONS:-}" = true' in build
    assert 'if [ "$arch" = arm64e ]; then' in build
    assert "flags+=(-fno-ptrauth-objc-class-ro)" in build
    with tempfile.TemporaryDirectory(prefix="minis-automation-source-") as temporary:
        folder = Path(temporary) / "generated"
        lock = prepare(folder)
        assert lock["upstream_commit"] == "62c4464f688c8f269419924b1a19c996de75f175"
        server = (folder / "OMMAV_MCPServer.m").read_text(encoding="utf-8")
        assert "htonl(INADDR_LOOPBACK)" in server and "INADDR_ANY" not in server
        assert 'OMMAAuthorize(headers[@"authorization"], clientSocket)' in server
        assert server.index('![basePath isEqualToString:@"/mcp"]') < server.index('// Route request')
        assert 'return OMMAToolAllowed(tool[@"name"])' in server and 'if (!OMMAToolAllowed(name)' in server
        assert 'OMMARequestEnd(ticket)' in server and 'OMMATraceReply(reply)' in server
        assert "retry once with type_text" not in server and "Use this as the fallback when input_text fails" not in server
        for path in folder.glob("*.m"):
            text = path.read_text(encoding="utf-8")
            for name in re.findall(r"@implementation\s+(\w+)", text): assert name.startswith("OMMAV_"), name
        hid = (folder / "HIDManager.m").read_text(encoding="utf-8")
        text = (folder / "OMMAV_TextInputManager.m").read_text(encoding="utf-8")
        assert "phase != TouchPhaseEnded && !OMMAInteractionAlive()" in hid
        assert "OMMADispatchAsync(_hidQueue" in hid and "OMMADispatchAsync(_inputQueue" in text
        assert "if (down && !OMMAInteractionAlive())" in text and "if (!OMMAInteractionAlive()) return NO;" in text
        assert 'if ([self typeTextViaHID:text' not in text
        assert "usleep(" not in hid and "usleep(" not in text
        assert "OMMAEventSleep(" in hid and "OMMAEventSleep(" in text
        logger = (folder / "OMMAV_MCPLogger.m").read_text(encoding="utf-8")
        assert "+ (BOOL)isDebugLoggingEnabled { return NO; }" in logger
        print(f"PASS verified pinned MIT/RootHide sources and generated {len(lock['generated_source_hashes'])} namespaced source/header files")


def native():
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Native compilation runs only on GitHub Actions macOS.")
    with tempfile.TemporaryDirectory(prefix="minis-automation-native-") as temporary:
        folder = Path(temporary)
        source = folder / "ClassROProbe.m"
        source.write_text(
            "__attribute__((objc_root_class))\n"
            "@interface OMMAAClassROProbe\n"
            "@end\n"
            "@implementation OMMAAClassROProbe\n"
            "@end\n",
            encoding="utf-8",
        )
        sdk = subprocess.check_output(
            ["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True
        ).strip()
        arm64_object = folder / "ClassROProbe.arm64.o"
        default_object = folder / "ClassROProbe.default.o"
        compatible_object = folder / "ClassROProbe.ios15.o"
        compiler = [
            "xcrun", "clang", "-target", "arm64e-apple-ios15.0", "-isysroot", sdk,
            "-fobjc-arc", "-fblocks", "-Wall", "-Wextra", "-Werror", "-c", str(source),
        ]
        arm64_compiler = compiler.copy()
        arm64_compiler[3] = "arm64-apple-ios15.0"
        subprocess.run(
            arm64_compiler + ["-o", str(arm64_object)],
            check=True,
            timeout=120,
        )
        subprocess.run(compiler + ["-o", str(default_object)], check=True, timeout=120)
        subprocess.run(
            compiler + ["-fno-ptrauth-objc-class-ro", "-o", str(compatible_object)],
            check=True,
            timeout=120,
        )
        arm64_metadata = macho_metadata(arm64_object)["arm64"]
        default_metadata = macho_metadata(default_object)["arm64e"]
        compatible_metadata = macho_metadata(compatible_object)["arm64e"]
        arm64_flags = arm64_metadata["objc_imageinfo_flags"]
        default_flags = default_metadata["objc_imageinfo_flags"]
        compatible_flags = compatible_metadata["objc_imageinfo_flags"]
        assert not arm64_flags & OBJC_IMAGEINFO_SIGNED_CLASS_RO, (
            f"arm64 unexpectedly emitted SignedClassRO: {arm64_flags:#x}")
        assert default_flags & OBJC_IMAGEINFO_SIGNED_CLASS_RO, (
            f"default arm64e metadata did not exercise SignedClassRO: {default_flags:#x}")
        assert not compatible_flags & OBJC_IMAGEINFO_SIGNED_CLASS_RO, (
            "-fno-ptrauth-objc-class-ro did not clear SignedClassRO: "
            f"{compatible_flags:#x}")
        assert default_flags & ~OBJC_IMAGEINFO_SIGNED_CLASS_RO == compatible_flags, (
            "the compatibility flag changed unrelated image flags: "
            f"{default_flags:#x} -> {compatible_flags:#x}")

        driver = subprocess.run(
            compiler + ["-fno-ptrauth-objc-class-ro", "-###"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        driver_text = driver.stdout + driver.stderr
        required_pac_flags = (
            "-fptrauth-calls",
            "-fptrauth-returns",
            "-fptrauth-intrinsics",
            "-fptrauth-indirect-gotos",
            "-fptrauth-auth-traps",
            "-fptrauth-vtable-pointer-address-discrimination",
            "-fptrauth-vtable-pointer-type-discrimination",
            "-fptrauth-objc-isa",
            "-fptrauth-objc-interface-sel",
        )
        for flag in required_pac_flags:
            assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", driver_text), (
                f"arm64e compatibility build lost required PAC flag: {flag}")
        assert not re.search(
            r"(?<![\w-])-fptrauth-objc-class-ro(?![\w-])", driver_text
        ), "arm64e compatibility build unexpectedly enabled SignedClassRO"

        fixture = folder / "fixture"
        fixture.mkdir(mode=0o700)
        binary = folder / "session-tests"
        subprocess.run(["xcrun", "clang", "-fobjc-arc", "-fblocks", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-DOMMA_SESSION_TESTS=1", '-DOMMA_BUILD_COMMIT="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
            '-DOMMA_BUILD_RUN_ID="12345"', "-I", str(ROOT), "-framework", "Foundation",
            str(ROOT / "MinisAutomationSession.m"), str(ROOT / "MinisAutomationPolicy.m"),
            str(ROOT / "tests/SessionTests.m"), "-o", str(binary)], check=True, timeout=120)
        subprocess.run([str(binary), str(fixture)], check=True, timeout=15)
        screen_header = folder / "ScreenManager.h"
        screen_header.write_text('#import <Foundation/Foundation.h>\n@interface ScreenManager : NSObject\n'
                                 '+ (instancetype)sharedInstance;\n- (NSDictionary *)deviceInteractionState;\n@end\n', encoding="utf-8")
        interaction = folder / "interaction-tests"
        subprocess.run(["xcrun", "clang", "-fobjc-arc", "-fblocks", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-DOMMA_SESSION_TESTS=1", '-DOMMA_BUILD_COMMIT="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
            '-DOMMA_BUILD_RUN_ID="12345"', "-I", str(folder), "-I", str(ROOT), "-framework", "Foundation",
            str(ROOT / "MinisAutomationSession.m"), str(ROOT / "MinisAutomationInteraction.m"),
            str(ROOT / "tests/InteractionTests.m"), "-o", str(interaction)], check=True, timeout=120)
        subprocess.run([str(interaction)], check=True, timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    static()
    if args.native: native()
