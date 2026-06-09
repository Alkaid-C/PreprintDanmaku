#!/usr/bin/env python3
"""
DanmakuHime — backend manifest / package generator.

Hashes the backend's source modules and writes backend.json next to them,
carrying those hashes plus the app_version / release_date / api_version strings
read straight out of main.py (where the version constants live).

The backend refuses to start unless main.py's APP_VERSION / RELEASE_DATE /
API_VERSION constants and the live sha256 of every module match this manifest, so
the workflow is:

    edit any backend .py  ->  python3 backend/build_backend.py  ->  python3 run.py

The api_version is the front/back contract version (see docs/SCHEMA.md). A frontend
manifest (frontends/<name>/frontend.json, built by that frontend's
build_frontend.py) carries its own api_version, and the backend refuses to serve
a frontend whose api_version does not equal this one — so a backend package and
a frontend package can ship independently and be combined as long as their
api_version strings match exactly.

This script is BACKEND-ONLY: it builds backend.json plus a backend package zip and
knows nothing about frontends. Combining a backend with one or more frontends into
a single runnable bundle is the job of the repo-root build.py (the assembler) — it
folds already-built frontend packages in next to the backend, mirroring the repo.

The backend package zip mirrors the repo layout so it drops straight into a runnable
tree once a frontend is added beside it:

    <stem>/run.py                 launcher
    <stem>/README.md              requirements.txt
    <stem>/backend/...            modules + config.toml + backend.json
    (<stem>/frontends/<name>/...  added by build.py, or dropped in by the operator)

config.toml's `frontend = "../frontends/<name>"` then resolves from backend/ to the
sibling frontend folder, unchanged. The zip lands in the repo-root dist/ folder.

The hashing here MUST stay byte-identical to VersionGuard.check_version() in
initialization.py (sha256 over raw file bytes, no text decode / newline
normalization).
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent          # backend/
REPO_ROOT = BASE_DIR.parent                          # repo / bundle root
DIST_DIR = REPO_ROOT / "dist"
MAIN_FILE = BASE_DIR / "main.py"
BACKEND_MANIFEST = BASE_DIR / "backend.json"

# Must match initialization.py's INTEGRITY_FILES (the backend self-check set):
# every backend source module, hashed and bound to backend.json.
INTEGRITY_FILES = (
    "util.py",
    "initialization.py",
    "server.py",
    "credentials.py",
    "stats.py",
    "bilibili.py",
    "main.py",
)

# Constants read straight out of main.py's source, in manifest order. Codenames
# ride along for display but are NOT checked by check_version (version strings are).
VERSION_CONSTANTS = (
    ("app_version", "APP_VERSION"),
    ("app_codename", "APP_CODENAME"),
    ("release_date", "RELEASE_DATE"),
    ("api_version", "API_VERSION"),
    ("api_codename", "API_CODENAME"),
)

# What the backend package ships, as (source path, arcname-in-zip) — the single
# source of truth for the backend package layout, reused by build.py when it folds
# frontends into a bundle. The frontend ships separately (its own folder under
# frontends/, with vendor/ and fonts/) and is NOT included here. An explicit
# allowlist keeps runtime/secret files (credential.json, the log/stats/event sinks,
# __pycache__) out of the package.
#
# backend/ modules + config.toml + backend.json land under backend/; the launcher
# and the project-level docs/deps sit at the package root, mirroring the repo.
_BACKEND_DIR_FILES = (
    "util.py",
    "initialization.py",
    "server.py",
    "credentials.py",
    "stats.py",
    "bilibili.py",
    "main.py",
    "build_backend.py",
    "backend.json",
    "config.toml",
)
_ROOT_FILES = (
    "run.py",
    "README.md",
    "requirements.txt",
)


def file_sha256(path: Path) -> str:
    """sha256 of a file's raw bytes, hex-encoded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backend_members() -> list[tuple[Path, str]]:
    """The backend package's (source path, arcname) pairs, laid out like the repo:
    backend/ files under backend/, root-level launcher/docs/deps at the top. Errors
    if any expected file is missing. Reused by build.py to seed a bundle."""
    members: list[tuple[Path, str]] = []
    for name in _BACKEND_DIR_FILES:
        path = BASE_DIR / name
        if not path.is_file():
            raise SystemExit(f"打包失败：缺少文件 backend/{name}")
        members.append((path, f"backend/{name}"))
    for name in _ROOT_FILES:
        path = REPO_ROOT / name
        if not path.is_file():
            raise SystemExit(f"打包失败：缺少文件 {name}")
        members.append((path, name))
    return members


def write_zip(members: list[tuple[Path, str]], kind: str) -> tuple[Path, int]:
    """Write `members` ((source, arcname) pairs) into dist/DanmakuHime-<kind>-<ts>.zip,
    each stored under a single top-level <stem>/ folder so it extracts into one tidy
    directory. Shared by this script (backend) and build.py (bundle)."""
    stem = f"DanmakuHime-{kind}-" + datetime.now().strftime("%Y-%m-%d-%H-%M")
    DIST_DIR.mkdir(exist_ok=True)
    zip_path = DIST_DIR / f"{stem}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in members:
            zf.write(path, f"{stem}/{arcname}")
    return zip_path, len(members)


def read_constant(source: str, name: str) -> str:
    """Pull a top-level `NAME = "..."` string literal out of main.py's source.

    Reading the constants from the file (rather than importing main.py, which
    would pull in bilibili_api/flask and trigger the version check itself) keeps
    build_backend.py dependency-free and runnable before the manifest exists.
    """
    match = re.search(rf'^{name}\s*=\s*"([^"]*)"', source, re.MULTILINE)
    if match is None:
        raise SystemExit(f"在 main.py 中找不到 {name} 常量。")
    return match.group(1)


def write_manifest() -> dict:
    """Read the version constants out of main.py, hash every INTEGRITY_FILES module,
    and write backend.json. Returns the manifest dict (build.py reuses api_version)."""
    source = MAIN_FILE.read_text(encoding="utf-8")
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
        print(f"  {name:<20} {digest}")
    return manifest


def main() -> None:
    write_manifest()
    zip_path, count = write_zip(backend_members(), "backend")
    print(f"已打包 {zip_path.relative_to(REPO_ROOT)}（{count} 个文件）")
    print("（如需把前端一并打成可直接运行的 bundle，改用仓库根目录的 build.py）")


if __name__ == "__main__":
    main()
