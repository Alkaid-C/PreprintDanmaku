# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this is

A Bilibili live-stream danmaku backend with swappable browser frontends. The backend connects to a live room and converts Bilibili events into a typed SSE event stream. The bundled `preprint` frontend renders each event as a typographic element of a fake paper (citations, theorems, acknowledgments).

## Architecture

A **backend** and a **swappable frontend**, two independently-shipped packages bound by **one contract**..

**`SCHEMA.md` is the authoritative front/back field contract** — it defines the SSE event shapes (`init` / `danmaku` / `gift` / `superchat` / `guard`) and nothing about rendering. Read it before changing any event shape, keep both sides in sync, and bump `API_VERSION` (the contract version) when it changes.

Three deliberately separate version axes:
- **`APP_VERSION`** / `RELEASE_DATE` — the backend's own version (constants in `main.py`).
- the **frontend version** / name / release date — per frontend, in its `index.html` comment block → `frontend.json`.
- **`API_VERSION`** — the front/back **contract** version (the `SCHEMA.md` event-shape version), independent of the other two. `main.py` declares one; each `frontend.json` declares the one it needs. They must be **exactly equal** or the backend refuses to serve that frontend — this is what lets any backend package combine with any frontend package iff their `API_VERSION` matches. (The contract version is *not* bound to `SCHEMA.md` itself; that doc doesn't ship — `main.py`'s `API_VERSION` and the frontend's `index.html` comment are the truth sources.)

Each axis also has an optional **codename** (`APP_CODENAME` / `API_CODENAME` in `main.py`; a frontend `codename` in its `index.html`). Codenames ride along in the manifests and print at startup but are **display-only — never validated**; only version strings are matched.

**Frontend details live in `frontends/CLAUDE.md`** — the per-frontend folder layout, the `index.html` + `.project` truth sources, local `vendor/`/`fonts/`, and the preprint rendering model. This file covers the backend.

### Backend

Seven single-responsibility modules in a strictly acyclic import graph, leaf → entry. Each module's top docstring states its responsibility; the shape:

- **`util.py`** — dependency-free leaf. Pure helpers that import nothing from this project so anyone may import them: `hhmm()`, `exception_summary()`, and the defensive parse helpers (`as_dict`, `parse_int`, `parse_float`) used while reading Bilibili's fragile nested payloads.
- **`initialization.py`** — everything that must be right *before* anything is served: `BASE_DIR` and friends; `AppConfig` (the typed runtime-config container, **no field defaults**) and `ConfigLoader` (its only legitimate producer — reads `config.toml`, fails fast with `ConfigError` on any missing/malformed key); and `VersionGuard`, the two startup self-checks. The version/codename strings live in `main.py` and are **passed into** `VersionGuard` as arguments, so this module never imports `main.py` back.
- **`server.py`** — the whole browser-facing HTTP/SSE surface: `EventHub` (the in-process fan-out every event flows through — holds the single `init` event, a bounded history `deque` replayed to each new subscriber, and per-subscriber drop-oldest queues; assigns the monotonic `id` on `publish`) plus `create_flask_app()` (serves the selected frontend folder at the web root, `/stream` SSE, `/health`). `_sse()` is the wire encoding, private to this module.
- **`credentials.py`** — `CredentialManager`, the whole Bilibili credential lifecycle behind one public `obtain_credential()`: QR-code login (delegated to `bilibili_api.login_v2`), JSON persistence with an `obtained_at` stamp, and the freshness policy keyed on credential age — **< 24h** load as-is; **24h–7d** `check_refresh()`/`refresh()` then re-stamp; **≥ 7d** (or missing/unreadable) re-login by QR. Thresholds are `credential_load_max_age_seconds` / `credential_refresh_max_age_seconds`.
- **`stats.py`** — `StatsTracker`, thread-safe per-uid accumulation of gift/SC yuan and guard months; renders the on-screen report and writes it on stream end and on Ctrl+C. Knows nothing about where events come from — the adapter feeds it via `add()`.
- **`bilibili.py`** — the only place that knows Bilibili's raw formats. `BilibiliEventAdapter._convert` maps `DANMU_MSG`→`danmaku`, `SEND_GIFT`→`gift`, `SUPER_CHAT_MESSAGE`→`superchat`, `GUARD_BUY`→`guard`; the first three share one unified user parser (`_build_sender` over the UserInfo object), `GUARD_BUY` has no UserInfo and is built by hand. Also the room-info fetch/parse for the `init` event (`fetch_room_info` / `room_info_from_api_response` / `empty_room_info`) and the backend's own `system_message()` (reconnect notices, stream-end report, forwarded errors emitted as schema events). Parsing is defensive throughout — the field-mapping subtleties (guard-level double inversion, money units, dwell, missing-UserInfo) are documented at their call sites; cross-reference `RAW_DATA.md`'s § notation.
- **`main.py`** — entry / orchestrator. Owns the version constants and `DanmakuHimeApp`, whose `run()` flow is: start Flask in a daemon thread → publish `init` (via `_build_init`) → obtain a `Credential` → `live.LiveDanmaku(...).on("ALL")` → `adapter.handle_event`, reconnecting forever. `main()` runs `VersionGuard.check_version(...)` (needs no config), loads config, runs `VersionGuard.check_frontend(...)`, then runs the app. Also `setup_logging()`: one process-wide `HH:MM:SS LEVEL message` format on the root logger, fanned to console (INFO+) and `log_file` (DEBUG+, appended); werkzeug muted to ERROR and `bilibili_api`'s `LiveDanmaku_*` logger rerouted via `_tame_lib_logger`. Use `log = logging.getLogger("danmakuhime")` — `info` for status, `warning` for recoverable oddities, `error(..., exc_info=True)` for failures (never `print` + `traceback.print_exc()`). Only interactive UI stays on raw `print()`: the QR block, the `\r` scan-poll spinner, and the stats report.

**Config:** `config.toml` (alongside `main.py`) is the backend runtime config — **no built-in defaults, env vars, or CLI flags**; `ConfigLoader.load()` fails fast rather than falling back. Basic keys are top-level, advanced ones under `[advanced]` (the loader checks both), and `[advanced.guard_dwell_seconds_by_schema_level]` is a string-keyed table parsed to int keys. **When adding a backend field, update `config.toml`, the `AppConfig` dataclass, and `_TOML_SCALAR_FIELDS` / `_TOML_PATH_FIELDS` together.** Path fields resolve against `BASE_DIR`; `frontend` is the directory selecting the served + integrity-checked frontend. The frontend masthead (preprint title/category/authors) is *not* backend config — it lives in `frontends/preprint/config.json`. To forward backend errors to the UI, set `debug_forward_errors = true`.

## Build & Verification

There is no test suite or linter. There **is** a per-package build step that is also a startup staleness/integrity guard — **after editing backend code you must rebuild, or the backend refuses to start:**

```bash
python3 build_backend.py                       # after editing any backend .py — regenerates backend.json
python3 frontends/build_frontend.py preprint   # after editing a frontend file or its .project (--all does every frontend)
python3 main.py                                 # then run
```

- **`build_backend.py` → `backend.json`** reads the version constants out of `main.py` by regex (so it stays dependency-free) and hashes every module in `INTEGRITY_FILES`. `VersionGuard.check_version()` runs first in `main()` and refuses to start unless the versions and every module's sha256 match.
- **`frontends/build_frontend.py <name>` → `frontends/<name>/frontend.json`** is the shared builder; its two hand-authored truth sources are the `index.html` comment block and `.project` (a gitignore-style allowlist, one pattern per line, matched with `pathspec`; each pattern → one sha256 over the files it matches). `VersionGuard.check_frontend()` runs after config load, checks `api_version` equality, then re-derives each payload group's hash.

Both are **guards, not tamper-proofing** (the json manifests are themselves unprotected): they catch editing a file, bumping a version, or pairing mismatched packages without rebuilding. Because the two packages ship separately, the hashing logic is **duplicated** between each build script and `initialization.py`'s `VersionGuard` and MUST stay byte-identical — `sha256` over raw bytes (`read_bytes()`), no text decode or newline normalization (`_file_sha256` mirrors `build_backend.py`; `_frontend_candidates` + `_frontend_group_hash` mirror `build_frontend.py`'s NON_PAYLOAD filter + `pathspec` matching + group digest). `INTEGRITY_FILES` in `initialization.py` must match `build_backend.py`'s exactly. Each build script also zips a shippable package (`*.zip`, gitignored).

Dependencies are in `requirements.txt` (lower-bound pins): `bilibili-api-python` (`import bilibili_api`, incl. `login_v2`), `Flask`, `Flask-Cors`, `pathspec`. Install with `python3 -m pip install -r requirements.txt`. After the first successful login a `credential.json` is written next to `main.py` and reused (see `credentials.py`).

## Documents

- **`README.md`** — the user-facing readme: run, configure, package.
- **`SCHEMA.md`** — the authoritative front/back SSE field contract (see Architecture). The source of truth for event shapes and `API_VERSION`.
- **`RAW_DATA.md`** — reference for Bilibili's raw event formats (UserInfo, envelope, per-`cmd` payloads). The `§` citations in `bilibili.py`'s parse comments point here.
- **`Record.txt`** — **real captured data**: one full live session's events (room `1921712061`, ~3970 lines), each line a Python `repr` of one event (`ast.literal_eval` to restore). The sample `RAW_DATA.md` describes and the realistic input to test parsing against. Gitignored (`*.txt`), not shipped.
