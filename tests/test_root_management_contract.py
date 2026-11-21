#!/usr/bin/env python3
"""Static root-client contracts and opt-in Actions-only native gate tests.

`--static` does not invoke a compiler. `--native` compiles and executes the
production authorization reducer and a non-spawning client harness on macOS.
Neither mode is evidence of an iOS persona transition or device root access.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
CLIENT = REPO / "src/ios/Diagnostics/RootManagementClient.c"
HEADER = CLIENT.with_suffix(".h")
MODEL = CLIENT.with_name("RootManagementAuthorization.swift")
VIEW = REPO / "src/ios/Views/Settings/RootManagementView.swift"


def static_checks():
    client, header, model, view = (p.read_text(encoding="utf-8") for p in (CLIENT, HEADER, MODEL, VIEW))
    allowed = re.findall(r'case OMRootOperation\w+: return "([a-z]+)";', client)
    assert allowed == ["identity", "prepare", "check", "cleanup"], allowed
    assert "const char *path" not in header and "const char *command" not in header
    for token in ("_NSGetExecutablePath", '"/minis-rootprobe"', "realpath(", "lstat(",
                  "S_ISREG", "st_nlink != 1", "O_NONBLOCK", "OM_ROOT_TIMEOUT_MS 6000",
                  "OM_ROOT_OUTPUT_CAPACITY 4096", "POSIX_SPAWN_CLOEXEC_DEFAULT", "WNOHANG"):
        assert token in client, token
    assert "getenv(" not in client and "environ" not in client.replace("environment", "")
    assert "system(" not in client and "popen(" not in client and '"/bin/sh"' not in client
    assert 'set_persona(&attributes, 99, 1)' in client
    assert 'set_uid(&attributes, 0)' in client and 'set_gid(&attributes, 0)' in client
    assert client.count('dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona') == 3
    assert 'kill(child, SIGKILL)' in client and 'waitpid(' in client
    assert '#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT' in client
    assert '#if MINIS_ROOT_MANAGEMENT' in model and '#if MINIS_ROOT_MANAGEMENT && os(iOS)' in view
    for token in ('authorization.consume(operation)', 'authorization.cancel()', 'authorization.revoke()',
                  'Task.detached', '.onChange(of: scenePhase)', '.onDisappear',
                  'RootHelperProtocol.validate(data, operation: operation, commit: build.commit)',
                  'transport.exitCode == 0', 'helper?.euid == 0'):
        assert token in view, token
    for token in ('decoded.helperCommit == commit', 'Set(object.keys).isSubset(of: allowedKeys)',
                  'data.count <= 4095', 'decoded.bytes == sampleBytes', 'decoded.removed == true'):
        assert token in model, token
    project = (REPO / "src/ios/Minis.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
    for name in ("RootManagementClient.c", "RootManagementAuthorization.swift", "RootManagementView.swift"):
        assert f"{name} in Sources" in project, name
    print("PASS root management source contracts: fixed enum/bundle executable, runtime persona lookup, bounded worker transport, explicit one-shot gate, scalar/matching-source response")


def native_checks():
    with tempfile.TemporaryDirectory(prefix="minis-root-client-") as directory:
        out = Path(directory)
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-parse-as-library",
                        "-D", "MINIS_ROOT_MANAGEMENT", str(MODEL),
                        str(REPO / "tests/RootManagementAuthorizationTests.swift"),
                        "-o", str(out / "authorization")], check=True, timeout=60)
        subprocess.run([str(out / "authorization")], check=True, timeout=5)
        subprocess.run(["xcrun", "clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-DMINIS_ROOT_MANAGEMENT=1", "-I", str(HEADER.parent), str(CLIENT),
                        str(REPO / "tests/RootManagementClientHarness.c"),
                        "-o", str(out / "client")], check=True, timeout=60)
        subprocess.run([str(out / "client")], check=True, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    static_checks()
    if args.native:
        native_checks()
    print("NOTE: no iOS device execution or root capability is verified by these gates")


if __name__ == "__main__":
    main()
