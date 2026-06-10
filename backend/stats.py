#!/usr/bin/env python3
"""
DanmakuHime — per-user consumption accounting.

A thread-safe ledger that accumulates gift/SuperChat yuan and guard months per
uid, renders the on-screen report, and writes it to disk. It does not know where
events come from — the adapter feeds it via add().
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict

from schema import GUARD_TIERS

log = logging.getLogger("danmakuhime")

# The accounting categories, as (key, report title). Money categories accumulate
# yuan (float, shown in 元); guard categories accumulate months (int, shown in 月)
# and are keyed by the tier's Chinese name, derived from schema.GUARD_TIERS so there
# is no second list of tier names to keep in sync. `add()` accepts exactly these
# keys.
_MONEY_SECTIONS = (("gift", "礼物"), ("superchat", "SuperChat"))
_GUARD_SECTIONS = tuple((tier.name, tier.name) for tier in GUARD_TIERS)
_MONEY_KEYS = frozenset(key for key, _ in _MONEY_SECTIONS)
_GUARD_KEYS = frozenset(key for key, _ in _GUARD_SECTIONS)


class StatsTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, Any]] = {}

    def add(self, uid: str, username: str, category: str, value: float) -> None:
        if not uid:
            log.warning("统计跳过：缺少 uid（username=%r, category=%s, value=%s）", username, category, value)
            return
        if category not in _MONEY_KEYS and category not in _GUARD_KEYS:
            log.warning("统计跳过：未知类目 %r（username=%r, value=%s）", category, username, value)
            return
        with self._lock:
            record = self._stats.setdefault(uid, self._new_record(username))
            record["username"] = username
            record[category] += value

    @staticmethod
    def _new_record(username: str) -> Dict[str, Any]:
        record: Dict[str, Any] = {"username": username}
        for key in _MONEY_KEYS:
            record[key] = 0.0
        for key in _GUARD_KEYS:
            record[key] = 0
        return record

    def report(self) -> str:
        with self._lock:
            if not self._stats:
                return "没有消费记录"
            lines = ["用户消费统计", "=" * 40]
            sections = (
                [(key, title, "元") for key, title in _MONEY_SECTIONS]
                + [(key, title, "月") for key, title in _GUARD_SECTIONS]
            )
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
