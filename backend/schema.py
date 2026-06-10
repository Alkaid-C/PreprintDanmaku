#!/usr/bin/env python3
"""
DanmakuHime — the SSE contract, in code.

The code embodiment of docs/SCHEMA.md: the event-type names, the shape of the
`sender` dict, and the guard-tier table that ties Bilibili's raw guard levels to
our schema. A dependency-free leaf so both bilibili.py (the producer) and stats.py
(a consumer) can share one definition instead of each hardcoding the field names
and the 舰长/提督/总督 ↔ 1/2/3 mapping.

The field names here ARE the wire contract — they intentionally match SCHEMA.md
exactly (snake_case). Bump main.py's API_VERSION and update SCHEMA.md together when
they change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    """The `type` field carried by every SSE event (SCHEMA.md §1). A str-Enum so it
    serializes straight to its value in json.dumps()."""

    INIT = "init"
    DANMAKU = "danmaku"
    GIFT = "gift"
    SUPERCHAT = "superchat"
    GUARD = "guard"


# The init event always carries id 0; it precedes the monotonic event stream.
INIT_EVENT_ID = 0

# Wire money unit: the schema's money fields (`value_cents`) are in cents.
CENTS_PER_YUAN = 100

# The backend's own system notices (reconnect / stream-end report / forwarded
# error) ride as superchat events under this reserved sender; the frontend can
# recognize them by sender.uid == "0".
SYSTEM_SENDER_ID = "0"
SYSTEM_SENDER_NAME = "DanmakuHime"


@dataclass(frozen=True)
class GuardTier:
    """One 大航海 tier, tying together the numbers/name that all mean the same rank,
    so nothing downstream has to re-hardcode the mapping."""

    schema_level: int   # our wire value: 1/2/3 = 舰长/提督/总督
    name: str           # Bilibili's Chinese tier name
    bili_level: int     # Bilibili's raw guard_level: 3/2/1 (inverted vs. ours)


# Ordered 舰长 → 提督 → 总督. `bili_level` is deliberately inverted from
# `schema_level` (Bilibili numbers 总督 lowest); it is kept as an explicit column
# rather than computed as `4 - schema_level` so the inversion stays visible.
GUARD_TIERS = (
    GuardTier(schema_level=1, name="舰长", bili_level=3),
    GuardTier(schema_level=2, name="提督", bili_level=2),
    GuardTier(schema_level=3, name="总督", bili_level=1),
)

_BILI_TO_SCHEMA = {tier.bili_level: tier.schema_level for tier in GUARD_TIERS}
_NAME_TO_SCHEMA = {tier.name: tier.schema_level for tier in GUARD_TIERS}
_SCHEMA_TO_NAME = {tier.schema_level: tier.name for tier in GUARD_TIERS}


def bili_guard_level_to_schema(raw_level: Any) -> int:
    """Bilibili's raw guard_level → our schema guard_level (3→1, 2→2, 1→3).
    Returns 0 (无) for 0, missing, or anything unrecognized."""
    try:
        level = int(raw_level or 0)
    except (TypeError, ValueError):
        return 0
    return _BILI_TO_SCHEMA.get(level, 0)


def guard_name_to_schema(name: Any) -> Optional[int]:
    """Chinese tier name (舰长/提督/总督) → schema guard_level, or None if unknown."""
    return _NAME_TO_SCHEMA.get(str(name or ""))


def guard_tier_name(schema_level: int) -> Optional[str]:
    """Schema guard_level → Chinese tier name, or None if not a guard tier (0/无)."""
    return _SCHEMA_TO_NAME.get(schema_level)


def sender(
    *,
    uid: str,
    username: str,
    avatar_url: str,
    badge_name: str,
    badge_level: int,
    guard_level: int,
) -> Dict[str, Any]:
    """Assemble a schema `sender` dict (SCHEMA.md §2) — the single definition of the
    sender shape. Callers do the parsing and hand in already-clean values."""
    return {
        "uid": uid,
        "username": username,
        "avatar_url": avatar_url,
        "badge_name": badge_name,
        "badge_level": badge_level,
        "guard_level": guard_level,
    }


def system_sender() -> Dict[str, Any]:
    """The reserved sender for the backend's own system notices (uid "0")."""
    return sender(
        uid=SYSTEM_SENDER_ID,
        username=SYSTEM_SENDER_NAME,
        avatar_url="",
        badge_name="",
        badge_level=0,
        guard_level=0,
    )
