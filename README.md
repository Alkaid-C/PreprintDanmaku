# DanmakuHime

Bilibili live-stream danmaku backend with swappable browser frontends.

The backend connects to one Bilibili live room, converts live events into the SSE contract in `SCHEMA.md`, and serves the selected frontend from `frontends/<name>/`. The included `preprint` frontend renders the stream as an arXiv-style preprint.

## Run

```bash
python3 build_backend.py
python3 frontends/build_frontend.py preprint
python3 app.py
```

After startup, scan the Bilibili login QR code in the terminal and open:

```text
http://127.0.0.1:19216/
```

## Configuration

Backend runtime settings live in `config.toml`: room id, host/port, selected frontend, credential/log/output files, reconnect timing, SSE buffering, and event value conversion.

Frontend-specific interpretation lives inside each frontend folder. For the bundled preprint frontend, edit `frontends/preprint/config.json` for the masthead title, stamp, category, and authors. This file is packaged with the frontend zip but is intentionally excluded from frontend payload hashing, so local user edits do not break startup integrity checks.

## Packaging

- Edit backend code: run `python3 build_backend.py`.
- Edit frontend payload or metadata: run `python3 frontends/build_frontend.py preprint`.
- `APP_VERSION` is the backend version.
- `API_VERSION` is the front/back event contract version; backend and frontend API versions must match exactly.

## Event Mapping

- `DANMU_MSG` -> `danmaku`
- `SEND_GIFT` -> `gift`
- `SUPER_CHAT_MESSAGE` -> `superchat`
- `GUARD_BUY` -> `guard`

The backend `init` event carries only neutral live-room facts in `room_info`. Frontends decide how to interpret or display those facts.
