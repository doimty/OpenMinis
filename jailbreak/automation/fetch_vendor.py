#!/usr/bin/env python3
"""Fetch only the MIT project-owned iOS MCP source at an immutable commit."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
COMMIT = "62c4464f688c8f269419924b1a19c996de75f175"
MODULES = ("AccessibilityManager AppManager ClipboardManager FileSystemManager HIDManager "
           "LogManager MCPAXAttributeBridge MCPAXNodeSource MCPAXQueryContext MCPAXRemoteContextResolver "
           "MCPLogger MCPOCRRequestContext MCPProcessUtil MCPServer MCPUIElementSerializer MCPUIElementsFacade "
           "OCRManager PaddleOCRManager ScreenManager TextInputManager VisionOCREngine").split()
FILES = [name + suffix for name in MODULES for suffix in (".h", ".m")]
FILES += ["AXPrivate.h", "IOHIDPrivate.h", "IOSMCPPreferences.h", "SpringBoardPrivate.h",
          "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"]


def fetch(name):
    url = f"https://raw.githubusercontent.com/witchan/ios-mcp/{COMMIT}/{name}"
    with urlopen(url, timeout=60) as response:
        data = response.read()
    path = ROOT / "vendor/ios-mcp" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return name, hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = dict(pool.map(fetch, FILES))
    manifest = {"schema": 1, "repository": "https://github.com/witchan/ios-mcp", "commit": COMMIT,
                "license": "MIT (project-owned top-level sources only)", "files": dict(sorted(hashes.items()))}
    (ROOT / "vendor/ios-mcp/source.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    roothide_commit = "cb236f9cf1c5e2ac932611328998a62b4b133e33"
    roothide_dir = ROOT / "vendor/libroothide"
    roothide_dir.mkdir(parents=True, exist_ok=True)
    roothide_hashes = {}
    for name in ("roothide.h", "stub.h", "roothide-xcode.h", "Makefile", "LICENSE"):
        with urlopen(f"https://raw.githubusercontent.com/roothide/libroothide/{roothide_commit}/{name}", timeout=60) as response:
            data = response.read()
        (roothide_dir / name).write_bytes(data)
        roothide_hashes[name] = hashlib.sha256(data).hexdigest()
    (roothide_dir / "source.json").write_text(json.dumps({"schema": 1,
        "repository": "https://github.com/roothide/libroothide", "commit": roothide_commit,
        "files": roothide_hashes}, indent=2) + "\n", encoding="utf-8")
    print(f"Fetched {len(hashes)} pinned MIT source/license files; no external binaries.")
