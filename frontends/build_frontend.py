#!/usr/bin/env python3
"""
DanmakuHime — frontend manifest generator (shared across all frontends/).

Lives at frontends/build_frontend.py and builds one frontend subfolder named on
the command line (or every one with --all):

    python3 frontends/build_frontend.py preprint
    python3 frontends/build_frontend.py --all

For the chosen frontend it writes <dir>/frontend.json, carrying the
name / version / release_date / api_version read out of the comment block at the
top of <dir>/index.html, plus one sha256 per line of <dir>/.project.

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

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pathspec

FRONTENDS_ROOT = Path(__file__).resolve().parent   # the frontends/ directory
SELF_PATH = Path(__file__).resolve()

# Metadata keys read out of index.html's comment block, in manifest order.
# `codename` is optional and may be empty (display-only, never validated); the
# rest are required. Order here is the order they appear in frontend.json.
META_KEYS = ("name", "version", "codename", "release_date", "api_version")
OPTIONAL_META = frozenset({"codename"})

# Names that may live in a frontend folder but are never part of the served/hashed
# payload: a frontend's `.project`, its generated manifest, and a builder copy left
# behind by unzipping a shipped package. Skipped from the candidate set on BOTH
# sides so a greedy pattern can't pull them in — MUST match app.py exactly.
NON_PAYLOAD = frozenset({"frontend.json", ".project"})


def read_meta(source: str, key: str) -> str:
    """Pull a `key: value` line out of index.html's DanmakuHime-Frontend block.
    Optional keys may be absent or empty (→ ""); required keys must be present."""
    optional = key in OPTIONAL_META
    value = r"(.*?)" if optional else r"(.+?)"
    # Horizontal-whitespace classes ([ \t], not \s) so an empty value can't let the
    # separator swallow the newline and capture the next line.
    match = re.search(rf"^[ \t]*{key}[ \t]*:[ \t]*{value}[ \t]*$", source, re.MULTILINE)
    if match is None:
        if optional:
            return ""
        raise SystemExit(f"在 index.html 的注释块中找不到 {key} 字段。")
    return match.group(1)


def read_patterns(project_file: Path) -> list[str]:
    """The `.project` allowlist, one pattern per line (blanks / # comments dropped)."""
    if not project_file.is_file():
        raise SystemExit(f"缺少 {project_file.name}（前端文件白名单，每行一个 gitignore 风格 pattern）。")
    lines = project_file.read_text(encoding="utf-8").splitlines()
    patterns = [s for line in lines if (s := line.strip()) and not s.startswith("#")]
    if not patterns:
        raise SystemExit(f"{project_file.name} 里没有任何 pattern。")
    return patterns


def candidate_files(frontend_dir: Path) -> list[Path]:
    """Every file under the frontend dir eligible to be matched by a pattern —
    i.e. minus the non-payload tooling/artifacts. Must match app.py exactly."""
    files = []
    for path in frontend_dir.rglob("*"):
        if not path.is_file() or path.suffix == ".zip":
            continue
        rel = path.relative_to(frontend_dir)
        if "__pycache__" in rel.parts or rel.as_posix() in NON_PAYLOAD:
            continue
        files.append(path)
    return files


def match_pattern(frontend_dir: Path, pattern: str, candidates: list[Path]) -> list[Path]:
    """The candidate files matched by one gitignore-style pattern, sorted by path."""
    spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
    matched = [p for p in candidates if spec.match_file(p.relative_to(frontend_dir).as_posix())]
    return sorted(matched, key=lambda p: p.relative_to(frontend_dir).as_posix())


def group_hash(frontend_dir: Path, files: list[Path]) -> str:
    """One sha256 sealing an ordered group of files: per file, the relative posix
    path then the raw bytes, each NUL-delimited so paths and content can't run
    together. Must stay byte-identical to app.py's _frontend_group_hash."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(frontend_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_zip(frontend_dir: Path, payload: set[Path]) -> tuple[Path, int]:
    """Pack one frontend into DanmakuHime-frontend-<dir>-YYYY-MM-DD-HH-MM.zip.

    Bundles the matched payload plus the frontend's `.project` and freshly written
    manifest, AND a copy of this shared builder dropped in as <dir>/build_frontend.py
    — so the zip stays a self-contained, rebuildable drop-in even though the repo
    keeps a single shared builder. Unzip into the backend's frontends/ and point
    config.toml at frontends/<dir>. Run after the manifest is written.
    """
    dirname = frontend_dir.name
    stem = f"DanmakuHime-frontend-{dirname}-" + datetime.now().strftime("%Y-%m-%d-%H-%M")
    zip_path = frontend_dir / f"{stem}.zip"

    members = sorted(payload | {frontend_dir / ".project", frontend_dir / "frontend.json"})
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in members:
            zf.write(path, f"{dirname}/{path.relative_to(frontend_dir).as_posix()}")

    return zip_path, len(members) + 1


def build_one(frontend_dir: Path) -> None:
    """Generate frontend.json + a package zip for a single frontend folder."""
    entry = frontend_dir / "index.html"
    if not entry.is_file():
        raise SystemExit(f"{frontend_dir} 缺少 index.html（前端必须有入口文件 index.html）。")

    meta = {key: read_meta(entry.read_text(encoding="utf-8"), key) for key in META_KEYS}

    patterns = read_patterns(frontend_dir / ".project")
    candidates = candidate_files(frontend_dir)

    payload: dict[str, str] = {}
    matched_files: set[Path] = set()
    for pattern in patterns:
        files = match_pattern(frontend_dir, pattern, candidates)
        if not files:
            raise SystemExit(f"{frontend_dir.name}/.project 中的 pattern {pattern!r} 没有匹配到任何文件。")
        payload[pattern] = group_hash(frontend_dir, files)
        matched_files.update(files)

    if entry not in matched_files:
        raise SystemExit(f"{frontend_dir.name}/.project 必须覆盖 index.html（它要被 serve 也要被校验）。")

    manifest = {**meta, "payload": payload}
    (frontend_dir / "frontend.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[{frontend_dir.name}] 已写入 frontend.json")
    for key in META_KEYS:
        print(f"  {key:<13} {meta[key]}")
    print(f"  payload       {len(payload)} 组 / {len(matched_files)} 个文件")
    for pattern, digest in payload.items():
        print(f"    {pattern:<14} {digest[:12]}…")

    zip_path, count = build_zip(frontend_dir, matched_files)
    print(f"  已打包 {zip_path.name}（{count} 个文件）")


def discover_frontends() -> list[Path]:
    """Every immediate subfolder of frontends/ that has an index.html."""
    return sorted(d for d in FRONTENDS_ROOT.iterdir() if (d / "index.html").is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 frontends/ 下某个前端的 frontend.json + 发布包。")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("name", nargs="?", help="要构建的前端子目录名（如 preprint）")
    group.add_argument("--all", action="store_true", help="构建 frontends/ 下所有含 index.html 的目录")
    args = parser.parse_args()

    if args.all:
        targets = discover_frontends()
        if not targets:
            raise SystemExit("frontends/ 下没有找到任何含 index.html 的前端目录。")
    else:
        target = FRONTENDS_ROOT / args.name
        if not target.is_dir():
            raise SystemExit(f"找不到前端目录 frontends/{args.name}。")
        targets = [target]

    for frontend_dir in targets:
        build_one(frontend_dir)


if __name__ == "__main__":
    main()
