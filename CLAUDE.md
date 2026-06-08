# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Bilibili live-stream danmaku wall rendered as an arXiv-style academic preprint. The backend connects to a live room, converts Bilibili events into a typed SSE event stream, and the browser frontend renders each event as a typographic element of a fake paper (citations, theorems, acknowledgments).

## Run

```bash
python3 build.py               # regenerate version.json (run after editing any of the 3 source files)
python3 app.py                 # starts server, prints a login QR in the terminal
```

Scan the QR with the Bilibili mobile app to log in, then open `http://127.0.0.1:19216/`. The page **only** works when served by the backend — it subscribes to `/stream` and has no offline/mock mode (opening the `.html` via `file://` fails by design).

All settings live in **`config.toml`** (alongside `app.py`) — the single source of truth. Edit it and restart. There are **no CLI flags and no env vars**; basics (room id, guard name, masthead, output filenames) are at the top level, advanced tuning under `[advanced]`, and `[[authors]]` / `[advanced.guard_dwell_seconds_by_schema_level]` are the structured sections. To forward backend errors to the UI, set `debug_forward_errors = true` under `[advanced]`.

There is no test suite or linter. There **is** one build step — a version/integrity guard (see below). Dependencies are not pinned in a requirements file; the imports needed are `bilibili_api` (incl. `login_v2`), `flask`, `flask_cors`. After the first successful login a `credential.json` is written next to `app.py` and reused on subsequent runs (see the persistence policy below).

### Version / integrity guard (`build.py` + `version.json`)

`app.py` carries two top-level constants, `APP_VERSION` and `RELEASE_DATE`, and **refuses to start** unless they — and the sha256 of all three source files (`app.py`, `danmaku-feed.jsx`, `preprint.html`) — match the manifest in `version.json`. `build.py` regenerates that manifest: it reads the two constants out of `app.py`'s source (by regex, so it stays dependency-free and doesn't import the app) and hashes the three files. `check_version()` in `app.py` runs first thing in `main()`, before `load_config()`, and raises `VersionMismatchError` → a clean `SystemExit` on any mismatch.

This is a **staleness/consistency guard, not tamper-proofing** (`version.json` itself is unprotected): it catches editing a source file or bumping a version constant without rebuilding. **Workflow: edit `app.py`/`danmaku-feed.jsx`/`preprint.html` → `python3 build.py` → `python3 app.py`.** Hashing MUST stay byte-identical on both sides — `sha256` over raw file bytes (`read_bytes()`), no text decode or newline normalization; `INTEGRITY_FILES` is defined in both files and must agree. `version.json` is a generated artifact (regenerate, don't hand-edit). `APP_VERSION` is also surfaced via `/health` and the startup log.

## Architecture

Three files, one contract. **`SCHEMA.md` is the authoritative front/back field contract — read it before changing any event shape, and keep it in sync with both sides.**

### Backend (`app.py`)

Single file, class-per-responsibility. The flow in `DanmakuHimePreprintApp.run()`:
1. Start Flask in a daemon thread (serves the static files + `/stream` SSE + `/health`).
2. Obtain a `bilibili_api.Credential` via `_obtain_credential` (see persistence policy below).
3. Publish the `init` masthead event, then `live.LiveDanmaku(...).on("ALL")` → `BilibiliEventAdapter.handle_event`, reconnecting forever on disconnect.

Key components:
- **`BilibiliLoginManager`** — QR-code login, delegated entirely to `bilibili_api.login_v2.QrCodeLogin` (we only poll `check_state()` and print the terminal/PNG QR). It returns a full `Credential` including `buvid3` and `ac_time_value` (the refresh token), so credentials can later be refreshed without re-scanning.
- **`CredentialStore`** — persists the credential to `credential.json` (incl. `buvid3`, `ac_time_value`, and an `obtained_at` ISO stamp). The freshness policy in `_obtain_credential`, keyed on the age of `obtained_at`: **< 24h** load as-is; **24h–7d** `check_refresh()`/`refresh()` then re-stamp; **≥ 7d** (or missing/unreadable) re-login by QR. Thresholds are `credential_load_max_age_seconds` / `credential_refresh_max_age_seconds` in `AppConfig`.
- **`EventHub`** — the SSE fan-out. Holds the single `init` event, a bounded history `deque` (replayed to each new subscriber so late joiners see recent state), and per-subscriber bounded queues that drop-oldest when full. Assigns the monotonic `id` on `publish` if absent.
- **`BilibiliEventAdapter`** — the only place that knows Bilibili's raw event format. `_convert` maps `DANMU_MSG`→`danmaku`, `SEND_GIFT`→`gift`, `SUPER_CHAT_MESSAGE`→`superchat`, `GUARD_BUY`→`guard`. DANMU_MSG / SEND_GIFT / SUPER_CHAT_MESSAGE share one unified user parser (`_sender_from_uinfo` over the UserInfo object); Bilibili's nested/positional payloads are fragile, so parsing uses defensive helpers (`_as_dict`, `_to_int`, `_danmaku_uinfo`'s positional fallback, `_apply_flat_fallback`). The special `PREPARING` event (stream ended) triggers a stats save + an on-screen report.
- **`StatsTracker`** — thread-safe per-uid accumulation of gift/SC yuan and guard months; written to `STATS_OUTPUT_FILENAME` on stream end and on Ctrl+C.

**`config.toml` is the single source of truth for every tunable** — there are no built-in defaults, env vars, or CLI flags. The `AppConfig` dataclass (under the `# Configuration` banner) is just the typed container, with **no field defaults**; `load_config()` reads `config.toml` and constructs it, raising `ConfigError` (→ a clean `SystemExit` from `main`) if the file is missing, unparseable, or short any key — i.e. it fails fast rather than falling back. **When adding a field, update `config.toml`, the `AppConfig` dataclass, and `_TOML_SCALAR_FIELDS`/`_TOML_PATH_FIELDS` together.** TOML notes: basic keys are top-level, advanced ones live under `[advanced]` (the loader looks in both via `require()`); `title` maps to `default_title`; the four file-name keys are bare names resolved against `BASE_DIR`; `authors` is `[[authors]]`; `guard_dwell_seconds_by_schema_level` is a string-keyed table parsed to int keys.

### Logging

`setup_logging()` configures one process-wide format — `HH:MM:SS LEVEL message` (`WARNING` is shortened to `WARN`) — on the root logger, fanned out to **both** the console and `log_file` (config key; appended, not truncated). Use the module logger `log = logging.getLogger("danmakuhime")`: `info` for status (startup, credential freshness, connect/reconnect), `warning` for recoverable oddities (missing price → 0, missing uid, save failures), `error(..., exc_info=True)` for failures — **never pair a `print` with `traceback.print_exc()`**; `exc_info=True` folds the trace into the one record. Third-party loggers are funneled through the same handlers: werkzeug's access log is muted to ERROR, and `bilibili_api`'s per-room `LiveDanmaku_*` logger is rerouted by `_tame_lib_logger()` (drops its own bracket handler, propagates, raised to WARNING so only connect failures/retries show, not its connect chatter). Only three things stay on raw `print()` because they're interactive UI / reports, not log lines: the QR-code block + scan prompt, the `\r` scan-poll spinner, and the multi-line stats report on stream-end / Ctrl+C. (Separately, `event_log_file` is a raw per-event data sink, truncated each run — unrelated to this logging.)

### Frontend (`preprint.html` + `danmaku-feed.jsx`)

No build tooling — React 18, ReactDOM, and Babel-standalone are served **locally** from `vendor/` (no CDN; the whole page works offline as long as the backend is up) and JSX is transpiled **in the browser** (`<script type="text/babel">`). `app.py` serves the `.jsx` with `mimetype="text/babel"` for this reason. Fonts are self-hosted too: `fonts/google/fonts.css` + the `fonts/google/files/` woff2 chunks provide **Tinos** (latin, the open metric-compatible Times New Roman substitute) and **Noto Serif SC** (CJK); these are served as static files by Flask (`static_folder=BASE_DIR, static_url_path=""`, so any file in the project dir is reachable at its path). The font stacks are pinned to the two hosted fonts with no client-system fallbacks: `--cm: "Tinos"`, `--cjk: "Noto Serif SC"`, `--serif: var(--cm), var(--cjk)` (latin from Tinos, CJK glyphs from Noto). `preprint.html` holds all CSS (LaTeX-style serif + Noto Serif SC) and mounts `<App>`; `danmaku-feed.jsx` holds the logic.

`useDanmakuStream` connects `EventSource('/stream')`, dedupes by `type:id`, and `adapt()`s backend fields into internal shapes. Rendering model (documented atop the jsx — `SCHEMA.md` is the backend field contract only and deliberately says nothing about rendering):
- **One shared FIFO queue** (`CAP`) holds danmaku + gifts. One in / one oldest out. Danmaku render as scrolling **References** (clip at top); gifts as the **Acknowledgments** band (retire = animated height collapse).
- **SuperChat + guard** bypass the FIFO into a **top pinned zone** (`PIN_MAX`, max 3) with a real time-based dwell (`dwell_seconds`, authoritative from backend). SuperChat→Remark/Observation, guard→Lemma/Theorem/Axiom.

### Mapping gotchas (where bugs hide)

- **Guard level is inverted twice.** Bilibili's `guard_level` is `3=舰长, 2=提督, 1=总督`; `_guard_level_to_schema` flips it to the schema's `1/2/3 = 舰长/提督/总督`. Don't "simplify" this.
- **Money units:** gifts count `total_coin` in milli-yuan (`÷1000`) — already the opened face value for blind boxes and `price × num` for normal gold gifts, and **0** for free (`coin_type == 'silver'`) gifts; `gifttotalvalue` is sent in **cents**. SuperChat raw `price` is in **yuan**; the emitted `value` is in cents. The frontend divides `value` by 100 for display.
- **SuperChat dwell is Bilibili's, not ours.** `dwell_seconds` = the raw `time` field (B 站 official per-amount dwell) × `superchat_dwell_multiplier` (config). Don't reintroduce home-grown amount tiers.
- **`GUARD_BUY` has no UserInfo** → no avatar or fan medal in the event; `sender.avatar_url`/`badgename` go empty (see the TODO to backfill via the user-profile API by uid). Every other sender comes from one unified parser, `_sender_from_uinfo` (`guardstat` from `medal.guard_level`, never `user.guard.level`).
- **`badgename: ""` means no fan medal** → frontend renders a `VtuRXiv:26xx.xxxx` preprint id instead of a journal/volume citation.
- **`id` ordering is load-bearing** — it drives dedupe, sort, and history replay. Always let `EventHub` assign it (or preserve it) rather than minting ids elsewhere.
