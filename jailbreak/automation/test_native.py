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

ROOT = Path(__file__).resolve().parent


def static():
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
