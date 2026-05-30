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
_VALID_TREND_PERIODS = {"all", "10y", "5y", "3y", "1y"}
_VALID_BASELINES = {"zero", "min"}
_VALID_INCLUDE = {"with", "without"}

# 設定キー → 許容値集合。load/save 両方の検証に用いる（評価額/配当タブで独立）。
_SETTINGS_VALIDATORS: dict[str, set] = {
    "dividend_mode": _VALID_DIVIDEND_MODES,
    "withdrawal_mode": _VALID_WITHDRAWAL_MODES,
    "asset_chart_type": _VALID_CHART_TYPES,
    "income_chart_type": _VALID_CHART_TYPES,
    "asset_trend_period": _VALID_TREND_PERIODS,
    "income_trend_period": _VALID_TREND_PERIODS,
    "asset_trend_baseline": _VALID_BASELINES,
    "income_trend_baseline": _VALID_BASELINES,
    # 配当推移グラフの系列選択（優待込み/取崩込み）
    "income_benefit": _VALID_INCLUDE,
    "income_withdrawal": _VALID_INCLUDE,
}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # 各キーを許容値で検証し、妥当なものだけ採用する。
        result = {}
        for key, valid in _SETTINGS_VALIDATORS.items():
            if key in data and data[key] in valid:
                result[key] = data[key]
        return result
    except Exception:
        return {}


def save_settings(**updates) -> None:
    """設定を部分更新する。既存値を読み込み、検証を通った項目のみ上書きする。

    例: save_settings(dividend_mode="net") / save_settings(asset_trend_period="1y")。
    withdrawal_mode=None は正当値（取崩なし）なので、明示指定されれば保存される。
    """
    _ensure_dirs()
    data = load_settings()
    for key, value in updates.items():
        valid = _SETTINGS_VALIDATORS.get(key)
        if valid is not None and value in valid:
            data[key] = value
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp_path.replace(SETTINGS_PATH)
