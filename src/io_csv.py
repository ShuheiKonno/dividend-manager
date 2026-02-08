from __future__ import annotations

import csv
from pathlib import Path
from shutil import copy2
from typing import Iterable

from .models import HOLDINGS_FIELDS, MARKET_DATA_FIELDS, FX_FIELDS, Holding, MarketData, FXRate
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


def _atomic_write(path: Path, rows: Iterable[dict[str, str]], fieldnames: list[str]) -> None:
    _ensure_dirs()
    if path.exists():
        backup_name = f"{path.stem}_{now_jst_iso().replace(':','').replace('-','')}" + path.suffix
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


def init_empty_files() -> None:
    _ensure_dirs()
    if not HOLDINGS_PATH.exists():
        _atomic_write(HOLDINGS_PATH, [], HOLDINGS_FIELDS)
    if not MARKET_DATA_PATH.exists():
        _atomic_write(MARKET_DATA_PATH, [], MARKET_DATA_FIELDS)
    if not FX_PATH.exists():
        _atomic_write(FX_PATH, [], FX_FIELDS)
