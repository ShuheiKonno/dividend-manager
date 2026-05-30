from __future__ import annotations

import csv
import json
from pathlib import Path
from shutil import copy2
from typing import Iterable

from .models import (
    HOLDINGS_FIELDS,
    MARKET_DATA_FIELDS,
    FX_FIELDS,
    HISTORY_FIELDS,
    Holding,
    MarketData,
    FXRate,
    HistoryRecord,
)
from .utils import now_jst_iso

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BACKUP_DIR = DATA_DIR / "backup"


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _atomic_write(
    path: Path,
    rows: Iterable[dict[str, str]],
    fieldnames: list[str],
    backup: bool = True,
) -> None:
    _ensure_dirs()
    # 履歴のように起動・更新の都度書き込むファイルは backup=False にして
    # data/backup/ の肥大を防ぐ（履歴は本質的に追記ログでバックアップ不要）。
    if backup and path.exists():
        backup_name = (
            f"{path.stem}_{now_jst_iso().replace(':', '').replace('-', '')}"
            + path.suffix
        )
        copy2(path, BACKUP_DIR / backup_name)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp_path.replace(path)


HOLDINGS_PATH = DATA_DIR / "holdings.csv"
MARKET_DATA_PATH = DATA_DIR / "market_data.csv"
FX_PATH = DATA_DIR / "fx.csv"
HISTORY_PATH = DATA_DIR / "history.csv"


def load_holdings() -> list[Holding]:
    return [Holding.from_dict(row) for row in _read_csv(HOLDINGS_PATH)]


def save_holdings(rows: list[Holding]) -> None:
    _atomic_write(HOLDINGS_PATH, [row.to_dict() for row in rows], HOLDINGS_FIELDS)


def load_market_data() -> list[MarketData]:
    return [MarketData.from_dict(row) for row in _read_csv(MARKET_DATA_PATH)]


def save_market_data(rows: list[MarketData]) -> None:
    _atomic_write(MARKET_DATA_PATH, [row.to_dict() for row in rows], MARKET_DATA_FIELDS)


def load_fx() -> list[FXRate]:
    return [FXRate.from_dict(row) for row in _read_csv(FX_PATH)]


def save_fx(rows: list[FXRate]) -> None:
    _atomic_write(FX_PATH, [row.to_dict() for row in rows], FX_FIELDS)


def load_history() -> list[HistoryRecord]:
    return [HistoryRecord.from_dict(row) for row in _read_csv(HISTORY_PATH)]


def save_history(rows: list[HistoryRecord]) -> None:
    """履歴を record_date 昇順で書き出す（バックアップは取らない）。"""
    ordered = sorted(rows, key=lambda r: r.record_date)
    _atomic_write(
        HISTORY_PATH, [row.to_dict() for row in ordered], HISTORY_FIELDS, backup=False
    )


def init_empty_files() -> None:
    _ensure_dirs()
    if not HOLDINGS_PATH.exists():
        _atomic_write(HOLDINGS_PATH, [], HOLDINGS_FIELDS)
    if not MARKET_DATA_PATH.exists():
        _atomic_write(MARKET_DATA_PATH, [], MARKET_DATA_FIELDS)
    if not FX_PATH.exists():
        _atomic_write(FX_PATH, [], FX_FIELDS)
    if not HISTORY_PATH.exists():
        _atomic_write(HISTORY_PATH, [], HISTORY_FIELDS, backup=False)


SETTINGS_PATH = DATA_DIR / "settings.json"
_VALID_DIVIDEND_MODES = {"gross", "net"}
_VALID_WITHDRAWAL_MODES = {None, "fixed", "rate"}
_VALID_CHART_TYPES = {"treemap", "pie"}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        result = {}
        if data.get("dividend_mode") in _VALID_DIVIDEND_MODES:
            result["dividend_mode"] = data["dividend_mode"]
        wm = data.get("withdrawal_mode")
        if wm in _VALID_WITHDRAWAL_MODES:
            result["withdrawal_mode"] = wm
        # 構成グラフ種別（評価額タブ・配当タブで独立）
        if data.get("asset_chart_type") in _VALID_CHART_TYPES:
            result["asset_chart_type"] = data["asset_chart_type"]
        if data.get("income_chart_type") in _VALID_CHART_TYPES:
            result["income_chart_type"] = data["income_chart_type"]
        return result
    except Exception:
        return {}


def save_settings(
    dividend_mode: str,
    withdrawal_mode: str | None,
    asset_chart_type: str | None = None,
    income_chart_type: str | None = None,
) -> None:
    _ensure_dirs()
    # 既存値を引き継ぎ、明示指定された項目のみ更新する（部分更新を許容）。
    data = load_settings()
    data["dividend_mode"] = dividend_mode
    data["withdrawal_mode"] = withdrawal_mode
    if asset_chart_type in _VALID_CHART_TYPES:
        data["asset_chart_type"] = asset_chart_type
    if income_chart_type in _VALID_CHART_TYPES:
        data["income_chart_type"] = income_chart_type
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp_path.replace(SETTINGS_PATH)
