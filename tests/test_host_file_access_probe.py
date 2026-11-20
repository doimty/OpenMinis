#!/usr/bin/env python3
"""Native host-side adversarial tests for the fixed-path iOS C probe."""
from __future__ import annotations
import os
import errno
import pathlib
import shutil
import subprocess
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src/ios/Diagnostics/HostFileAccessProbe.c"
HARNESS = REPO / "tests/HostFileAccessProbeHarness.c"
HEADER = REPO / "src/ios/Diagnostics/HostFileAccessProbe.h"
SAMPLE_ID = "20820eed-3d91-46bb-be1d-2d1b9058b29b"
PAYLOAD = f"OPENMINIS-PROBE-V1:{SAMPLE_ID}\n".encode()
MATCH, ERROR, NOT_REG, SIZE, MARKER, CHANGED, LINKS = range(7)


def require(ok: bool, msg: str) -> None:
    if not ok:
        raise AssertionError(msg)


def run_case(binary: pathlib.Path, root: pathlib.Path, *, drop_privs=False):
    env = dict(os.environ, MINIS_HOST_FILE_PROBE_TEST_ROOT=str(root))
    preexec = None
    if drop_privs:
        if os.geteuid() != 0:
            raise RuntimeError("permission-denied case requires root test runner to drop to nobody")
        preexec = lambda: (os.setgid(65534), os.setuid(65534))
    proc = subprocess.run([str(binary)], env=env, text=True, capture_output=True,
                          timeout=3, preexec_fn=preexec)
    require(proc.returncode == 0, f"harness failed rc={proc.returncode}: {proc.stderr}")
    cols = proc.stdout.rstrip("\n").split("\t")
    require(len(cols) == 7, f"unexpected harness output: {proc.stdout!r}")
    return int(cols[0]), int(cols[1]), cols[2], cols[3], cols[4:]


def make_tree(root: pathlib.Path):
    sample_dir = root / "private/var/mobile/Media/OpenMinisPermissionProbe"
    sample_dir.mkdir(parents=True)
    return sample_dir


def assert_case(binary, root, expected, *, stage=None, err=None, sample_id=None, drop=False):
    outcome, eno, got_stage, got_id, metadata = run_case(binary, root, drop_privs=drop)
    require(outcome == expected, f"{root.name}: expected outcome {expected}, got {outcome} ({eno}, {got_stage})")
    if stage is not None: require(got_stage == stage, f"expected stage {stage}, got {got_stage}")
    if err is not None: require(eno == err, f"expected errno {err}, got {eno}")
    if sample_id is not None: require(got_id == sample_id, f"sample ID mismatch: {got_id}")
    else: require(got_id == "", f"failure leaked sample ID: {got_id}")
    require("OPENMINIS-PROBE-V1:" not in "\t".join([got_stage, got_id, *metadata]), "file content leaked")


def main():
    temp = pathlib.Path(tempfile.mkdtemp(prefix="openminis-host-probe-"))
    temp.chmod(0o755)
    binary = temp / "probe"
    try:
        subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-DMINIS_HOST_FILE_PROBE=1", "-DMINIS_HOST_FILE_PROBE_TEST_ROOT=1",
                        "-D_POSIX_C_SOURCE=200809L", "-I", str(HEADER.parent),
                        str(SRC), str(HARNESS), "-o", str(binary)], check=True, timeout=30)
        # Exact match; only canned non-sensitive metadata may be returned.
        root = temp / "valid"; d = make_tree(root); (d / "sample-v1.txt").write_bytes(PAYLOAD)
        assert_case(binary, root, MATCH, stage="sample.compare", sample_id=SAMPLE_ID)

        root = temp / "missing"; make_tree(root)
        assert_case(binary, root, ERROR, stage="sample.stat", err=errno.ENOENT)

        root = temp / "symlink"; d = make_tree(root); outside = root / "outside"
        outside.write_bytes(PAYLOAD); (d / "sample-v1.txt").symlink_to(outside)
        assert_case(binary, root, NOT_REG, stage="sample.stat")

        root = temp / "fifo"; d = make_tree(root); os.mkfifo(d / "sample-v1.txt")
        assert_case(binary, root, NOT_REG, stage="sample.stat")

        root = temp / "directory"; d = make_tree(root); (d / "sample-v1.txt").mkdir()
        assert_case(binary, root, NOT_REG, stage="sample.stat")

        root = temp / "oversize"; d = make_tree(root); (d / "sample-v1.txt").write_bytes(PAYLOAD + b"X")
        assert_case(binary, root, SIZE, stage="sample.stat")

        root = temp / "short"; d = make_tree(root); (d / "sample-v1.txt").write_bytes(PAYLOAD[:-1])
        assert_case(binary, root, SIZE, stage="sample.stat")

        root = temp / "bad-marker"; d = make_tree(root); (d / "sample-v1.txt").write_bytes(b"X" + PAYLOAD[1:])
        assert_case(binary, root, MARKER, stage="sample.compare")

        root = temp / "hardlink"; d = make_tree(root); f = d / "sample-v1.txt"; f.write_bytes(PAYLOAD)
        os.link(f, root / "extra-link")
        assert_case(binary, root, LINKS, stage="sample.stat")

        root = temp / "ancestor-link"; root.mkdir()
        base = root / "private/var/mobile"; base.mkdir(parents=True)
        target = root / "outside-media"; target.mkdir()
        (base / "Media").symlink_to(target, target_is_directory=True)
        out = target / "OpenMinisPermissionProbe"; out.mkdir(); (out / "sample-v1.txt").write_bytes(PAYLOAD)
        assert_case(binary, root, ERROR, stage="Media", err=errno.ELOOP)

        if os.geteuid() == 0:
            root = temp / "denied"; d = make_tree(root); (d / "sample-v1.txt").write_bytes(PAYLOAD)
            media = str(root / "private/var/mobile/Media")
            for p in [root, root / "private", root / "private/var", root / "private/var/mobile"]: p.chmod(0o755)
            os.chown(media, 0, 0); os.chmod(media, 0o700)
            assert_case(binary, root, ERROR, stage="Media", err=errno.EACCES, drop=True)
        else:
            print("SKIP permission-denied case: runner is not root")

        print("PASS host C probe: exact marker, missing, symlink, ancestor symlink, FIFO, directory, size, marker, hardlink, permission denial")
    finally:
        os.chmod(temp, 0o755)
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
