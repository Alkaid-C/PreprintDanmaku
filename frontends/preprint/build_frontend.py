#!/usr/bin/env python3
"""
DanmakuHime — frontend manifest generator.

Writes frontend.json next to this script, carrying the name / version /
release_date / api_version read out of the comment block at the top of
index.html, plus one sha256 per line of the `.project` allowlist.

`.project` declares which files ship, one gitignore-style pattern per line (matched
with `pathspec`). Each line maps to ONE hash that seals every file that pattern
matches, so the author picks the granularity: give a file its own line for a
per-file hash, or use a glob (`fonts/**`) to seal a whole tree under one hash.
A pattern matching nothing is an error (likely a typo).

index.html's comment block and `.project` are the two hand-authored truth sources;
this script only mirrors them into the (fully generated, never hand-edited) json.
The backend (app.py) re-derives the same hashes from `.project`'s patterns recorded
as the json's keys and refuses to start on any mismatch (or an api_version that
differs from its own), so a frontend package and a backend package ship separately
and combine iff their api_version strings match.

The pattern matching and hashing here MUST stay byte-identical to app.py's
check_frontend (same pathspec gitwildmatch, same candidate filter, same digest
construction — sorted relative posix path + NUL + raw bytes + NUL per file).
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pathspec

FRONTEND_DIR = Path(__file__).resolve().parent
ENTRY_FILE = FRONTEND_DIR / "index.html"
MANIFEST_FILE = FRONTEND_DIR / "frontend.json"
PROJECT_FILE = FRONTEND_DIR / ".project"
SELF_NAME = Path(__file__).name

# Metadata keys read out of index.html's comment block, in manifest order.
META_KEYS = ("name", "version", "release_date", "api_version")

# Files that live in the folder but are never part of the served/hashed payload:
# the build tooling and the manifest itself (a file can't hash itself; the json is
# written, and the zip created, after hashing). Skipped from the candidate set on
# BOTH sides so a greedy pattern can't pull them in — must match app.py exactly.
NON_PAYLOAD = frozenset({MANIFEST_FILE.name, SELF_NAME, PROJECT_FILE.name})


def read_meta(source: str, key: str) -> str:
    """Pull a `key: value` line out of index.html's DanmakuHime-Frontend block."""
    match = re.search(rf"^\s*{key}\s*:\s*(.+?)\s*$", source, re.MULTILINE)
    if match is None:
        raise SystemExit(f"在 index.html 的注释块中找不到 {key} 字段。")
    return match.group(1)


def read_patterns() -> list[str]:
    """The `.project` allowlist, one pattern per line (blanks / # comments dropped)."""
    if not PROJECT_FILE.is_file():
        raise SystemExit(f"缺少 {PROJECT_FILE.name}（前端文件白名单，每行一个 gitignore 风格 pattern）。")
    lines = PROJECT_FILE.read_text(encoding="utf-8").splitlines()
    patterns = [s for line in lines if (s := line.strip()) and not s.startswith("#")]
    if not patterns:
        raise SystemExit(f"{PROJECT_FILE.name} 里没有任何 pattern。")
    return patterns


def candidate_files() -> list[Path]:
    """Every file under the frontend dir eligible to be matched by a pattern —
    i.e. minus the non-payload tooling/artifacts. Must match app.py exactly."""
    files = []
    for path in FRONTEND_DIR.rglob("*"):
        if not path.is_file() or path.suffix == ".zip":
            continue
        rel = path.relative_to(FRONTEND_DIR)
        if "__pycache__" in rel.parts or rel.as_posix() in NON_PAYLOAD:
            continue
        files.append(path)
    return files


def match_pattern(pattern: str, candidates: list[Path]) -> list[Path]:
    """The candidate files matched by one gitignore-style pattern, sorted by path."""
    spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
    matched = [p for p in candidates if spec.match_file(p.relative_to(FRONTEND_DIR).as_posix())]
    return sorted(matched, key=lambda p: p.relative_to(FRONTEND_DIR).as_posix())


def group_hash(files: list[Path]) -> str:
    """One sha256 sealing an ordered group of files: per file, the relative posix
    path then the raw bytes, each NUL-delimited so paths and content can't run
    together. Must stay byte-identical to app.py's _frontend_group_hash."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(FRONTEND_DIR).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_zip(payload: set[Path]) -> tuple[Path, int]:
    """Pack the whole frontend into DanmakuHime-frontend-<dir>-YYYY-MM-DD-HH-MM.zip.

    Bundles the matched payload plus this script, `.project` and the freshly written
    manifest, under a top-level <dir>/ folder (the frontend's directory name) so it
    extracts as a drop-in: unzip into the backend's frontends/ and point config.toml
    at frontends/<dir>. Run after the manifest is written so it is included.
    """
    dirname = FRONTEND_DIR.name
    stem = f"DanmakuHime-frontend-{dirname}-" + datetime.now().strftime("%Y-%m-%d-%H-%M")
    zip_path = FRONTEND_DIR / f"{stem}.zip"

    members = sorted(payload | {FRONTEND_DIR / SELF_NAME, PROJECT_FILE, MANIFEST_FILE})
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in members:
            arcname = path.relative_to(FRONTEND_DIR).as_posix()
            zf.write(path, f"{dirname}/{arcname}")

    return zip_path, len(members)


def main() -> None:
    if not ENTRY_FILE.is_file():
        raise SystemExit("缺少 index.html（前端必须有入口文件 index.html）。")

    source = ENTRY_FILE.read_text(encoding="utf-8")
    meta = {key: read_meta(source, key) for key in META_KEYS}

    patterns = read_patterns()
    candidates = candidate_files()

    payload: dict[str, str] = {}
    matched_files: set[Path] = set()
    for pattern in patterns:
        files = match_pattern(pattern, candidates)
        if not files:
            raise SystemExit(f"{PROJECT_FILE.name} 中的 pattern {pattern!r} 没有匹配到任何文件。")
        payload[pattern] = group_hash(files)
        matched_files.update(files)

    if ENTRY_FILE not in matched_files:
        raise SystemExit(f"{PROJECT_FILE.name} 必须覆盖 index.html（它要被 serve 也要被校验）。")

    manifest = {**meta, "payload": payload}
    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"已写入 {MANIFEST_FILE.name}")
    for key in META_KEYS:
        print(f"  {key:<13} {meta[key]}")
    print(f"  payload       {len(payload)} 组 / {len(matched_files)} 个文件")
    for pattern, digest in payload.items():
        print(f"    {pattern:<14} {digest[:12]}…")

    zip_path, count = build_zip(matched_files)
    print(f"已打包 {zip_path.name}（{count} 个文件）")


if __name__ == "__main__":
    main()
