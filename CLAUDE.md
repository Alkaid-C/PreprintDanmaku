# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this is

A Bilibili live-stream danmaku backend with swappable browser frontends. The backend connects to a live room and converts Bilibili events into a typed SSE event stream. The bundled `preprint` frontend renders each event as a typographic element of a fake paper (citations, theorems, acknowledgments).

## Architecture

A **backend** and a **swappable frontend**, two independently-shipped packages bound by **one contract**..

**`docs/SCHEMA.md` is the authoritative front/back field contract** — it defines the SSE event shapes (`init` / `danmaku` / `gift` / `superchat` / `guard`) and nothing about rendering. Read it before changing any event shape, keep both sides in sync, and bump `API_VERSION` (the contract version) when it changes.

Three deliberately separate version axes:
- **`APP_VERSION`** / `RELEASE_DATE` — the backend's own version (constants in `main.py`).
- the **frontend version** / name / release date — per frontend, in its `index.html` comment block → `frontend.json`.
- **`API_VERSION`** — the front/back **contract** version (the `docs/SCHEMA.md` event-shape version), independent of the other two. `main.py` declares one; each `frontend.json` declares the one it needs. They must be **exactly equal** or the backend refuses to serve that frontend — this is what lets any backend package combine with any frontend package iff their `API_VERSION` matches. (The contract version is *not* bound to `docs/SCHEMA.md` itself; that doc doesn't ship — `main.py`'s `API_VERSION` and the frontend's `index.html` comment are the truth sources.)

Each axis also has an optional **codename** (`APP_CODENAME` / `API_CODENAME` in `main.py`; a frontend `codename` in its `index.html`). Codenames ride along in the manifests and print at startup but are **display-only — never validated**; only version strings are matched.

Internals live one level down, loaded when you work in that package:
- **`backend/CLAUDE.md`** — the seven modules, `config.toml` / `AppConfig`, and the backend build/integrity mechanics.
- **`frontends/CLAUDE.md`** — the per-frontend folder layout, the `index.html` + `.project` truth sources, local `vendor/`/`fonts/`, and the preprint rendering model.

This top-level file is the cross-cutting overview: the contract, the version axes, the repo layout, and how the pieces build and combine.

### Repository layout

```
run.py            launcher — `python3 run.py` (puts backend/ on sys.path, then calls backend/main.py:main)
build.py          bundle assembler — folds the backend + chosen frontend(s) into one runnable zip → dist/
backend/          the backend package: the 7 modules + build_backend.py + backend.json + config.toml
frontends/<name>/ each swappable frontend package (built by frontends/build_frontend.py)
dev/              dev-only tooling, NOT shipped: mock_backend.py + its mock_record.txt replay
docs/             SCHEMA.md, RAW_DATA.md — reference docs, not shipped
dist/             build outputs (*.zip, gitignored)
```

The backend reaches the frontend through exactly one resolved path: `config.toml`'s `frontend = "../frontends/<name>"`, resolved against `BASE_DIR` (= `backend/`) up to the sibling `frontends/` folder. `run.py` (root) and `build.py` (root) are thin launcher/assembler shims and are **not** part of the integrity-checked backend package. A built package/bundle mirrors this repo tree exactly — `run.py` + `backend/…` + `frontends/<name>/…` — so the config string works unchanged after extraction.

## Build & Verification

There is no test suite or linter. There **is** a per-package build step that is also a startup staleness/integrity guard — **after editing backend or frontend code you must rebuild, or the backend refuses to start:**

```bash
python3 backend/build_backend.py               # after editing any backend .py — regenerates backend.json (+ backend-only zip → dist/)
python3 frontends/build_frontend.py preprint   # after editing a frontend file or its .project (--all does every frontend)
python3 run.py                                  # then run
python3 build.py preprint                       # (optional) fold backend + frontend(s) into one runnable bundle → dist/
```

Each package builds itself and carries its own manifest + startup guard: the **backend** via `backend/build_backend.py` → `backend.json` ↔ `VersionGuard.check_version()` (mechanics in `backend/CLAUDE.md`); each **frontend** via `frontends/build_frontend.py` → `frontend.json` ↔ `VersionGuard.check_frontend()` (mechanics in `frontends/CLAUDE.md`). The root **`build.py`** is the **assembler** — it refreshes `backend.json`, then folds the backend package plus one or more *already-built* frontends into a single `dist/` bundle laid out like the repo (`run.py` + `backend/…` + `frontends/<name>/…`); folding does not re-hash a frontend, it just copies the already-built files in. It is the only place that knows about combining the two packages.

These are **guards, not tamper-proofing** (the json manifests are themselves unprotected): they catch editing a file, bumping a version, or pairing mismatched packages without rebuilding. Because the two packages ship separately, the hashing logic is **duplicated** across each package's builder and `initialization.py`'s `VersionGuard`, and MUST stay byte-identical — `sha256` over raw bytes (`read_bytes()`), no text decode or newline normalization. Keep the copies in sync: `file_sha256` + `INTEGRITY_FILES` in `backend/build_backend.py` ↔ `initialization.py`; and `_frontend_candidates` + `_frontend_group_hash` in `initialization.py` ↔ `build_frontend.py`'s NON_PAYLOAD filter + `pathspec` matching + group digest.

Install dependencies with `python3 -m pip install -r requirements.txt` (lower-bound pins in the root `requirements.txt`; the backend's dependency list and the `credential.json` it writes after login are documented in `backend/CLAUDE.md`).

## Documents

- **`README.md`** (root) — the user-facing readme: run, configure, package.
- **`backend/CLAUDE.md`** — backend internals: the seven modules, `config.toml` / `AppConfig`, the backend build/integrity mechanics.
- **`frontends/CLAUDE.md`** — frontend-author guide: folder layout, `index.html` + `.project` truth sources, the target OBS runtime.
- **`docs/SCHEMA.md`** — the authoritative front/back SSE field contract (see Architecture). The source of truth for event shapes and `API_VERSION`.
- **`docs/RAW_DATA.md`** — reference for Bilibili's raw event formats (UserInfo, envelope, per-`cmd` payloads). The `§` citations in `bilibili.py`'s parse comments point here.
- **`dev/mock_record.txt`** — a small captured sample (one event per line, a Python `repr` restored with `ast.literal_eval`) that `dev/mock_backend.py` replays over SSE for frontend development. The realistic input to test parsing against; not shipped (`mock_backend.py` lives in `dev/`).
