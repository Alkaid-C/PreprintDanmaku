#!/usr/bin/env python3
"""
DanmakuHime — bundle assembler.

Combines the backend package with one or more already-built frontends into a
single runnable bundle zip. This is the only place that knows about *assembling*
the two independently-shipped packages; each package still builds itself:

    python3 backend/build_backend.py          # backend package + backend.json
    python3 frontends/build_frontend.py NAME   # one frontend package
    python3 build.py NAME [NAME ...]           # fold them into one bundle  ->  dist/

As a convenience, build.py refreshes backend.json itself (so the bundled backend
manifest is always current), then folds in the requested frontends. Folding does
NOT re-hash or re-generate a frontend — each frontend keeps its own separately-built
frontend.json, and this only copies the already-built files in (it errors if a
requested frontend was never built).

The bundle mirrors the repo so it extracts straight into a runnable tree:

    <stem>/run.py                 launcher        ->  python3 run.py
    <stem>/README.md              requirements.txt
    <stem>/backend/...            modules + config.toml + backend.json
    <stem>/frontends/<name>/...   each folded-in frontend, with its frontend.json

which matches config.toml's `frontend = "../frontends/<name>"` unchanged. The shared
build_frontend.py is NOT shipped — a folded-in frontend is already built; the backend
only needs its .project + frontend.json to re-verify at startup. The zip lands in dist/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent           # repo / bundle root
BACKEND_DIR = BASE_DIR / "backend"
FRONTENDS_ROOT = BASE_DIR / "frontends"
FRONTEND_BUILDER = FRONTENDS_ROOT / "build_frontend.py"

# Reuse the backend builder's package layout, manifest refresh, and zip writer.
sys.path.insert(0, str(BACKEND_DIR))
import build_backend as bb  # noqa: E402  (path is set above on purpose)


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


def resolve_frontends(args: argparse.Namespace) -> list[str]:
    """The frontend folder names to fold in, from the CLI."""
    if args.all:
        names = sorted(d.name for d in FRONTENDS_ROOT.iterdir() if (d / "index.html").is_file())
        if not names:
            raise SystemExit("frontends/ 下没有找到任何含 index.html 的前端目录。")
        return names
    if not args.frontends:
        raise SystemExit("请指定至少一个前端名（或用 --all 打包全部）。例如：python3 build.py preprint")
    return list(dict.fromkeys(args.frontends))  # preserve order, drop duplicates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把后端与一个或多个已构建的前端合并成一个可直接运行的 bundle。")
    parser.add_argument("frontends", nargs="*", metavar="NAME",
                        help="要打进 bundle 的前端目录名（需已 build_frontend 过）")
    parser.add_argument("--all", action="store_true",
                        help="把 frontends/ 下所有含 index.html 的前端都打进同一个 bundle")
    args = parser.parse_args()
    frontends = resolve_frontends(args)

    # Refresh backend.json so the bundled manifest is current, then seed the members
    # with the backend package laid out exactly as in the repo.
    manifest = bb.write_manifest()
    api_version = manifest["api_version"]
    members = bb.backend_members()

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

    zip_path, count = bb.write_zip(members, "bundle")
    print(f"  含前端：{', '.join(frontends)}")
    print(f"已打包 {zip_path.relative_to(BASE_DIR)}（{count} 个文件）")


if __name__ == "__main__":
    main()
