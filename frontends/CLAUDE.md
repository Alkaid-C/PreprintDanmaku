# frontends/CLAUDE.md

Guidance for building a **frontend** for DanmakuHime. A frontend is a self-contained folder under `frontends/`, shipped as its own package and combined with any backend at runtime via `config.toml`'s `frontend` key. Its only binding to the backend is the SSE contract, so you develop against **`SCHEMA.md`** (the authoritative event-shape contract) — not against backend code. Everything you need to build a frontend is in this file plus `SCHEMA.md`; the bundled example is documented in `frontends/preprint/CLAUDE.md`.

## The contract with the backend

These are hard requirements — break one and the backend won't serve your frontend.

**Entry & serving.** `index.html` is the mandatory entry file, served at `/`. The backend mounts the **entire folder** at the web root (`static_folder=<your dir>, static_url_path=""`), so every relative reference in `index.html` (`vendor/…`, `fonts/…`, `*.jsx`, `config.json`) resolves unchanged. `.jsx` responses are re-tagged `text/babel` by extension, so in-browser Babel transpilation works. Nothing is special-cased by filename except `index.html`.

**Data.** Connect `EventSource('/stream')`. **`SCHEMA.md` is authoritative** for event shapes: a single `init` event carrying neutral `room_info`, then a stream of `danmaku` / `gift` / `superchat` / `guard`. The `id` field is monotonically increasing — use it to sort, dedupe (e.g. by `type:id`), and reconcile the bounded history the backend replays to every new subscriber on connect. There is **no offline/mock mode**: the page only runs when served by the backend, so develop with the backend running.

**Identity & version match.** The comment block at the top of `index.html` is a hand-authored truth source carrying `name` / `version` / `codename` / `release_date` / `api_version`:

```html
<!-- DanmakuHime-Frontend
     name: Preprint
     version: 0.1.3
     codename:
     release_date: Jun 8, 2026
     api_version: 0.3
-->
```

`codename` is optional (display-only, never validated); the rest are required. **`api_version` must exactly equal the backend's `API_VERSION`** or the backend refuses to serve the frontend at startup. This is the whole point of the split: any frontend with a matching `api_version` drops in.

## Build & integrity

A frontend has two hand-authored truth sources — the `index.html` comment block above and **`.project`** — and one generated manifest, **`frontend.json`**.

`.project` is a gitignore-style allowlist (one pattern per line, `#` comments and blanks ignored) declaring which files ship. Each line maps to **one** sha256 group in `frontend.json`'s `payload`, so you choose the granularity: give a file its own line for a per-file hash (precise failure reporting), or use a glob like `fonts/**` to seal a whole tree under one hash. `.project` **must** cover `index.html`, and a pattern matching nothing is a build error.

`frontend.json` is **fully generated — never hand-edit it.** After changing any payload file, `.project`, or the `index.html` comment block, rebuild:

```bash
python3 frontends/build_frontend.py <name>   # or --all for every frontend
```

The backend re-derives every payload group's hash at startup and refuses to start on any mismatch — so a forgotten rebuild, a stale package, or a mismatched front/back pair is caught before anything is served. (The hashing internals, and why they must stay byte-identical to the backend, live in the top-level `CLAUDE.md`; as a frontend author you only need: edit → rebuild.)

`config.json` and the tooling files are deliberately **excluded** from the hashed payload — see below.

## Conventions & recommendations

These aren't enforced, but follow them unless you have a reason not to.

- **Keep your own configuration in `config.json`.** It's served and packaged but excluded from payload hashing, so end-users can edit it (titles, options, theming knobs) without tripping the startup integrity check. Don't bake user-facing settings into hashed files.
- **No CDN.** Vendor every JS/CSS dependency locally (e.g. under `vendor/`) and list it in `.project`. The page must render fully offline / on a flaky network — see the runtime below.
- **Self-host fonts** the same way (don't rely on Google Fonts or similar). List them in `.project` too.

## The target runtime

Design for where the frontend actually runs:

- It is loaded as an **OBS browser source**: Chromium 127 via the Chromium Embedded Framework, rendering off-screen to a composited texture layer. There is **no user interaction** (no clicks, hover, scroll, or focus), and a small OBS-specific JS API is injected at `window.obsstudio`. This is why local-only assets matter: there's no user to retry a failed CDN load.
- Content is **multilingual** — Bilibili audiences mix Chinese, Japanese, and English in one stream — so ensure your fonts cover zh / ja / en.
- A common canvas is a **landscape 1080p** stream where the danmaku region is only a *sub-area*, not the whole screen (roughly 400–800px wide × 800–1000px tall is typical). Other layouts exist too — full-screen transparent overlays, floating bubbles at random positions, etc. Design to a configurable box rather than assuming a fixed fullscreen size.
