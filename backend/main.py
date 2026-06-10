#!/usr/bin/env python3
"""
DanmakuHime

Live Bilibili danmaku backend with swappable frontends. This module is the entry
point and orchestrator: it owns the package's version identity, wires the
components together (EventHub, adapter, credentials, stats), and runs the
lifecycle — start the server, publish init, log in, connect to the live room and
reconnect forever.

    edit any backend .py  ->  python3 backend/build_backend.py  ->  python3 run.py
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any, Dict, Optional

from bilibili_api import Credential, live, sync

from bilibili import (
    BilibiliEventAdapter,
    empty_room_info,
    fetch_room_info,
    room_info_from_api_response,
    system_message,
)
import schema
from credentials import CredentialManager
from initialization import (
    AppConfig,
    ConfigError,
    ConfigLoader,
    VersionGuard,
    VersionMismatchError,
)
from server import EventHub, create_flask_app
from stats import StatsTracker
from util import exception_summary, hhmm

APP_VERSION = "0.5.1"
APP_CODENAME = "Out-of-the-loop Performance"
RELEASE_DATE = "Jun 9, 2026"
# Front/back contract version — the docs/SCHEMA.md event-shape version, independent of
# APP_VERSION and of any frontend's own version. The backend refuses to serve a
# frontend whose manifest api_version does not equal this exactly (see
# VersionGuard.check_frontend), so a backend package and a frontend package ship
# separately and combine iff their api_version strings match. Bump this whenever
# the event contract in docs/SCHEMA.md changes in a way the frontend must track.
API_VERSION = "0.4"
# Codenames are display-only: they ride along in the manifests and are printed at
# startup, but DO NOT participate in any version/integrity check (a codename never
# blocks startup or front/back pairing). Version strings are the only thing matched.
API_CODENAME = "回忆是抓不到的月光"

# Single application logger. All of our own output goes through this; format and
# handlers (console + file) are configured once in DanmakuHimeApp._setup_logging().
# Third-party loggers (werkzeug, bilibili_api's LiveDanmaku_*) are tamed there / at
# use so everything shares one HH:MM:SS LEVEL line format and the same log file.
log = logging.getLogger("danmakuhime")


class DanmakuHimeApp:
    def __init__(self, config: AppConfig, frontend_manifest: Dict[str, Any]):
        self.config = config
        self.frontend_dir = config.frontend
        self.frontend_manifest = frontend_manifest
        self.hub = EventHub(config.history_size, config.subscriber_queue_size)
        self.stats = StatsTracker()
        self.adapter = BilibiliEventAdapter(config, self.hub, self.stats)
        self.cred_manager = CredentialManager(config)
        self._server_thread: Optional[threading.Thread] = None

    def run(self) -> None:
        self._setup_logging()
        self.config.event_log_file.write_text("", encoding="utf-8")
        self._start_server()
        self._log_startup()
        self._publish_room_init()

        # Both startup steps fail soft: room_info above can be empty, and login
        # below may return an empty Credential() (anonymous) after its retries.
        # The only thing that stops us here is Ctrl+C, caught once for both the
        # login and the connect loop so stats are saved exactly once on exit.
        try:
            credential = self.cred_manager.obtain_credential()
            self._connect_loop(credential)
        except KeyboardInterrupt:
            log.info("程序被中断。")
            print(self.stats.save(self.config.stats_output_file))

    def _start_server(self) -> None:
        flask_app = create_flask_app(self.config, self.frontend_dir, self.hub)
        self._server_thread = threading.Thread(
            target=lambda: flask_app.run(
                host=self.config.host,
                port=self.config.port,
                debug=False,
                use_reloader=False,
                threaded=True,
            ),
            daemon=True,
        )
        self._server_thread.start()

    def _log_startup(self) -> None:
        log.info("后端版本：%s（%s）", APP_VERSION, APP_CODENAME)
        log.info("API 版本：%s（%s）", API_VERSION, API_CODENAME)
        m = self.frontend_manifest
        codename = m.get("codename") or ""
        log.info(
            "前端版本：%s %s%s [%s，API %s]",
            m.get("name"), m.get("version"),
            f"（{codename}）" if codename else "",
            self.frontend_dir.name, m.get("api_version"),
        )
        log.info("前端地址：http://%s:%s/", self.config.host, self.config.port)
        log.info("目标直播间：%s", self.config.room_id)

    def _publish_room_init(self) -> None:
        init_event = self._build_init()
        room_info = init_event["room_info"]
        log.info(
            "直播间信息：%s / %s（%s）",
            room_info.get("streamer_username", ""),
            room_info.get("title", ""),
            room_info.get("room_id", ""),
        )
        log.debug("直播间 room_info：%s", json.dumps(room_info, ensure_ascii=False))
        self.hub.set_init(init_event)

    def _build_init(self) -> Dict[str, Any]:
        raw_room_info = fetch_room_info(self.config)
        event = {
            "type": schema.EventType.INIT,
            "id": schema.INIT_EVENT_ID,
            "timestamp": hhmm(),
            "room_info": (
                room_info_from_api_response(raw_room_info, self.config.room_id)
                if raw_room_info is not None
                else empty_room_info(self.config.room_id)
            ),
        }
        return event

    def _connect_loop(self, credential: Credential) -> None:
        log.info("正在连接直播间 %s...", self.config.room_id)
        room = live.LiveDanmaku(self.config.room_id, credential=credential)
        self._tame_lib_logger(room.logger)
        room.on("ALL")(self.adapter.handle_event)
        while True:
            try:
                result = sync(room.connect())
                log.info(
                    "room.connect() 已返回：%r，%s 秒后重新连接。",
                    result,
                    self.config.reconnect_delay_seconds,
                )
                self._publish_connection_notice(
                    f"直播连接已结束，{self.config.reconnect_delay_seconds} 秒后重新连接"
                )
                time.sleep(self.config.reconnect_delay_seconds)
            except (KeyboardInterrupt, SystemExit):
                # Stats are saved once by run()'s handler, covering both this loop
                # and the login phase. Re-raise rather than swallow.
                raise
            except BaseException as exc:
                log.error(
                    "连接中断：%s，%s 秒后尝试重新连接。",
                    exception_summary(exc),
                    self.config.reconnect_delay_seconds,
                    exc_info=True,
                )
                self._publish_connection_notice(
                    f"连接中断，{self.config.reconnect_delay_seconds} 秒后尝试重新连接"
                )
                time.sleep(self.config.reconnect_delay_seconds)

    def _publish_connection_notice(self, text: str) -> None:
        self.hub.publish(system_message(text, self.config.reconnect_notice_dwell_seconds))

    def _setup_logging(self) -> None:
        """Configure the one log format for the whole process: HH:MM:SS LEVEL message.

        Our own logger, werkzeug and bilibili_api's loggers all funnel through the
        same console + file handlers on the root logger, so the terminal stays in a
        single format and everything is mirrored to `log_file` for later review.

        The console handler is INFO+; the file handler is DEBUG+, so `log.debug`
        diagnostics (credential freshness, masthead echo) stay out of the terminal
        but are still recorded to `log_file` for after-the-fact review. Only our own
        `log` is raised to DEBUG — third-party loggers keep inheriting INFO from root,
        so this doesn't unleash library debug chatter into the file.
        """
        logging.addLevelName(logging.WARNING, "WARN")
        logging.addLevelName(logging.CRITICAL, "CRIT")
        logging.addLevelName(logging.DEBUG, "DBG")
        formatter = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        console.setLevel(logging.INFO)
        file_handler = logging.FileHandler(self.config.log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)

        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.INFO)
        root.addHandler(console)
        root.addHandler(file_handler)

        # Our own diagnostics go down to DEBUG (file-only); everything else stays at
        # the root's INFO threshold.
        log.setLevel(logging.DEBUG)

        # werkzeug's per-request access log is noise here; let only its errors through
        # (they still flow to our handlers via propagation).
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @staticmethod
    def _tame_lib_logger(lib_logger: logging.Logger, level: int = logging.WARNING) -> None:
        """Route a third-party logger through our handlers instead of its own.

        bilibili_api attaches its own bracket-format StreamHandler to each
        LiveDanmaku_* logger. Drop those handlers and let records propagate to the
        root logger so they share our format and file; raise the level so only real
        problems (connect failures, retries) surface rather than its connect chatter.
        """
        lib_logger.handlers.clear()
        lib_logger.propagate = True
        lib_logger.setLevel(level)


def main() -> None:
    try:
        VersionGuard.check_version(APP_VERSION, RELEASE_DATE, API_VERSION)
    except VersionMismatchError as exc:
        raise SystemExit(str(exc))
    try:
        config = ConfigLoader.load()
    except ConfigError as exc:
        raise SystemExit(f"启动失败：{exc}")
    try:
        # Needs config to know which frontend folder to verify, so it runs after
        # the config load (unlike the backend self-check, which needs nothing).
        frontend_manifest = VersionGuard.check_frontend(config, API_VERSION)
    except VersionMismatchError as exc:
        raise SystemExit(str(exc))
    app = DanmakuHimeApp(config, frontend_manifest)
    app.run()


if __name__ == "__main__":
    main()
