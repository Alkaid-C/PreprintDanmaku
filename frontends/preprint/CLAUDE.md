# frontends/preprint/CLAUDE.md

The bundled example frontend: the live stream rendered as the body of an arXiv-style **preprint** ("VtuRXiv"). This file is what you need to edit *this* frontend safely — the shared frontend rules (serving, `.project`/build, the SSE contract, the OBS runtime) live in `frontends/CLAUDE.md`, and the event field definitions in `docs/SCHEMA.md`. Here we cover only how preprint *interprets and renders* the stream.

## What it is

A preprint-styled danmaku wall. Each live event becomes a typographic element of a fake paper:

| event | becomes |
|---|---|
| `danmaku` | a numbered **References** citation |
| `gift` | an **Acknowledgments** funding line |
| `superchat` | a pinned **Remark** (< ¥30) / **Observation** (≥ ¥30) box |
| `guard` 舰长/提督/总督 | a pinned **Lemma / Theorem / Axiom** box |

Guard rank also rides along as a `†/‡/§` superscript (`RANK_MARK`) wherever the sender is shown.

## Files

- **`index.html`** — all CSS, the masthead markup, and the React mount. It loads **vendored** React 18 + ReactDOM + Babel from `vendor/` (no CDN) and the Tinos (latin) + Noto Serif SC (CJK) fonts from `fonts/`, then mounts `<App>` → `<DanmakuFeed theme="classic" stream={useDanmakuStream()} width="100%" height="100%" />`. The fixed-size `.widget` (1200px) is the delivery frame. JSX is transpiled in-browser (`<script type="text/babel">`).
- **`danmaku-feed.jsx`** — all the logic, exported to `window`: `useDanmakuStream` (SSE wiring + state), `adapt` (schema → internal shapes), the row renderers (`DmCite` / `DmFundLine` / `DmBox`), the `Collapse` height animation, and `DanmakuFeed` (layout). Its top `MODEL` comment is the canonical short statement of the rendering model below.
- **`config.json`** — the masthead data (title, authors, etc.); excluded from payload hashing so it's user-editable. See "Masthead" below.

## Rendering model (read before touching the queue logic)

Two mechanisms, deliberately separate:

- **One combined FIFO body queue**, `body`, cap `CAP = 16`, holding danmaku **and** gifts in one shared sequence — one in, one oldest out. It feeds two zones, split by type at render time:
  - **References** — danmaku as a scrolling column pinned to the bottom, clipping at the top.
  - **Acknowledgments** — gifts as a visible list; a retiring gift animates a height collapse (`Collapse`, ~380ms) rather than vanishing, hence the separate `leaving` list.
- **Pinned top zone** — superchat + guard **bypass** the FIFO (`emit` routes them to `pushPinned`) into a top zone with a **real time-based dwell**: each box lives `dwell` ms then collapses out. `PIN_MAX = 3`; when full, the oldest is evicted early. **superchat** `dwell` is the backend's `dwell_seconds` (× 1000, min 1000); **guard** has no backend dwell in API 0.4 — its dwell comes from `config.json`'s `guard_dwell_seconds` (per-rank seconds, keyed 舰长/提督/总督). The `.dm-pin-timer` bar animates this duration.

## Editing notes (the non-obvious bits)

- **Dedupe & init.** `adapt` drops duplicates by `type:id` and treats `init` specially (updates the masthead via `applyInit`, renders nothing). Every other event must keep a stable `id` — it's the React key and the dedupe key.
- **Citation vs preprint id.** A danmaku **with** a fan badge renders as a journal citation (`badgename` as journal, `badgelevel` as `Vol.`); **without** one it gets a synthetic id `VtuRXiv:2606.xxxx` derived from the event `id` (`preprintId`). Both paths must stay — they're the two `DmCite` branches. (A fan badge is `sender.badge_name` as journal, `sender.badge_level` as `Vol.`)
- **Unit conversions.** All amounts arrive as `value_cents` (**cents**) → divide by 100 for the `¥` amount. Superchat has no backend `level` in API 0.4 — the Remark/Observation tier is derived from the amount (`SUPER_TIER2_YUAN`, ¥30). `months` on a guard becomes the "开通了 N 个月的…" line. Superchat `dwell_seconds` → ms; guard dwell comes from config (see above). These match `docs/SCHEMA.md`; if a field's meaning seems off, check there first.
- **Env counters.** `Remark/Observation/Lemma/Theorem/Axiom` are numbered by a running counter (`state.env`), and References lines by `state.ln`. They're monotonic per session, not per zone.

## Masthead & config.json

The masthead (stamp id + category, title, authors, date) is filled from `config.json`, with **`init.room_info` as fallback** — config wins, room_info fills any gap:

| config.json key | falls back to (room_info) |
|---|---|
| `title` | `title` |
| `preprint_id` | `room_id` |
| `category` | `parent_area_name` + `.` + `area_name` |
| `authors[]` (`name`/`affiliation`/`corresponding`) | `streamer_username` (shown as a lone author line) |
| `stamp_label` | — (prefix on the stamp id, e.g. `Bilibili:`) |

So a freshly-pointed frontend shows sensible defaults from the live room even with an empty `config.json`, and an author can override any of it without rebuilding (config.json isn't hashed).

## Knobs

- **Theme:** `DM_THEMES` is a map of CSS-variable palettes (currently only `classic`); `DanmakuFeed` reads the `theme` prop and spreads the palette as `--bg`/`--ink`/… onto `.dm-root`. To add a theme, add an entry and pass its key. `width`/`height` props size the root.
- **Capacities:** `CAP` (body FIFO) and `PIN_MAX` (pinned slots) are the two tuning constants at the top of the jsx.
