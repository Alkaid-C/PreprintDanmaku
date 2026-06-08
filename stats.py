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

log = logging.getLogger("danmakuhime")


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
