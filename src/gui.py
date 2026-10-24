"""PySide6 ネイティブGUI（配当優待管理アプリのエントリ）。

ロジック層（io_csv / models / calc / market_data / utils）は無改変で再利用し、
表示層のみを Qt6 で再構築する。レガシー TUI は src/legacy_tui.py に温存。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal, QSortFilterProxyModel, QRect, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDesktopServices,
    QStandardItemModel,
    QStandardItem,
    QShortcut,
    QKeySequence,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QTableView,
    QLabel,
    QLineEdit,
    QDialog,
    QProgressBar,
    QAbstractItemView,
    QHeaderView,
    QDoubleSpinBox,
    QMessageBox,
    QStackedWidget,
    QSplitter,
    QFileDialog,
    QComboBox,
    QMenu,
    QCheckBox,
    QToolBar,
)

# 検証ヘルパーはダイアログと共に dialogs.py へ移設した。tests/test_gui_helpers.py が
# gui._parse_num 等を参照するため、ここで再エクスポートして互換を維持する。
from .dialogs import (  # noqa: F401 — 再エクスポート
    EditHoldingDialog,
    NewHoldingDialog,
    _non_negative,
    _optional_iso_date,
    _optional_rate,
    _parse_num,
)
# DATA_DIR は実行中に変わる（データフォルダの変更）ため、値を import して
# 固定せずモジュール経由で参照する。
from . import app_config, io_csv, shortcut, splits
from .io_csv import (
    init_empty_files,
    set_data_dir,
    copy_data_files,
    load_holdings,
    save_holdings,
    load_market_data,
    save_market_data,
    load_fx,
    save_fx,
    load_settings,
    save_settings,
    load_history,
    save_history,
    export_holdings_csv,
    export_history_csv,
    write_text_export,
    read_history_csv,
    read_holdings_csv,
)
from .md_export import build_holdings_markdown
from .models import FXRate, Holding, HistoryRecord
from .calc import (
    DEFAULT_FUND_SHARE_UNIT,
    FUND_SHARE_UNITS,
    FUND_SHARE_UNIT_COLUMN_LABELS,
    FUND_SHARE_UNIT_LABELS,
    compute_rows,
    latest_fx,
    monthly_breakdown,
    to_display_shares,
    summary_yields,
    row_yield_keys,
    ASSET_CLASSES,
    ASSET_CLASS_LABELS,
    DEFAULT_ASSET_SCOPE,
    asset_class,
    asset_scope_label,
    is_all_asset_scope,
    normalize_asset_scope,
    scope_holdings,
    YIELD_BENEFIT_SUFFIX,
)
from .history import (
    INCOME_BASE_KEYS,
    upsert_record,
    merge_records,
    combine_income,
    class_breakdown,
    scope_history_record,
    scoped_history_start,
)
# market_data は yfinance / ToushinReader / pandas を芋づる式に読み込み起動を数秒遅らせる。
# 取得は p / u キーのワーカースレッドでしか走らないため、import を run() 内へ遅延させる。
# 配色はテーマ切替で張り替わる「現在値」なので、値で import せず theme.X で
# 参照する（値 import すると切替後も旧テーマの色を使い続ける）。
from . import theme
from .utils import (
    fmt_number,
    fmt_percent,
    now_jst,
    now_jst_iso,
    safe_float,
    today_jst_str,
)
from .widgets import (
    BarChartWidget,
    TreemapWidget,
    PieChartWidget,
    LineChartWidget,
    KpiCard,
    profit_text_qcolor,
)


# 同一 symbol が複数行（複数口座・取得単価）になり得るため、口座・種別を
# コード/銘柄名の直後に置いて行を判別できるようにする。両テーブルで
# 0=symbol / 1=name / 2=account の並びを揃える（NumericSortProxy の絞り込みが
# この列位置を前提にする）。
PRICE_COLUMNS = [
    ("コード", "symbol"),
    ("銘柄名", "name"),
    ("口座", "account"),
    # 未反映の株式分割の旗。列 0/1/2 は絞り込みが前提なので、その後ろへ置く。
    ("分割", "split_flag"),
    ("種別", "asset_type"),
    ("株数", "shares"),
    ("通貨", "currency"),
    ("購入単価", "avg_cost"),
    ("現在株価", "last_price"),
    ("簿価(円)", "book_value_jpy"),
    ("時価(円)", "market_value_jpy"),
    ("損益(円)", "profit_jpy"),
    ("損益率", "profit_rate"),
]
INCOME_COLUMNS = [
    ("コード", "symbol"),
    ("銘柄名", "name"),
    ("口座", "account"),
    ("分割", "split_flag"),
    ("種別", "asset_type"),
    ("株数", "shares"),
    ("通貨", "currency"),
    ("配当/株", "annual_div_ps"),
    ("年間配当(円)", "annual_dividend_jpy"),
    ("優待(円)", "annual_benefit_jpy"),
    # 優待内容は金額の直後（説明と額を離すと同じ行でも対応が読み取りにくい）。
    # 列 0/1/2 は NumericSortProxy の絞り込みが前提にしているので手前へ入れない。
    ("優待内容", "benefit_detail"),
    ("簿価利回り", "yield_on_cost"),
    ("時価利回り", "yield_on_value"),
    ("配当更新日", "div_forecast_as_of"),
]
# 資産種別の表示名（CSV 上の値 → 日本語ラベル）
ASSET_TYPE_LABELS = {"stock": "株式", "fund": "投信"}
# 収入モードの選択肢。内部表現の None を "none" キーで表す（ドロップダウン用）。
WITHDRAWAL_CHOICES = [("none", "配当"), ("fixed", "定額取崩"), ("rate", "定率取崩")]
# 収入モード → 表示ラベル（情報バー・KPI タイトル用）
WITHDRAWAL_LABELS = {None: "配当", "fixed": "定額取崩", "rate": "定率取崩"}
# 税引後モードで配当推移グラフの操作行へ出す注記（履歴は税引前で記録している）
NET_TREND_NOTE = "※推移は税引前で記録しています"
# チャートの系列色はテーマで変わるため、モジュール定数に QColor を焼き込まず
# 描画のたびに theme から作る（定数にすると切替後も旧テーマの色で描かれる）。


def asset_trend_series() -> list[tuple[str, str, QColor]]:
    """評価額タブの推移系列 (ラベル, history キー, 線色)。評価額＋簿価の2本固定。"""
    return [
        ("評価額", "total_market_value_jpy", QColor(theme.SERIES_VALUE)),
        ("簿価", "total_book_value_jpy", QColor(theme.SERIES_BOOK)),
    ]


def income_trend_color() -> QColor:
    """配当タブの推移は優待/取崩の選択で合成した単一系列（_refresh_trend で構築）。"""
    return QColor(theme.SERIES_INCOME)


def monthly_series() -> list[tuple[str, str, QColor]]:
    """月別配当タブの積み上げ系列 (ラベル, バケットのキー, 塗り色)。下から順に積む。

    緑は配当推移の線が使っているため、優待は橙にして系列の取り違えを避ける。
    """
    return [
        ("配当", "dividend_jpy", QColor(theme.SERIES_DIVIDEND)),
        ("優待", "benefit_jpy", QColor(theme.SERIES_BENEFIT)),
    ]


# 月別内訳リストの列。編集導線を持たない読み取り専用テーブル。
MONTHLY_COLUMNS = [
    ("コード", "symbol"),
    ("銘柄名", "name"),
    ("口座", "account"),
    ("配当(円)", "dividend_jpy"),
    ("優待(円)", "benefit_jpy"),
    ("優待内容", "benefit_detail"),
    ("合計(円)", "total_jpy"),
]
# 優待「含まない」で伏せる列と、そのときの並び替え基準列。列構成そのものを
# 差し替えるとヘッダラベル・NumericSortProxy の数値列集合・並び替え列を毎回
# 組み直すことになるため、モデルは固定して setColumnHidden で見せ方だけ変える。
_MONTHLY_COL = {key: i for i, (_, key) in enumerate(MONTHLY_COLUMNS)}
MONTHLY_COL_DIVIDEND = _MONTHLY_COL["dividend_jpy"]
MONTHLY_COL_BENEFIT = _MONTHLY_COL["benefit_jpy"]
# 優待内容は優待(円) と一緒に伏せる。金額を隠したまま説明だけ残すと、
# 見出しの合計（配当のみ）と表に見えているものが食い違う。
MONTHLY_COL_BENEFIT_DETAIL = _MONTHLY_COL["benefit_detail"]
MONTHLY_COL_TOTAL = _MONTHLY_COL["total_jpy"]
# 自由記述の列（長さが青天井になり得る）。表は setStretchLastSection(False) で
# 内容幅のまま並べる方針なので、resizeColumnsToContents に任せると長い優待内容が
# 1件あるだけで右側の数値列が画面外へ押し出される（実測: 35文字で 426px）。
# 初期幅だけ上限を当て、手動で広げる余地とツールチップでの全文表示を残す。
FREE_TEXT_COLUMNS = {"benefit_detail"}
FREE_TEXT_COLUMN_MAX_WIDTH = 280

NUMERIC_SORT_COLUMNS = {
    "split_flag",
    "shares",
    "avg_cost",
    "last_price",
    "annual_div_ps",
    "annual_dividend_jpy",
    "annual_benefit_jpy",
    "book_value_jpy",
    "market_value_jpy",
    "yield_on_cost",
    "yield_on_value",
    "yield_on_cost_div_only",
    "yield_on_value_div_only",
    "profit_jpy",
    "profit_rate",
    "dividend_jpy",
    "benefit_jpy",
    "total_jpy",
}


# 数値として並び替えるが、文字列を表示するので右寄せにしない列。
LEFT_ALIGNED_SORT_COLUMNS = {"split_flag"}


def clamp_free_text_columns(view: QTableView, columns: list[tuple[str, str]]) -> None:
    """自由記述列の幅を上限で頭打ちにする（自動調整の直後にだけ呼ぶ）。

    保存済みの列幅を復元した場合は呼ばない——ユーザーが広げた幅を起動のたびに
    縮めることになる。
    """
    for index, (_label, key) in enumerate(columns):
        if key in FREE_TEXT_COLUMNS and view.columnWidth(index) > FREE_TEXT_COLUMN_MAX_WIDTH:
            view.setColumnWidth(index, FREE_TEXT_COLUMN_MAX_WIDTH)


def row_for_holding_index(stored_indices: list[int], holding_idx: int) -> int | None:
    """row_holding_indices のリストから該当 holdings インデックスのソース行を探す。

    見つからない場合（削除直後など）は None を返す。
    """
    try:
        return stored_indices.index(holding_idx)
    except ValueError:
        return None


def cell_sort_value(
    row: dict, key: str, fund_share_unit: str = DEFAULT_FUND_SHARE_UNIT
) -> Optional[float]:
    """数値列の並び替えに使う生値。株数だけは表示単位へ揃える。

    表示と別の値で並べると「上の行の方が数字が小さい」という見た目になるため、
    投信を1口単位で表示しているときは並び替えも 1口単位の値で行う（株式の株数と
    投信の口数が同じ列に混ざるのは単位設定に関わらず元からの性質）。
    """
    if key == "split_flag":
        # 旗のある行だけ値を持たせる（値の無い行は昇順・降順どちらでも末尾へ沈む）。
        if row.get("pending_splits"):
            return 1.0
        return 2.0 if row.get("applied_splits") else None
    value = safe_float(row.get(key))
    if key == "shares":
        return to_display_shares(value, row.get("asset_type"), fund_share_unit)
    return value


def _format_cell(
    row: dict, key: str, fund_share_unit: str = DEFAULT_FUND_SHARE_UNIT
) -> tuple[str, Optional[QColor]]:
    """セル表示文字列と文字色を返す。

    無効（active=False）銘柄は集計対象外なので、損益の赤青より優先して
    行全体を淡色にする。
    """
    text, color = _cell_text_and_color(row, key, fund_share_unit)
    if row.get("active") is False:
        return text, QColor(theme.TEXT_DISABLED)
    return text, color


def _cell_text_and_color(
    row: dict, key: str, fund_share_unit: str = DEFAULT_FUND_SHARE_UNIT
) -> tuple[str, Optional[QColor]]:
    """有効銘柄としての表示文字列と文字色（損益のみ赤青）を返す。"""
    val = row.get(key)
    if key == "split_flag":
        events = row.get("pending_splits") or []
        if events:
            return splits.flag_text(events), QColor(theme.SPLIT_FLAG)
        # 反映済みにした直後は旗を消さず、色と印を変えて残す（押したことが画面で読める）。
        done = row.get("applied_splits") or []
        if done:
            return splits.done_flag_text(done), QColor(theme.SPLIT_DONE)
        return "", None
    if key == "shares":
        # 投信の口数は表示単位で見せる（保存値は常に万口単位）。
        return fmt_number(
            to_display_shares(safe_float(val), row.get("asset_type"), fund_share_unit),
            2,
        ), None
    if key == "asset_type":
        text = str(val or "")
        return ASSET_TYPE_LABELS.get(text, text), None
    if key in (
        "symbol", "name", "account", "currency", "div_forecast_as_of", "benefit_detail",
    ):
        return str(val or ""), None
    if key in ("avg_cost", "last_price", "annual_div_ps"):
        return fmt_number(val, 2), None
    if key in ("annual_dividend_jpy", "annual_benefit_jpy", "book_value_jpy", "market_value_jpy"):
        return fmt_number(val, 0), None
    if key == "profit_jpy":
        return fmt_number(val, 0), profit_text_qcolor(val)
    if key == "profit_rate":
        return fmt_percent(val, 2), profit_text_qcolor(val)
    if key in (
        "yield_on_cost", "yield_on_value",
        "yield_on_cost_div_only", "yield_on_value_div_only",
    ):
        return fmt_percent(val, 2), None
    return str(val or ""), None


def _format_import_holdings_preview(
    holdings: list[Holding], max_rows: int = 5
) -> str:
    """保有 CSV 取込の確認ダイアログに出す先頭数行のプレビュー文字列を組む。

    投信の口数単位（fund_share_unit）の変換は掛けず CSV 保存値のまま表示する。
    """
    lines: list[str] = []
    for h in holdings[:max_rows]:
        name = h.name or "（名称なし）"
        shares = f"{h.shares:,.4f}".rstrip("0").rstrip(".") or "0"
        book = h.shares * h.avg_cost
        if h.currency == "USD":
            book_str = f"${fmt_number(book, 2)}"
        else:
            book_str = f"¥{fmt_number(book, 0)}"
        lines.append(f"・{h.symbol} {name}: 株数 {shares} / 簿価 {book_str}")
    rest = len(holdings) - len(lines)
    if rest > 0:
        lines.append(f"…ほか {rest} 件")
    return "\n".join(lines)


class NumericSortProxy(QSortFilterProxyModel):
    """数値列は Qt.UserRole の生値で比較（桁区切り文字列の誤ソート回避）。

    数値が無い行は legacy `_sort_rows` と同様、昇順/降順いずれでも末尾へ。
    """

    def __init__(self, numeric_columns: set[int], parent=None) -> None:
        super().__init__(parent)
        self._numeric_columns = numeric_columns
        self._filter_text = ""

    def set_filter_text(self, text: str) -> None:
        """コード(col0)・銘柄名(col1)・口座(col2)への部分一致フィルタ語を設定する。"""
        self._filter_text = (text or "").strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent) -> bool:  # noqa: N802 (Qt API)
        if not self._filter_text:
            return True
        model = self.sourceModel()
        for col in (0, 1, 2):  # symbol / name / account（両テーブル共通の並び）
            idx = model.index(source_row, col, source_parent)
            value = model.data(idx, Qt.ItemDataRole.DisplayRole) or ""
            if self._filter_text in str(value).lower():
                return True
        return False

    def lessThan(self, left, right) -> bool:  # noqa: N802 (Qt API)
        if left.column() in self._numeric_columns:
            lv = self.sourceModel().data(left, Qt.ItemDataRole.UserRole)
            rv = self.sourceModel().data(right, Qt.ItemDataRole.UserRole)
            if lv is None and rv is None:
                return False
            if lv is None:
                return self.sortOrder() == Qt.SortOrder.DescendingOrder
            if rv is None:
                return self.sortOrder() == Qt.SortOrder.AscendingOrder
            return float(lv) < float(rv)
        return super().lessThan(left, right)


# ---- バックグラウンド取得ワーカー ----

@dataclass
class MarketUpdate:
    """F5 の取得結果。株価・配当に加え、USD 銘柄があれば同時に取った為替を載せる。

    為替だけ Ctrl+U の別操作にしていた頃は、F5 で株価だけが新しくなり、円換算は
    いつのものか分からない古いレートのまま（画面にも出ない）だった。為替の失敗は
    株価の成功を止めず fx_error に積むだけにする。
    """

    market: object  # market_data.MarketFetchResult（遅延 import のため型は書かない）
    fx: FXRate | None = None
    fx_error: str | None = None


class MarketWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self, holdings: list[Holding], market_map: dict, fetch_fx: bool = False
    ) -> None:
        super().__init__()
        self._holdings = holdings
        self._market_map = market_map
        # USD 銘柄の有無はメインスレッドで決めて渡す（ワーカーから window の
        # 状態を読まない）。
        self._fetch_fx = fetch_fx

    def run(self) -> None:
        from .market_data import fetch_market_data, fetch_usdjpy
        try:
            result = fetch_market_data(
                self._holdings,
                self._market_map,
                force=True,
                progress_cb=lambda d, t, lbl: self.progress.emit(d, t, lbl),
            )
        except Exception as exc:  # noqa: BLE001 — UI に通知して継続
            self.error.emit(f"更新に失敗しました: {exc}")
            return
        update = MarketUpdate(market=result)
        if self._fetch_fx:
            self.progress.emit(0, 0, "USDJPYレートを取得中...")
            try:
                update.fx = fetch_usdjpy()
            except Exception as exc:  # noqa: BLE001 — 株価の結果は捨てない
                update.fx_error = f"USDJPY更新に失敗しました: {exc}"
        self.finished.emit(update)


class FxWorker(QObject):
    finished = Signal(object)
    error = Signal(str)

    def run(self) -> None:
        from .market_data import fetch_usdjpy
        try:
            fx = fetch_usdjpy()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"USDJPY更新に失敗しました: {exc}")
            return
        self.finished.emit(fx)


# ---- メインウィンドウ ----

class DividendWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("配当優待管理")

        _saved = load_settings()
        # テーマは QSS・チャート配色の前提なので、ウィジェットを1つも作る前に
        # 適用する（_build_ui / KpiCard より後だと初回描画だけ旧配色になる）。
        self.theme_name: str = theme.set_theme(_saved.get("theme", theme.DEFAULT_THEME))
        self._restore_window_geometry(_saved.get("window_geometry"))
        self.dividend_mode: str = _saved.get("dividend_mode", "gross")
        self.withdrawal_mode: str | None = _saved.get("withdrawal_mode", None)
        self.asset_chart_type: str = _saved.get("asset_chart_type", "treemap")
        self.income_chart_type: str = _saved.get("income_chart_type", "treemap")
        self.asset_trend_period: str = _saved.get("asset_trend_period", "all")
        self.income_trend_period: str = _saved.get("income_trend_period", "all")
        self.asset_trend_baseline: str = _saved.get("asset_trend_baseline", "zero")
        self.income_trend_baseline: str = _saved.get("income_trend_baseline", "zero")
        # 優待を収入グラフ（配当推移・月別配当）へ含めるか。既定は配当のみ。
        # 取崩の有無は「収入」モード（withdrawal_mode）が唯一の入口なので独立の
        # 選択肢を持たない（二重に持つと表と推移で別々の状態になり食い違う）。
        self.income_benefit: str = _saved.get("income_benefit", "without")
        # 表示対象の資産クラス（日本株 / 米国株 / 投信の空でない組み合わせ）。
        # 表・KPI・構成グラフ・月別・推移・Markdown を同じ部分集合で描く。履歴の
        # 記録と CSV エクスポートは常に全件（scope_holdings を通さない）。
        self.asset_scope: tuple[str, ...] = (
            normalize_asset_scope(_saved.get("asset_scope")) or DEFAULT_ASSET_SCOPE
        )
        # 円グラフの「その他」集約閾値（評価額/配当タブで独立）と起動時の表示タブ。
        self.pie_threshold_asset: float = float(_saved.get("pie_threshold_asset", 2.0))
        self.pie_threshold_income: float = float(_saved.get("pie_threshold_income", 2.0))
        # 無効銘柄をテーブルへ表示するか。既定は非表示（従来どおり）だが、
        # 表示できないと編集ダイアログで無効化した銘柄を復帰できなくなる。
        self.show_inactive: bool = bool(_saved.get("show_inactive", False))
        # 投信の口数の見せ方・入れ方（万口単位 / 1口単位）。CSV の shares は常に
        # 万口単位で、この設定は表示と入力欄の変換だけに効く（calc の変換ヘルパー）。
        self.fund_share_unit: str = _saved.get(
            "fund_share_unit", DEFAULT_FUND_SHARE_UNIT
        )
        # 月別配当タブ: 選択月は当月から始める（前回終了時の月を復元しても翌月には
        # 陳腐化するため保存しない）。
        self.monthly_month: int = now_jst().date().month
        # 並び替えはヘッダクリックのみ。初回だけ既定（合計の降順）を当てる。
        self._monthly_sorted_once: bool = False
        self._monthly_label: str = "年間配当"
        self._monthly_columns_sized: bool = False
        self._monthly_benefit_detail_sized: bool = False
        self._initial_tab: int = _saved.get("last_tab", 0)
        # テーブル列幅: 保存値があれば初回描画で適用、無ければ内容に合わせて自動調整。
        self._saved_column_widths: dict[str, list[int]] = {
            "price": _saved.get("column_widths_price", []),
            "income": _saved.get("column_widths_income", []),
        }
        self._columns_sized: dict[str, bool] = {"price": False, "income": False}
        self.holdings: list[Holding] = []
        self.market_data_map: dict = {}
        self.fx_rate: float | None = None
        self.fx_fetched_at: str | None = None
        self.market_source: str | None = None
        self.market_fetched_at: str | None = None
        self.history: list[HistoryRecord] = []
        self._monthly = monthly_breakdown([])
        self.row_holding_indices: dict[str, list[int]] = {"price": [], "income": []}
        # メニュー・ツールバー・ショートカットが共有する QAction（_build_actions が充填）
        self._actions: dict[str, QAction] = {}
        self._busy = False
        self._thread: QThread | None = None
        self._worker: QObject | None = None

        self._build_ui()
        self._register_shortcuts()
        self._apply_title_bar_theme()

        # 設定されたデータフォルダが書けない（外付けドライブを外した・権限が無い）
        # 場合は既定フォルダへ落として必ずウィンドウを出す。ここで例外のまま落ちると
        # 「フォルダを直すメニュー」にも到達できず、config.json の手編集でしか復旧
        # できなくなる。設定ファイルは書き換えないので、復旧すれば次回起動で戻る。
        try:
            init_empty_files()
        except OSError as exc:
            failed = io_csv.DATA_DIR
            set_data_dir(app_config.DEFAULT_DATA_DIR, persist=False)
            init_empty_files()
            QMessageBox.warning(
                self,
                "データフォルダを使用できません",
                f"{failed}\nを使用できませんでした（{exc}）。\n"
                f"既定のフォルダ {io_csv.DATA_DIR} で起動します。\n"
                "「ファイル」→「データフォルダの変更...」で設定し直せます。",
            )
        self._reload_all()

    def _restore_window_geometry(self, geometry: list | None) -> None:
        """保存済みのウィンドウ位置・サイズを復元する（無ければ既定サイズ）。

        モニタ構成の変更などで保存位置がどの画面にも掛からない場合は、
        位置を捨ててサイズのみ適用する（画面外に出て操作不能になるのを防ぐ）。
        """
        # ツールバー・KPI・表が読める下限。これ未満に縮めると各所が潰れる。
        self.setMinimumSize(900, 600)
        if not geometry:
            self.resize(1180, 760)
            return
        x, y, w, h = geometry
        rect = QRect(x, y, w, h)
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(rect):
                self.setGeometry(rect)
                return
        self.resize(w, h)

    def _save_window_state(self) -> None:
        """ウィンドウジオメトリと列幅を設定へ保存する（正常終了時のみ呼ぶ）。"""
        g = self.geometry()
        self._save_or_notify(lambda: save_settings(
            window_geometry=[g.x(), g.y(), g.width(), g.height()],
            column_widths_price=self._column_widths(self._price_view),
            column_widths_income=self._column_widths(self._income_view),
        ))

    @staticmethod
    def _column_widths(view: QTableView) -> list[int]:
        """現在の列幅を validator の許容範囲（10〜2000px）へクランプして返す。"""
        header = view.horizontalHeader()
        return [
            min(2000, max(10, header.sectionSize(col)))
            for col in range(header.count())
        ]

    def _apply_title_bar_theme(self) -> None:
        """Windows のタイトルバーを現在のテーマに合わせる（DWM API、失敗しても無害）。

        QSS はクライアント領域までしか効かないため、タイトルバーだけ別 API で
        切り替える。テーマ変更のたびに呼び直すこと（呼ばないと本体がライトでも
        タイトルバーだけ黒いままになる）。
        """
        try:
            import ctypes

            hwnd = int(self.winId())
            value = ctypes.c_int(1 if theme.is_dark() else 0)
            # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（Win10 20H1 以降 / Win11）
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:  # noqa: BLE001 — 非 Windows や旧ビルドでは何もしない
            pass

    # ---- UI 構築 ----
    def _build_ui(self) -> None:
        # 配色・フォント等のトークンと QSS 生成は src/theme.py に集約。
        # ダーク / ライトは「表示」→「テーマ」で切り替える（action_set_theme が
        # ここと同じ build_app_qss() を再適用する）。
        self.setStyleSheet(theme.build_app_qss())
        # QAction はメニュー・ツールバー・ショートカットの共有元なので最初に作る。
        self._build_actions()
        self._build_menubar()
        self._build_toolbar()
        central = QWidget()
        root = QVBoxLayout(central)

        # 1行目＝ポートフォリオ資産の要約、2行目＝収入の内訳と利回り。
        # 「合計」は年間配当＋優待の合算なので収入側（2行目）へ置く。
        # 4枚と5枚では行をまたいだ列境界を揃えられないため、グリッドではなく
        # 行ごとの QHBoxLayout にして行内を均等割りする。
        row_defs = (
            ("評価額計", "簿価計", "損益", "損益率"),
            ("年間配当", "優待", "合計", "簿価利回り", "時価利回り"),
        )
        kpi_bar = QWidget()
        outer = QVBoxLayout(kpi_bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)  # 行間。省くと Qt 既定の ~11px になり2行が離れて見える
        self._cards: dict[str, KpiCard] = {}
        for titles in row_defs:
            row = QHBoxLayout()
            row.setSpacing(6)  # カード間
            for title in titles:
                card = KpiCard(title)
                self._cards[title] = card
                row.addWidget(card, 1)  # 伸縮1で行内を均等割り
            outer.addLayout(row)
        root.addWidget(kpi_bar)

        self._tabs = QTabWidget()
        self._tabs.setCornerWidget(self._build_filter_box(), Qt.Corner.TopRightCorner)
        # 評価額タブ（左=構成グラフ[ヒートマップ⇔円グラフ] / 右=評価額推移）
        self._treemap = TreemapWidget()
        self._piechart = PieChartWidget()
        self._asset_line = LineChartWidget()
        asset_tab = self._make_dashboard_tab(
            kind="asset",
            treemap=self._treemap,
            piechart=self._piechart,
            line_chart=self._asset_line,
        )
        self._tabs.addTab(asset_tab, "評価額")
        # 配当金タブ（左=構成グラフ / 右=配当推移）
        self._treemap_div = TreemapWidget()
        self._piechart_div = PieChartWidget()
        self._income_line = LineChartWidget()
        income_tab = self._make_dashboard_tab(
            kind="income",
            treemap=self._treemap_div,
            piechart=self._piechart_div,
            line_chart=self._income_line,
        )
        self._tabs.addTab(income_tab, "配当金")
        # 株価 / 配当テーブルタブ
        self._price_view, self._price_model, self._price_proxy = self._make_table(PRICE_COLUMNS)
        self._income_view, self._income_model, self._income_proxy = self._make_table(INCOME_COLUMNS)
        # 月別配当タブ（上=支払月ごとの積み上げ棒 / 下=選択月の銘柄別内訳）。
        # 生成はテーブル生成の後（内訳ビルダが表と同じ書式ヘルパーを使う）だが、
        # 並びはグラフ系3枚 → 明細2枚になるよう表より手前へ挿す。
        monthly_tab = self._make_monthly_tab()
        self._tabs.addTab(monthly_tab, "月別配当")
        self._tabs.addTab(self._price_view, "株価詳細")
        self._tabs.addTab(self._income_view, "配当優待詳細")
        # 前回終了時のタブを復元してからシグナル接続する（起動時の無駄な保存を避ける）。
        if 0 <= self._initial_tab < self._tabs.count():
            self._tabs.setCurrentIndex(self._initial_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs, 1)

        self.setCentralWidget(central)

        # 恒久情報（レート・取得時刻・未算出件数）はステータスバー右側へ置き、
        # 一時通知は左側の showMessage が使う（Qt の作法どおり領域を分ける）。
        # 画面上部の情報ラベル行が無くなるぶん、表とグラフに縦幅を回せる。
        self._info = QLabel("")
        self._info.setObjectName("infoLabel")
        self.statusBar().addPermanentWidget(self._info)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(260)
        self._progress.setVisible(False)
        self.statusBar().addPermanentWidget(self._progress)

    # (キー, ラベル, ショートカット, スロット属性名) — メニュー・ツールバー・
    # ショートカットの唯一の定義元。ショートカットは QAction に持たせるため、
    # ラベルへ手書きでキーを書かない（メニューが自動表示する）。
    _ACTION_DEFS = [
        ("update_market", "株価・配当・為替を取得", "F5", "action_update_market"),
        ("update_fx", "為替取得（USDJPY）", "Ctrl+U", "action_update_fx"),
        ("reload", "再読込", "Ctrl+R", "_reload_all"),
        ("new", "新規登録...", "Ctrl+N", "action_new_holding"),
        ("edit", "編集...", "Ctrl+E", "action_edit"),
        ("delete", "削除", "Ctrl+D", "action_delete"),
        # 株式分割の補正。旗（「分割」列）が立った行に対して押す。自動では書き換えない
        # （証券会社の反映時期とずれ、手で直済みの行に自動適用すると二重適用になる）。
        ("split_apply", "分割を反映...", "Ctrl+Shift+S", "action_apply_split"),
        ("split_dismiss", "分割を反映済みにする（数値は変えない）", None,
         "action_dismiss_split"),
        ("split_apply_all", "未反映の分割をすべて反映...", None,
         "action_apply_all_splits"),
        ("toggle_mode", "税引前 / 税引後 の切替", "Ctrl+M", "action_toggle_mode"),
        ("toggle_withdrawal", "収入モードの切替", "Ctrl+I", "action_toggle_withdrawal"),
        # 形式名はサブメニュー名ではなく項目側に付ける（エクスポートに CSV と
        # Markdown が並ぶため。サブメニュー名を "エクスポート (CSV)" のままに
        # すると Markdown が CSV の下に入って読めない）。
        ("export_holdings", "保有銘柄 (CSV)...", None, "action_export_holdings"),
        ("export_history", "推移履歴 (CSV)...", None, "action_export_history"),
        ("export_holdings_md", "保有銘柄 (Markdown)...", None,
         "action_export_holdings_markdown"),
        ("import_holdings", "保有銘柄 (CSV)...", None, "action_import_holdings"),
        ("import_history", "推移履歴 (CSV)...", None, "action_import_history"),
        ("change_data_dir", "データフォルダの変更...", None, "action_change_data_dir"),
        ("open_data_dir", "データフォルダを開く", None, "action_open_data_dir"),
        ("create_shortcut", "デスクトップにショートカットを作成...", None,
         "action_create_shortcut"),
        ("focus_filter", "絞り込みへ移動", "Ctrl+F", "_focus_filter"),
        ("help_shortcuts", "キーボードショートカット一覧...", "F1", "_show_shortcut_help"),
        ("quit", "終了", "Ctrl+Q", "close"),
    ]
    # タブ切替（Ctrl+1〜5）。ラベルは QTabWidget のタブ名と揃える。
    _TAB_ACTION_LABELS = ["評価額", "配当金", "月別配当", "株価詳細", "配当優待詳細"]

    def _build_actions(self) -> None:
        """メニュー・ツールバー・ショートカットが共有する QAction を生成する。

        同じ操作をメニュー・ボタン・ショートカットで別々に定義すると有効/無効を
        同期できず「押せるのに何も起きないボタン」が生まれる。QAction を唯一の
        定義元にして、3者が同じインスタンスを参照する。

        ショートカットは全て修飾キー付き（Ctrl / F キー）。一文字キーだと表で
        銘柄コードを打つだけで誤爆するため、入力中ガードごと廃止した。
        """
        for key, label, sequence, slot_name in self._ACTION_DEFS:
            action = QAction(label, self)
            if sequence:
                action.setShortcut(QKeySequence(sequence))
            # triggered は checked(bool) を渡すため、引数なしスロットへはラムダで吸収する。
            action.triggered.connect(
                lambda _checked=False, name=slot_name: getattr(self, name)()
            )
            self.addAction(action)  # ウィンドウ配下でショートカットを有効化
            self._actions[key] = action

        # ショートカット作成は Windows 専用。可否がプラットフォームだけで決まり
        # 実行中に変わらないので、_sync_action_states ではなくここで一度伏せる。
        self._actions["create_shortcut"].setEnabled(shortcut.is_supported())

        # 無効銘柄トグルはチェック状態を持たせ、現在値をメニュー上で示す。
        inactive = QAction("無効銘柄も表示", self)
        inactive.setShortcut(QKeySequence("Ctrl+Shift+I"))
        inactive.setCheckable(True)
        inactive.setChecked(self.show_inactive)
        inactive.triggered.connect(lambda _checked=False: self.action_toggle_inactive())
        self.addAction(inactive)
        self._actions["toggle_inactive"] = inactive

        # 表示対象の資産クラス。複数選択なので排他グループにせず、1つずつ
        # チェックを持たせる。表示メニューとツールバーのボタンが同じ QAction を
        # 共有する（最後の1つは _sync_asset_scope_controls が押せなくする）。
        for cls in ASSET_CLASSES:
            action = QAction(ASSET_CLASS_LABELS[cls], self)
            action.setCheckable(True)
            action.setChecked(cls in self.asset_scope)
            action.triggered.connect(
                lambda checked=False, c=cls: self.action_toggle_asset_class(c, checked)
            )
            self.addAction(action)
            self._actions[f"asset_{cls}"] = action
        show_all = QAction("すべて表示", self)
        show_all.triggered.connect(
            lambda _checked=False: self._set_asset_scope(ASSET_CLASSES)
        )
        self.addAction(show_all)
        self._actions["asset_all"] = show_all

        # テーマ（ダーク / ライト）は排他選択。QActionGroup に入れて現在の
        # テーマだけにチェックが付くようにする（トグル1個だと「今どちらか」が
        # メニューを開いても読み取れない）。
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for name in theme.THEMES:
            action = QAction(theme.theme_label(name), self)
            action.setCheckable(True)
            action.setChecked(name == self.theme_name)
            action.setActionGroup(self._theme_group)
            action.triggered.connect(
                lambda _checked=False, n=name: self.action_set_theme(n)
            )
            self.addAction(action)
            self._actions[f"theme_{name}"] = action

        # 投信の口数単位も排他選択（テーマと同じ作り）。トグル1個にすると
        # 「今どちらの単位で表示されているか」がメニューを開いても読めない。
        self._fund_unit_group = QActionGroup(self)
        self._fund_unit_group.setExclusive(True)
        for unit in FUND_SHARE_UNITS:
            action = QAction(FUND_SHARE_UNIT_LABELS[unit], self)
            action.setCheckable(True)
            action.setChecked(unit == self.fund_share_unit)
            action.setActionGroup(self._fund_unit_group)
            action.triggered.connect(
                lambda _checked=False, u=unit: self.action_set_fund_share_unit(u)
            )
            self.addAction(action)
            self._actions[f"fund_unit_{unit}"] = action

        for index, label in enumerate(self._TAB_ACTION_LABELS):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(
                lambda _checked=False, i=index: self._tabs.setCurrentIndex(i)
            )
            self.addAction(action)
            self._actions[f"tab{index}"] = action

    def _build_menubar(self) -> None:
        """メニューバーを構築する（項目は _build_actions の QAction を共有）。

        メニュータイトルの &F 等は Alt 系ニーモニック。各項目のショートカットは
        QAction 側に設定済みで、Qt が右端へ自動表示する。
        """
        bar = self.menuBar()
        act = self._actions

        file_menu = bar.addMenu("ファイル(&F)")
        export_menu = file_menu.addMenu("エクスポート")
        export_menu.addAction(act["export_holdings"])
        export_menu.addAction(act["export_history"])
        export_menu.addSeparator()
        export_menu.addAction(act["export_holdings_md"])
        import_menu = file_menu.addMenu("インポート")
        import_menu.addAction(act["import_holdings"])
        import_menu.addAction(act["import_history"])
        file_menu.addSeparator()
        file_menu.addAction(act["change_data_dir"])
        file_menu.addAction(act["open_data_dir"])
        file_menu.addSeparator()
        file_menu.addAction(act["create_shortcut"])
        file_menu.addSeparator()
        file_menu.addAction(act["quit"])

        data_menu = bar.addMenu("データ(&D)")
        data_menu.addAction(act["update_market"])
        data_menu.addAction(act["update_fx"])
        data_menu.addAction(act["reload"])
        data_menu.addSeparator()
        data_menu.addAction(act["new"])
        data_menu.addAction(act["edit"])
        data_menu.addAction(act["delete"])
        data_menu.addSeparator()
        data_menu.addAction(act["split_apply"])
        data_menu.addAction(act["split_dismiss"])
        data_menu.addAction(act["split_apply_all"])

        view_menu = bar.addMenu("表示(&V)")
        view_menu.addAction(act["toggle_mode"])
        view_menu.addAction(act["toggle_withdrawal"])
        view_menu.addAction(act["toggle_inactive"])
        view_menu.addMenu(self._build_asset_scope_menu())
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("テーマ")
        for name in theme.THEMES:
            theme_menu.addAction(act[f"theme_{name}"])
        unit_menu = view_menu.addMenu("投信の口数単位")
        for unit in FUND_SHARE_UNITS:
            unit_menu.addAction(act[f"fund_unit_{unit}"])
        view_menu.addSeparator()
        view_menu.addAction(act["focus_filter"])
        view_menu.addSeparator()
        for index in range(len(self._TAB_ACTION_LABELS)):
            view_menu.addAction(act[f"tab{index}"])

        help_menu = bar.addMenu("ヘルプ(&H)")
        help_menu.addAction(act["help_shortcuts"])

    def _build_asset_scope_menu(self) -> QMenu:
        """表示メニューの「表示対象の資産」サブメニュー。

        ツールバー側はチェックボックスで、状態は _sync_asset_scope_controls が
        QAction と同時に揃える。
        """
        menu = QMenu("表示対象の資産", self)
        for cls in ASSET_CLASSES:
            menu.addAction(self._actions[f"asset_{cls}"])
        menu.addSeparator()
        menu.addAction(self._actions["asset_all"])
        return menu

    def _show_shortcut_help(self) -> None:
        """キーボードショートカットの一覧を表示する（F1）。"""
        QMessageBox.information(
            self,
            "キーボードショートカット",
            "F5: 株価・配当・為替を取得（為替は USD 銘柄があるとき）\n"
            "Ctrl+U: 為替取得（USDJPY）\n"
            "Ctrl+R: 再読込（ローカルCSV）\n"
            "\n"
            "Ctrl+N: 新規登録\n"
            "Ctrl+E: 選択銘柄の編集（ダブルクリック / Enter でも可）\n"
            "Ctrl+D / Delete: 選択銘柄の削除\n"
            "Ctrl+Shift+S: 選択銘柄の株式分割を反映（「分割」列に旗が立った行）\n"
            "\n"
            "Ctrl+M: 税引前 / 税引後 の切替\n"
            "Ctrl+I: 収入モードの切替（配当 → 定額取崩 → 定率取崩）\n"
            "Ctrl+Shift+I: 無効銘柄の表示切替\n"
            "\n"
            "Ctrl+1〜5: タブ切替（評価額 / 配当金 / 月別配当 / 株価詳細 / 配当優待詳細）\n"
            "Ctrl+F: 絞り込みボックスへ移動\n"
            "Esc: 絞り込みのクリア\n"
            "F1: このヘルプ\n"
            "Ctrl+Q: 終了\n"
            "\n※列のソートはヘッダークリック、Tab キーで各操作へ移動できます。",
        )

    def _make_table(self, columns: list[tuple[str, str]]):
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels([label for label, _ in columns])
        numeric_cols = {i for i, (_, key) in enumerate(columns) if key in NUMERIC_SORT_COLUMNS}
        proxy = NumericSortProxy(numeric_cols)
        proxy.setSourceModel(model)
        view = QTableView()
        view.setModel(proxy)
        view.setSortingEnabled(True)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setAlternatingRowColors(True)
        header = view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # どの列も引き伸ばさず内容幅のまま並べる。最終列（損益率）を伸ばすと
        # 損益(円) との間に数百px の空白ができ、1行を読むのに視線が大きく飛ぶ。
        # 余りは表の右端に残す＝行の途中に空洞を作らない方が圧倒的に読みやすい
        # （実データ 1536px 幅で描き比べて決定）。列幅は各自ドラッグで調整できる。
        header.setStretchLastSection(False)
        view.verticalHeader().setVisible(False)
        view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        view.doubleClicked.connect(lambda _idx: self.action_edit())
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(
            lambda pos, v=view: self._show_table_context_menu(v, pos)
        )
        # 表の上でだけ効くキー（WidgetShortcut）。Delete/Enter は GUI の標準操作だが、
        # ウィンドウ全体に効かせるとグラフタブや入力欄で誤爆するため文脈を絞る。
        for sequence, slot in (
            (QKeySequence(Qt.Key.Key_Delete), self.action_delete),
            (QKeySequence(Qt.Key.Key_Return), self.action_edit),
            (QKeySequence(Qt.Key.Key_Enter), self.action_edit),
        ):
            shortcut = QShortcut(sequence, view, activated=slot)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        # 選択が変わるたびに編集・削除の可否を更新する。
        view.selectionModel().selectionChanged.connect(
            lambda *_args: self._sync_action_states()
        )
        return view, model, proxy

    def _show_table_context_menu(self, view: QTableView, pos) -> None:
        """テーブルの右クリックメニュー（編集/削除/新規登録）を表示する。

        右クリックされた行を選択してから表示するため、既存の action_edit /
        action_delete（現在選択行を対象とする）がそのまま機能する。
        """
        idx = view.indexAt(pos)
        if idx.isValid():
            view.selectRow(idx.row())
        # メニュー・ツールバーと同じ QAction を並べる。選択に応じた有効/無効は
        # selectionChanged → _sync_action_states が済ませているため、ここでは
        # 状態を触らない。項目の実行は QAction の triggered が直接担う。
        menu = QMenu(self)
        menu.addAction(self._actions["edit"])
        menu.addAction(self._actions["delete"])
        menu.addSeparator()
        menu.addAction(self._actions["split_apply"])
        menu.addAction(self._actions["split_dismiss"])
        menu.addSeparator()
        menu.addAction(self._actions["new"])
        menu.exec(view.viewport().mapToGlobal(pos))

    def _make_pie_tab(self, kind: str, piechart: PieChartWidget) -> QWidget:
        """閾値スピンボックス付きの円グラフコンテナを生成して返す。

        kind は "asset" / "income"。閾値は self.pie_threshold_{kind} から
        初期化し、変更を piechart.set_threshold と設定保存の両方へ流す。
        評価額用・配当用それぞれが独立した閾値を持てるよう、チャート単位で生成する。
        """
        initial = (
            self.pie_threshold_asset if kind == "asset" else self.pie_threshold_income
        )
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.addWidget(QLabel("その他にまとめる閾値:"))
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 20.0)
        spin.setValue(initial)
        spin.setSuffix(" %")
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setFixedWidth(100)
        row.addWidget(spin)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(piechart, 1)
        piechart.set_threshold(initial)

        def on_threshold(value: float) -> None:
            piechart.set_threshold(value)
            if kind == "asset":
                self.pie_threshold_asset = value
                self._save_or_notify(lambda: save_settings(pie_threshold_asset=value))
            else:
                self.pie_threshold_income = value
                self._save_or_notify(lambda: save_settings(pie_threshold_income=value))

        # setValue(initial) より後に接続する（初期化時の無駄な保存を避ける）。
        spin.valueChanged.connect(on_threshold)
        return container

    def _build_toolbar(self) -> None:
        """主要アクションをツールバーへ並べる（メニューと同じ QAction を共有）。

        取得系 / 編集系をセパレータで区切り、参照操作と破壊的操作（削除）が
        同列に並ぶのを避ける。ボタンの有効/無効は QAction 側の状態に追随する
        ため、_sync_action_states の1箇所で3者まとめて制御できる。

        表示モードは2段目へ分ける。1段に詰めると最小ウィンドウ幅では収まらず、
        あふれた項目が Qt の拡張メニューへ追いやられて画面から消えてしまう。
        絞り込みはツールバーに置かない（_build_filter_box の docstring 参照）。
        """
        bar = QToolBar("操作")
        bar.setObjectName("mainToolBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        act = self._actions

        # ボタン面だけ短い名前にする（iconText。メニューは text の完全な名前のまま）。
        # 完全な名前を並べると最小幅 900px で1段目があふれ、右端の操作が拡張メニューへ
        # 消える（_check_toolbar が固定）。何をするかはツールチップで完全な名前を出す。
        act["update_market"].setIconText("株価・為替を取得")
        act["update_fx"].setIconText("為替取得")
        for key in ("update_market", "update_fx", "reload"):
            bar.addAction(act[key])
        bar.addSeparator()
        for key in ("new", "edit", "delete"):
            bar.addAction(act[key])
        bar.addSeparator()
        bar.addAction(act["split_apply"])
        # 削除は取り消せない操作なので、ホバー時の配色で他と区別する。
        delete_button = bar.widgetForAction(act["delete"])
        if delete_button is not None:
            delete_button.setObjectName("dangerAction")
        # 最頻出の操作なのでアクセント色で他と区別する（配色は theme.py の #primaryAction）。
        primary_button = bar.widgetForAction(act["update_market"])
        if primary_button is not None:
            primary_button.setObjectName("primaryAction")
        # ボタン面にはショートカットを書かないので、ツールチップで補う。
        for key in ("update_market", "update_fx", "reload", "new", "edit", "delete"):
            action = act[key]
            sequence = action.shortcut().toString()
            action.setToolTip(
                f"{action.text().rstrip('.')} ({sequence})" if sequence else action.text()
            )
        # F5 の為替は円換算に効く USD 銘柄があるときだけ取る（無ければ株価・配当のみ）。
        act["update_market"].setToolTip(
            act["update_market"].toolTip() + "\n為替（USDJPY）は USD 銘柄があるときだけ取得します"
        )
        # ボタン面は短い名前にしてあるので、何をするボタンかはツールチップで補う。
        act["split_apply"].setToolTip(
            "選択行の株式分割を、株数・購入単価・配当の上書き値へ反映します "
            f"({act['split_apply'].shortcut().toString()})。"
            "「分割」列に旗が立った行だけ押せます"
        )

        self.addToolBar(bar)
        self._toolbar = bar

        self.addToolBarBreak()
        view_bar = QToolBar("表示")
        view_bar.setObjectName("viewToolBar")
        view_bar.setMovable(False)
        view_bar.setFloatable(False)
        self._build_mode_controls(view_bar)
        self.addToolBar(view_bar)
        self._view_toolbar = view_bar

    def _build_mode_controls(self, bar: QToolBar) -> None:
        """表示モードの切替を2段目のツールバーへ配置する。

        トグルをドロップダウンにして現在のモードを常時可視にする（従来は情報
        ラベルの1行テキストにしか出ず、今どちらのモードか分からなかった）。
        Ctrl+M / Ctrl+I のサイクル操作でもここの表示が追随する。
        """
        bar.addWidget(QLabel("税引: "))
        self._mode_combo = self._make_combo(
            [("gross", "税引前"), ("net", "税引後")],
            self.dividend_mode,
            lambda key: self._set_dividend_mode(key),
        )
        bar.addWidget(self._mode_combo)
        bar.addWidget(QLabel("   収入: "))
        self._withdrawal_combo = self._make_combo(
            WITHDRAWAL_CHOICES,
            self.withdrawal_mode or "none",
            lambda key: self._set_withdrawal_mode(None if key == "none" else key),
        )
        bar.addWidget(self._withdrawal_combo)
        # 優待は税引・収入と同じ「収入の数え方」の設定なのでここへ並べる
        # （推移グラフの操作行にあった頃はタブを開かないと存在に気付けなかった）。
        bar.addWidget(QLabel("   優待: "))
        self._benefit_combo = self._make_combo(
            [("without", "含まない"), ("with", "含む")],
            self.income_benefit,
            self._on_income_benefit,
        )
        bar.addWidget(self._benefit_combo)
        # 表示対象の資産クラス。複数選択なので3つのチェックボックスを常に並べる
        # （KPI がすべて変わるので、何を選んでいるかが開かずに見える形にする）。
        # 状態は表示メニューの QAction と同じく _sync_asset_scope_controls の
        # 1箇所で揃える。clicked で受けるのは、同期時の setChecked で再発火させないため。
        bar.addWidget(QLabel("   対象: "))
        tip = (
            "表・KPI・グラフ・Markdown 出力の対象にする資産。"
            "米国株＝USD 建ての株式・ETF、投信＝資産種別が投資信託。"
            "最後の1つは外せません"
        )
        self._asset_scope_checks: dict[str, QCheckBox] = {}
        for cls in ASSET_CLASSES:
            check = QCheckBox(ASSET_CLASS_LABELS[cls])
            check.setObjectName(f"assetScope_{cls}")
            check.setToolTip(tip)
            check.clicked.connect(
                lambda checked=False, c=cls: self.action_toggle_asset_class(c, checked)
            )
            bar.addWidget(check)
            self._asset_scope_checks[cls] = check
        self._sync_asset_scope_controls()

    def _build_filter_box(self) -> QWidget:
        """絞り込み欄（ラベル＋入力）を作る。タブ見出しの右端（コーナー）に置く。

        2段目のツールバーに置いていた頃は、利回りコンボを足した時点で最小幅 900px
        に収まらなくなり（sizeHint 1131px）、右端の絞り込みが拡張メニューへ消えた。
        絞り込みは表タブにだけ効く操作なので、タブ見出しの横が意味的にも合う。
        ツールバーへ戻さないこと——コンボを1つ足すたびに同じあふれが再発する。
        """
        box = QWidget()
        box.setObjectName("filterBox")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("絞り込み:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("コード / 銘柄名 / 口座")
        self._filter.setClearButtonEnabled(True)
        # コーナーは sizeHint の幅で置かれ伸縮しない（QLineEdit の既定は狭く、
        # 実フォントでは 150px まで痩せてプレースホルダが切れた）ので幅を決め打つ。
        # 最小幅 900px でもタブ見出し（約440px）の右に 200px 以上の余白が残る。
        self._filter.setFixedWidth(220)
        # proxy は後段で生成されるが、textChanged は実行時にしか発火しないため接続順は問題ない。
        self._filter.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._filter)
        return box

    def _sync_mode_controls(self) -> None:
        """ショートカット経由でモードを変えたとき、ドロップダウンの表示を揃える。

        setCurrentIndex は activated を発火しないため、再保存・再描画のループに
        ならない（_make_combo が activated で接続しているのが前提）。
        """
        combo = getattr(self, "_mode_combo", None)
        if combo is not None:
            combo.setCurrentIndex(combo.findData(self.dividend_mode))
        combo = getattr(self, "_withdrawal_combo", None)
        if combo is not None:
            combo.setCurrentIndex(combo.findData(self.withdrawal_mode or "none"))
        self._sync_asset_scope_controls()

    def _sync_asset_scope_controls(self) -> None:
        """資産クラスの QAction とツールバーのチェックボックスのチェック・可否を揃える。

        選ばれているのが1つだけなら、それを外せなくする（空集合にすると全画面が
        空になる）。
        """
        only_one = len(self.asset_scope) == 1
        for cls in ASSET_CLASSES:
            action = self._actions.get(f"asset_{cls}")
            if action is None:
                continue
            checked = cls in self.asset_scope
            action.setChecked(checked)
            action.setEnabled(not (only_one and checked))
        show_all = self._actions.get("asset_all")
        if show_all is not None:
            show_all.setEnabled(not is_all_asset_scope(self.asset_scope))
        for cls, check in getattr(self, "_asset_scope_checks", {}).items():
            checked = cls in self.asset_scope
            check.setChecked(checked)
            check.setEnabled(not (only_one and checked))

    def _make_combo(
        self, defs: list[tuple[str, str]], current_key: str, on_select
    ) -> QComboBox:
        """(キー, ラベル) のリストからドロップダウンを生成する。

        初期選択は current_key に合わせる。activated（ユーザー操作時のみ発火・
        setCurrentIndex では発火しない）で on_select(キー) を呼ぶため、初期化時の
        誤った保存・再描画が起きない。
        """
        combo = QComboBox()
        selected = 0
        for i, (key, label) in enumerate(defs):
            combo.addItem(label, key)
            if key == current_key:
                selected = i
        combo.setCurrentIndex(selected)
        combo.activated.connect(lambda _i: on_select(combo.currentData()))
        return combo

    def _make_dashboard_tab(
        self,
        kind: str,
        treemap: TreemapWidget,
        piechart: PieChartWidget,
        line_chart: LineChartWidget,
    ) -> QWidget:
        """構成グラフ（ヒートマップ⇔円グラフ切替）と推移グラフを横並びにしたタブを返す。

        kind は "asset"（評価額）/ "income"（配当金）。構成グラフ種別は
        self.{asset,income}_chart_type に保持し、設定へ永続化する。
        """
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)

        # 構成グラフ（QStackedWidget でヒートマップ/円グラフを切替）
        stack = QStackedWidget()
        stack.addWidget(treemap)                             # index 0 = ヒートマップ
        stack.addWidget(self._make_pie_tab(kind, piechart))  # index 1 = 円グラフ（閾値付き）
        chart_type = self.asset_chart_type if kind == "asset" else self.income_chart_type
        stack.setCurrentIndex(1 if chart_type == "pie" else 0)

        def on_chart(key: str) -> None:
            stack.setCurrentIndex(1 if key == "pie" else 0)
            if kind == "asset":
                self.asset_chart_type = key
                self._save_or_notify(lambda: save_settings(asset_chart_type=key))
            else:
                self.income_chart_type = key
                self._save_or_notify(lambda: save_settings(income_chart_type=key))

        # 切替ツールバー（構成グラフの種別＝ドロップダウン）
        bar = QHBoxLayout()
        bar.addWidget(QLabel("構成グラフ:"))
        bar.addWidget(self._make_combo(
            [("treemap", "ヒートマップ"), ("pie", "円グラフ")], chart_type, on_chart
        ))
        bar.addStretch()
        outer.addLayout(bar)

        # 構成グラフと推移グラフを左右分割
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.addWidget(QLabel("構成"))
        left_v.addWidget(stack, 1)
        right = QWidget()
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.addWidget(self._make_trend_controls(kind, line_chart))
        right_v.addWidget(line_chart, 1)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([500, 500])
        outer.addWidget(split, 1)
        return tab

    def _make_monthly_tab(self) -> QWidget:
        """月別の積み上げ棒グラフと、選択月の銘柄別内訳を縦に並べたタブを返す。

        棒が横に12本並ぶため、評価額/配当金タブのような左右分割ではなく上下分割に
        する（左右だとグラフと表の双方が潰れる）。
        """
        self._monthly_bar = BarChartWidget()
        self._monthly_bar.month_clicked.connect(self._on_monthly_month)
        self._monthly_view, self._monthly_model, self._monthly_proxy = (
            self._make_breakdown_table()
        )

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)

        top = QWidget()
        top_v = QVBoxLayout(top)
        top_v.setContentsMargins(0, 0, 0, 0)
        top_v.setSpacing(2)
        top_v.addWidget(self._monthly_bar, 1)
        self._monthly_note = QLabel("")
        self._monthly_note.setObjectName("dialogNote")
        self._monthly_note.setWordWrap(True)
        top_v.addWidget(self._monthly_note)

        bottom = QWidget()
        bottom_v = QVBoxLayout(bottom)
        bottom_v.setContentsMargins(0, 0, 0, 0)
        bottom_v.setSpacing(2)
        # 並び替えの専用ドロップダウンは置かない（ヘッダクリックで足りる導線を
        # 二重に持つと、片方だけ動かしたとき表示と選択が食い違う）。
        self._monthly_head = QLabel("")
        bottom_v.addWidget(self._monthly_head)
        bottom_v.addWidget(self._monthly_view, 1)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([440, 300])
        outer.addWidget(split, 1)
        return tab

    def _make_breakdown_table(self):
        """月別内訳の読み取り専用テーブルを生成する。

        _make_table は doubleClicked→編集 / Delete / 右クリックメニューを配線するが、
        月別タブは _active_tab_key が "dashboard" を返すので action_edit は即 return
        する。あれを使い回すと「押せるのに何も起きない」導線が生まれるため、
        表示専用のビルダを分ける（_render_table も同じ理由で通さない）。
        """
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels([label for label, _ in MONTHLY_COLUMNS])
        numeric_cols = {
            i for i, (_, key) in enumerate(MONTHLY_COLUMNS)
            if key in NUMERIC_SORT_COLUMNS
        }
        proxy = NumericSortProxy(numeric_cols)
        proxy.setSourceModel(model)
        view = QTableView()
        view.setModel(proxy)
        view.setSortingEnabled(True)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setAlternatingRowColors(True)
        header = view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        view.verticalHeader().setVisible(False)
        return view, model, proxy

    def _on_monthly_month(self, month: int) -> None:
        """棒グラフのクリックで内訳の対象月を切り替える（グラフ側は選択済み）。"""
        self.monthly_month = month
        self._render_monthly_breakdown()

    def _apply_monthly_sort(self) -> None:
        """内訳テーブルの並び順を決める（ヘッダクリックの選択を保つ）。

        並び替えの導線はヘッダクリックだけ（専用ドロップダウンは持たない）。
        再描画のたびに既定へ戻すと、月を切り替えただけでユーザーの並びが消える
        ため、現在のソート矢印を読み直して同じ列・同じ向きで並べ直す。
        """
        header = self._monthly_view.horizontalHeader()
        column = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        if not self._monthly_sorted_once:
            # 初回だけ既定（金額の多い順）を当てる。setSortingEnabled 直後の
            # 矢印は列0・昇順なので、読み取った値をそのまま使うと銘柄コード順になる。
            self._monthly_sorted_once = True
            column, order = MONTHLY_COL_TOTAL, Qt.SortOrder.DescendingOrder
        if self._monthly_view.isColumnHidden(column):
            # 優待を外して合計列を伏せた直後。伏せた列で並べると矢印がどこにも
            # 出ず、何順なのか画面から読み取れなくなる。
            column, order = MONTHLY_COL_DIVIDEND, Qt.SortOrder.DescendingOrder
        # proxy.sort ではなく view 経由で並べ替える（ヘッダのソート矢印も追随させる）。
        self._monthly_view.sortByColumn(column, order)

    def _make_trend_controls(self, kind: str, line_chart: LineChartWidget) -> QWidget:
        """推移グラフ上部の操作行（期間・Y軸下限）を生成する。

        全てドロップダウン。期間/底辺は self.{asset,income}_trend_{period,baseline}
        に保持し設定へ永続化する。優待・取崩はこの行では扱わない——優待はツール
        バーの「優待」、取崩は同じくツールバーの「収入」が唯一の入口。
        """
        period = self.asset_trend_period if kind == "asset" else self.income_trend_period
        baseline = (
            self.asset_trend_baseline if kind == "asset" else self.income_trend_baseline
        )

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        row.addWidget(QLabel("推移  期間:"))
        row.addWidget(self._make_combo(
            [("all", "全期間"), ("10y", "10年"), ("5y", "5年"),
             ("3y", "3年"), ("1y", "1年")],
            period, lambda k: self._on_trend_period(kind, line_chart, k),
        ))
        row.addWidget(QLabel("底辺:"))
        row.addWidget(self._make_combo(
            [("zero", "0から"), ("min", "最低値")],
            baseline, lambda k: self._on_trend_baseline(kind, line_chart, k),
        ))

        row.addStretch()
        if kind == "asset":
            # 資産を絞ったときの記録開始日を出す枠（配当側は下の注記と共用）。
            self._asset_trend_note = QLabel("")
            self._asset_trend_note.setObjectName("dialogNote")
            row.addWidget(self._asset_trend_note)
        if kind == "income":
            # 取崩収入の記録開始日を出す枠。収入モードを取崩にすると、記録前の日は
            # combine_income が None を返して線が末尾だけになる——理由が画面に無いと
            # 「切り替えたらグラフが消えた」という不具合に見える。
            self._income_trend_note = QLabel("")
            self._income_trend_note.setObjectName("dialogNote")
            row.addWidget(self._income_trend_note)

        # 設定値をウィジェットへ初期反映（既定 all/zero と保存値が異なる場合）。
        line_chart.set_period(period)
        line_chart.set_baseline(baseline)
        return bar

    def _on_trend_period(self, kind: str, line_chart: LineChartWidget, key: str) -> None:
        line_chart.set_period(key)
        if kind == "asset":
            self.asset_trend_period = key
            self._save_or_notify(lambda: save_settings(asset_trend_period=key))
        else:
            self.income_trend_period = key
            self._save_or_notify(lambda: save_settings(income_trend_period=key))

    def _on_trend_baseline(self, kind: str, line_chart: LineChartWidget, key: str) -> None:
        line_chart.set_baseline(key)
        if kind == "asset":
            self.asset_trend_baseline = key
            self._save_or_notify(lambda: save_settings(asset_trend_baseline=key))
        else:
            self.income_trend_baseline = key
            self._save_or_notify(lambda: save_settings(income_trend_baseline=key))

    def _on_income_benefit(self, key: str) -> None:
        """優待の含む/含まないを更新し、収入グラフ（推移・月別）を描き直す。

        _refresh_trend だけでは月別タブの棒・月平均・注記・内訳が古いままになる
        （見出しの合計と表の中身が食い違う）ため _refresh_view を通す。ローカル
        CSV の再集計だけなので体感の遅延は無い。
        """
        if key == self.income_benefit:
            return
        self.income_benefit = key
        self._save_or_notify(lambda: save_settings(income_benefit=key))
        # 内訳テーブルの列構成（優待・合計の表示/非表示）が変わるので幅を取り直す。
        self._monthly_columns_sized = False
        self._monthly_benefit_detail_sized = False
        self._refresh_view()
        self._resize_yield_columns()

    def action_toggle_asset_class(self, cls: str, checked: bool) -> None:
        """資産クラス1つの表示を切り替える（最後の1つは外さない）。"""
        scope = set(self.asset_scope)
        if checked:
            scope.add(cls)
        else:
            scope.discard(cls)
        if not scope:
            # QAction は押せなくしてあるが、ほかの経路から呼ばれても守る。
            self._sync_asset_scope_controls()
            self._notify("表示対象は少なくとも1つ選んでください", error=True)
            return
        self._set_asset_scope(tuple(scope))

    def _set_asset_scope(self, scope) -> None:
        """表示対象の資産クラスを更新し、保存して全タブを描き直す。

        列構成は変わらないので列幅は取り直さない（ユーザーが広げた幅を保つ）。
        """
        normalized = normalize_asset_scope(list(scope)) or DEFAULT_ASSET_SCOPE
        if normalized == self.asset_scope:
            self._sync_asset_scope_controls()
            return
        self.asset_scope = normalized
        self._save_or_notify(lambda: save_settings(asset_scope=list(normalized)))
        self._sync_asset_scope_controls()
        self._refresh_view()

    def _scoped(self) -> list[tuple[int, Holding]]:
        """画面に出す (holdings インデックス, Holding)。表示対象と無効銘柄の表示に従う。"""
        return scope_holdings(
            self.holdings, self.asset_scope, include_inactive=self.show_inactive
        )

    def _out_of_scope_note(self, holding: Holding) -> str:
        """保存した銘柄が表示対象外なら、一覧から消えた理由を返す（対象内なら ""）。"""
        cls = asset_class(holding.asset_type, holding.currency)
        if cls in self.asset_scope:
            return ""
        return (
            f"（{ASSET_CLASS_LABELS[cls]}は表示対象外のため一覧に出ません。"
            "ツールバーの「対象」のチェックで表示できます）"
        )

    def _on_filter_changed(self, text: str) -> None:
        self._price_proxy.set_filter_text(text)
        self._income_proxy.set_filter_text(text)

    def _on_tab_changed(self, index: int) -> None:
        """タブ切替時に再描画し、最後に開いたタブとして設定へ保存する。"""
        self._save_or_notify(lambda: save_settings(last_tab=index))
        self._refresh_view()
        # タブによって編集・削除・絞り込みの可否が変わるため状態を取り直す。
        self._sync_action_states()

    def _register_shortcuts(self) -> None:
        """QAction で表現できないショートカットだけを登録する。

        主要操作は _build_actions の QAction が持つ（メニューへ自動表示される）。
        ここに残るのは Esc（メニュー項目にしても意味が無い取り消し操作）のみ。
        """
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._on_escape)

    def _sync_action_states(self) -> None:
        """選択状態・実行中フラグに応じてアクションの有効/無効を同期する。

        メニュー・ツールバー・ショートカットが同じ QAction を共有しているため、
        ここ1箇所で3者すべてが揃う。「押せるのに何も起きない」（グラフタブでの
        編集・削除、取得中の更新ボタン）を無くすのが目的。
        """
        if not self._actions:
            return
        tab_key = self._active_tab_key()
        has_row = (
            tab_key != "dashboard"
            and self._view_for_tab(tab_key).currentIndex().isValid()
        )
        for key in ("edit", "delete"):
            self._actions[key].setEnabled(has_row)
        # 取得中は CSV を書き換える操作を一律で伏せる（多重実行と競合を防ぐ）。
        idle = not self._busy
        # 分割の補正は、選択行に未反映の分割があるときだけ（旗の無い行では押せない）。
        selected_pending = self._selected_pending_splits() is not None
        for key in ("split_apply", "split_dismiss"):
            self._actions[key].setEnabled(idle and selected_pending)
        self._actions["split_apply_all"].setEnabled(
            idle
            and bool(
                splits.pending_holdings(
                    self.holdings,
                    self.market_data_map,
                    include_inactive=self.show_inactive,
                )
            )
        )
        # データフォルダの切替も同様（走行中のワーカーが旧フォルダへ書き戻す）。
        for key in ("update_market", "update_fx", "reload", "new",
                    "import_holdings", "import_history", "change_data_dir"):
            self._actions[key].setEnabled(idle)
        # 出力対象が無いエクスポートは押せないようにする。
        self._actions["export_holdings"].setEnabled(idle and bool(self.holdings))
        self._actions["export_holdings_md"].setEnabled(idle and bool(self.holdings))
        self._actions["export_history"].setEnabled(idle and bool(self.history))
        # 絞り込みはテーブルにしか効かないため、グラフタブでは無効にする。
        filter_box = getattr(self, "_filter", None)
        if filter_box is not None:
            filter_box.setEnabled(tab_key != "dashboard")

    def _focus_filter(self) -> None:
        """絞り込みボックスへフォーカスを移す（Ctrl+F）。

        絞り込みは表にしか効かないため、グラフタブなら株価詳細タブへ切り替えて
        からフォーカスする（押しても何も起きない状態を作らない）。
        """
        if self._active_tab_key() == "dashboard":
            self._tabs.setCurrentIndex(3)
        self._filter.setFocus()
        self._filter.selectAll()

    def _on_escape(self) -> None:
        """Esc: フィルタ入力があればクリアし、フィルタ編集中ならテーブルへ戻す。"""
        if self._filter.text():
            self._filter.clear()
            return
        if self._filter.hasFocus():
            tab_key = self._active_tab_key()
            if tab_key == "dashboard":
                self._tabs.setFocus()
            else:
                self._view_for_tab(tab_key).setFocus()

    # ---- データ読み込み / 再描画（legacy DividendApp から移植） ----
    def _reload_all(self) -> None:
        # 4種の CSV をすべて読み終えてから self の状態を更新する（途中失敗で新旧が混ざらない）。
        holdings = load_holdings()
        market_data = load_market_data()
        fx = latest_fx(load_fx())
        history = load_history()
        self.holdings = holdings
        self.market_data_map = {row.symbol: row for row in market_data}
        self.fx_rate = fx.rate if fx is not None else None
        self.fx_fetched_at = fx.timestamp if fx is not None else None
        if market_data:
            latest = max((row.fetched_at or "") for row in market_data)
            self.market_fetched_at = latest or None
            self.market_source = market_data[0].source
        else:
            self.market_fetched_at = None
            self.market_source = None
        self.history = history
        self._refresh_view()
        # 当日のスナップショットを履歴へ upsert（タブ切替で呼ばれる _refresh_view では
        # 記録せず、再読込・取得完了時のみ記録して CSV の過剰書込を避ける）。
        history_notice = self._record_history_snapshot()
        if history_notice:
            self._notify(history_notice)

    def _refresh_view(self) -> None:
        # 表示対象で絞った保有と、表示行 ↔ 保有の対応を同じ1回の呼び出しから作る
        # （インデックスを別の条件で組むと編集・削除が別の銘柄に当たる）。
        scoped = self._scoped()
        rows, summary = compute_rows(
            [h for _, h in scoped], self.market_data_map, self.fx_rate,
            self.dividend_mode, self.withdrawal_mode,
            include_inactive=self.show_inactive,
        )
        visible_indices = [i for i, _ in scoped]
        # 優待スイッチは構成グラフ・月別・推移だけでなく、KPI カードと両テーブルの
        # 利回り（分子）にも効く。先に決めて表・カード・グラフで同じ値を使う。
        with_benefit = self.income_benefit == "with"
        yield_suffix = YIELD_BENEFIT_SUFFIX if with_benefit else ""
        # 利回りの定義（分子と集計対象）。KPI カードと表のヘッダの両方へ
        # ツールチップで載せる——接尾辞は幅次第で切れるので、切れても読める
        # 経路を必ず用意する（自由記述列と同じ作法）。
        yield_tip = (
            f"分子: {'配当＋優待' if with_benefit else '配当のみ'}"
            f" / 対象: {asset_scope_label(self.asset_scope)}"
        )
        # 株数列のヘッダは投信の口数単位を示すので、両テーブルとも毎回貼り直す
        # （設定を切り替えたのに「株数」のままだと桁が変わった理由が読めない）。
        price_cols = self._with_shares_label(PRICE_COLUMNS)
        self._price_model.setHorizontalHeaderLabels([lbl for lbl, _ in price_cols])
        self._render_table("price", price_cols, self._price_model, self._price_view, rows, visible_indices)
        income_cols = self._with_shares_label(INCOME_COLUMNS)
        if self.withdrawal_mode is not None:
            income_cols = [
                ("年間収入(円)", key) if lbl == "年間配当(円)" else (lbl, key)
                for lbl, key in income_cols
            ]
        # 利回りの分子は優待スイッチで変わる。KPI カードだけ切り替えて列を据え置くと、
        # 同じ「簿価利回り」が画面によって別の定義になる。列数・並びは変えず
        # キーとラベルだけ差し替える（列幅の保存値は列数ぶんの配列なので）。
        yoc_key, yov_key = row_yield_keys(with_benefit)
        yield_key_map = {"簿価利回り": yoc_key, "時価利回り": yov_key}
        income_cols = [
            (f"{lbl}{yield_suffix}", yield_key_map[lbl]) if lbl in yield_key_map
            else (lbl, key)
            for lbl, key in income_cols
        ]
        self._income_model.setHorizontalHeaderLabels([lbl for lbl, _ in income_cols])
        for _col, (_lbl, _) in enumerate(income_cols):
            if _lbl.startswith(("簿価利回り", "時価利回り")):
                self._income_model.setHeaderData(
                    _col, Qt.Orientation.Horizontal, yield_tip,
                    Qt.ItemDataRole.ToolTipRole,
                )
        self._render_table("income", income_cols, self._income_model, self._income_view, rows, visible_indices)
        # 構成グラフは集計と揃えるため常に有効銘柄のみを対象にする
        # （無効銘柄は Summary に含まれず、構成比に混ぜると割合が狂う）。
        chart_rows = [r for r in rows if r.get("active") is not False]
        self._treemap.set_data(chart_rows)
        self._piechart.set_data(chart_rows)
        # 配当額ベース: 面積 = 年間配当（取崩モード時は「年間収入」に上書きされた額）。
        # ツールバーの「優待」が「含む」なら配当＋優待の合算列へ切り替える
        # （推移・月別と同じ数え方に揃える）。div_label / div_empty は月別タブが
        # 「＋優待」を自前で足すので、ここでは素のままにして構成グラフ用の
        # ラベルだけ別に作る（両方に足すと「＋優待＋優待」になる）。
        div_label = "年間収入" if self.withdrawal_mode is not None else "年間配当"
        div_empty = f"{div_label}データがありません（F5 で取得 / 配当のある銘柄が必要です）"
        comp_key = (
            "annual_dividend_with_benefit_jpy" if with_benefit
            else "annual_dividend_jpy"
        )
        comp_label = f"{div_label}＋優待" if with_benefit else div_label
        # 優待込みでは優待だけの銘柄でも描けるので、空メッセージの条件も変える。
        comp_need = "配当か優待のある銘柄" if with_benefit else "配当のある銘柄"
        comp_empty = (
            f"{comp_label}データがありません（F5 で取得 / {comp_need}が必要です）"
        )
        self._treemap_div.set_data(
            chart_rows, value_key=comp_key, value_label=comp_label,
            empty_message=comp_empty,
        )
        self._piechart_div.set_data(
            chart_rows, value_key=comp_key, value_label=comp_label,
            empty_message=comp_empty,
        )
        # 月別配当（構成グラフと同じく有効銘柄のみ）
        self._monthly = monthly_breakdown(chart_rows)
        self._refresh_monthly(div_label, div_empty)
        # 推移グラフ（履歴スナップショット）
        self._refresh_trend()

        # 税引・収入モードはツールバーのドロップダウンが示すため、ここでは
        # 他に表示場所の無い恒久情報だけを載せる。
        missing_note = (
            f" | 未算出 {summary.missing_market_value_count}銘柄"
            if summary.missing_market_value_count else ""
        )
        # 絞っているときは数字が全体ではないことを、ボタン以外にも常設で出す。
        scope_note = (
            "" if is_all_asset_scope(self.asset_scope)
            else f" | 対象: {asset_scope_label(self.asset_scope)}"
        )
        # 為替と株価は取得時刻が別（Ctrl+U だけ・手編集の fx.csv もある）なので
        # それぞれの時刻を出す。円換算がいつのレートかは画面のどこにも無かった。
        self._info.setText(
            f"USDJPY {fmt_number(self.fx_rate, 4)} "
            f"({self._compact_timestamp(self.fx_fetched_at)}) | "
            f"株価 {self._compact_timestamp(self.market_fetched_at)} "
            f"({self.market_source or 'N/A'}){missing_note}{scope_note}"
        )
        income_kpi_label = "年間収入" if self.withdrawal_mode is not None else "年間配当"
        self._cards["年間配当"].set_title(income_kpi_label)
        self._cards["評価額計"].set_value(fmt_number(summary.total_market_value_jpy, 0))
        self._cards["簿価計"].set_value(fmt_number(summary.total_book_value_jpy, 0))
        self._cards["年間配当"].set_value(fmt_number(summary.total_dividend_jpy, 0))
        self._cards["優待"].set_value(fmt_number(summary.total_benefit_jpy, 0))
        self._cards["合計"].set_value(fmt_number(summary.total_numerator_jpy, 0))
        # 利回りは優待スイッチで分子が変わるので、カード名に接尾辞を付ける
        # （年間配当 → 年間収入 の入替と同じやり方）。表示対象の資産は全カードに
        # 効くので利回りにだけ付けず、ツールバーのボタンとステータスバーで示す。
        yoc, yov = summary_yields(summary, with_benefit)
        self._cards["簿価利回り"].set_title(f"簿価利回り{yield_suffix}")
        self._cards["時価利回り"].set_title(f"時価利回り{yield_suffix}")
        # 定義（分子・対象）はツールチップで読めるようにする。
        for _title in ("簿価利回り", "時価利回り"):
            self._cards[_title].setToolTip(yield_tip)
        self._cards["簿価利回り"].set_value(fmt_percent(yoc, 2))
        self._cards["時価利回り"].set_value(fmt_percent(yov, 2))
        self._cards["損益"].set_value(
            fmt_number(summary.total_profit_jpy, 0),
            profit_text_qcolor(summary.total_profit_jpy),
        )
        self._cards["損益率"].set_value(
            fmt_percent(summary.profit_rate, 2),
            profit_text_qcolor(summary.profit_rate),
        )
        # 行数・選択状態が変わり得るのでアクションの可否を取り直す。
        self._sync_action_states()

    # ---- 履歴（推移グラフ） ----
    def _refresh_trend(self) -> None:
        """推移グラフ 2 種へ最新の履歴を反映する。

        評価額タブ＝評価額・簿価の2系列。配当タブ＝ツールバーの「優待」と「収入」
        に応じて combine_income で合成した単一系列。
        """
        # 表示対象の資産で絞った値へ差し替える（全クラスなら合計列のまま）。
        # 系列定義・combine_income・取崩の注記は同じキーを読むだけで済む。
        raw_dicts = self._history_as_dicts()
        hist_dicts = [scope_history_record(r, self.asset_scope) for r in raw_dicts]
        scope_note = self._scope_coverage_note(raw_dicts)
        partial = not is_all_asset_scope(self.asset_scope)
        # 一部の資産だけの線は、資産別の記録を始めた日からしか引けない。
        scoped_empty = (
            f"{asset_scope_label(self.asset_scope)}の内訳はまだ記録がありません"
            "（株価を取得した日から記録されます）"
        )
        self._asset_line.set_data(
            hist_dicts, asset_trend_series(),
            empty_message=scoped_empty if partial else
            "評価額の履歴がありません（株価更新か『インポート』で記録されます）",
        )
        self._asset_trend_note.setText(scope_note)
        with_benefit = self.income_benefit == "with"
        mode = self.withdrawal_mode
        income_dicts = [
            {"date": rec["date"],
             "income_value": combine_income(rec, with_benefit, mode)}
            for rec in hist_dicts
        ]
        label = self._income_trend_label(
            with_benefit, mode, dividend_mode=self.dividend_mode
        )
        self._income_line.set_data(
            income_dicts, [(label, "income_value", income_trend_color())],
            empty_message=scoped_empty if partial else
            "配当の履歴がありません（株価更新か『インポート』で記録されます）",
        )
        # 絞っているときは資産別の列が全モードぶん同じ日から始まるので、取崩の
        # 記録開始日より後になる。注記は資産別の開始日の1つにまとめる。
        notes = [
            scope_note if partial
            else self._withdrawal_coverage_note(hist_dicts, mode)
        ]
        # 履歴は表示モードに依存させないため税引前で記録している（変えない）。
        # 税引後を選んでいるのに推移だけ税引前のままだと、KPI の年間配当と
        # グラフ右端の値が食い違って見えるので、凡例と注記の両方で明示する。
        if self.dividend_mode == "net":
            notes.append(NET_TREND_NOTE)
        self._income_trend_note.setText(" / ".join(n for n in notes if n))

    def _scope_coverage_note(self, hist_dicts: list[dict]) -> str:
        """資産を絞っているとき、推移の線を引ける最初の日を注記で返す。

        資産別の列は後から足したので、それより前の日は合計しか無く線が切れる
        （推定で埋めない）。注記が無いと「対象を絞ったらグラフが短くなった」
        理由が画面に残らない。全クラスを選んでいる・履歴が空なら ""。
        """
        if is_all_asset_scope(self.asset_scope) or not hist_dicts:
            return ""
        first = scoped_history_start(hist_dicts, self.asset_scope)
        if first is None or first == hist_dicts[0]["date"]:
            return ""  # 1点も無い（空メッセージ側の担当）か、全期間ぶんある
        # 最小幅 900px の操作行に収まる長さにする（「資産別の内訳は」だと末尾が切れた）。
        return f"※資産別は {first} 以降のみ記録"

    @staticmethod
    def _withdrawal_coverage_note(hist_dicts: list[dict], mode: str | None) -> str:
        """選択中の収入モードの列が履歴の途中からしか無いとき、開始日を注記で返す。

        取崩収入の列は後から足したもので、それ以前の日（旧アプリからの取込ぶんを
        含む）は空。combine_income はそこで None を返して線を分断するため、注記が
        無いと収入モードを取崩にした瞬間グラフが短くなった理由が画面に残らない。
        定額と定率は別の列＝別の記録開始日なので、モードごとに見る列を変える。
        """
        if mode is None:
            return ""
        key = INCOME_BASE_KEYS[mode]
        first = next(
            (r["date"] for r in hist_dicts if safe_float(r.get(key)) is not None),
            None,
        )
        if first is None or first == hist_dicts[0]["date"]:
            return ""  # 全期間ぶんある（または1点も無い＝空メッセージ側の担当）
        return f"※{WITHDRAWAL_LABELS[mode]}収入は {first} 以降のみ記録"

    @staticmethod
    def _income_trend_label(
        with_benefit: bool, mode: str | None, dividend_mode: str = "gross"
    ) -> str:
        """凡例の系列名。取崩は定額/定率のどちらを描いているか名前で区別する。

        税引後モードでも履歴は税引前の値なので、そのときだけ「（税引前）」を付ける
        （税引前モードで付けないのは、表示中の数え方と一致していて冗長なため）。
        """
        base = "配当" if mode is None else f"{WITHDRAWAL_LABELS[mode]}収入"
        label = f"{base}＋優待" if with_benefit else base
        return f"{label}（税引前）" if dividend_mode == "net" else label

    def _history_as_dicts(self) -> list[dict]:
        """LineChartWidget.set_data 用に履歴を dict 列へ変換（record_date 昇順）。"""
        result: list[dict] = []
        for rec in self.history:
            result.append(
                {
                    "date": rec.record_date,
                    "total_market_value_jpy": rec.total_market_value_jpy,
                    "total_book_value_jpy": rec.total_book_value_jpy,
                    "total_dividend_jpy": rec.total_dividend_jpy,
                    "total_benefit_jpy": rec.total_benefit_jpy,
                    "total_profit_jpy": rec.total_profit_jpy,
                    "total_withdrawal_income_jpy": rec.total_withdrawal_income_jpy,
                    "total_fixed_withdrawal_income_jpy": (
                        rec.total_fixed_withdrawal_income_jpy
                    ),
                    # 資産別の内訳（表示対象を絞ったときに scope_history_record が使う）。
                    **rec.by_class,
                }
            )
        return result

    def _record_history_snapshot(self) -> str | None:
        """当日のスナップショットを履歴へ upsert して保存する。

        表示モードに依存させないため、税引前・取崩無し（mode="gross",
        withdrawal_mode=None）
        で再計算する。取崩収入は定額・定率それぞれで別途計算し、収入モードごとの
        系列の値とする（1 列で兼ねると、定額を選んでいるのに定率の線が出る）。
        評価額・配当・優待が全て None（起動直後で株価未取得）の場合は記録しない。
        株価が当日未取得・分割が未反映の場合も記録せず、呼出側へ通知文を返す。
        """
        pending = splits.pending_holdings(self.holdings, self.market_data_map)
        if pending:
            # 記録は全件で判定するので、表示対象外の銘柄の旗でも止まる。その旗は
            # 画面に出ていないため、どの銘柄かを通知文に書く。
            hidden = [
                self.holdings[i].symbol for i, _ in pending
                if asset_class(self.holdings[i].asset_type, self.holdings[i].currency)
                not in self.asset_scope
            ]
            if hidden:
                return (
                    "分割を反映するまで推移を記録しません"
                    f"（表示対象外の銘柄: {', '.join(dict.fromkeys(hidden))}）"
                )
            return "分割を反映するまで推移を記録しません"
        today = today_jst_str()
        for holding in self.holdings:
            if not holding.active:
                continue
            data = self.market_data_map.get(holding.symbol)
            # 基準価額が無い投信は従来どおり簿価フォールバックを認める。
            if holding.asset_type == "fund" and (data is None or data.last_price is None):
                continue
            if data is None or not (data.price_fetched_at or "").startswith(today):
                return "本日取得した株価が揃うまで推移を記録しません"
        _, base = compute_rows(
            self.holdings, self.market_data_map, self.fx_rate,
            "gross", None,
        )
        market = base.total_market_value_jpy
        dividend = base.total_dividend_jpy
        benefit = base.total_benefit_jpy
        if base.missing_market_value_count > 0:
            return
        if market is None and dividend is None and benefit is None:
            return
        # 取崩込み収入は表示中のモードに関係なく両方を記録する（あとから収入
        # モードを切り替えても、その日の値が欠けていて線が切れることが無い）。
        _, wd_rate = compute_rows(
            self.holdings, self.market_data_map, self.fx_rate,
            "gross", "rate",
        )
        _, wd_fixed = compute_rows(
            self.holdings, self.market_data_map, self.fx_rate,
            "gross", "fixed",
        )
        record = HistoryRecord(
            record_date=today,
            total_market_value_jpy=market,
            total_book_value_jpy=base.total_book_value_jpy,
            total_dividend_jpy=dividend,
            total_benefit_jpy=benefit,
            total_profit_jpy=base.total_profit_jpy,
            total_withdrawal_income_jpy=wd_rate.total_dividend_jpy,
            total_fixed_withdrawal_income_jpy=wd_fixed.total_dividend_jpy,
            source="app",
            # 表示対象（資産クラス）に関係なく全クラスぶんを記録する。あとから
            # 対象を絞っても、記録を始めた日以降の線は欠けずに引ける。
            by_class=class_breakdown(
                self.holdings, self.market_data_map, self.fx_rate
            ),
        )
        history = upsert_record(self.history, record)
        if not self._save_or_notify(lambda: save_history(history)):
            return
        self.history = history
        self._refresh_trend()  # 追加した当日点を推移グラフへ即時反映

    @staticmethod
    def _compact_timestamp(value: str | None) -> str:
        """ISO 日時を "YYYY-MM-DD HH:MM" へ詰める（秒・タイムゾーンは落とす）。

        ステータスバーの恒久情報は一時通知（showMessage）と横幅を分け合うため、
        狭いウィンドウで通知が潰れないよう短く保つ。
        """
        if not value:
            return "N/A"
        return value[:16].replace("T", " ")

    @staticmethod
    def _with_csv_suffix(path: str) -> str:
        """保存ダイアログが拡張子を付けない環境向けに .csv を補う。"""
        return path if path.lower().endswith(".csv") else path + ".csv"

    @staticmethod
    def _with_md_suffix(path: str) -> str:
        """保存ダイアログが拡張子を付けない環境向けに .md を補う。"""
        return path if path.lower().endswith(".md") else path + ".md"

    def action_export_holdings(self) -> None:
        """保有銘柄を CSV へエクスポートする（holdings.csv と同一フォーマット）。"""
        if not self.holdings:
            self._notify("エクスポートする保有銘柄がありません", error=True)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保有銘柄をエクスポート", f"holdings_{today_jst_str()}.csv",
            "CSV ファイル (*.csv)",
        )
        if not path:
            return
        path = self._with_csv_suffix(path)
        if self._save_or_notify(lambda: export_holdings_csv(path, self.holdings)):
            self._notify(f"保有銘柄を書き出しました（{len(self.holdings)} 件）")

    def action_export_holdings_markdown(self) -> None:
        """保有銘柄を LLM 分析用の Markdown へエクスポートする。

        CSV 版が holdings.csv と同じ「入力値のまま・取込で読み戻せる」形なのに対し、
        こちらは compute_rows の計算済み行と Summary を出す（時価・損益・利回りが
        無ければ分析できない）。往復は目的外。
        """
        if not self.holdings:
            self._notify("エクスポートする保有銘柄がありません", error=True)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保有銘柄を Markdown でエクスポート",
            f"holdings_{today_jst_str()}.md", "Markdown ファイル (*.md)",
        )
        if not path:
            return
        path = self._with_md_suffix(path)
        # 画面と同じ条件で計算し直す。compute_rows は純関数で
        # _record_history_snapshot も同様に追加で呼んでいる——表示結果を
        # self._last_rows のようなキャッシュへ溜める経路は増やさない。
        # 画面と同じく表示対象の資産で絞る（CSV 版は取込が全置換なので全件のまま）。
        rows, summary = compute_rows(
            [h for _, h in self._scoped()], self.market_data_map, self.fx_rate,
            self.dividend_mode, self.withdrawal_mode,
            include_inactive=self.show_inactive,
        )
        text = build_holdings_markdown(
            rows, summary,
            dividend_mode=self.dividend_mode,
            withdrawal_mode=self.withdrawal_mode,
            asset_scope=self.asset_scope,
            with_benefit=self.income_benefit == "with",
            fx_rate=self.fx_rate,
            fx_fetched_at=self.fx_fetched_at,
            market_fetched_at=self.market_fetched_at,
            market_source=self.market_source,
            generated_at=now_jst_iso(),
            # 画面と同じ単位で株数を出す（優待スイッチと違い、こちらは表に載る
            # 数字そのものが変わるので「出力条件」にも書く）。
            fund_share_unit=self.fund_share_unit,
        )
        if self._save_or_notify(lambda: write_text_export(path, text)):
            # CSV 版は self.holdings を全件出すが、こちらは画面と同じく
            # show_inactive に従うので件数が食い違い得る。理由を通知に残す。
            note = "" if self.show_inactive else " / 無効を除く"
            if not is_all_asset_scope(self.asset_scope):
                note += f" / 対象: {asset_scope_label(self.asset_scope)}"
            self._notify(f"保有銘柄を Markdown で書き出しました（{len(rows)} 件{note}）")

    def action_export_history(self) -> None:
        """推移履歴を CSV へエクスポートする（history.csv と同一フォーマット）。"""
        if not self.history:
            self._notify("エクスポートする履歴がありません", error=True)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "推移履歴をエクスポート", f"history_{today_jst_str()}.csv",
            "CSV ファイル (*.csv)",
        )
        if not path:
            return
        path = self._with_csv_suffix(path)
        if self._save_or_notify(lambda: export_history_csv(path, self.history)):
            self._notify(f"履歴を書き出しました（{len(self.history)} 日分）")

    def action_create_shortcut(self) -> None:
        """デスクトップへ起動用ショートカットを作成する（タスクバーのアイコン対策）。

        Windows のタスクバーはウィンドウのアイコンではなく起動元のショートカットから
        アイコンを引くため、消してしまうと Python のアイコンに戻る。ここから作り直せる。
        PowerShell の起動を待つ1秒弱だけ UI が止まるので砂時計カーソルにする
        （市場データ取得と違い一瞬で終わる同期処理なので QThread には載せない）。
        """
        path = shortcut.shortcut_path()
        if path.exists():
            reply = QMessageBox.question(
                self,
                "ショートカットの作成",
                f"既にあります:\n{path}\n\n作り直しますか？"
                "（Python の場所が変わったときは作り直しが必要です）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            created = shortcut.create_desktop_shortcut()
        except Exception as exc:  # noqa: BLE001 — UI に通知して継続
            QMessageBox.warning(
                self, "ショートカットの作成", f"作成に失敗しました:\n{exc}"
            )
            self._notify("ショートカットの作成に失敗しました", error=True)
            return
        finally:
            QGuiApplication.restoreOverrideCursor()

        QMessageBox.information(
            self,
            "ショートカットの作成",
            f"デスクトップに作成しました:\n{created}\n\n"
            "タスクバーへアプリのアイコンを出すには、このショートカットから"
            "起動してください。",
        )
        self._notify("デスクトップにショートカットを作成しました")

    def action_import_holdings(self) -> None:
        """エクスポートした保有銘柄 CSV を選択して取り込む（全置換）。

        symbol が一意でない（複数口座・取得単価）ためマージできず、取込は現在の
        保有を完全に置き換える。破壊的操作のため確認ダイアログを挟む（CSV は
        save_holdings が自動バックアップする）。
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "保有銘柄をインポート (CSV)", "",
            "CSV ファイル (*.csv);;すべて (*.*)",
        )
        if not path:
            return
        try:
            imported = read_holdings_csv(path)
        except Exception as exc:  # noqa: BLE001 — UI に通知して継続
            self._notify(f"取込に失敗しました: {exc}", error=True)
            return
        if not imported:
            self._notify("取込対象のデータが見つかりませんでした", error=True)
            return
        preview = _format_import_holdings_preview(imported)
        reply = QMessageBox.question(
            self,
            "保有銘柄の取込",
            f"現在の保有銘柄（{len(self.holdings)} 件）を取込内容（{len(imported)} 件）で"
            "置き換えますか？\n\n"
            f"【取込プレビュー】\n{preview}\n\n"
            "この操作は現在の保有を上書きします"
            "（CSV は自動バックアップされます）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._save_or_notify(lambda: save_holdings(imported)):
            return
        self._reload_all()  # 保存済み CSV から再読込し全表示を更新
        self._notify(f"保有銘柄を取り込みました（{len(imported)} 件）")

    def action_import_history(self) -> None:
        """エクスポートした履歴 CSV を選択して取り込む（既存の日付は保持）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "履歴をインポート (CSV)", "",
            "CSV ファイル (*.csv);;すべて (*.*)",
        )
        if not path:
            return
        try:
            imported = read_history_csv(path)
        except Exception as exc:  # noqa: BLE001 — UI に通知して継続
            self._notify(f"取込に失敗しました: {exc}", error=True)
            return
        if not imported:
            self._notify("取込対象のデータが見つかりませんでした", error=True)
            return
        before = len(self.history)
        # 既存（アプリ記録など）を優先し、履歴に無い日付だけ埋める。
        # 同一日付は上書きしないため、編集して取り込み直す用途には向かない。
        history = merge_records(self.history, imported, prefer="existing")
        if not self._save_or_notify(lambda: save_history(history)):
            return
        self.history = history
        added = len(history) - before
        self._refresh_view()
        self._notify(f"履歴を取り込みました（新規 {added} 日分 / 計 {len(self.history)} 日分）")

    # ---- データフォルダ（保存先の切替 / エクスプローラーで開く） ----

    def action_change_data_dir(self) -> None:
        """データフォルダをフォルダ選択ダイアログで選び直す。

        設定値そのものはデータフォルダの外（app_config.config_path）に保存する。
        `settings.json`（ウィンドウ位置・列幅・円グラフ閾値）は**データフォルダ側**
        にあるため切替後は新フォルダのものになる。ただし切替した瞬間に読み直すと
        画面状態だけが別データの設定へ飛んで見えるので、現在のセッションの表示状態
        は保ったまま次回起動から新フォルダの設定を使う（確認ダイアログで明示する）。
        """
        if self._busy:
            self._notify("更新処理が実行中です", error=True)
            return
        current = Path(io_csv.DATA_DIR)
        chosen = QFileDialog.getExistingDirectory(
            self, "データフォルダを選択", str(current),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return
        new_dir = Path(chosen)
        if new_dir.resolve() == current.resolve():
            self._notify("現在と同じフォルダが選択されました")
            return

        note = (
            "\n\nウィンドウ位置・列幅などの設定は新しいフォルダの settings.json を"
            "使うようになります（次回起動から反映）。"
        )
        copy = False
        if (new_dir / "holdings.csv").exists():
            reply = QMessageBox.question(
                self,
                "データフォルダの変更",
                f"データフォルダを切り替えます。\n\n変更前: {current}\n変更後: {new_dir}\n\n"
                "選択したフォルダには既にデータがあります。そのデータを読み込みます"
                "（現在のデータはコピーしません）。" + note,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.question(
                self,
                "データフォルダの変更",
                f"データフォルダを切り替えます。\n\n変更前: {current}\n変更後: {new_dir}\n\n"
                "選択したフォルダにデータがありません。現在のデータ"
                f"（保有 {len(self.holdings)} 件 / 履歴 {len(self.history)} 日分）を"
                "コピーしますか？\n"
                "「いいえ」を選ぶと空のデータで開始します"
                "（変更前のフォルダはそのまま残ります）。" + note,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            copy = reply == QMessageBox.StandardButton.Yes

        prev_state = (
            list(getattr(self, "holdings", [])),
            dict(getattr(self, "market_data_map", {})),
            getattr(self, "fx_rate", None),
            getattr(self, "fx_fetched_at", None),
            getattr(self, "market_fetched_at", None),
            getattr(self, "market_source", None),
            list(getattr(self, "history", [])),
        )
        switched = False
        try:
            if copy:
                copy_data_files(current, new_dir)
            set_data_dir(new_dir)
            switched = True
            init_empty_files()
            self._reload_all()
        except Exception as exc:  # noqa: BLE001 — 旧フォルダ・旧設定・旧表示へ復元して通知
            (
                self.holdings,
                self.market_data_map,
                self.fx_rate,
                self.fx_fetched_at,
                self.market_fetched_at,
                self.market_source,
                self.history,
            ) = prev_state
            if switched:
                io_csv.restore_data_dir(current)
                # _reload_all が新フォルダの内容を描いた後で失敗した場合も、画面を旧データへ戻す。
                try:
                    self._refresh_view()
                except Exception:  # noqa: BLE001 — 復元中の描画失敗で通知まで潰さない
                    pass
            self._notify(f"データフォルダを変更できませんでした: {exc}", error=True)
            return
        self._notify(f"データフォルダを変更しました: {new_dir}")

    def action_open_data_dir(self) -> None:
        """現在のデータフォルダをエクスプローラーで開く。"""
        path = Path(io_csv.DATA_DIR)
        if not path.exists():
            self._notify(f"データフォルダが見つかりません: {path}", error=True)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._notify(f"フォルダを開けませんでした: {path}", error=True)

    def _refresh_monthly(self, div_label: str, div_empty: str) -> None:
        """月別タブ（棒グラフ・注記・内訳）を最新の集計で描き直す。

        優待「含まない」では配当系列だけを積み、月平均も配当ぶんだけで割る
        （棒から優待を外して平均線だけ優待込みのままにすると、線が棒の上に浮く）。
        """
        with_benefit = self.income_benefit == "with"
        buckets = [
            {
                "month": b.month,
                "dividend_jpy": b.dividend_jpy,
                "benefit_jpy": b.benefit_jpy,
            }
            for b in self._monthly.buckets
        ]
        self._monthly_bar.set_data(
            buckets,
            monthly_series() if with_benefit else monthly_series()[:1],
            average=(
                self._monthly.monthly_average_jpy if with_benefit
                else self._monthly.known_dividend_jpy / 12.0
            ),
            empty_message=div_empty,
        )
        self._monthly_bar.set_selected_month(self.monthly_month)
        self._monthly_note.setText(self._monthly_note_text(div_label))
        # 内訳の見出しは月の切替でも描き直すため、収入ラベルを保持しておく。
        self._monthly_label = div_label
        self._render_monthly_breakdown()

    def _monthly_note_text(self, div_label: str) -> str:
        """推定である旨と、按分できなかった額を注記にまとめる。

        月不明を黙って落とすと Σ(棒グラフ) と KPI「合計」が食い違い、その差が画面の
        どこにも現れない。0 の項目は出さず、必要なときだけ行を足す。
        """
        with_benefit = self.income_benefit == "with"
        avg_label = f"{div_label}＋優待" if with_benefit else div_label
        parts = [
            "※支払月は権利落ち月からの推定です（JPY 建ては +3ヶ月 / USD 建ては同月）。"
            f"月平均は按分できた{avg_label}を12で割った額です。"
        ]
        unknown = self._monthly.unknown_dividend_jpy
        if with_benefit:
            unknown += self._monthly.unknown_benefit_jpy
        if unknown > 0:
            # unknown_symbols は配当・優待どちらかで月不明になった銘柄の合計数
            # なので、優待を外したときの銘柄数としては過大になる。数えられない
            # 数を書くより出さない。
            count = (
                f"（{len(self._monthly.unknown_symbols)}銘柄・グラフ未計上）"
                if with_benefit else "（グラフ未計上）"
            )
            parts.append(f"月不明 ¥{fmt_number(unknown, 0)}{count}")
        if self._monthly.na_symbols:
            parts.append(f"{div_label}が N/A {len(self._monthly.na_symbols)}銘柄")
        if self.withdrawal_mode is not None:
            parts.append(
                "取崩額は支払月が定まらないため月不明に入ります"
                "（銘柄編集で支払月を指定すると按分されます）"
            )
        return " / ".join(parts)

    def _render_monthly_breakdown(self) -> None:
        """選択中の月の銘柄別内訳を描き直す（読み取り専用テーブル）。"""
        bucket = next(
            (b for b in self._monthly.buckets if b.month == self.monthly_month), None
        )
        items = bucket.items if bucket is not None else []
        with_benefit = self.income_benefit == "with"
        if bucket is None:
            total = 0.0
        else:
            total = bucket.total_jpy if with_benefit else bucket.dividend_jpy
        # 優待を含めないときは優待列と合計列を伏せる（合計＝配当の重複表示になり、
        # 見出しの金額とも食い違うため）。モデルの列構成自体は変えない。
        self._monthly_view.setColumnHidden(MONTHLY_COL_BENEFIT, not with_benefit)
        self._monthly_view.setColumnHidden(MONTHLY_COL_BENEFIT_DETAIL, not with_benefit)
        self._monthly_view.setColumnHidden(MONTHLY_COL_TOTAL, not with_benefit)
        label = "配当" if self._monthly_label == "年間配当" else "収入"
        self._monthly_head.setText(
            f"{self.monthly_month}月の{label}内訳: ¥{fmt_number(total, 0)}"
            f"（{len(items)}銘柄）"
        )

        self._monthly_model.setRowCount(0)
        for item in items:
            cells: list[QStandardItem] = []
            values = {
                "symbol": item.symbol,
                "name": item.name,
                "account": item.account,
                "dividend_jpy": item.dividend_jpy,
                "benefit_jpy": item.benefit_jpy,
                # 優待が入らない月は calc 側で "" になっている（金額と説明を揃える）。
                "benefit_detail": item.benefit_detail,
                "total_jpy": item.total_jpy,
            }
            for _label, key in MONTHLY_COLUMNS:
                value = values[key]
                if key in NUMERIC_SORT_COLUMNS:
                    cell = QStandardItem(fmt_number(value, 0))
                    cell.setData(float(value), Qt.ItemDataRole.UserRole)
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                else:
                    cell = QStandardItem(str(value))
                    if key in FREE_TEXT_COLUMNS and value:
                        cell.setToolTip(str(value))
                cells.append(cell)
            self._monthly_model.appendRow(cells)
        self._apply_monthly_sort()
        # 列幅は「中身が入った最初の1回」だけ調整する。毎回呼ぶと、バーをクリックする
        # たびユーザーが広げた列幅が元へ戻ってしまう（他テーブルの _size_columns_once
        # と同じ方針。ただし永続化はしないのでフラグだけで足りる）。
        # 初回が配当のみの月だった場合、優待内容列は見出し幅で確定するため、非空の
        # 優待内容を初めて描いたときにその列だけ片方向で広げる。
        has_benefit_detail = with_benefit and any(item.benefit_detail for item in items)
        if items and not self._monthly_columns_sized:
            self._monthly_columns_sized = True
            self._monthly_benefit_detail_sized = has_benefit_detail
            self._monthly_view.resizeColumnsToContents()
            clamp_free_text_columns(self._monthly_view, MONTHLY_COLUMNS)
        elif has_benefit_detail and not getattr(
            self, "_monthly_benefit_detail_sized", False
        ):
            self._monthly_benefit_detail_sized = True
            self._resize_monthly_benefit_detail_column()

    def _resize_monthly_benefit_detail_column(self) -> None:
        """月別内訳の「優待内容」列を、非空の優待内容を初めて描画したときに片方向で広げる。

        配当のみの月で初回描画（_monthly_columns_sized）が確定すると、優待内容列は
        見出し幅のまま残る。後から優待のある月を選んだとき、resizeColumnToContents は
        使わず（手で広げた幅を縮めない）、上限 FREE_TEXT_COLUMN_MAX_WIDTH の範囲で
        足りない分だけ広げる。
        """
        col = MONTHLY_COL_BENEFIT_DETAIL
        header = self._monthly_view.horizontalHeader()
        needed = min(
            FREE_TEXT_COLUMN_MAX_WIDTH,
            max(header.sectionSizeHint(col), self._monthly_view.sizeHintForColumn(col)),
        )
        if header.sectionSize(col) < needed:
            header.resizeSection(col, needed)

    def _with_shares_label(
        self, columns: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """株数列のヘッダを現在の口数単位のラベルへ差し替えた列定義を返す。"""
        label = FUND_SHARE_UNIT_COLUMN_LABELS[self.fund_share_unit]
        return [(label if key == "shares" else lbl, key) for lbl, key in columns]

    def _render_table(self, tab_key, columns, model, view, rows, holding_indices: list[int] | None = None) -> None:
        state = self._capture_table_state(tab_key)
        header = view.horizontalHeader()
        sort_col = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()

        model.setRowCount(0)
        stored_indices: list[int] = []
        for i, row in enumerate(rows):
            items: list[QStandardItem] = []
            for _, key in columns:
                text, color = _format_cell(row, key, self.fund_share_unit)
                item = QStandardItem(text)
                if key in NUMERIC_SORT_COLUMNS:
                    item.setData(
                        cell_sort_value(row, key, self.fund_share_unit),
                        Qt.ItemDataRole.UserRole,
                    )
                    if key not in LEFT_ALIGNED_SORT_COLUMNS:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                if key == "split_flag" and text:
                    holding_idx = holding_indices[i] if holding_indices is not None else i
                    item.setToolTip(self._split_flag_tooltip(row, holding_idx))
                    # 太字は未反映の旗だけ。完了表示は目立たせない（対応が要るのは旗のほう）。
                    if row.get("pending_splits"):
                        flag_font = item.font()
                        flag_font.setBold(True)
                        item.setFont(flag_font)
                if key in FREE_TEXT_COLUMNS and text:
                    # 列幅の上限で切れた分をツールチップで読めるようにする。
                    item.setToolTip(text)
                if color is not None:
                    item.setForeground(color)
                items.append(item)
            model.appendRow(items)
            stored_indices.append(holding_indices[i] if holding_indices is not None else i)
        self.row_holding_indices[tab_key] = stored_indices
        view.sortByColumn(sort_col, sort_order)
        self._size_columns_once(tab_key, view, columns)
        self._restore_table_state(tab_key, state)

    def _resize_yield_columns(self) -> None:
        """配当優待詳細タブの利回り2列を、必要なときだけ広げる。

        優待スイッチでヘッダが `簿価利回り` ⇔ `簿価利回り＋優待` と伸び縮みする。
        列幅は初回描画時に確定して以降は維持する（_size_columns_once）ので、
        短いヘッダで確定したあと優待を含めると接尾辞が切れ、利回りの定義が
        判別できなくなる。**_columns_sized を落として全体をやり直さないこと**
        ——ユーザーが広げた他の列の幅まで巻き戻る。動かすのはこの2列だけにする。

        **resizeColumnToContents は使わない**——内容幅ちょうどに合わせる＝
        ユーザーが手で広げた幅を縮めてしまう（優待を切り替えただけで列が痩せる）。
        収まらないときだけ広げる片方向にする。
        """
        header = self._income_view.horizontalHeader()
        for col, (label, _) in enumerate(INCOME_COLUMNS):
            if label not in ("簿価利回り", "時価利回り"):
                continue
            # ヘッダ（文字＋余白＋ソート矢印）と中身、どちらも収まる幅。
            needed = max(
                header.sectionSizeHint(col), self._income_view.sizeHintForColumn(col)
            )
            if header.sectionSize(col) < needed:
                header.resizeSection(col, needed)

    def _size_columns_once(
        self, tab_key: str, view: QTableView, columns: list[tuple[str, str]]
    ) -> None:
        """列幅を初回描画時のみ調整する（以降はユーザーの手動調整幅を維持）。

        保存済みの列幅（前回終了時）があればそれを適用し、無ければ内容に合わせる。
        保存値の要素数が現在の列数と食い違う場合（列の追加・削除をまたいだ設定）は
        列ごとに幅がずれるため、保存値を捨てて内容に合わせ直す。
        """
        if self._columns_sized[tab_key]:
            return
        self._columns_sized[tab_key] = True
        header = view.horizontalHeader()
        saved = self._saved_column_widths.get(tab_key) or []
        if len(saved) == header.count():
            for col, width in enumerate(saved):
                header.resizeSection(col, width)
        else:
            view.resizeColumnsToContents()
            # 保存値を使ったときは当てない（ユーザーが広げた幅を縮めてしまう）。
            clamp_free_text_columns(view, columns)

    def _capture_table_state(self, tab_key: str) -> dict:
        """再描画前の選択行（holdings インデックス）とスクロール位置を取得する。"""
        view = self._view_for_tab(tab_key)
        proxy = self._proxy_for_tab(tab_key)
        state: dict = {
            "holding_idx": None,
            "view_row": None,
            "scroll": view.verticalScrollBar().value(),
        }
        idx = view.currentIndex()
        if idx.isValid():
            state["view_row"] = idx.row()
            src_row = proxy.mapToSource(idx).row()
            indices = self.row_holding_indices[tab_key]
            if 0 <= src_row < len(indices):
                state["holding_idx"] = indices[src_row]
        return state

    def _restore_table_state(self, tab_key: str, state: dict) -> None:
        """再描画後に選択行とスクロール位置を復元する。

        元の銘柄（holdings インデックス）を最優先で選択し直し、削除などで
        見つからない場合は元の視覚行位置へフォールバックする。
        """
        view = self._view_for_tab(tab_key)
        proxy = self._proxy_for_tab(tab_key)
        target_row: int | None = None
        if state.get("holding_idx") is not None:
            src_row = row_for_holding_index(
                self.row_holding_indices[tab_key], state["holding_idx"]
            )
            if src_row is not None:
                proxy_idx = proxy.mapFromSource(proxy.sourceModel().index(src_row, 0))
                if proxy_idx.isValid():
                    target_row = proxy_idx.row()
        if target_row is None and state.get("view_row") is not None and proxy.rowCount() > 0:
            target_row = min(state["view_row"], proxy.rowCount() - 1)
        if target_row is not None and target_row >= 0:
            view.selectRow(target_row)
        view.verticalScrollBar().setValue(state.get("scroll", 0))

    # ---- アクション ----
    def _set_dividend_mode(self, mode: str) -> None:
        """税引前/税引後を設定する（ドロップダウン選択とショートカットの共通経路）。"""
        if mode == self.dividend_mode:
            return
        self.dividend_mode = mode
        self._save_or_notify(lambda: save_settings(dividend_mode=mode))
        self._sync_mode_controls()
        self._refresh_view()

    def _set_withdrawal_mode(self, mode: str | None) -> None:
        """収入モードを設定する（ドロップダウン選択とショートカットの共通経路）。"""
        if mode == self.withdrawal_mode:
            return
        self.withdrawal_mode = mode
        self._save_or_notify(lambda: save_settings(withdrawal_mode=mode))
        self._sync_mode_controls()
        self._refresh_view()

    def action_toggle_mode(self) -> None:
        self._set_dividend_mode("net" if self.dividend_mode == "gross" else "gross")

    def action_toggle_withdrawal(self) -> None:
        cycle: dict[str | None, str | None] = {None: "fixed", "fixed": "rate", "rate": None}
        self._set_withdrawal_mode(cycle[self.withdrawal_mode])

    def action_toggle_inactive(self) -> None:
        """無効銘柄をテーブルへ表示するかを切り替える。

        無効銘柄は集計・構成グラフの対象外のまま、テーブルにだけ淡色で並ぶ。
        これが無いと編集ダイアログで「有効」を外した銘柄へ二度と到達できない。
        """
        self.show_inactive = not self.show_inactive
        self._save_or_notify(lambda: save_settings(show_inactive=self.show_inactive))
        action = self._actions.get("toggle_inactive")
        if action is not None:
            # setChecked は triggered を発火しないので再入しない。
            action.setChecked(self.show_inactive)
        self._refresh_view()
        self._notify(
            "無効銘柄を表示します（淡色＝集計対象外）"
            if self.show_inactive
            else "無効銘柄を非表示にしました"
        )

    def action_set_theme(self, name: str) -> None:
        """表示テーマ（ダーク / ライト）を切り替えて設定へ保存する。

        QSS を作り直して貼り直すだけではチャートは変わらない（描画時に theme を
        読むため）ので、_refresh_view() で全ウィジェットを描き直す。タイトルバーは
        QSS の外なので DWM 側も切り替える。
        """
        applied = theme.set_theme(name)
        if applied == self.theme_name:
            self._sync_theme_actions()
            return
        self.theme_name = applied
        self.setStyleSheet(theme.build_app_qss())
        self._apply_title_bar_theme()
        self._sync_theme_actions()
        self._save_or_notify(lambda: save_settings(theme=applied))
        self._refresh_view()
        self._notify(f"テーマを「{theme.theme_label(applied)}」に切り替えました")

    def action_set_fund_share_unit(self, unit: str) -> None:
        """投信の口数を表示・入力する単位を切り替えて設定へ保存する。

        変えるのは表示（株数列とヘッダ）と編集ダイアログの入力欄だけで、CSV の
        shares は常に万口単位のまま＝計算結果（時価・簿価・配当）は動かない。
        """
        if unit not in FUND_SHARE_UNITS:
            return
        if unit == self.fund_share_unit:
            self._sync_fund_unit_actions()
            return
        self.fund_share_unit = unit
        self._sync_fund_unit_actions()
        self._save_or_notify(lambda: save_settings(fund_share_unit=unit))
        self._refresh_view()
        # 株数列は桁数が 1万倍変わる。保存済みの列幅のままだと数字が切れて
        # 読めないので、この列だけ内容へ合わせ直す（他の列の幅は触らない）。
        for view, columns in (
            (self._price_view, PRICE_COLUMNS), (self._income_view, INCOME_COLUMNS)
        ):
            view.resizeColumnToContents([key for _, key in columns].index("shares"))
        self._notify(
            f"投信の口数を「{FUND_SHARE_UNIT_LABELS[unit]}」で表示します"
            "（保存値・計算結果は変わりません）"
        )

    def _sync_fund_unit_actions(self) -> None:
        """口数単位のチェック状態を現在値へ揃える（不正な設定値で開いた場合の保険）。"""
        for unit in FUND_SHARE_UNITS:
            action = self._actions.get(f"fund_unit_{unit}")
            if action is not None:
                action.setChecked(unit == self.fund_share_unit)

    def _sync_theme_actions(self) -> None:
        """テーマのチェック状態を現在値へ揃える（未知の設定値で開いた場合の保険）。"""
        for name in theme.THEMES:
            action = self._actions.get(f"theme_{name}")
            if action is not None:
                # setChecked は triggered を発火しないので再入しない。
                action.setChecked(name == self.theme_name)

    def _active_tab_key(self) -> str:
        # タブ構成: [評価額(0), 配当金(1), 月別配当(2), 株価詳細(3), 配当優待詳細(4)]。
        # 0/1/2 はグラフタブで編集・削除・ソート対象外（dashboard 扱い）。
        # 月別配当の内訳テーブルは読み取り専用なので、ここで拾わないのが正しい
        # （拾うと _view_for_tab が株価テーブルを返し、別タブの行を編集してしまう）。
        idx = self._tabs.currentIndex()
        if idx == 3:
            return "price"
        if idx == 4:
            return "income"
        return "dashboard"

    def _view_for_tab(self, tab_key: str) -> QTableView:
        return self._income_view if tab_key == "income" else self._price_view

    def _proxy_for_tab(self, tab_key: str):
        return self._income_proxy if tab_key == "income" else self._price_proxy

    def action_edit(self) -> None:
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return
        view = self._view_for_tab(tab_key)
        proxy = self._proxy_for_tab(tab_key)
        idx = view.currentIndex()
        if not idx.isValid():
            return
        src_row = proxy.mapToSource(idx).row()
        indices = self.row_holding_indices[tab_key]
        if src_row < 0 or src_row >= len(indices):
            return
        holding_idx = indices[src_row]
        if holding_idx < 0 or holding_idx >= len(self.holdings):
            return
        holding = self.holdings[holding_idx]
        # market は支払月の自動判定プレビューに使う（率と同じく表示層で算出しない）。
        dlg = EditHoldingDialog(
            replace(holding), self, market=self.market_data_map.get(holding.symbol),
            fund_share_unit=self.fund_share_unit,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            holdings = list(self.holdings)
            holdings[holding_idx] = dlg.result_holding
            if not self._save_or_notify(lambda: save_holdings(holdings)):
                return
            self.holdings = holdings
            self._reload_all()
            # 種別・通貨を変えて表示対象外になると、保存した直後に行が消える。
            self._notify(
                "銘柄を更新しました" + self._out_of_scope_note(dlg.result_holding)
            )

    def action_delete(self) -> None:
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return
        view = self._view_for_tab(tab_key)
        proxy = self._proxy_for_tab(tab_key)
        idx = view.currentIndex()
        if not idx.isValid():
            return
        src_row = proxy.mapToSource(idx).row()
        indices = self.row_holding_indices[tab_key]
        if src_row < 0 or src_row >= len(indices):
            return
        holding_idx = indices[src_row]
        if holding_idx < 0 or holding_idx >= len(self.holdings):
            return
        holding = self.holdings[holding_idx]
        label = holding.name or holding.symbol or "(無名)"
        reply = QMessageBox.question(
            self,
            "銘柄削除",
            f"「{label}」を削除しますか？\nこの操作は元に戻せません（CSV は自動バックアップされます）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        holdings = list(self.holdings)
        del holdings[holding_idx]
        if not self._save_or_notify(lambda: save_holdings(holdings)):
            return
        self.holdings = holdings
        self._reload_all()
        self._notify("銘柄を削除しました")

    def action_new_holding(self) -> None:
        holding = Holding(
            symbol="", name="", asset_type="stock", currency="JPY",
            shares=0.0, avg_cost=0.0, account="",
            annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
            div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
            withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
            # 今日入力する株数・単価は今日時点の値（過去の分割は反映済み）。空のままだと
            # 直近60日の分割に旗が立ち、入力済みの数値へ二重に補正をかけかねない。
            split_applied_through=today_jst_str(),
        )
        dlg = NewHoldingDialog(holding, self, fund_share_unit=self.fund_share_unit)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            result = dlg.result_holding
            holdings = [*self.holdings, result]
            if not self._save_or_notify(lambda: save_holdings(holdings)):
                return
            self.holdings = holdings
            self._reload_all()
            self._select_holding_row(len(self.holdings) - 1)
            self._notify("銘柄を追加しました" + self._out_of_scope_note(result))

    # ---- 株式分割の補正 ----
    def _current_holding_index(self) -> int | None:
        """現在のテーブルタブで選択中の行に対応する holdings インデックス。無ければ None。"""
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return None
        view = self._view_for_tab(tab_key)
        idx = view.currentIndex()
        if not idx.isValid():
            return None
        src_row = self._proxy_for_tab(tab_key).mapToSource(idx).row()
        indices = self.row_holding_indices[tab_key]
        if src_row < 0 or src_row >= len(indices):
            return None
        holding_idx = indices[src_row]
        if holding_idx < 0 or holding_idx >= len(self.holdings):
            return None
        return holding_idx

    def _selected_pending_splits(self) -> tuple[int, list[splits.Split]] | None:
        """選択行が未反映の分割を持つなら (holdings インデックス, 分割)。無ければ None。"""
        holding_idx = self._current_holding_index()
        if holding_idx is None:
            return None
        holding = self.holdings[holding_idx]
        events = splits.pending_splits(holding, self.market_data_map.get(holding.symbol))
        return (holding_idx, events) if events else None

    def _split_flag_tooltip(self, row: dict, holding_idx: int) -> str:
        """旗セルのツールチップ。補正前後を見せて、押す前に中身を確認できるようにする。"""
        events = row.get("pending_splits") or []
        if not events:
            done = row.get("applied_splits") or []
            if not done:
                return ""
            return (
                "株式分割は反映済みです（"
                + "、".join(f"{d} {splits.format_ratio(r)}" for d, r in done)
                + "）。\n株数・購入単価は分割後の値として扱っています。"
                f"この表示は反映から{splits.APPLIED_DISPLAY_DAYS}日で消えます。"
            )
        if not (0 <= holding_idx < len(self.holdings)):
            return ""
        lines = splits.correction_lines(
            self.holdings[holding_idx], events, row.get("last_price")
        )
        return (
            "株式分割が株数・購入単価へ未反映です\n\n"
            + "\n".join(lines)
            + "\n\nデータメニューの「分割を反映」で補正します。"
            "証券会社の値に合わせて手で直済みなら「分割を反映済みにする」を選びます。"
        )

    @staticmethod
    def _holding_label(holding: Holding) -> str:
        name = holding.name or holding.symbol or "(無名)"
        return f"{name}（{holding.symbol}／{holding.account or '口座なし'}）"

    def _split_notice(self) -> str:
        """取得後にステータスバーへ添える、未反映の分割の件数メッセージ（無ければ ""）。"""
        pending = splits.pending_holdings(
            self.holdings, self.market_data_map, include_inactive=self.show_inactive
        )
        if not pending:
            return ""
        symbols = ", ".join(dict.fromkeys(self.holdings[i].symbol for i, _ in pending))
        return (
            f" ／ 株式分割の未反映 {len(pending)}件: {symbols}"
            "（「分割」列の旗を確認し、データメニューの「分割を反映」で補正）"
        )

    def _commit_split_holdings(self, updates: dict[int, Holding], message: str) -> None:
        """holdings の該当行を差し替えて保存し、再読込する（補正・反映済み記録の共通経路）。"""
        holdings = list(self.holdings)
        for index, holding in updates.items():
            holdings[index] = holding
        if not self._save_or_notify(lambda: save_holdings(holdings)):
            return
        self.holdings = holdings
        self._reload_all()
        self._notify(message)

    def action_apply_split(self) -> None:
        """選択行の未反映の分割を、株数・購入単価・配当の上書き値へ反映する。

        書き換える前に補正前後と現在株価を見せて確認する。手で直済みの行への二重適用は
        エラーにならず時価だけが狂うので、既定は「いいえ」にして、逃げ道
        （分割を反映済みにする）を文面で案内する。
        """
        target = self._selected_pending_splits()
        if target is None or self._busy:
            return
        holding_idx, events = target
        holding = self.holdings[holding_idx]
        market = self.market_data_map.get(holding.symbol)
        lines = splits.correction_lines(
            holding, events, market.last_price if market else None
        )
        reply = QMessageBox.question(
            self,
            "株式分割の反映",
            f"{self._holding_label(holding)} に株式分割を反映します。\n\n"
            + "\n".join(lines)
            + "\n\n購入単価と現在株価の桁が合っているか確認してください。"
            "すでに証券会社の値に合わせて株数・単価を手で直してある場合は"
            "「いいえ」を選び、「分割を反映済みにする」を使ってください"
            "（二重に反映されます）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        corrected = splits.apply_correction(holding, events)
        self._commit_split_holdings(
            {holding_idx: corrected},
            f"{holding.name or holding.symbol} に分割を反映しました"
            f"（株数 {holding.shares:g} → {corrected.shares:g}）",
        )

    def action_dismiss_split(self) -> None:
        """選択行の分割を「反映済み」とだけ記録する（株数・単価は変えない）。"""
        target = self._selected_pending_splits()
        if target is None or self._busy:
            return
        holding_idx, events = target
        holding = self.holdings[holding_idx]
        reply = QMessageBox.question(
            self,
            "分割を反映済みにする",
            f"{self._holding_label(holding)} の分割（"
            + "、".join(f"{d} {splits.format_ratio(r)}" for d, r in events)
            + "）を反映済みとして記録します。\n株数・購入単価・配当の上書きは変更しません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._commit_split_holdings(
            {holding_idx: splits.mark_applied(holding, events)},
            f"{holding.name or holding.symbol} の分割を反映済みにしました",
        )

    def action_apply_all_splits(self) -> None:
        """未反映の分割を持つ全行へ、まとめて反映する（確認は1回）。"""
        if self._busy:
            return
        pending = splits.pending_holdings(
            self.holdings, self.market_data_map, include_inactive=self.show_inactive
        )
        if not pending:
            return
        summary = []
        # 一括反映は表示対象に関係なく全件へ掛ける（未反映が残ると推移の記録が
        # 止まるため）。画面に出ていない行が混ざることを確認文で必ず示す。
        hidden_count = 0
        for holding_idx, events in pending:
            holding = self.holdings[holding_idx]
            plan = splits.plan_correction(holding, events)
            hidden = (
                asset_class(holding.asset_type, holding.currency)
                not in self.asset_scope
            )
            hidden_count += hidden
            summary.append(
                ("［表示対象外］" if hidden else "")
                + f"{self._holding_label(holding)}: "
                + "、".join(f"{d} {splits.format_ratio(r)}" for d, r in events)
                + f" / 株数 {holding.shares:g} → {plan.shares[1]:g}"
                f" / 単価 {holding.avg_cost:g} → {plan.avg_cost[1]:g}"
            )
        reply = QMessageBox.question(
            self,
            "未反映の分割をすべて反映",
            f"{len(pending)} 行に株式分割を反映します。"
            + (
                f"\n（うち {hidden_count} 行は表示対象外の資産で、いまの一覧には出ていません）"
                if hidden_count else ""
            )
            + "\n\n"
            + "\n".join(summary)
            + "\n\nすでに手で直してある行が混ざっていると二重に反映されます。"
            "その行は「いいえ」で戻り、1行ずつ「分割を反映済みにする」を使ってください。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        updates = {
            holding_idx: splits.apply_correction(self.holdings[holding_idx], events)
            for holding_idx, events in pending
        }
        self._commit_split_holdings(updates, f"{len(updates)} 行に分割を反映しました")

    def _select_holding_row(self, holding_idx: int) -> None:
        """指定 holdings インデックスの行を現在のテーブルタブで選択・表示する。

        ダッシュボードタブ表示中や、フィルタで対象行が非表示の場合は何もしない。
        """
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return
        view = self._view_for_tab(tab_key)
        proxy = self._proxy_for_tab(tab_key)
        src_row = row_for_holding_index(self.row_holding_indices[tab_key], holding_idx)
        if src_row is None:
            return
        proxy_idx = proxy.mapFromSource(proxy.sourceModel().index(src_row, 0))
        if proxy_idx.isValid():
            view.selectRow(proxy_idx.row())
            view.scrollTo(proxy_idx)

    # ---- バックグラウンド取得 ----
    def _start_worker(self, worker: QObject, on_finished, on_progress=None) -> None:
        if self._busy:
            self._notify("更新処理が実行中です", error=True)
            return
        self._busy = True
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if on_progress is not None:
            worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_done)
        self._thread = thread
        self._worker = worker
        self._sync_action_states()  # 実行中は更新系アクションを伏せる
        thread.start()

    def _on_thread_done(self) -> None:
        self._busy = False
        self._worker = None
        self._thread = None
        self._progress.setVisible(False)
        self._sync_action_states()

    def _on_worker_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._notify(message, error=True)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        # 取得処理中の終了は QThread を走行中のまま破棄してクラッシュするため抑止。
        if self._busy:
            self._notify("更新処理が実行中です。完了までお待ちください", error=True)
            event.ignore()
            return
        # 完了済みだが後始末前のスレッドが残っていれば確実に停止させてから終了。
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(3000)
        # 正常終了が確定したパスでのみウィンドウ状態を保存する
        # （busy で ignore した場合は保存しない）。
        self._save_window_state()
        event.accept()

    def action_update_market(self) -> None:
        if self._busy:
            self._notify("更新処理が実行中です", error=True)
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        # 有効な USD 銘柄があるときだけ為替も取る（円換算に効くのはそこだけ）。
        fetch_fx = any(h.active and h.currency == "USD" for h in self.holdings)
        self.statusBar().showMessage(
            "株価・配当・為替を取得中..." if fetch_fx else "株価・配当データを取得中..."
        )
        worker = MarketWorker(self.holdings, self.market_data_map, fetch_fx=fetch_fx)
        self._start_worker(worker, self._on_market_finished, self._on_market_progress)

    def _on_market_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)  # 為替の取得中（件数の無い段階）
        self.statusBar().showMessage(label)

    def _on_market_finished(self, update: MarketUpdate) -> None:
        result = update.market
        if not self._save_or_notify(lambda: save_market_data(result.rows)):
            return
        self.market_data_map = {row.symbol: row for row in result.rows}
        latest = max(((row.fetched_at or "") for row in result.rows), default="")
        self.market_fetched_at = latest or None
        self.market_source = result.source
        if result.name_updates:
            holdings = list(self.holdings)
            updated = False
            for index, holding in enumerate(holdings):
                if holding.symbol in result.name_updates and not holding.name:
                    holdings[index] = replace(holding, name=result.name_updates[holding.symbol])
                    updated = True
            if updated:
                if self._save_or_notify(lambda: save_holdings(holdings)):
                    self.holdings = holdings
        # 為替は再描画・スナップショットより前に反映する（記録する評価額が
        # 新しい株価×古いレートにならないように）。失敗はエラーに積むだけ。
        errors = list(result.errors)
        if update.fx_error:
            errors.append(update.fx_error)
        elif update.fx is not None and not self._store_fx(update.fx):
            errors.append("USDJPYレートを反映できませんでした")
        self._refresh_view()
        # 最新株価で評価額が確定した時点で当日スナップショットを記録。
        history_notice = self._record_history_snapshot()
        split_notice = self._split_notice()
        if history_notice:
            split_notice += " / " + history_notice
        if errors:
            self._notify(
                f"一部の取得に失敗しました（{len(errors)}件）: {errors[0]}"
                + split_notice,
                error=True,
            )
        else:
            updated = "株価・配当・為替" if update.fx is not None else "株価・配当"
            self._notify(f"{updated}を更新しました" + split_notice)

    def action_update_fx(self) -> None:
        if self._busy:
            self._notify("更新処理が実行中です", error=True)
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # indeterminate
        self.statusBar().showMessage("USDJPYレートを取得中...")
        self._start_worker(FxWorker(), self._on_fx_finished)

    def _store_fx(self, fx: FXRate) -> bool:
        """取得した為替を fx.csv へ追記して現在値にする（F5 と Ctrl+U の共通経路）。

        使えないレート（0・nan）は保存も反映もしない（latest_fx と同じ基準）。
        失敗の通知はここで出す（保存失敗は _save_or_notify が理由付きで出す）。
        """
        if latest_fx([fx]) is None:
            self._notify(f"USDJPYレートが不正な値です: {fx.rate}", error=True)
            return False
        rows = load_fx()
        rows.append(fx)
        if not self._save_or_notify(lambda: save_fx(rows)):
            return False
        self.fx_rate = fx.rate
        self.fx_fetched_at = fx.timestamp
        return True

    def _on_fx_finished(self, fx) -> None:
        if fx:
            if not self._store_fx(fx):
                return
            self._refresh_view()
            history_notice = self._record_history_snapshot()
            self._notify(
                f"USDJPY = {fx.rate:.4f} に更新しました"
                + (" / " + history_notice if history_notice else "")
            )
        else:
            self._notify("USDJPYレートを取得できませんでした", error=True)

    def _notify(self, message: str, error: bool = False) -> None:
        """ステータスバーへ一時メッセージを出す（エラーは警告色＋⚠ で差別化）。

        配色は theme.build_app_qss の QStatusBar[error="true"] が持つ。動的
        プロパティは unpolish/polish を挟まないと QSS へ反映されない
        （dialogs._revalidate の赤枠切替と同じ手順）。
        """
        bar = self.statusBar()
        bar.setProperty("error", bool(error))
        bar.style().unpolish(bar)
        bar.style().polish(bar)
        bar.showMessage(f"⚠ {message}" if error else message, 6000)

    def _save_or_notify(self, action) -> bool:
        try:
            action()
        except OSError as exc:
            self._notify(str(exc), error=True)
            return False
        return True


# アプリアイコン。生成は tools/make_app_icon.py で行い、ここでは読み込むだけにする
# （起動パスへ描画処理を載せない）。これで効くのは**タイトルバー**まで。
# Windows のタスクバーはウィンドウのアイコンではなく起動元のショートカットから
# アイコンを引くため、そちらは tools/make_shortcut.ps1 が担当する（実測は CLAUDE.md）。
APP_ICON_PATH = Path(__file__).resolve().parents[1] / "assets" / "app.ico"


def _apply_app_icon(app: QApplication) -> None:
    """アプリ既定のウィンドウアイコンを設定する（無ければ何もしない）。

    ファイルが欠けても例外にしない。build_window() はヘッドレス検証の入口でもあり、
    見た目だけの資産で検証が落ちるのは割に合わない。
    """
    if not APP_ICON_PATH.exists():
        return
    icon = QIcon(str(APP_ICON_PATH))
    if not icon.isNull():
        app.setWindowIcon(icon)


def build_window() -> DividendWindow:
    """ウィンドウを生成して返す（ヘッドレス検証用ファクトリ）。"""
    _ensure_app()
    return DividendWindow()


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    # Fusion は QSS と相性が良く、矢印等の複合サブコントロールが崩れない
    # （Windows 既定スタイルは部分的な QSS 適用で描画が乱れることがある）。
    app.setStyle("Fusion")
    _apply_app_icon(app)
    # QSS の font-family は QPainter 直描画（チャート類の self.font()）へ
    # 反映されないため、アプリ既定フォントも同じ族で揃える。
    app.setFont(QFont(theme.FONT_FAMILY_PRIMARY, theme.FONT_SIZE_PT))
    return app


def main() -> None:
    app = _ensure_app()
    win = build_window()
    win.show()
    sys.exit(app.exec())
