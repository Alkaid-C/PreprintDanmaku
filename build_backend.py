#!/usr/bin/env python3
"""
DanmakuHime — backend manifest / package generator.

Hashes the backend's source modules and writes backend.json next to them,
carrying those hashes plus the app_version / release_date / api_version strings
read straight out of main.py (where the version constants live).

The backend refuses to start unless main.py's APP_VERSION / RELEASE_DATE /
API_VERSION constants and the live sha256 of every module match this manifest, so
the workflow is:

    edit any backend .py  ->  python3 build_backend.py  ->  python3 main.py

The api_version is the front/back contract version (see SCHEMA.md). A frontend
manifest (frontends/<name>/frontend.json, built by that frontend's
build_frontend.py) carries its own api_version, and the backend refuses to serve
a frontend whose api_version does not equal this one — so a backend package and
a frontend package can ship independently and be combined as long as their
api_version strings match exactly.

By default the zip is backend-only (the frontend ships in its own package). Pass
`--frontend NAME` (repeatable) or `--all-frontends` to fold one or more already-built
frontends into the SAME zip, laid out exactly like the repo so the bundle extracts
straight into a runnable tree:

    python3 build_backend.py --frontend preprint   ->  unzip  ->  python3 main.py

Folding a frontend in does NOT re-hash or re-generate it: each frontend keeps its
own separately-built frontend.json, and this script only copies the already-built
files in (it errors if a requested frontend was never built). The bundle layout is

    <stem>/                       backend files + config.toml + backend.json
    <stem>/frontends/<name>/...   each folded-in frontend, with its frontend.json

which matches config.toml's `frontend = "frontends/<name>"` unchanged.

The hashing here MUST stay byte-identical to VersionGuard.check_version() in
initialization.py (sha256 over raw file bytes, no text decode / newline
normalization).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MAIN_FILE = BASE_DIR / "main.py"
BACKEND_MANIFEST = BASE_DIR / "backend.json"
FRONTENDS_ROOT = BASE_DIR / "frontends"
FRONTEND_BUILDER = FRONTENDS_ROOT / "build_frontend.py"

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

# Everything a backend package needs. By default the frontend ships separately
# (its own folder under frontends/, with vendor/ and fonts/) and is NOT bundled —
# the operator drops a matching frontend package in and points config.toml at it.
# (Pass --frontend/--all-frontends to additionally fold already-built frontends
# into the same zip; see build_zip.) An explicit allowlist keeps runtime/secret
# files (credential.json, the log/stats/event sinks, __pycache__) out of the bundle.
PACKAGE_FILES = (
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
    "requirements.txt",
    "README.md",
)


def file_sha256(path: Path) -> str:
    """sha256 of a file's raw bytes, hex-encoded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_frontend_builder():
    """Import frontends/build_frontend.py as a module so we can reuse its `.project`
    matching to find exactly which files a frontend ships — without duplicating that
    logic here. (It pulls in `pathspec`, already a runtime dependency.)"""
    if not FRONTEND_BUILDER.is_file():
        raise SystemExit(f"打包前端需要 {FRONTEND_BUILDER.relative_to(BASE_DIR)}，但没找到。")
    spec = importlib.util.spec_from_file_location("_dh_build_frontend", FRONTEND_BUILDER)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 {FRONTEND_BUILDER.relative_to(BASE_DIR)}。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_frontend_files(name: str, bf) -> tuple[Path, list[Path]]:
    """Resolve frontends/<name> and return (dir, files-to-ship). The shipped set is
    the `.project` payload (matched via the shared builder) plus the sidecar files
    a frontend package carries: .project, the already-built frontend.json, and an
    optional config.json. Errors if the frontend was never built — we copy its hashes
    in, we don't regenerate them."""
    frontend_dir = FRONTENDS_ROOT / name
    if not (frontend_dir / "index.html").is_file():
        raise SystemExit(f"找不到前端 frontends/{name}（缺少 index.html）。")
    manifest = frontend_dir / "frontend.json"
    if not manifest.is_file():
        raise SystemExit(
            f"frontends/{name} 还没构建（缺少 frontend.json）。"
            f"先运行：python3 frontends/build_frontend.py {name}"
        )

    patterns = bf.read_patterns(frontend_dir / ".project")
    candidates = bf.candidate_files(frontend_dir)
    payload: set[Path] = set()
    for pattern in patterns:
        files = bf.match_pattern(frontend_dir, pattern, candidates)
        if not files:
            raise SystemExit(f"frontends/{name}/.project 中的 pattern {pattern!r} 没有匹配到任何文件。")
        payload.update(files)

    sidecars = [
        path
        for path in (manifest, frontend_dir / ".project", frontend_dir / "config.json")
        if path.is_file()
    ]
    return frontend_dir, sorted(payload | set(sidecars))


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


def build_zip(frontends: list[str], api_version: str) -> tuple[Path, int]:
    """Pack the backend run-time files — and any requested frontends — into one zip.

    Backend-only (no frontends) → DanmakuHime-backend-YYYY-MM-DD-HH-MM.zip; with
    frontends folded in → DanmakuHime-bundle-…. Everything is stored under a top-level
    <stem>/ folder so the zip extracts into one tidy, runnable directory. Frontends go
    under <stem>/frontends/<name>/, mirroring the repo so config.toml's
    `frontend = "frontends/<name>"` works unchanged. The shared build_frontend.py is
    NOT shipped — a folded-in frontend is already built; the backend only needs its
    .project + frontend.json to re-verify at startup. Run after the manifest is written
    so the bundled backend.json is current.
    """
    kind = "bundle" if frontends else "backend"
    stem = f"DanmakuHime-{kind}-" + datetime.now().strftime("%Y-%m-%d-%H-%M")
    zip_path = BASE_DIR / f"{stem}.zip"

    members: list[tuple[Path, str]] = []
    for name in PACKAGE_FILES:
        path = BASE_DIR / name
        if not path.is_file():
            raise SystemExit(f"打包失败：缺少文件 {name}")
        members.append((path, name))

    if frontends:
        bf = _load_frontend_builder()
        for name in frontends:
            frontend_dir, files = collect_frontend_files(name, bf)
            fe_api = json.loads((frontend_dir / "frontend.json").read_text("utf-8")).get("api_version")
            if fe_api != api_version:
                print(f"  ⚠ frontends/{name} 的 api_version={fe_api!r} 与后端 {api_version!r} 不一致，"
                      f"后端将拒绝 serve 它。")
            for path in files:
                rel = path.relative_to(FRONTENDS_ROOT).as_posix()
                members.append((path, f"frontends/{rel}"))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in members:
            zf.write(path, f"{stem}/{arcname}")

    return zip_path, len(members)


def resolve_frontends(args: argparse.Namespace) -> list[str]:
    """The frontend folder names to fold into the zip, from the CLI flags."""
    if args.all_frontends:
        names = sorted(d.name for d in FRONTENDS_ROOT.iterdir() if (d / "index.html").is_file())
        if not names:
            raise SystemExit("frontends/ 下没有找到任何含 index.html 的前端目录。")
        return names
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(args.frontend or []))


def main() -> None:
    parser = argparse.ArgumentParser(description="构建后端 backend.json + 发布包（可选一并打包前端）。")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--frontend", "-f", action="append", metavar="NAME",
                       help="把指定前端一并打进同一个 zip（可多次指定；前端需已 build_frontend 过）")
    group.add_argument("--all-frontends", action="store_true",
                       help="把 frontends/ 下所有已构建的前端都打进同一个 zip")
    args = parser.parse_args()
    frontends = resolve_frontends(args)

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

    zip_path, count = build_zip(frontends, meta["api_version"])
    if frontends:
        print(f"  含前端：{', '.join(frontends)}")
    print(f"已打包 {zip_path.name}（{count} 个文件）")


if __name__ == "__main__":
    main()
