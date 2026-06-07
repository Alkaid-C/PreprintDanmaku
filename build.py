#!/usr/bin/env python3
"""
DanmakuHime Preprint — build / version-manifest generator.

Hashes the three source files (app.py, danmaku-feed.jsx, preprint.html) and
writes version.json next to them, carrying those hashes plus the app_version
and release_date strings read straight out of app.py.

app.py refuses to start unless its own APP_VERSION / RELEASE_DATE constants and
the live sha256 of each file match this manifest, so the workflow is:

    edit app.py / danmaku-feed.jsx / preprint.html  ->  python3 build.py  ->  python3 app.py

The hashing here MUST stay byte-identical to app.py's check_version()
(sha256 over raw file bytes, no text decode / newline normalization).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_FILE = BASE_DIR / "app.py"
VERSION_FILE = BASE_DIR / "version.json"

# Must match app.py's INTEGRITY_FILES.
INTEGRITY_FILES = ("app.py", "danmaku-feed.jsx", "preprint.html")


def file_sha256(path: Path) -> str:
    """sha256 of a file's raw bytes, hex-encoded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_constant(source: str, name: str) -> str:
    """Pull a top-level `NAME = "..."` string literal out of app.py's source.

    Reading the constants from the file (rather than importing app.py, which
    would pull in bilibili_api/flask and trigger the version check itself) keeps
    build.py dependency-free and runnable before the manifest exists.
    """
    match = re.search(rf'^{name}\s*=\s*"([^"]*)"', source, re.MULTILINE)
    if match is None:
        raise SystemExit(f"在 app.py 中找不到 {name} 常量。")
    return match.group(1)


def main() -> None:
    source = APP_FILE.read_text(encoding="utf-8")
    app_version = read_constant(source, "APP_VERSION")
    release_date = read_constant(source, "RELEASE_DATE")

    hashes = {name: file_sha256(BASE_DIR / name) for name in INTEGRITY_FILES}

    manifest = {
        "app_version": app_version,
        "release_date": release_date,
        "hashes": hashes,
    }

    VERSION_FILE.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"已写入 {VERSION_FILE.name}")
    print(f"  app_version : {app_version}")
    print(f"  release_date: {release_date}")
    for name, digest in hashes.items():
        print(f"  {name:<18} {digest}")


if __name__ == "__main__":
    main()
