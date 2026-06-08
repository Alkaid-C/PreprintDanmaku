#!/usr/bin/env python3
"""
DanmakuHime — backend manifest / package generator.

Hashes the backend's one source file (app.py) and writes backend.json next to
it, carrying that hash plus the app_version / release_date / api_version strings
read straight out of app.py.

app.py refuses to start unless its own APP_VERSION / RELEASE_DATE / API_VERSION
constants and the live sha256 of app.py match this manifest, so the workflow is:

    edit app.py  ->  python3 build_backend.py  ->  python3 app.py

The api_version is the front/back contract version (see SCHEMA.md). A frontend
manifest (frontends/<name>/frontend.json, built by that frontend's
build_frontend.py) carries its own api_version, and the backend refuses to serve
a frontend whose api_version does not equal this one — so a backend package and
a frontend package can ship independently and be combined as long as their
api_version strings match exactly.

The hashing here MUST stay byte-identical to app.py's check_version()
(sha256 over raw file bytes, no text decode / newline normalization).
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_FILE = BASE_DIR / "app.py"
BACKEND_MANIFEST = BASE_DIR / "backend.json"

# Must match app.py's INTEGRITY_FILES (the backend self-check set).
INTEGRITY_FILES = ("app.py",)

# Constants read straight out of app.py's source, in manifest order. Codenames
# ride along for display but are NOT checked by check_version (version strings are).
VERSION_CONSTANTS = (
    ("app_version", "APP_VERSION"),
    ("app_codename", "APP_CODENAME"),
    ("release_date", "RELEASE_DATE"),
    ("api_version", "API_VERSION"),
    ("api_codename", "API_CODENAME"),
)

# Everything a backend package needs. The frontend ships separately (its own
# folder under frontends/, with vendor/ and fonts/), so it is NOT bundled here —
# the operator drops a matching frontend package in and points config.toml at it.
# An explicit allowlist keeps runtime/secret files (credential.json, the
# log/stats/event sinks, __pycache__) out of the bundle.
PACKAGE_FILES = (
    "app.py",
    "build_backend.py",
    "backend.json",
    "config.toml",
    "requirements.txt",
    "README.md",
)


def file_sha256(path: Path) -> str:
    """sha256 of a file's raw bytes, hex-encoded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_constant(source: str, name: str) -> str:
    """Pull a top-level `NAME = "..."` string literal out of app.py's source.

    Reading the constants from the file (rather than importing app.py, which
    would pull in bilibili_api/flask and trigger the version check itself) keeps
    build_backend.py dependency-free and runnable before the manifest exists.
    """
    match = re.search(rf'^{name}\s*=\s*"([^"]*)"', source, re.MULTILINE)
    if match is None:
        raise SystemExit(f"在 app.py 中找不到 {name} 常量。")
    return match.group(1)


def build_zip() -> tuple[Path, int]:
    """Pack the backend run-time files into DanmakuHime-backend-YYYY-MM-DD-HH-MM.zip.

    Files are stored under a top-level <stem>/ folder so the zip extracts into one
    tidy directory. Run after the manifest is written so the bundled backend.json
    is current.
    """
    stem = "DanmakuHime-backend-" + datetime.now().strftime("%Y-%m-%d-%H-%M")
    zip_path = BASE_DIR / f"{stem}.zip"

    members: list[tuple[Path, str]] = []
    for name in PACKAGE_FILES:
        path = BASE_DIR / name
        if not path.is_file():
            raise SystemExit(f"打包失败：缺少文件 {name}")
        members.append((path, name))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in members:
            zf.write(path, f"{stem}/{arcname}")

    return zip_path, len(members)


def main() -> None:
    source = APP_FILE.read_text(encoding="utf-8")
    meta = {key: read_constant(source, const) for key, const in VERSION_CONSTANTS}

    hashes = {name: file_sha256(BASE_DIR / name) for name in INTEGRITY_FILES}

    manifest = {**meta, "hashes": hashes}

    BACKEND_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"已写入 {BACKEND_MANIFEST.name}")
    for key, _ in VERSION_CONSTANTS:
        print(f"  {key:<13} {meta[key]}")
    for name, digest in hashes.items():
        print(f"  {name:<13} {digest}")

    zip_path, count = build_zip()
    print(f"已打包 {zip_path.name}（{count} 个文件）")


if __name__ == "__main__":
    main()
