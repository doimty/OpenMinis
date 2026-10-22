#!/usr/bin/env python3
"""Mutation check for the tool-group projection.

Each mutant must COMPILE first (an uncompilable mutant proves nothing) and then
make the native regression fail. A survivor is a real gap in the tests.
Linux returns 77: this is not a native pass.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys

MUTANTS = [
    ("single-tool-threshold", "if run.count >= 2 {", "if run.count >= 1 {"),
    ("drop-boundary-row", "                result.append(item)\n", ""),
    ("ignore-descriptor-change", "previous[id] != groups[id]", "false"),
    ("ignore-expansion-change",
     "expanded.contains(id) != previouslyExpanded.contains(id)", "false"),
    ("always-show-members", "if expanded.contains(id) {", "if true {"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--swiftc", required=True)
    ap.add_argument("--source", required=True, type=pathlib.Path)
    ap.add_argument("--test", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
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
        mutated_path = args.work / f"{name}.swift"
        mutated_path.write_text(original.replace(old, new, 1))
        binary = args.work / name
        build = subprocess.run(
            [args.swiftc, "-swift-version", "5", str(mutated_path), str(args.test), "-o", str(binary)],
            capture_output=True, text=True)
        if build.returncode != 0:
            failures.append(f"{name}: mutant did not compile")
            print(f"INVALID {name}: compile failed\n{build.stderr[:600]}")
            continue
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        if run.returncode == 0:
            failures.append(f"{name}: survived")
            print(f"SURVIVED {name}")
        else:
            first = next((line for line in run.stderr.splitlines() if line.startswith("FAIL")), "")
            print(f"CAUGHT {name} {first}")

    print(f"MUTANTS {len(MUTANTS) - len(failures)}/{len(MUTANTS)} caught")
    if failures:
        for item in failures:
            print(f"ERROR {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
