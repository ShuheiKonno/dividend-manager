from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST)


def now_jst_iso() -> str:
    return now_jst().isoformat(timespec="seconds")


def today_jst_str() -> str:
    return now_jst().date().isoformat()


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


def fmt_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}"


def fmt_percent(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:,.{digits}f}%"


def rjust(text: str, width: int) -> str:
    if len(text) >= width:
        return text
    return " " * (width - len(text)) + text
