#!/usr/bin/env python3
"""
DanmakuHime — dependency-free helpers.

The bottom layer: pure functions that know nothing about this project and import
nothing from it, so any module may import them freely. Time formatting, exception
summarizing, and the defensive parse helpers used while reading Bilibili's fragile
nested payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def exception_summary(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        return f"SystemExit({exc.code})"
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_int(value: Any) -> Optional[int]:
    """Parse to int, or None when missing/unparseable so callers can react."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> Optional[float]:
    """Parse to float, or None when missing/unparseable so callers can react."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
