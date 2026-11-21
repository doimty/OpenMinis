#!/usr/bin/env python3
"""Actions-only probe of real guarded C objects, including Xcode coverage code.

This records compiler and nm output before applying the same production object
audit. It never starts a helper or executes iPhoneOS code on the host.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import audit_standard_ios_build as audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("Run iOS compiler probes on GitHub Actions macOS only.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
    source = audit.IOS / "Diagnostics/RootManagementClient.c"
    rows = []
    for name, flags, enabled in (("off", [], False),
                                 ("off-coverage", ["-fprofile-instr-generate", "-fcoverage-mapping"], False),
                                 ("on", ["-DMINIS_ROOT_MANAGEMENT=1"], True)):
        target = args.output_dir / (name + ".o")
        command = ["xcrun", "--sdk", "iphoneos", "clang", "-target", "arm64-apple-ios15.0",
                   "-isysroot", sdk, "-Os", "-g", "-Wall", "-Wextra", "-Werror", *flags,
                   "-c", str(source), "-o", str(target)]
        subprocess.run(command, check=True, timeout=60)
        symbols = subprocess.check_output(["xcrun", "nm", "-U", str(target)], text=True)
        (args.output_dir / (name + ".nm.txt")).write_text(symbols, encoding="utf-8")
        print(name + ":\n" + symbols, flush=True)
        try:
            audit.prestrip_symbols(target, reject_all=True)
            refused = False
            error = None
        except audit.PackageError as failure:
            refused = True
            error = str(failure)
        rows.append({"name": name, "compile": command, "refused": refused, "expected_refusal": enabled,
                     "error": error})
    # Release compiles the Minis target with -fvisibility=hidden. The packager
    # proves the Root client compiled into the product by finding an externally
    # visible _OMRootManagementClientVersion, so the sentinel must keep default
    # visibility under that flag.
    hidden_target = args.output_dir / "on-hidden.o"
    hidden_command = ["xcrun", "--sdk", "iphoneos", "clang", "-target", "arm64-apple-ios15.0",
                      "-isysroot", sdk, "-Os", "-g", "-Wall", "-Wextra", "-Werror",
                      "-DMINIS_ROOT_MANAGEMENT=1", "-fvisibility=hidden",
                      "-c", str(source), "-o", str(hidden_target)]
    subprocess.run(hidden_command, check=True, timeout=60)
    hidden_symbols = subprocess.check_output(["xcrun", "nm", "-gU", str(hidden_target)], text=True)
    (args.output_dir / "on-hidden.nm.txt").write_text(hidden_symbols, encoding="utf-8")
    print("on-hidden:\n" + hidden_symbols, flush=True)
    if "T _OMRootManagementClientVersion" not in hidden_symbols:
        raise SystemExit("root client sentinel lost external visibility under -fvisibility=hidden")
    (args.output_dir / "guard-probe.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0 if all(row["refused"] == row["expected_refusal"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
