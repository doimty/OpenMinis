#!/usr/bin/env python3
"""Host contract test for the root probe helper (run as root in Actions).

Proves the source's safety rules by compiling a test-root variant and exercising
only the paths a symlink/hardlink/policy attack could take. The iOS build's
fixed `/private/var/mobile/Library` path is unaffected by the test macro.
"""
from __future__ import annotations
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "roothelper/main.c"
MARKER = "fb81bca8-f8c0-4f2e-9ff2-fbfaf351aa87"


def run(binary, root, op, *, uid=None):
    env = dict(os.environ, MINIS_ROOT_PROBE_TEST_ROOT=str(root))
    pre = None
    if uid is not None:
        pre = lambda: (os.setgid(uid), os.setuid(uid))
    p = subprocess.run([str(binary), op], env=env, text=True, capture_output=True, timeout=5, preexec_fn=pre)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    try:
        return p.returncode, json.loads(line)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"non-JSON output for {op}: {p.stdout!r} {p.stderr!r}") from exc


def main():
    if os.geteuid() != 0:
        print("SKIP root helper contract test: needs root to build/run the setuid surface")
        return
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="openminis-root-probe-"))
    # The negative test drops to uid 501 before exec. Keep the temporary parent
    # traversable so that case reaches the helper's non-root guard on macOS.
    tmp.chmod(0o755)
    binary = tmp / "rootprobe"
    try:
        subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-DMINIS_ROOT_PROBE_TEST_ROOT=1", "-D_POSIX_C_SOURCE=200809L",
                        str(SRC), "-o", str(binary)], check=True, timeout=30)
        root = tmp / "root"; root.mkdir()
        # Identity requires no filesystem parent and preserves observed IDs.
        rc, obj = run(binary, root, "identity")
        assert rc == 0 and obj["ok"] and obj["schema"] == 1, obj
        assert obj["helper_commit"] == "unknown" and obj["uid"] == obj["euid"] == 0, obj
        assert not list(root.iterdir()), "identity created files"
        rc, obj = run(binary, root, "identity", uid=501)
        assert rc == 77 and obj["ok"] is False and obj["uid"] == obj["euid"] == 501, obj
        assert not list(root.iterdir()), "non-root identity created files"
        for name in ("private", "private/var", "private/var/mobile", "private/var/mobile/Library"):
            (root / name).mkdir()
        parent = root / "private/var/mobile/Library"

        # 1. prepare creates a root:wheel 0700 dir and a root 0600 file.
        rc, obj = run(binary, root, "prepare")
        assert rc == 0 and obj["ok"] is True, obj
        probe_dir = parent / "OpenMinisRootProbe"
        st = probe_dir.stat(); assert st.st_uid == 0 and (st.st_mode & 0o777) == 0o700, oct(st.st_mode)
        f = probe_dir / "sample-v1.txt"; fs = f.stat()
        assert fs.st_uid == 0 and (fs.st_mode & 0o777) == 0o600 and fs.st_nlink == 1

        # 2. check reads the exact marker and returns stable identity.
        rc, obj = run(binary, root, "check")
        assert rc == 0 and obj["ok"] and obj["marker_id"] == MARKER, obj
        inode = obj["inode"]

        # 3. cleanup removes the file, keeps the directory.
        rc, obj = run(binary, root, "cleanup")
        assert rc == 0 and obj["ok"] and not f.exists() and probe_dir.is_dir(), obj

        # 4. A replaced directory with wrong owner/mode is refused.
        probe_dir.rmdir()
        probe_dir.symlink_to(tmp)
        rc, obj = run(binary, root, "check")
        assert rc != 0 and obj["ok"] is False, obj
        probe_dir.unlink()

        # 5. A sample replaced by a symlink is refused (O_NOFOLLOW).
        rc, obj = run(binary, root, "prepare")
        assert rc == 0, obj
        target = tmp / "outside.txt"; target.write_text(f"OPENMINIS-ROOT-PROBE-V1:{MARKER}\n")
        f.unlink(); f.symlink_to(target)
        rc, obj = run(binary, root, "check")
        assert rc != 0 and obj["ok"] is False, obj
        f.unlink()

        # 6. Wrong mode on the sample is refused even with the right bytes.
        rc, obj = run(binary, root, "prepare")
        assert rc == 0, obj
        os.chmod(f, 0o644)
        rc, obj = run(binary, root, "check")
        assert rc != 0 and obj["ok"] is False, obj

        # 7. A hard link to the sample is refused.
        os.chmod(f, 0o600)
        os.link(f, tmp / "linked-sample")
        rc, obj = run(binary, root, "check")
        assert rc != 0 and obj["ok"] is False, obj
        (tmp / "linked-sample").unlink()

        # 8. Unknown operation is refused without touching the filesystem.
        rc, obj = run(binary, root, "rm -rf /")
        assert rc == 64 and obj["ok"] is False and obj["operation"] == "operation-not-allowed", obj

        # 9. Non-root caller cannot reach the privileged code path when setuid is absent.
        os.chmod(binary, 0o755)
        rc, obj = run(binary, root, "check", uid=501)
        assert rc == 77 and obj["ok"] is False, obj

        print("PASS root probe helper: identity without filesystem access, prepare/check/cleanup, dir policy, symlink, mode, hardlink, op allowlist, non-root refusal")
        print("NOTE  real iOS setuid/entitlement behaviour is unverified here; only source-level contracts were exercised")
    finally:
        os.chmod(tmp, 0o755)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
