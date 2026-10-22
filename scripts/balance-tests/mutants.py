#!/usr/bin/env python3
"""Mutation check for BalanceLookup.

A mutant must COMPILE first and then make the native regression fail. A survivor
means the tests do not actually pin the behaviour. Linux returns 77.
"""
import argparse
import pathlib
import subprocess
import sys

MUTANTS = [
    ("duplicate-version-segment",
     "        var components = URLComponents()\n"
     "        components.scheme = \"https\"\n"
     "        components.host = url.host\n"
     "        components.port = url.port\n"
     "        components.path = path\n",
     "        var components = URLComponents(string: raw + path) ?? URLComponents()\n"),
    ("allow-plain-http",
     "        guard url.scheme?.lowercased() == \"https\" else { return .unsupported(.insecureScheme) }",
     "        guard true else { return .unsupported(.insecureScheme) }"),
    ("hide-derived-remainder",
     "remainingIsDerived: derived)",
     "remainingIsDerived: false)"),
    ("accept-boolean-amounts",
     "        if let value = any as? NSNumber {\n"
     "            if CFGetTypeID(value) == CFBooleanGetTypeID() { return nil }\n"
     "            return value.doubleValue\n",
     "        if let value = any as? NSNumber {\n"
     "            return value.doubleValue\n"),
    ("drop-401-mapping",
     "        case 401, 403:",
     "        case 403:"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--swiftc", required=True)
    ap.add_argument("--source", required=True, type=pathlib.Path)
    ap.add_argument("--test", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    ap.add_argument("--swift-arg", action="append", default=[])
    args = ap.parse_args()

    original = args.source.read_text()
    args.work.mkdir(parents=True, exist_ok=True)
    failures = []

    for name, old, new in MUTANTS:
        hits = original.count(old)
        if hits != 1:
            failures.append(f"{name}: pattern matched {hits} times, expected 1")
            print(f"INVALID {name}: pattern matched {hits} times")
            continue
        mutated = args.work / f"{name}.swift"
        mutated.write_text(original.replace(old, new, 1))
        binary = args.work / name
        build = subprocess.run(
            [args.swiftc, "-swift-version", "5", *args.swift_arg,
             str(mutated), str(args.test), "-o", str(binary)],
            capture_output=True, text=True)
        if build.returncode != 0:
            failures.append(f"{name}: mutant did not compile")
            print(f"INVALID {name}: compile failed\n{build.stderr[:600]}")
            continue
        if not binary.exists():
            failures.append(f"{name}: compiler reported success but produced no binary")
            print(f"INVALID {name}: no binary produced")
            continue
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        if run.returncode == 0:
            failures.append(f"{name}: survived")
            print(f"SURVIVED {name}")
        else:
            first = next((l for l in run.stderr.splitlines() if l.startswith("FAIL")), "")
            print(f"CAUGHT {name} {first}")

    print(f"MUTANTS {len(MUTANTS) - len(failures)}/{len(MUTANTS)} caught")
    if failures:
        for item in failures:
            print(f"ERROR {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
