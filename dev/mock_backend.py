#!/usr/bin/env python3
"""
DanmakuHime mock backend for frontend development.

This server does not read config.toml, connect to Bilibili, log in, or run the
package integrity guards. It serves one frontend directory and replays
mock_record.txt through the existing Bilibili event adapter so the browser still
receives the normal docs/SCHEMA.md SSE events.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

# mock_backend lives in dev/, a sibling of backend/. Put backend/ on the import
# path so the backend modules below import flat, exactly as they do in-package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import schema
from bilibili import BilibiliEventAdapter
from server import EventHub, create_flask_app
from stats import StatsTracker
from util import hhmm, parse_int

# This dev tool's own files (the replay record + its throwaway sinks) live next to
# it in dev/. Frontend paths are resolved from the repo root so CLI arguments match
# the normal tree layout, e.g. `frontends/preprint`.
DEV_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEV_DIR.parent

HOST = "0.0.0.0"
PORT = 19216
DEFAULT_FRONTEND_DIR = "frontends/preprint"
RECORD_FILE = DEV_DIR / "mock_record.txt"
PLAYBACK_INTERVAL_SECONDS = 2.0
PLAYBACK_LOOP_PAUSE_SECONDS = 30.0
ROOM_ID_FALLBACK = 1921712061

log = logging.getLogger("danmakuhime")


class MockEventHub(EventHub):
    def __init__(self, history_size: int, subscriber_queue_size: int, first_subscriber: threading.Event):
        super().__init__(history_size, subscriber_queue_size)
        self._first_subscriber = first_subscriber

    def subscribe(self):
        subscription = super().subscribe()
        self._first_subscriber.set()
        return subscription


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a frontend and replay mock_record.txt over SSE.")
    parser.add_argument(
        "frontend_dir",
        nargs="?",
        default=DEFAULT_FRONTEND_DIR,
        help="Frontend directory to serve, relative to the repo root unless absolute.",
    )
    return parser.parse_args()


def resolve_frontend_dir(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not (path / "index.html").is_file():
        raise SystemExit(f"前端目录无效：{path}（缺少 index.html）")
    return path


def load_record(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"读取回放文件失败：{path}：{exc}") from exc

    for lineno, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            continue
        try:
            event = ast.literal_eval(text)
        except (SyntaxError, ValueError) as exc:
            raise SystemExit(f"{path.name}:{lineno} 不是合法的 Python repr：{exc}") from exc
        if not isinstance(event, dict):
            raise SystemExit(f"{path.name}:{lineno} 必须是 dict 事件。")
        events.append(event)

    if not events:
        raise SystemExit(f"回放文件为空：{path}")
    return events


def room_id_from_record(events: List[Dict[str, Any]]) -> int:
    for event in events:
        room_id = parse_int(event.get("room_real_id")) or parse_int(event.get("room_display_id"))
        if room_id:
            return room_id
    return ROOM_ID_FALLBACK


def build_mock_config(room_id: int) -> SimpleNamespace:
    # Only the AppConfig attributes the adapter / server actually read; the mock
    # builds events through the real BilibiliEventAdapter, so they come out in the
    # current docs/SCHEMA.md shape automatically.
    return SimpleNamespace(
        room_id=room_id,
        host=HOST,
        port=PORT,
        sse_heartbeat_seconds=20,
        event_log_file=DEV_DIR / "mock_event_log.txt",
        stats_output_file=DEV_DIR / "mock_stats.txt",
        stream_end_report_dwell_seconds=20,
        debug_forward_errors=False,
        debug_error_dwell_seconds=30,
    )


def build_init_event(room_id: int) -> Dict[str, Any]:
    return {
        "type": schema.EventType.INIT,
        "id": schema.INIT_EVENT_ID,
        "timestamp": hhmm(),
        "room_info": {
            "room_id": room_id,
            "title": "Mock replay from mock_record.txt",
            "streamer_username": "DanmakuHime Mock",
            "streamer_uid": 0,
            "streamer_avatar_url": "",
            "parent_area_name": "Mock",
            "area_name": "Frontend Debug",
            "cover_image_url": "",
        },
    }


def playback_loop(
    adapter: BilibiliEventAdapter,
    events: List[Dict[str, Any]],
    first_subscriber: threading.Event,
) -> None:
    log.info("等待前端连接 /stream 后开始回放。")
    first_subscriber.wait()
    log.info("前端已连接，开始回放。")
    while True:
        for event in events:
            asyncio.run(adapter.handle_event(event))
            time.sleep(PLAYBACK_INTERVAL_SECONDS)
        log.info("本轮回放结束，暂停 %.1f 秒后开始下一轮。", PLAYBACK_LOOP_PAUSE_SECONDS)
        time.sleep(PLAYBACK_LOOP_PAUSE_SECONDS)


def setup_logging() -> None:
    logging.addLevelName(logging.WARNING, "WARN")
    formatter = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(console)

    log.setLevel(logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


def main() -> None:
    args = parse_args()
    setup_logging()

    frontend_dir = resolve_frontend_dir(args.frontend_dir)
    events = load_record(RECORD_FILE)
    room_id = room_id_from_record(events)
    config = build_mock_config(room_id)
    config.event_log_file.write_text("", encoding="utf-8")

    first_subscriber = threading.Event()
    hub = MockEventHub(history_size=0, subscriber_queue_size=300, first_subscriber=first_subscriber)
    hub.set_init(build_init_event(room_id))
    adapter = BilibiliEventAdapter(config, hub, StatsTracker())

    replay_thread = threading.Thread(target=playback_loop, args=(adapter, events, first_subscriber), daemon=True)
    replay_thread.start()

    log.info("模拟后端已启动：不读取 config.toml，不连接 Bilibili。")
    log.info("前端目录：%s", frontend_dir)
    log.info(
        "回放文件：%s（%s 条，默认 %.1f 秒/条，每轮暂停 %.1f 秒）",
        RECORD_FILE,
        len(events),
        PLAYBACK_INTERVAL_SECONDS,
        PLAYBACK_LOOP_PAUSE_SECONDS,
    )
    log.info("前端地址：http://%s:%s/", HOST, PORT)

    flask_app = create_flask_app(config, frontend_dir, hub)
    flask_app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
