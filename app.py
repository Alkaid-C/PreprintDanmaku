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
    # SuperChat dwell = Bilibili's authoritative `time` (seconds) × this multiplier.
    superchat_dwell_multiplier: float
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


class _Anomalies:
    """Collects the parse fallbacks hit while converting a single event, so
    handle_event can emit one honest warning (with the raw event attached) when a
    required field was missing/unparseable and we substituted a placeholder.
    Legitimate "absence has meaning" states (no fan medal, no guard, not a reply)
    are NOT recorded here — only genuine substitutions are."""

    __slots__ = ("notes",)

    def __init__(self) -> None:
        self.notes: List[str] = []

    def note(self, message: str) -> None:
        self.notes.append(message)

    def __bool__(self) -> bool:
        return bool(self.notes)


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

        anomalies = _Anomalies()
        try:
            converted = self._convert(event_type, event, anomalies)
        except Exception as exc:
            log.error("处理 %s 失败：%s", event_type, exc, exc_info=True)
            if self.config.debug_forward_errors:
                self._publish_debug_error(event_type, exc)
            return

        # Honest accounting: when a field was missing/unparseable and we substituted
        # a placeholder, say so once with the full raw event attached.
        if anomalies:
            log.warning("解析 %s 命中兜底：%s；原始数据：%r",
                        event_type or "UNKNOWN", "；".join(anomalies.notes), event)

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

    def _convert(self, event_type: str, event: Dict[str, Any], anomalies: "_Anomalies") -> Optional[Dict[str, Any]]:
        if event_type == "DANMU_MSG":
            return self._danmaku(event, anomalies)
        if event_type == "SEND_GIFT":
            return self._gift(event, anomalies)
        if event_type == "SUPER_CHAT_MESSAGE":
            return self._superchat(event, anomalies)
        if event_type == "GUARD_BUY":
            return self._guard(event, anomalies)
        return None

    def _danmaku(self, event: Dict[str, Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        info = event["data"]["info"]
        sender = _build_sender(self._danmaku_uinfo(info, anomalies), anomalies)
        text = str(info[1])

        # Emoticon danmaku: info[0][13] is a dict carrying the image url and info[1]
        # is its caption (RAW_DATA §4.1); plain danmaku has info[0][13] == '{}'.
        emoticon = info[0][13]
        if isinstance(emoticon, dict):
            url = emoticon.get("url")
            if not url:
                anomalies.note("image_url 缺失→''")
            return {
                "type": "danmaku",
                "timestamp": _hhmm(),
                "sender": sender,
                "text": text,
                "is_image": True,
                "image_url": str(url or ""),
            }

        reply_uname = self._danmaku_extra(info).get("reply_uname") or ""
        if reply_uname:
            text = f"@{reply_uname}: {text}"
        return {
            "type": "danmaku",
            "timestamp": _hhmm(),
            "sender": sender,
            "text": text,
            "is_image": False,
        }

    @staticmethod
    def _danmaku_uinfo(info: List[Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        """The unified UserInfo for a danmaku, at info[0][15].user (RAW_DATA §2.1).

        Falls back to the legacy positional layout (info[2]=user, info[3]=medal)
        for pre-new-format / dirty payloads, shaped to look like a UserInfo so it
        flows through _build_sender. Those indices are unstable — best effort, and
        reaching them at all is itself an anomaly (the new format was universal in
        the sample), so it is noted.
        """
        try:
            user = info[0][15]["user"]
            if isinstance(user, dict):
                return user
        except (KeyError, IndexError, TypeError):
            pass

        anomalies.note("UserInfo 缺失（info[0][15].user），回退到旧版位置数组")
        user: Dict[str, Any] = {}
        try:
            user["uid"] = info[2][0]
            user["base"] = {"name": info[2][1]}
        except (IndexError, TypeError):
            pass
        try:
            medal_arr = info[3]
            if isinstance(medal_arr, list) and len(medal_arr) >= 2:
                user["medal"] = {
                    "level": medal_arr[0],
                    "name": medal_arr[1],
                    "guard_level": medal_arr[10] if len(medal_arr) > 10 else 0,
                }
        except (IndexError, TypeError):
            pass
        return user

    @staticmethod
    def _danmaku_extra(info: List[Any]) -> Dict[str, Any]:
        try:
            raw = info[0][15].get("extra")
            return json.loads(raw) if raw else {}
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return {}

    def _gift(self, event: Dict[str, Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        data = _as_dict(_as_dict(event.get("data")).get("data"))
        if not data:
            raise ValueError("SEND_GIFT missing data.data")
        sender = _build_sender(
            data.get("sender_uinfo"), anomalies,
            flat_uid=data.get("uid"), flat_username=data.get("uname"),
        )
        count = _parse_int(data.get("num"))
        if count is None or count < 1:
            anomalies.note(f"giftcount 缺失/无效（{data.get('num')!r}）→1")
            count = 1
        gift_name = data.get("giftName") or data.get("gift_name")
        if not gift_name:
            anomalies.note("giftname 缺失→'礼物'")
            gift_name = "礼物"
        gift_name = str(gift_name)
        total_yuan = self._gift_value_yuan(data, gift_name, anomalies)
        self.stats.add(sender["uid"], sender["username"], "gift", total_yuan)
        return {
            "type": "gift",
            "timestamp": _hhmm(),
            "sender": sender,
            "giftname": gift_name,
            "giftcount": count,
            "gifttotalvalue": int(round(total_yuan * self.config.cents_per_yuan)),
        }

    def _gift_value_yuan(self, data: Dict[str, Any], gift_name: str, anomalies: "_Anomalies") -> float:
        """Yuan a gift counts for (RAW_DATA §5.2/§5.3).

        Free gifts (`coin_type == 'silver'`) count 0; everything else counts
        `total_coin` (milli-yuan). For blind boxes total_coin is already the opened
        face value (not what was paid), and for normal gold gifts it equals
        price × num — so a single branch covers both.
        """
        if str(data.get("coin_type") or "").lower() == "silver":
            return 0.0
        total_coin = _parse_float(data.get("total_coin"))
        if total_coin is None:
            anomalies.note(f"total_coin 缺失/无法解析（{data.get('total_coin')!r}）→0 元，gift={gift_name!r}")
            return 0.0
        return total_coin / self.config.gift_price_to_yuan_divisor

    def _superchat(self, event: Dict[str, Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        data = event["data"]["data"]
        sender = _build_sender(
            data.get("uinfo"), anomalies,
            flat_uid=data.get("uid"), flat_username=_as_dict(data.get("user_info")).get("uname"),
        )
        price_yuan = _parse_int(data.get("price"))  # SC price is in yuan (RAW_DATA §5.1)
        if price_yuan is None:
            anomalies.note(f"price 缺失/无法解析（{data.get('price')!r}）→0 元")
            price_yuan = 0
        self.stats.add(sender["uid"], sender["username"], "superchat", price_yuan)
        message = data.get("message")
        if not message:
            anomalies.note("text 缺失→''")
        return {
            "type": "superchat",
            "timestamp": _hhmm(),
            "sender": sender,
            "level": 2 if price_yuan >= self.config.superchat_observation_threshold_yuan else 1,
            "dwell_seconds": self._superchat_dwell_seconds(data.get("time"), anomalies),
            "value": price_yuan * self.config.cents_per_yuan,
            "text": str(message or ""),
        }

    def _superchat_dwell_seconds(self, raw_time: Any, anomalies: "_Anomalies") -> int:
        """Bilibili's authoritative dwell `time` (seconds) × the configured
        multiplier (RAW_DATA §4.3), at least 1 second."""
        seconds = _parse_int(raw_time)
        if seconds is None:
            anomalies.note(f"superchat time 缺失/无法解析（{raw_time!r})→0")
            seconds = 0
        return max(1, int(round(seconds * self.config.superchat_dwell_multiplier)))

    def _guard(self, event: Dict[str, Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        data = event["data"]["data"]
        uid = str(data.get("uid") or "")
        if not uid:
            anomalies.note("uid 缺失→''")
        username = data.get("username") or data.get("uname")
        if not username:
            username = uid or "匿名用户"
            anomalies.note(f"username 缺失→{username!r}")
        username = str(username)

        schema_level = self._guard_schema_level(data, anomalies)
        months = _parse_int(data.get("num"))
        if months is None or months < 1:
            anomalies.note(f"months 缺失/无效（{data.get('num')!r}）→1")
            months = 1
        # GUARD_BUY 不含 UserInfo，事件本身给不出头像和粉丝牌（RAW_DATA §2.2）。这是
        # 已知的事件结构限制（非兜底），故不告警。
        # TODO: 如需头像/粉丝牌，后续用 uid 走用户资料 API 查询补全。
        sender = {
            "uid": uid,
            "username": username,
            "avatar_url": "",
            "badgename": "",
            "badgelevel": 0,
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
            "months": months,
            "dwell_seconds": self.config.guard_dwell_seconds_by_schema_level[schema_level],
        }

    @staticmethod
    def _guard_schema_level(data: Dict[str, Any], anomalies: "_Anomalies") -> int:
        """Schema guard level (1/2/3 = 舰长/提督/总督). Prefer raw `guard_level`;
        if it's missing/unrecognized, derive from `gift_name` (舰长/提督/总督)
        rather than blindly guessing; default to 舰长 only if both fail."""
        schema_level = _guard_level_to_schema(data.get("guard_level"))
        if schema_level:
            return schema_level
        by_name = {"舰长": 1, "提督": 2, "总督": 3}.get(str(data.get("gift_name") or ""))
        if by_name:
            anomalies.note(f"guard_level 无效，按 gift_name 推断为 {data.get('gift_name')}")
            return by_name
        anomalies.note(
            f"guard_level 与 gift_name 均无法识别"
            f"（guard_level={data.get('guard_level')!r}, gift_name={data.get('gift_name')!r}）→舰长"
        )
        return 1


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
    return {"uid": SYSTEM_SENDER_ID, "username": SYSTEM_SENDER_NAME, "avatar_url": "", "badgename": "", "badgelevel": 0, "guardstat": 0}


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


def _build_sender(
    uinfo: Any,
    anomalies: "_Anomalies",
    *,
    flat_uid: Any = None,
    flat_username: Any = None,
) -> Dict[str, Any]:
    """Build the schema `sender` from a unified UserInfo object (RAW_DATA §2).

    DANMU_MSG / SEND_GIFT / SUPER_CHAT_MESSAGE all carry this same shape, only at
    different paths; GUARD_BUY has no UserInfo and is built by hand in _guard().
    `flat_uid` / `flat_username` are an event's top-level fields, used only as a
    fallback when the UserInfo lacks them (GIFT/SC carry both).

    Honesty: uid/username/avatar are "必有" per RAW_DATA §2.3, so substituting a
    placeholder for any of them is recorded on `anomalies`. An absent `medal`
    (no fan badge) and a 0 `guard_level` are legitimate states, not fallbacks, so
    they are silent. guardstat is read from medal.guard_level, never
    user.guard.level (RAW_DATA §2.2 warning).
    """
    uinfo = _as_dict(uinfo)
    base = _as_dict(uinfo.get("base"))
    medal = _as_dict(uinfo.get("medal"))

    uid = str(uinfo.get("uid") or flat_uid or "")
    if not uid:
        anomalies.note("uid 缺失→''")

    name = base.get("name") or flat_username
    if name:
        username = str(name)
    else:
        username = uid or "匿名用户"
        anomalies.note(f"username 缺失→{username!r}")

    face = base.get("face")
    if not face:
        anomalies.note("avatar_url 缺失→''")

    badgename = str(medal.get("name") or "")  # "" = 无粉丝牌（语义，非兜底）
    if badgename:
        badgelevel = _parse_int(medal.get("level"))
        if badgelevel is None:
            anomalies.note(f"badgelevel 无法解析（{medal.get('level')!r}）→0")
            badgelevel = 0
    else:
        badgelevel = 0

    return {
        "uid": uid,
        "username": username,
        "avatar_url": str(face or ""),
        "badgename": badgename,
        "badgelevel": badgelevel,
        "guardstat": _guard_level_to_schema(medal.get("guard_level")),
    }


def _guard_level_to_schema(raw_level: Any) -> int:
    try:
        level = int(raw_level or 0)
    except (TypeError, ValueError):
        return 0
    return {3: 1, 2: 2, 1: 3}.get(level, 0)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_int(value: Any) -> Optional[int]:
    """Parse to int, or None when missing/unparseable so callers can react."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    "superchat_dwell_multiplier",
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
        authors = [dict(author) for author in require("authors")]
        kwargs: Dict[str, Any] = {key: require(key) for key in _TOML_SCALAR_FIELDS}
        kwargs.update({key: BASE_DIR / require(key) for key in _TOML_PATH_FIELDS})
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"配置文件 {path.name} 的某个值格式不对：{exc}") from exc

    return AppConfig(
        default_title=require("title"),
        authors=authors,
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
