#!/usr/bin/env python3
"""
DanmakuHime — the Bilibili integration boundary.

The only place that knows Bilibili's raw formats — their raw event shapes (field
paths, money units, the guard-level inversion) are documented in
docs/bilibili_api_info/api_fact.md, which the parsers below don't restate. Two
kinds of translation live here, both turning fragile Bilibili payloads into the
clean schema (see schema.py / docs/SCHEMA.md):
  - live events: BilibiliEventAdapter maps DANMU_MSG / SEND_GIFT /
    SUPER_CHAT_MESSAGE / GUARD_BUY into typed events and publishes them.
  - room info: fetch_room_info() calls the room API (with retries) and
    room_info_from_api_response() shapes the response into the init event's
    room_info; empty_room_info() is the same-shaped soft fallback.

Also home to the backend's own "system" messages (reconnect notices, the
stream-end report, forwarded errors), which are emitted as schema events.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from bilibili_api import live, sync

import schema
from initialization import AppConfig
from schema import EventType
from server import EventHub
from stats import StatsTracker
from util import as_dict, exception_summary, hhmm, parse_float, parse_int

log = logging.getLogger("danmakuhime")

# Bilibili's raw event `cmd` values we translate (the input vocabulary; not our
# schema's `type` — that's schema.EventType).
_CMD_DANMU_MSG = "DANMU_MSG"
_CMD_SEND_GIFT = "SEND_GIFT"
_CMD_SUPER_CHAT_MESSAGE = "SUPER_CHAT_MESSAGE"
_CMD_GUARD_BUY = "GUARD_BUY"
_CMD_PREPARING = "PREPARING"

# Gift money fields (total_coin / price) are in milli-yuan (毫元); ÷1000 = yuan.
MILLIYUAN_PER_YUAN = 1000


def system_message(text: str, dwell_seconds: int) -> Dict[str, Any]:
    """A pinned-zone superchat published by the backend itself (reports, notices,
    errors). It is the one place a dwell is backend-chosen rather than from Bilibili
    (the notice's own display duration); value is 0 and the sender is the reserved
    system sender."""
    return {
        "type": EventType.SUPERCHAT,
        "timestamp": hhmm(),
        "sender": schema.system_sender(),
        "dwell_seconds": dwell_seconds,
        "value_cents": 0,
        "text": text,
    }


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
        cmd = event.get("type", "")  # Bilibili's raw event cmd (DANMU_MSG, ...), not our schema.EventType

        if cmd == _CMD_PREPARING:
            report = self.stats.save(self.config.stats_output_file)
            self.hub.publish(system_message(report, self.config.stream_end_report_dwell_seconds))
            return

        anomalies = _Anomalies()
        try:
            converted = self._convert(cmd, event, anomalies)
        except Exception as exc:
            log.error("处理 %s 失败：%s", cmd, exc, exc_info=True)
            if self.config.debug_forward_errors:
                self._publish_debug_error(cmd, exc)
            return

        # Honest accounting: when a field was missing/unparseable and we substituted
        # a placeholder, say so once with the full raw event attached.
        if anomalies:
            log.warning("解析 %s 命中兜底：%s；原始数据：%r",
                        cmd or "UNKNOWN", "；".join(anomalies.notes), event)

        if converted:
            self.hub.publish(converted)

    def _publish_debug_error(self, cmd: str, exc: Exception) -> None:
        self.hub.publish(
            system_message(
                f"后端处理 {cmd or 'UNKNOWN'} 失败：{type(exc).__name__}: {exc}",
                self.config.debug_error_dwell_seconds,
            )
        )

    def _log_event(self, event: Dict[str, Any]) -> None:
        with self._event_log_lock:
            with self.config.event_log_file.open("a", encoding="utf-8") as file:
                file.write(repr(event))
                file.write("\n")

    def _convert(self, cmd: str, event: Dict[str, Any], anomalies: "_Anomalies") -> Optional[Dict[str, Any]]:
        if cmd == _CMD_DANMU_MSG:
            return self._danmaku(event, anomalies)
        if cmd == _CMD_SEND_GIFT:
            return self._gift(event, anomalies)
        if cmd == _CMD_SUPER_CHAT_MESSAGE:
            return self._superchat(event, anomalies)
        if cmd == _CMD_GUARD_BUY:
            return self._guard(event, anomalies)
        return None

    def _danmaku(self, event: Dict[str, Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        info = event["data"]["info"]
        sender_dict = self._build_sender(self._danmaku_uinfo(info, anomalies), anomalies)
        text = str(info[1])

        # Emoticon danmaku: info[0][13] is a dict carrying the image url and info[1]
        # is its caption; plain danmaku has info[0][13] == '{}'.
        emoticon = info[0][13]
        if isinstance(emoticon, dict):
            url = emoticon.get("url")
            if not url:
                anomalies.note("image_url 缺失→''")
            return {
                "type": EventType.DANMAKU,
                "timestamp": hhmm(),
                "sender": sender_dict,
                "text": text,
                "is_image": True,
                "image_url": str(url or ""),
            }

        reply_uname = self._danmaku_extra(info).get("reply_uname") or ""
        if reply_uname:
            text = f"@{reply_uname}: {text}"
        return {
            "type": EventType.DANMAKU,
            "timestamp": hhmm(),
            "sender": sender_dict,
            "text": text,
            "is_image": False,
        }

    @staticmethod
    def _danmaku_uinfo(info: List[Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        """The unified UserInfo for a danmaku.

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
        data = event["data"]["data"]
        sender_dict = self._build_sender(
            data.get("sender_uinfo"), anomalies,
            flat_uid=data.get("uid"), flat_username=data.get("uname"),
        )
        count = parse_int(data.get("num"))
        if count is None or count < 1:
            anomalies.note(f"gift_count 缺失/无效（{data.get('num')!r}）→1")
            count = 1
        gift_name = data.get("giftName") or data.get("gift_name")
        if not gift_name:
            anomalies.note("gift_name 缺失→'礼物'")
            gift_name = "礼物"
        gift_name = str(gift_name)
        total_yuan = self._gift_value_yuan(data, gift_name, anomalies)
        self.stats.add(sender_dict["uid"], sender_dict["username"], "gift", total_yuan)
        return {
            "type": EventType.GIFT,
            "timestamp": hhmm(),
            "sender": sender_dict,
            "gift_name": gift_name,
            "gift_count": count,
            "value_cents": int(round(total_yuan * schema.CENTS_PER_YUAN)),
        }

    def _gift_value_yuan(self, data: Dict[str, Any], gift_name: str, anomalies: "_Anomalies") -> float:
        """Yuan a gift counts for: free gifts (`coin_type == 'silver'`) count 0,
        everything else counts `total_coin`. One branch covers both blind boxes
        (total_coin is the opened face value, not what was paid) and normal gold
        gifts (where it equals price × num).
        """
        if str(data.get("coin_type") or "").lower() == "silver":
            return 0.0
        total_coin = parse_float(data.get("total_coin"))
        if total_coin is None:
            anomalies.note(f"total_coin 缺失/无法解析（{data.get('total_coin')!r}）→0 元，gift={gift_name!r}")
            return 0.0
        return total_coin / MILLIYUAN_PER_YUAN

    def _superchat(self, event: Dict[str, Any], anomalies: "_Anomalies") -> Dict[str, Any]:
        data = event["data"]["data"]
        sender_dict = self._build_sender(
            data.get("uinfo"), anomalies,
            flat_uid=data.get("uid"), flat_username=as_dict(data.get("user_info")).get("uname"),
        )
        price_yuan = parse_int(data.get("price"))  # SC price is in yuan
        if price_yuan is None:
            anomalies.note(f"price 缺失/无法解析（{data.get('price')!r}）→0 元")
            price_yuan = 0
        self.stats.add(sender_dict["uid"], sender_dict["username"], "superchat", price_yuan)
        message = data.get("message")
        if not message:
            anomalies.note("text 缺失→''")
        return {
            "type": EventType.SUPERCHAT,
            "timestamp": hhmm(),
            "sender": sender_dict,
            "dwell_seconds": self._superchat_dwell_seconds(data.get("time"), anomalies),
            "value_cents": price_yuan * schema.CENTS_PER_YUAN,
            "text": str(message or ""),
        }

    @staticmethod
    def _superchat_dwell_seconds(raw_time: Any, anomalies: "_Anomalies") -> int:
        """Bilibili's authoritative SC display duration `time` (seconds), passed
        through as-is, at least 1 second."""
        seconds = parse_int(raw_time)
        if seconds is None:
            anomalies.note(f"superchat time 缺失/无法解析（{raw_time!r})→0")
            seconds = 0
        return max(1, seconds)

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

        guard_level = self._guard_schema_level(data, anomalies)
        months = parse_int(data.get("num"))
        if months is None or months < 1:
            anomalies.note(f"months 缺失/无效（{data.get('num')!r}）→1")
            months = 1
        # GUARD_BUY carries no UserInfo, so the event itself gives no avatar or fan
        # medal. This is a known event-shape limit (not a fallback), so it is not
        # warned about.
        # TODO: if avatar/medal are wanted, look them up later by uid via the user
        # profile API.
        sender_dict = schema.sender(
            uid=uid,
            username=username,
            avatar_url="",
            badge_name="",
            badge_level=0,
            guard_level=guard_level,
        )

        tier_name = schema.guard_tier_name(guard_level)
        if tier_name:
            self.stats.add(uid, username, tier_name, months)

        return {
            "type": EventType.GUARD,
            "timestamp": hhmm(),
            "sender": sender_dict,
            "guard_level": guard_level,
            "months": months,
        }

    @staticmethod
    def _guard_schema_level(data: Dict[str, Any], anomalies: "_Anomalies") -> int:
        """Schema guard level (1/2/3 = 舰长/提督/总督). Prefer raw `guard_level`;
        if it's missing/unrecognized, derive from `gift_name` (舰长/提督/总督)
        rather than blindly guessing; default to 舰长 only if both fail."""
        guard_level = schema.bili_guard_level_to_schema(data.get("guard_level"))
        if guard_level:
            return guard_level
        by_name = schema.guard_name_to_schema(data.get("gift_name"))
        if by_name:
            anomalies.note(f"guard_level 无效，按 gift_name 推断为 {data.get('gift_name')}")
            return by_name
        anomalies.note(
            f"guard_level 与 gift_name 均无法识别"
            f"（guard_level={data.get('guard_level')!r}, gift_name={data.get('gift_name')!r}）→舰长"
        )
        return 1

    @staticmethod
    def _build_sender(
        uinfo: Any,
        anomalies: "_Anomalies",
        *,
        flat_uid: Any = None,
        flat_username: Any = None,
    ) -> Dict[str, Any]:
        """Build the schema `sender` from a unified UserInfo object. `flat_uid` /
        `flat_username` are the event's top-level fields, used only as a fallback
        when the UserInfo lacks them.

        Honesty: uid/username/avatar are always present, so substituting a
        placeholder for any of them is recorded on `anomalies`; an absent `medal`
        (no fan badge) and a 0 `guard_level` are legitimate states, not fallbacks,
        so they stay silent. guard_level is read from medal.guard_level, never
        user.guard.level — these are easy to get wrong, hence the note.
        """
        uinfo = as_dict(uinfo)
        base = as_dict(uinfo.get("base"))
        medal = as_dict(uinfo.get("medal"))

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

        badge_name = str(medal.get("name") or "")  # "" = 无粉丝牌（语义，非兜底）
        if badge_name:
            badge_level = parse_int(medal.get("level"))
            if badge_level is None:
                anomalies.note(f"badge_level 无法解析（{medal.get('level')!r}）→0")
                badge_level = 0
        else:
            badge_level = 0

        return schema.sender(
            uid=uid,
            username=username,
            avatar_url=str(face or ""),
            badge_name=badge_name,
            badge_level=badge_level,
            guard_level=schema.bili_guard_level_to_schema(medal.get("guard_level")),
        )


# ==================== Room info (init event) ====================
#
# Fetching and parsing the live room's metadata into the init event's room_info.
# This is Bilibili-format knowledge too, so it lives here; main.py's _build_init
# just wraps the result into the init event.


def fetch_room_info(config: AppConfig) -> Optional[Dict[str, Any]]:
    """Fetch the raw room info via the room API, retrying per the shared startup
    budget. Returns None after the last failure so the caller can fall back to an
    empty same-shape room_info and keep starting."""
    retries = config.initialization_retries
    for attempt in range(retries + 1):
        try:
            return sync(live.LiveRoom(config.room_id).get_room_info())
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if attempt >= retries:
                log.warning(
                    "获取直播间信息连续失败，使用空 init 信息继续启动：%s",
                    exception_summary(exc),
                    exc_info=True,
                )
                return None
            log.warning(
                "获取直播间信息失败：%s，%s 秒后重试（%s/%s）。",
                exception_summary(exc),
                config.login_retry_delay_seconds,
                attempt + 1,
                retries,
            )
            time.sleep(config.login_retry_delay_seconds)
    return None


def room_info_from_api_response(api_response: Any, configured_room_id: int) -> Dict[str, Any]:
    data = as_dict(api_response)
    room = as_dict(data.get("room_info"))
    anchor = as_dict(as_dict(data.get("anchor_info")).get("base_info"))
    return room_info_dict(
        room_id=configured_room_id,
        title=room.get("title"),
        streamer_username=anchor.get("uname"),
        streamer_uid=room.get("uid"),
        streamer_avatar_url=anchor.get("face"),
        parent_area_name=room.get("parent_area_name"),
        area_name=room.get("area_name"),
        cover_image_url=room.get("cover"),
    )


def empty_room_info(configured_room_id: int) -> Dict[str, Any]:
    return room_info_dict(room_id=configured_room_id)


def room_info_dict(
    *,
    room_id: int,
    title: Any = "",
    streamer_username: Any = "",
    streamer_uid: Any = 0,
    streamer_avatar_url: Any = "",
    parent_area_name: Any = "",
    area_name: Any = "",
    cover_image_url: Any = "",
) -> Dict[str, Any]:
    uid = parse_int(streamer_uid)
    return {
        "room_id": room_id,
        "title": str(title or ""),
        "streamer_username": str(streamer_username or ""),
        "streamer_uid": uid if uid is not None else 0,
        "streamer_avatar_url": str(streamer_avatar_url or ""),
        "parent_area_name": str(parent_area_name or ""),
        "area_name": str(area_name or ""),
        "cover_image_url": str(cover_image_url or ""),
    }
