#!/usr/bin/env python3
"""
DanmakuHime Preprint

Live Bilibili danmaku backend for the arXiv-style frontend.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import sys
import threading
import time
import tomllib
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from bilibili_api import Credential, live, sync
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
from flask import Flask, Response, send_from_directory
from flask_cors import CORS


APP_VERSION = "Out-of-the-loop 0.4.2"
RELEASE_DATE = "Jun 7, 2026"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.toml"
VERSION_FILE = BASE_DIR / "version.json"

# The three source files whose integrity is bound to version.json (see build.py).
# Keyed by the names build.py records; resolved against BASE_DIR at check time.
INTEGRITY_FILES = ("app.py", "danmaku-feed.jsx", "preprint.html")

# Single application logger. All of our own output goes through this; format and
# handlers (console + file) are configured once in setup_logging(). Third-party
# loggers (werkzeug, bilibili_api's LiveDanmaku_*) are tamed there / at use so
# everything shares one HH:MM:SS LEVEL line format and the same log file.
log = logging.getLogger("danmakuhime")


# ==================== Configuration ====================
#
# `config.toml` (alongside this file) is the single source of truth for every
# tunable. There are no built-in defaults, env vars, or CLI flags — load_config()
# reads config.toml and fails fast if it is missing, unreadable, or short a key.
# AppConfig below is just the typed container the loader populates.

SYSTEM_SENDER_ID = "0"
SYSTEM_SENDER_NAME = "Askr"


@dataclass
class AppConfig:
    # Livestream target
    room_id: int
    guard_name: str

    # Web server
    host: str
    port: int

    # Masthead sent by the backend init event
    stamp_label: str
    preprint_id: str
    category: str
    default_title: str
    authors: List[Dict[str, Any]]

    # Login (QR login is handled by bilibili_api.login_v2; we only poll it)
    login_poll_interval_seconds: int
    login_retry_delay_seconds: int

    # Credential persistence and freshness policy (see CredentialStore)
    credential_load_max_age_seconds: int      # < this: load as-is
    credential_refresh_max_age_seconds: int   # < this: refresh; else re-login

    # SSE buffering
    history_size: int
    subscriber_queue_size: int
    sse_heartbeat_seconds: int

    # Reconnect / report notices
    reconnect_delay_seconds: int
    reconnect_notice_dwell_seconds: int
    stream_end_report_dwell_seconds: int
    debug_forward_errors: bool
    debug_error_dwell_seconds: int

    # Event value conversion and pinned dwell
    gift_price_to_yuan_divisor: int
    cents_per_yuan: int
    # SuperChat below this amount is Remark; this amount and above is Observation.
    superchat_observation_threshold_yuan: int
    # Each tier is (below_yuan, dwell_seconds); the final tier's below is None
    # ("this amount and above"). Matched top-down against the SC amount.
    superchat_dwell_tiers: List[Tuple[Optional[int], int]]
    guard_dwell_seconds_by_schema_level: Dict[int, int]

    # Output files (resolved relative to this module)
    event_log_file: Path
    stats_output_file: Path
    qr_image_file: Path
    credential_file: Path
    log_file: Path


class EventHub:
    def __init__(self, history_size: int, subscriber_queue_size: int):
        self._history: Deque[Dict[str, Any]] = deque(maxlen=history_size)
        self._subscribers: List[queue.Queue] = []
        self._subscriber_queue_size = subscriber_queue_size
        self._lock = threading.Lock()
        self._next_id = 0
        self._init_event: Dict[str, Any] = {}

    def set_init(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._init_event = dict(event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            self._offer(subscriber, self._init_event)

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            if "id" not in event:
                self._next_id += 1
                event = {**event, "id": self._next_id}
            else:
                self._next_id = max(self._next_id, int(event["id"]))
            self._history.append(event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            self._offer(subscriber, event)

    def subscribe(self) -> Tuple[queue.Queue, Dict[str, Any], List[Dict[str, Any]]]:
        subscriber: queue.Queue = queue.Queue(maxsize=self._subscriber_queue_size)
        with self._lock:
            init_event = dict(self._init_event)
            history = list(self._history)
            self._subscribers.append(subscriber)
        return subscriber, init_event, history

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    @staticmethod
    def _offer(subscriber: queue.Queue, event: Dict[str, Any]) -> None:
        try:
            subscriber.put_nowait(event)
        except queue.Full:
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass


class BilibiliLoginManager:
    """QR-code login delegated to bilibili_api.login_v2.QrCodeLogin."""

    def __init__(self, config: AppConfig):
        self.config = config

    def login(self) -> Optional[Credential]:
        try:
            return sync(self._qr_login())
        except Exception as exc:
            log.error("扫码登录失败：%s", exc, exc_info=True)
            return None

    async def _qr_login(self) -> Optional[Credential]:
        log.info("=== 登录 B 站账号 ===")
        login = QrCodeLogin()
        await login.generate_qrcode()
        self._show_qrcode(login)

        last_message = ""
        while True:
            await asyncio.sleep(self.config.login_poll_interval_seconds)
            state = await login.check_state()
            if state == QrCodeLoginEvents.DONE:
                print()  # close the \r poll line before the next log line
                log.info("登录成功。")
                return login.get_credential()
            if state == QrCodeLoginEvents.TIMEOUT:
                print()  # close the \r poll line before the next log line
                log.warning("二维码已失效。")
                return None
            if state == QrCodeLoginEvents.CONF:
                last_message = self._print_poll_message("已扫码，请在手机上确认...", last_message)
            else:  # QrCodeLoginEvents.SCAN
                last_message = self._print_poll_message("等待扫码...", last_message)

    def _show_qrcode(self, login: QrCodeLogin) -> None:
        # The QR block and the \r poll spinner below are interactive UI, not log
        # lines, so they stay on raw print().
        print("\n请使用 B 站手机客户端扫描二维码：")
        print(login.get_qrcode_terminal())
        try:
            login.get_qrcode_picture().to_file(str(self.config.qr_image_file))
            log.info("二维码图片已保存：%s", self.config.qr_image_file)
        except Exception as exc:
            log.warning("保存二维码图片失败：%s", exc)

    @staticmethod
    def _print_poll_message(message: str, last_message: str) -> str:
        if message != last_message:
            print(f"\r{message}", end="", flush=True)
        return message


class CredentialStore:
    """Persists a Credential (incl. buvid3 + ac_time_value) to JSON with a freshness stamp.

    Policy, keyed on the age of `obtained_at`:
      < load_max_age      -> load and use as-is
      load .. refresh_max -> refresh() and re-stamp
      >= refresh_max      -> caller should re-login (see DanmakuHimePreprintApp)
    """

    def __init__(self, path: Path):
        self._path = path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> Tuple[Credential, Optional[datetime]]:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        credential = Credential(
            sessdata=data.get("sessdata"),
            bili_jct=data.get("bili_jct"),
            buvid3=data.get("buvid3"),
            dedeuserid=data.get("dedeuserid"),
            ac_time_value=data.get("ac_time_value"),
        )
        obtained_at: Optional[datetime] = None
        raw = data.get("obtained_at")
        if raw:
            try:
                obtained_at = datetime.fromisoformat(raw)
            except ValueError:
                pass
        return credential, obtained_at

    def save(self, credential: Credential) -> None:
        cookies = sync(credential.get_buvid_cookies())
        data = {
            "sessdata": cookies.get("SESSDATA", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "buvid3": cookies.get("buvid3", ""),
            "dedeuserid": cookies.get("DedeUserID", ""),
            "ac_time_value": credential.ac_time_value or "",
            "obtained_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


class StatsTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, Any]] = {}

    def add(self, uid: str, username: str, category: str, value: float) -> None:
        if not uid:
            log.warning("统计跳过：缺少 uid（username=%r, category=%s, value=%s）", username, category, value)
            return
        with self._lock:
            item = self._stats.setdefault(
                uid,
                {"username": username, "gift": 0.0, "superchat": 0.0, "captain": 0, "admiral": 0, "governor": 0},
            )
            item["username"] = username
            item[category] += value

    def report(self) -> str:
        with self._lock:
            if not self._stats:
                return "没有消费记录"
            lines = ["用户消费统计", "=" * 40]
            sections = [
                ("gift", "礼物", "元"),
                ("superchat", "SuperChat", "元"),
                ("captain", "舰长", "月"),
                ("admiral", "提督", "月"),
                ("governor", "总督", "月"),
            ]
            for key, title, unit in sections:
                rows = [(v["username"], v[key]) for v in self._stats.values() if v[key]]
                if not rows:
                    continue
                lines.append("")
                lines.append(title)
                for username, value in sorted(rows, key=lambda row: row[1], reverse=True):
                    if isinstance(value, float):
                        lines.append(f"{username} - {value:.1f}{unit}")
                    else:
                        lines.append(f"{username} - {value}{unit}")
            return "\n".join(lines)

    def save(self, path: Path) -> str:
        report = self.report()
        path.write_text(report, encoding="utf-8")
        return report


class BilibiliEventAdapter:
    def __init__(self, config: AppConfig, hub: EventHub, stats: StatsTracker):
        self.config = config
        self.hub = hub
        self.stats = stats
        self._event_log_lock = threading.Lock()

    async def handle_event(self, event: Dict[str, Any]) -> None:
        self._log_event(event)
        event_type = event.get("type", "")

        if event_type == "PREPARING":
            report = self.stats.save(self.config.stats_output_file)
            self.hub.publish(_system_message(report, self.config.stream_end_report_dwell_seconds))
            return

        try:
            converted = self._convert(event_type, event)
        except Exception as exc:
            log.error("处理 %s 失败：%s", event_type, exc, exc_info=True)
            if self.config.debug_forward_errors:
                self._publish_debug_error(event_type, exc)
            return

        if converted:
            self.hub.publish(converted)

    def _publish_debug_error(self, event_type: str, exc: Exception) -> None:
        self.hub.publish(
            _system_message(
                f"后端处理 {event_type or 'UNKNOWN'} 失败：{type(exc).__name__}: {exc}",
                self.config.debug_error_dwell_seconds,
            )
        )

    def _log_event(self, event: Dict[str, Any]) -> None:
        with self._event_log_lock:
            with self.config.event_log_file.open("a", encoding="utf-8") as file:
                file.write(repr(event))
                file.write("\n")

    def _convert(self, event_type: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if event_type == "DANMU_MSG":
            return self._danmaku(event)
        if event_type == "SEND_GIFT":
            return self._gift(event)
        if event_type == "SUPER_CHAT_MESSAGE":
            return self._superchat(event)
        if event_type == "GUARD_BUY":
            return self._guard(event)
        return None

    def _danmaku(self, event: Dict[str, Any]) -> Dict[str, Any]:
        info = event["data"]["info"]
        extra = self._danmaku_extra(info)
        reply_uname = extra.get("reply_uname") or ""
        text = str(info[1])
        if reply_uname:
            text = f"@{reply_uname}: {text}"

        medal = _danmaku_medal(info)
        sender = _sender(
            uid=str(info[2][0]),
            username=str(info[2][1]),
            medal=medal,
        )
        return {
            "type": "danmaku",
            "timestamp": _hhmm(),
            "sender": sender,
            "text": text,
        }

    @staticmethod
    def _danmaku_extra(info: List[Any]) -> Dict[str, Any]:
        try:
            raw = info[0][15].get("extra")
            return json.loads(raw) if raw else {}
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return {}

    def _gift(self, event: Dict[str, Any]) -> Dict[str, Any]:
        data = _as_dict(_as_dict(event.get("data")).get("data"))
        if not data:
            raise ValueError("SEND_GIFT missing data.data")
        uid = str(data.get("uid") or "")
        username = str(data.get("uname") or "匿名用户")
        count = max(1, _to_int(data.get("num"), 1))
        gift_name = str(data.get("giftName") or data.get("gift_name") or "礼物")
        raw_price = data.get("price")
        parsed_price = _parse_float(raw_price)
        if parsed_price is None:
            log.warning("SEND_GIFT 价格缺失/无法解析，按 0 计：price=%r, gift=%r, uid=%r", raw_price, gift_name, uid)
        price = parsed_price or 0.0
        total_yuan = price * count / self.config.gift_price_to_yuan_divisor
        sender_uinfo = _as_dict(data.get("sender_uinfo"))
        sender = _sender(uid=uid, username=username, medal=sender_uinfo.get("medal"))
        self.stats.add(uid, username, "gift", total_yuan)
        return {
            "type": "gift",
            "timestamp": _hhmm(),
            "sender": sender,
            "giftname": gift_name,
            "giftcount": count,
            "gifttotalvalue": int(round(total_yuan * self.config.cents_per_yuan)),
        }

    def _superchat(self, event: Dict[str, Any]) -> Dict[str, Any]:
        data = event["data"]["data"]
        user_info = data.get("user_info") or {}
        uid = str(data.get("uid") or user_info.get("uid") or "")
        username = str(user_info.get("uname") or data.get("uname") or "匿名用户")
        price_yuan = int(data.get("price") or 0)
        medal = data.get("medal_info") or data.get("medal") or {}
        sender = _sender(uid=uid, username=username, medal=medal)
        self.stats.add(uid, username, "superchat", price_yuan)
        return {
            "type": "superchat",
            "timestamp": _hhmm(),
            "sender": sender,
            "level": 2 if price_yuan >= self.config.superchat_observation_threshold_yuan else 1,
            "dwell_seconds": self._superchat_dwell_seconds(price_yuan),
            "value": price_yuan * self.config.cents_per_yuan,
            "text": str(data.get("message") or ""),
        }

    def _superchat_dwell_seconds(self, price_yuan: int) -> int:
        for upper_bound, dwell_seconds in self.config.superchat_dwell_tiers:
            if upper_bound is None or price_yuan < upper_bound:
                return dwell_seconds
        return 60  # 仅当配置末档不是 (None, ...) 时的保底 dwell

    def _guard(self, event: Dict[str, Any]) -> Dict[str, Any]:
        data = event["data"]["data"]
        uid = str(data.get("uid") or "")
        username = str(data.get("username") or data.get("uname") or "匿名用户")
        # GUARD_BUY 必定是真买了大航海，落到 0（其他）说明 payload 缺 guard_level，
        # 兜底按舰长处理（schema: 1=舰长, 2=提督, 3=总督）。
        schema_level = _guard_level_to_schema(data.get("guard_level")) or 1
        months = int(data.get("num") or 1)
        sender = {
            "uid": uid,
            "username": username,
            "badgename": self.config.guard_name,
            "badgelevel": int(data.get("medal_level") or 0),
            "guardstat": schema_level,
        }

        stat_key = {1: "captain", 2: "admiral", 3: "governor"}.get(schema_level)
        if stat_key:
            self.stats.add(uid, username, stat_key, months)

        return {
            "type": "guard",
            "timestamp": _hhmm(),
            "sender": sender,
            "level": schema_level,
            "newguard": bool(data.get("is_first") or data.get("is_first_guard")),
            "months": months,
            "dwell_seconds": self.config.guard_dwell_seconds_by_schema_level[schema_level],
        }


class DanmakuHimePreprintApp:
    def __init__(self, config: AppConfig):
        self.config = config
        self.hub = EventHub(config.history_size, config.subscriber_queue_size)
        self.stats = StatsTracker()
        self.adapter = BilibiliEventAdapter(config, self.hub, self.stats)
        self.login_manager = BilibiliLoginManager(config)
        self.cred_store = CredentialStore(config.credential_file)
        self._server_thread: Optional[threading.Thread] = None

    def create_flask_app(self) -> Flask:
        app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
        CORS(app)

        @app.route("/")
        def index():
            return send_from_directory(BASE_DIR, "preprint.html")

        @app.route("/danmaku-feed.jsx")
        def feed_jsx():
            return send_from_directory(BASE_DIR, "danmaku-feed.jsx", mimetype="text/babel")

        @app.route("/stream")
        def stream():
            subscriber, init_event, history = self.hub.subscribe()

            def generate():
                try:
                    if init_event:
                        yield _sse(init_event)
                    for event in history:
                        yield _sse(event)
                    while True:
                        try:
                            event = subscriber.get(timeout=self.config.sse_heartbeat_seconds)
                            yield _sse(event)
                        except queue.Empty:
                            yield ": heartbeat\n\n"
                except GeneratorExit:
                    pass
                finally:
                    self.hub.unsubscribe(subscriber)

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        @app.route("/health")
        def health():
            return {"status": "ok", "room_id": self.config.room_id, "version": APP_VERSION}

        return app

    def run(self) -> None:
        setup_logging(self.config)
        self.config.event_log_file.write_text("", encoding="utf-8")
        self._start_server()
        self._log_startup()

        credential = self._login_until_success()
        if not credential:
            return

        self._publish_configured_init()
        self._connect_loop(credential)

    def _login_until_success(self) -> Optional[Credential]:
        while True:
            try:
                credential = self._obtain_credential()
                if credential is not None and credential.has_sessdata():
                    return credential

                log.warning(
                    "登录未完成，%s 秒后重试。按 Ctrl+C 退出。",
                    self.config.login_retry_delay_seconds,
                )
                time.sleep(self.config.login_retry_delay_seconds)
            except KeyboardInterrupt:
                log.info("程序被中断。")
                print(self.stats.save(self.config.stats_output_file))
                return None
            except BaseException as exc:
                log.error(
                    "登录流程异常：%s，%s 秒后重试。",
                    _exception_summary(exc),
                    self.config.login_retry_delay_seconds,
                    exc_info=True,
                )
                time.sleep(self.config.login_retry_delay_seconds)

    def _obtain_credential(self) -> Optional[Credential]:
        """Apply the freshness policy: load < 24h, refresh < 7d, otherwise re-login."""
        if not self.cred_store.exists():
            log.debug("未找到凭据文件，开始扫码登录。")
            return self._login_and_store()

        try:
            credential, obtained_at = self.cred_store.load()
        except Exception as exc:
            log.warning("读取凭据失败：%s，改为扫码登录。", exc)
            return self._login_and_store()

        age = self._credential_age_seconds(obtained_at)
        if age is None or age >= self.config.credential_refresh_max_age_seconds:
            log.debug("凭据已超过 7 天或时间戳缺失，重新扫码登录。")
            return self._login_and_store()

        if age < self.config.credential_load_max_age_seconds:
            log.debug("凭据在 %.1f 小时内，直接载入。", age / 3600)
            return credential

        log.debug("凭据已过 %.1f 小时，尝试刷新。", age / 3600)
        try:
            if sync(credential.check_refresh()):
                sync(credential.refresh())
                log.debug("凭据刷新完成。")
            else:
                log.debug("凭据仍有效，无需刷新，仅更新时间戳。")
            self.cred_store.save(credential)
            return credential
        except Exception as exc:
            log.warning("刷新失败：%s，改为扫码登录。", exc)
            return self._login_and_store()

    def _login_and_store(self) -> Optional[Credential]:
        credential = self.login_manager.login()
        if credential is None:
            return None
        try:
            self.cred_store.save(credential)
            log.debug("凭据已保存：%s", self.config.credential_file)
        except Exception as exc:
            log.warning("保存凭据失败：%s", exc)
        return credential

    @staticmethod
    def _credential_age_seconds(obtained_at: Optional[datetime]) -> Optional[float]:
        if obtained_at is None:
            return None
        return (datetime.now() - obtained_at).total_seconds()

    def _start_server(self) -> None:
        flask_app = self.create_flask_app()
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
        log.info("Version: %s", APP_VERSION)
        log.info("前端地址：http://%s:%s/", self.config.host, self.config.port)
        log.info("目标直播间：%s", self.config.room_id)
        log.debug("目标粉丝牌：%s", self.config.guard_name)

    def _publish_configured_init(self) -> None:
        init_event = self._build_init()
        log.debug("页面标题：%s", init_event.get("room_title", ""))
        log.debug("页面作者：%s", ", ".join(author.get("name", "") for author in init_event.get("authors", [])))
        self.hub.set_init(init_event)

    def _connect_loop(self, credential: Credential) -> None:
        log.info("正在连接直播间 %s...", self.config.room_id)
        room = live.LiveDanmaku(self.config.room_id, credential=credential)
        _tame_lib_logger(room.logger)
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
            except KeyboardInterrupt:
                log.info("程序被中断。")
                print(self.stats.save(self.config.stats_output_file))
                return
            except BaseException as exc:
                log.error(
                    "连接中断：%s，%s 秒后尝试重新连接。",
                    _exception_summary(exc),
                    self.config.reconnect_delay_seconds,
                    exc_info=True,
                )
                self._publish_connection_notice(
                    f"连接中断，{self.config.reconnect_delay_seconds} 秒后尝试重新连接"
                )
                time.sleep(self.config.reconnect_delay_seconds)

    def _publish_connection_notice(self, text: str) -> None:
        self.hub.publish(_system_message(text, self.config.reconnect_notice_dwell_seconds))

    def _build_init(self) -> Dict[str, Any]:
        event = {
            "type": "init",
            "id": 0,
            "timestamp": _hhmm(),
        }
        if self.config.stamp_label:
            event["stamp_label"] = self.config.stamp_label
        if self.config.preprint_id:
            event["preprint_id"] = self.config.preprint_id
        if self.config.category:
            event["category"] = self.config.category
        if self.config.default_title:
            event["room_title"] = self.config.default_title
        if self.config.authors:
            event["authors"] = self.config.authors
            first_author = self.config.authors[0]
            event["anchor"] = _format_author_line(first_author)
        return event


def _hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _exception_summary(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        return f"SystemExit({exc.code})"
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _format_author_line(author: Dict[str, Any]) -> str:
    parts = [str(author.get("name") or ""), str(author.get("affiliation") or "")]
    return "，".join(part for part in parts if part)


def _system_sender() -> Dict[str, Any]:
    return {"uid": SYSTEM_SENDER_ID, "username": SYSTEM_SENDER_NAME, "badgename": "", "badgelevel": 0, "guardstat": 0}


def _system_message(text: str, dwell_seconds: int) -> Dict[str, Any]:
    """A pinned-zone superchat published by the backend itself (reports, notices, errors)."""
    return {
        "type": "superchat",
        "timestamp": _hhmm(),
        "sender": _system_sender(),
        "level": 1,
        "dwell_seconds": dwell_seconds,
        "value": 0,
        "text": text,
    }


def _sender(uid: str, username: str, medal: Any) -> Dict[str, Any]:
    medal = _as_dict(medal)
    badgename = str(medal.get("name") or medal.get("medal_name") or "")
    badgelevel = _to_int(medal.get("level") or medal.get("medal_level"), 0) if badgename else 0
    return {
        "uid": uid,
        "username": username or uid or "匿名用户",
        "badgename": badgename,
        "badgelevel": badgelevel,
        "guardstat": _guard_level_to_schema(medal.get("guard_level") or medal.get("guardLevel")),
    }


def _guard_level_to_schema(raw_level: Any) -> int:
    try:
        level = int(raw_level or 0)
    except (TypeError, ValueError):
        return 0
    return {3: 1, 2: 2, 1: 3}.get(level, 0)


def _danmaku_medal(info: List[Any]) -> Dict[str, Any]:
    try:
        medal = info[0][15]["user"]["medal"]
        if isinstance(medal, dict):
            return medal
    except (KeyError, IndexError, TypeError):
        pass

    try:
        medal_list = info[3]
        if isinstance(medal_list, list) and len(medal_list) >= 2:
            return {
                "level": medal_list[0] or 0,
                "name": medal_list[1] or "",
                "guard_level": medal_list[10] if len(medal_list) > 10 else 0,
            }
    except (KeyError, IndexError, TypeError):
        pass

    return {}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any) -> Optional[float]:
    """Parse to float, or None when missing/unparseable so callers can react."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ConfigError(Exception):
    """config.toml is missing, unreadable, or missing/has a malformed key."""


class VersionMismatchError(Exception):
    """version.json is missing/unreadable, or the version strings or one of the
    file hashes disagree with what build.py last recorded — i.e. a source file
    changed (or APP_VERSION/RELEASE_DATE bumped) without re-running build.py."""


# Shown to the operator for any file-hash failure (missing/unrecorded/unreadable
# hash, or a content mismatch). The specific cause is appended after it.
INTEGRITY_FAIL_MESSAGE = "文件完整性校验失败，请联系开发者。"


def _file_sha256(path: Path) -> str:
    """sha256 of a file's raw bytes, hex-encoded. Must stay byte-identical to
    build.py's hashing (raw bytes, no text decode / newline normalization)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_version() -> None:
    """Fail fast unless APP_VERSION, RELEASE_DATE, and the sha256 of every file
    in INTEGRITY_FILES all match what build.py recorded in version.json.

    This is a staleness / consistency guard, not a tamper-proofing mechanism:
    it catches editing a source file (or bumping a version constant) without
    re-running build.py. Run `python3 build.py` after any such change.
    """
    try:
        manifest = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise VersionMismatchError(
            f"找不到 {VERSION_FILE.name}，请先运行 `python3 build.py` 生成版本清单。"
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionMismatchError(f"无法读取 {VERSION_FILE.name}：{exc}")

    if manifest.get("app_version") != APP_VERSION:
        raise VersionMismatchError(
            f"app_version 不匹配：app.py 为 {APP_VERSION!r}，"
            f"version.json 为 {manifest.get('app_version')!r}。请运行 `python3 build.py`。"
        )
    if manifest.get("release_date") != RELEASE_DATE:
        raise VersionMismatchError(
            f"release_date 不匹配：app.py 为 {RELEASE_DATE!r}，"
            f"version.json 为 {manifest.get('release_date')!r}。请运行 `python3 build.py`。"
        )

    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict):
        raise VersionMismatchError(
            f"{INTEGRITY_FAIL_MESSAGE}（{VERSION_FILE.name} 缺少 hashes 字段）"
        )

    for name in INTEGRITY_FILES:
        expected = hashes.get(name)
        if expected is None:
            raise VersionMismatchError(
                f"{INTEGRITY_FAIL_MESSAGE}（{VERSION_FILE.name} 未记录 {name} 的哈希）"
            )
        path = BASE_DIR / name
        try:
            actual = _file_sha256(path)
        except FileNotFoundError:
            raise VersionMismatchError(
                f"{INTEGRITY_FAIL_MESSAGE}（找不到文件 {name}）"
            )
        except OSError as exc:
            raise VersionMismatchError(
                f"{INTEGRITY_FAIL_MESSAGE}（无法读取 {name} 进行哈希：{exc}）"
            )
        if actual != expected:
            raise VersionMismatchError(
                f"{INTEGRITY_FAIL_MESSAGE}（{name} 哈希不匹配，文件内容与版本清单不一致）"
            )


# Scalar config.toml keys that map 1:1 onto an AppConfig field of the same name.
# (`title` -> default_title and the structured keys below are handled separately.)
_TOML_SCALAR_FIELDS = (
    "room_id", "guard_name", "host", "port",
    "stamp_label", "preprint_id", "category",
    "login_poll_interval_seconds", "login_retry_delay_seconds",
    "credential_load_max_age_seconds", "credential_refresh_max_age_seconds",
    "history_size", "subscriber_queue_size", "sse_heartbeat_seconds",
    "reconnect_delay_seconds", "reconnect_notice_dwell_seconds",
    "stream_end_report_dwell_seconds", "debug_forward_errors", "debug_error_dwell_seconds",
    "gift_price_to_yuan_divisor", "cents_per_yuan", "superchat_observation_threshold_yuan",
)
# File-name keys: stored as bare names in the TOML, resolved relative to BASE_DIR.
_TOML_PATH_FIELDS = ("event_log_file", "stats_output_file", "qr_image_file", "credential_file", "log_file")


def load_config(path: Path = CONFIG_FILE) -> AppConfig:
    """Build the runtime config from config.toml — the single source of truth.

    Raises ConfigError if the file is missing, unparseable, or short any key.
    """
    if not path.exists():
        raise ConfigError(f"找不到配置文件 {path.name}（应与 app.py 放在同一目录）。")
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"配置文件 {path.name} 读取失败：{exc}") from exc

    # Basic keys sit at top level; advanced ones under [advanced]. Look in both so
    # an editor can move a key between sections without breaking it.
    advanced = data.get("advanced", {})

    def require(key: str) -> Any:
        if key in data:
            return data[key]
        if key in advanced:
            return advanced[key]
        raise ConfigError(f"配置文件 {path.name} 缺少必填项：{key}")

    try:
        guard_raw = require("guard_dwell_seconds_by_schema_level")
        guard = {int(k): int(v) for k, v in guard_raw.items()}
        # "below" omitted on the final tier means "this amount and above".
        tiers = [(tier.get("below"), tier["dwell_seconds"]) for tier in require("superchat_dwell_tiers")]
        authors = [dict(author) for author in require("authors")]
        kwargs: Dict[str, Any] = {key: require(key) for key in _TOML_SCALAR_FIELDS}
        kwargs.update({key: BASE_DIR / require(key) for key in _TOML_PATH_FIELDS})
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"配置文件 {path.name} 的某个值格式不对：{exc}") from exc

    return AppConfig(
        default_title=require("title"),
        authors=authors,
        superchat_dwell_tiers=tiers,
        guard_dwell_seconds_by_schema_level=guard,
        **kwargs,
    )


def setup_logging(config: AppConfig) -> None:
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
    file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
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
        check_version()
    except VersionMismatchError as exc:
        raise SystemExit(str(exc))
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"启动失败：{exc}")
    app = DanmakuHimePreprintApp(config)
    app.run()


if __name__ == "__main__":
    main()
