from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from shutil import copy2
from typing import Callable, Iterable, TypeVar

from . import app_config
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
from .calc import (  # 設定 "fund_share_unit" / "yield_scope" の許容値
    FUND_SHARE_UNITS as _FUND_SHARE_UNITS,
    YIELD_SCOPES as _YIELD_SCOPES,
)
from .theme import THEMES as _THEMES  # 設定 "theme" の許容値（Qt 非依存）
from .utils import now_jst, normalize_iso_date

# データフォルダはユーザーが変更できる（set_data_dir）。以下は定数ではなく
# 「現在値」で、set_data_dir が DATA_DIR ごとまとめて張り替える。個々の関数は
# 呼び出し時にこのグローバルを読むため、切替後は次の読み書きから新フォルダを見る。
DATA_DIR = app_config.load_data_dir()
BACKUP_DIR = DATA_DIR / "backup"
_WRITE_RETRIES = 3
_WRITE_RETRY_DELAY_SECONDS = 0.2
_BACKUP_KEEP_PER_FILE = 30
_T = TypeVar("_T")


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _retry_on_permission_error(action: Callable[[], _T], path: Path) -> _T:
    for attempt in range(_WRITE_RETRIES):
        try:
            return action()
        except PermissionError as exc:
            if attempt == _WRITE_RETRIES - 1:
                raise PermissionError(
                    f"{path.name} を保存できません。Excel などでファイルを開いている場合は "
                    "閉じてから再試行してください。"
                ) from exc
            time.sleep(_WRITE_RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable")


def _prune_backups(path: Path) -> None:
    backups = sorted(BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"))
    for old in backups[:-_BACKUP_KEEP_PER_FILE]:
        try:
            old.unlink()
        except OSError:
            pass


def _atomic_write(
    path: Path,
    rows: Iterable[dict[str, str]],
    fieldnames: list[str],
    backup: bool = True,
    encoding: str = "utf-8",
) -> None:
    _ensure_dirs()
    # 履歴のように起動・更新の都度書き込むファイルは backup=False にして
    # data/backup/ の肥大を防ぐ（履歴は本質的に追記ログでバックアップ不要）。
    if backup and path.exists():
        backup_name = f"{path.stem}_{now_jst().strftime('%Y%m%dT%H%M%S%f%z')}{path.suffix}"
        _retry_on_permission_error(lambda: copy2(path, BACKUP_DIR / backup_name), path)
        _prune_backups(path)
    # encoding は data/ 内の正規ファイルでは "utf-8"。ユーザー指定先への
    # エクスポートだけ "utf-8-sig"（BOM）にして Excel での日本語文字化けを防ぐ。
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    _retry_on_permission_error(lambda: tmp_path.replace(path), path)


HOLDINGS_PATH = DATA_DIR / "holdings.csv"
MARKET_DATA_PATH = DATA_DIR / "market_data.csv"
FX_PATH = DATA_DIR / "fx.csv"
HISTORY_PATH = DATA_DIR / "history.csv"

# DATA_DIR 配下に置くファイル（グローバル名 → ファイル名）。set_data_dir が
# ここを回してパスを張り替えるので、**新しいファイルを足したらここにも足す**
# （足し忘れると1つだけ旧フォルダを読み書きし続け、エラーも出ない）。
# tests/test_io_csv.py が「*_PATH / *_DIR が全て DATA_DIR 配下か」を検証する。
_DERIVED_PATHS = {
    "BACKUP_DIR": "backup",
    "HOLDINGS_PATH": "holdings.csv",
    "MARKET_DATA_PATH": "market_data.csv",
    "FX_PATH": "fx.csv",
    "HISTORY_PATH": "history.csv",
    "SETTINGS_PATH": "settings.json",
}

# copy_data_files の対象。settings.json（ウィンドウ位置・列幅）は「データ」では
# ないので含めない——フォルダを分ける目的は資産データの置き場所であって、
# 画面状態を持ち回ることではない。
_DATA_FILE_NAMES = ("holdings.csv", "market_data.csv", "fx.csv", "history.csv")


def set_data_dir(path: str | Path, persist: bool = True) -> Path:
    """データフォルダを切り替える（フォルダ作成 → 設定保存 → パス張替の順）。

    設定の保存に失敗したときに「今回だけ切り替わって次回は戻る」状態にしない
    ため、永続化が成功してからグローバルを張り替える。`persist=False` は
    テスト用（実ユーザーの %APPDATA% を汚さない）。
    """
    new_dir = Path(path).expanduser()
    new_dir.mkdir(parents=True, exist_ok=True)
    if persist:
        app_config.save_data_dir(new_dir)
    globals()["DATA_DIR"] = new_dir
    for name, child in _DERIVED_PATHS.items():
        globals()[name] = new_dir / child
    return new_dir


def copy_data_files(src: str | Path, dst: str | Path) -> list[str]:
    """データ CSV を別フォルダへコピーし、コピーしたファイル名を返す。

    **既に同名ファイルがある場合は上書きしない**（フォルダ切替は「データを
    持って行く」操作であって、行き先の既存データを潰す操作ではない）。
    """
    src_dir, dst_dir = Path(src), Path(dst)
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in _DATA_FILE_NAMES:
        source = src_dir / name
        target = dst_dir / name
        if source.exists() and not target.exists():
            copy2(source, target)
            copied.append(name)
    return copied


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


# ---- 外部パスへのエクスポート / 外部パスからのインポート ----
# data/ 内の正規 CSV と同じ列・同じ列名で読み書きするため、エクスポート結果は
# そのまま import で読み戻せる（往復可能）。Excel での文字化け回避のため BOM 付き
# UTF-8（utf-8-sig）で書き出す。バックアップ・原子的置換は _atomic_write に委譲する。


def export_holdings_csv(path: str | Path, rows: list[Holding]) -> None:
    """保有銘柄を外部パスへ CSV 出力する（holdings.csv と同一フォーマット）。

    shares は保存値のまま（投信は万口単位）出す。設定 fund_share_unit は表示と
    入力欄だけの変換で、ここで掛けると export → import が恒等でなくなり、
    取り込むたびに口数が 1万倍ずれる。read_holdings_csv 側も同様に変換しない。
    """
    _atomic_write(
        Path(path),
        [row.to_dict() for row in rows],
        HOLDINGS_FIELDS,
        backup=False,
        encoding="utf-8-sig",
    )


def export_history_csv(path: str | Path, rows: list[HistoryRecord]) -> None:
    """推移履歴を外部パスへ CSV 出力する（record_date 昇順 / history.csv と同一）。"""
    ordered = sorted(rows, key=lambda r: r.record_date)
    _atomic_write(
        Path(path),
        [row.to_dict() for row in ordered],
        HISTORY_FIELDS,
        backup=False,
        encoding="utf-8-sig",
    )


def write_text_export(path: str | Path, text: str) -> None:
    """ユーザー指定パスへテキストを原子的に書き出す（Markdown エクスポート用）。

    _atomic_write は csv.DictWriter 専用で自由テキストを通せないため別関数に
    するが、.tmp → replace と _retry_on_permission_error（エディタが掴んでいる
    ときに日本語で説明的な PermissionError を出す）は同じものを使う。

    CSV エクスポートと違い **BOM は付けない**。utf-8-sig は Excel の文字化け
    対策であって、Markdown の先頭に BOM が入ると多くのビューアで1行目の `#` が
    見出しとして解釈されなくなる。ここを utf-8-sig へ「揃える」と壊れる。
    バックアップも取らない（アプリの管理対象外のパスのため。export_*_csv と同方針）。
    """
    target = Path(path)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    # 書込みと置換の両方を包む（_atomic_write が copy2 と replace の両方を包むのと
    # 同じ理由）。置換だけ包むと、書込み側が弾かれたときに Windows の生の
    # PermissionError がそのまま出て、他の保存経路と案内文が食い違う。
    _retry_on_permission_error(
        lambda: tmp_path.write_text(text, encoding="utf-8", newline="\n"), target
    )
    _retry_on_permission_error(lambda: tmp_path.replace(target), target)


def read_history_csv(path: str | Path) -> list[HistoryRecord]:
    """外部 CSV を推移履歴として読み込む（エクスポートした history CSV の取込用）。

    record_date は ISO "YYYY-MM-DD" へ正規化し、解析不能な行（空・誤ファイルの
    別列・"abc" 等）は除外する。これにより誤ったファイル（保有 CSV 等）を選んでも
    履歴を壊さず「取込対象なし」になり、Excel 再保存で "2024/1/1" のように崩れた
    日付も ISO へ直して取り込める（後段の filter_by_period のクラッシュを防ぐ）。
    欠損列は from_dict が None で吸収するため、列が一部欠けた CSV も読める。
    """
    records: list[HistoryRecord] = []
    for row in _read_csv(Path(path)):
        rec = HistoryRecord.from_dict(row)
        iso = normalize_iso_date(rec.record_date)
        if iso is None:
            continue
        rec.record_date = iso
        records.append(rec)
    return records


def read_holdings_csv(path: str | Path) -> list[Holding]:
    """外部 CSV を保有銘柄として読み込む（エクスポートした holdings CSV の取込用）。

    symbol が空の行は除外する。これにより誤ったファイル（履歴 CSV 等）を選んでも
    保有を壊さず「取込対象なし」として扱える。欠損列は from_dict が既定値で
    吸収するため、列が一部欠けた CSV も読める。symbol は一意でない（複数口座・
    取得単価）ため取込はマージせず全置換（呼び出し側が確認の上で保存する）。
    """
    holdings = [Holding.from_dict(row) for row in _read_csv(Path(path))]
    return [h for h in holdings if h.symbol]


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

def _is_int(value: object) -> bool:
    """厳密な int 判定（bool は int のサブクラスなので除外する）。"""
    return type(value) is int


def _is_number(value: object) -> bool:
    """厳密な数値判定（bool を除く int / float のみ許容）。"""
    return type(value) in (int, float)


def _is_geometry(value: object) -> bool:
    """ウィンドウジオメトリ [x, y, w, h]。x/y は負値可（マルチモニタ）、
    w/h は 200〜10000 に制限する。"""
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(_is_int(v) for v in value)
        and 200 <= value[2] <= 10000
        and 200 <= value[3] <= 10000
    )


def _is_column_widths(value: object) -> bool:
    """テーブル列幅リスト。正の int（10〜2000 px）のみ、最大 30 要素。"""
    return (
        isinstance(value, list)
        and 0 < len(value) <= 30
        and all(_is_int(v) and 10 <= v <= 2000 for v in value)
    )


# 設定キー → 許容値集合 または 検証 callable。load/save 両方の検証に用いる
# （評価額/配当タブで独立）。集合キーの検証挙動は従来と互換。
_SETTINGS_VALIDATORS: dict[str, set | Callable[[object], bool]] = {
    "dividend_mode": _VALID_DIVIDEND_MODES,
    "withdrawal_mode": _VALID_WITHDRAWAL_MODES,
    "asset_chart_type": _VALID_CHART_TYPES,
    "income_chart_type": _VALID_CHART_TYPES,
    "asset_trend_period": _VALID_TREND_PERIODS,
    "income_trend_period": _VALID_TREND_PERIODS,
    "asset_trend_baseline": _VALID_BASELINES,
    "income_trend_baseline": _VALID_BASELINES,
    # 収入グラフ（配当推移・月別配当）に優待を含めるか。取崩の有無は
    # withdrawal_mode が唯一の入口なので専用キーを持たない。
    "income_benefit": _VALID_INCLUDE,
    # 簿価/時価利回りの集計対象（投信込み / 投信除く）。効くのは KPI カード2枚と
    # Markdown サマリーだけで、行データ・グラフ・履歴には影響しない。
    "yield_scope": set(_YIELD_SCOPES),
    # ウィンドウ状態の永続化（GUI が closeEvent / タブ切替時に保存する）
    "window_geometry": _is_geometry,
    # タブは [評価額, 配当金, 月別配当, 株価詳細, 配当優待詳細] の5枚。
    "last_tab": lambda v: _is_int(v) and 0 <= v <= 4,
    "pie_threshold_asset": lambda v: _is_number(v) and 0 <= v <= 20,
    "pie_threshold_income": lambda v: _is_number(v) and 0 <= v <= 20,
    "column_widths_price": _is_column_widths,
    "column_widths_income": _is_column_widths,
    # 無効（active=false）銘柄をテーブルに表示するか。既定 False。
    "show_inactive": lambda v: isinstance(v, bool),
    # 表示テーマ（ダーク / ライト）。許容値は theme.THEMES が唯一の定義元。
    "theme": set(_THEMES),
    # 投信の口数を「万口単位（口数÷10,000）」と「1口単位」のどちらで
    # 表示・入力するか。CSV の shares は常に万口単位で、この設定は表示層と
    # 入力欄だけを変換する（calc の変換ヘルパー参照）。
    "fund_share_unit": set(_FUND_SHARE_UNITS),
}


def _is_valid_setting(key: str, value: object) -> bool:
    """設定キーと値の組が許容されるか判定する（未知キーは常に拒否）。"""
    validator = _SETTINGS_VALIDATORS.get(key)
    if validator is None:
        return False
    if isinstance(validator, set):
        try:
            return value in validator
        except TypeError:  # list 等の非ハッシュ値が紛れても全体を壊さない
            return False
    return bool(validator(value))


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # 各キーを検証し、妥当なものだけ採用する。
        result = {}
        for key in _SETTINGS_VALIDATORS:
            if key in data and _is_valid_setting(key, data[key]):
                result[key] = data[key]
        return result
    except Exception:
        return {}


def save_settings(**updates) -> None:
    """設定を部分更新する。既存値を読み込み、検証を通った項目のみ上書きする。

    例: save_settings(dividend_mode="net") / save_settings(window_geometry=[0, 0, 1180, 760])。
    withdrawal_mode=None は正当値（取崩なし）なので、明示指定されれば保存される。
    """
    _ensure_dirs()
    data = load_settings()
    for key, value in updates.items():
        if _is_valid_setting(key, value):
            data[key] = value
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    _retry_on_permission_error(lambda: tmp_path.replace(SETTINGS_PATH), SETTINGS_PATH)
