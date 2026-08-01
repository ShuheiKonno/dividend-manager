"""PySide6 ネイティブGUI（配当管理アプリのエントリ）。

ロジック層（io_csv / models / calc / market_data / utils）は無改変で再利用し、
表示層のみを Qt6 で再構築する。レガシー TUI は src/legacy_tui.py に温存。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal, QSortFilterProxyModel, QRect
from PySide6.QtGui import (
    QAction,
    QStandardItemModel,
    QStandardItem,
    QShortcut,
    QKeySequence,
    QColor,
    QFont,
    QGuiApplication,
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
    QToolBar,
    QSizePolicy,
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
from .io_csv import (
    init_empty_files,
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
    read_history_csv,
    read_holdings_csv,
)
from .models import Holding, HistoryRecord
from .calc import compute_rows, monthly_breakdown
from .history import upsert_record, merge_records, combine_income
# market_data は yfinance / ToushinReader / pandas を芋づる式に読み込み起動を数秒遅らせる。
# 取得は p / u キーのワーカースレッドでしか走らないため、import を run() 内へ遅延させる。
from .theme import (
    ACCENT,
    build_app_qss,
    FONT_FAMILY_PRIMARY,
    FONT_SIZE_PT,
    TEXT_DISABLED,
)
from .utils import fmt_number, fmt_percent, now_jst, safe_float, today_jst_str
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
    ("種別", "asset_type"),
    ("株数", "shares"),
    ("通貨", "currency"),
    ("配当/株", "annual_div_ps"),
    ("年間配当(円)", "annual_dividend_jpy"),
    ("優待(円)", "annual_benefit_jpy"),
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
# 評価額タブの推移系列 (ラベル, history キー, 線色)。評価額＋簿価の2本固定。
ASSET_TREND_SERIES = [
    ("評価額", "total_market_value_jpy", QColor("#42a5f5")),
    ("簿価", "total_book_value_jpy", QColor("#9e9e9e")),
]
# 配当タブの推移は優待/取崩の選択で合成した単一系列（_refresh_trend で構築）。
INCOME_TREND_COLOR = QColor("#66bb6a")
# 月別配当タブの積み上げ系列 (ラベル, バケットのキー, 塗り色)。下から順に積む。
# 緑は配当推移の線が使っているため、優待は橙にして系列の取り違えを避ける。
MONTHLY_SERIES = [
    ("配当", "dividend_jpy", QColor(ACCENT)),
    ("優待", "benefit_jpy", QColor("#ffa726")),
]
# 月別内訳リストの列。編集導線を持たない読み取り専用テーブル。
MONTHLY_COLUMNS = [
    ("コード", "symbol"),
    ("銘柄名", "name"),
    ("口座", "account"),
    ("配当(円)", "dividend_jpy"),
    ("優待(円)", "benefit_jpy"),
    ("合計(円)", "total_jpy"),
]
NUMERIC_SORT_COLUMNS = {
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
    "profit_jpy",
    "profit_rate",
    "dividend_jpy",
    "benefit_jpy",
    "total_jpy",
}


def row_for_holding_index(stored_indices: list[int], holding_idx: int) -> int | None:
    """row_holding_indices のリストから該当 holdings インデックスのソース行を探す。

    見つからない場合（削除直後など）は None を返す。
    """
    try:
        return stored_indices.index(holding_idx)
    except ValueError:
        return None


def _format_cell(row: dict, key: str) -> tuple[str, Optional[QColor]]:
    """セル表示文字列と文字色を返す。

    無効（active=False）銘柄は集計対象外なので、損益の赤青より優先して
    行全体を淡色にする。
    """
    text, color = _cell_text_and_color(row, key)
    if row.get("active") is False:
        return text, QColor(TEXT_DISABLED)
    return text, color


def _cell_text_and_color(row: dict, key: str) -> tuple[str, Optional[QColor]]:
    """有効銘柄としての表示文字列と文字色（損益のみ赤青）を返す。"""
    val = row.get(key)
    if key == "asset_type":
        text = str(val or "")
        return ASSET_TYPE_LABELS.get(text, text), None
    if key in ("symbol", "name", "account", "currency", "div_forecast_as_of"):
        return str(val or ""), None
    if key in ("shares", "avg_cost", "last_price", "annual_div_ps"):
        return fmt_number(val, 2), None
    if key in ("annual_dividend_jpy", "annual_benefit_jpy", "book_value_jpy", "market_value_jpy"):
        return fmt_number(val, 0), None
    if key == "profit_jpy":
        return fmt_number(val, 0), profit_text_qcolor(val)
    if key == "profit_rate":
        return fmt_percent(val, 2), profit_text_qcolor(val)
    if key in ("yield_on_cost", "yield_on_value"):
        return fmt_percent(val, 2), None
    return str(val or ""), None


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

class MarketWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, holdings: list[Holding], market_map: dict) -> None:
        super().__init__()
        self._holdings = holdings
        self._market_map = market_map

    def run(self) -> None:
        from .market_data import fetch_market_data
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
        self.finished.emit(result)


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
        self.setWindowTitle("配当管理")

        _saved = load_settings()
        self._restore_window_geometry(_saved.get("window_geometry"))
        self.dividend_mode: str = _saved.get("dividend_mode", "gross")
        self.withdrawal_mode: str | None = _saved.get("withdrawal_mode", None)
        self.asset_chart_type: str = _saved.get("asset_chart_type", "treemap")
        self.income_chart_type: str = _saved.get("income_chart_type", "treemap")
        self.asset_trend_period: str = _saved.get("asset_trend_period", "all")
        self.income_trend_period: str = _saved.get("income_trend_period", "all")
        self.asset_trend_baseline: str = _saved.get("asset_trend_baseline", "zero")
        self.income_trend_baseline: str = _saved.get("income_trend_baseline", "zero")
        # 配当推移グラフの系列選択（優待込み / 取崩込み）。既定は配当のみ。
        self.income_benefit: str = _saved.get("income_benefit", "without")
        self.income_withdrawal: str = _saved.get("income_withdrawal", "without")
        # 円グラフの「その他」集約閾値（評価額/配当タブで独立）と起動時の表示タブ。
        self.pie_threshold_asset: float = float(_saved.get("pie_threshold_asset", 2.0))
        self.pie_threshold_income: float = float(_saved.get("pie_threshold_income", 2.0))
        # 無効銘柄をテーブルへ表示するか。既定は非表示（従来どおり）だが、
        # 表示できないと編集ダイアログで無効化した銘柄を復帰できなくなる。
        self.show_inactive: bool = bool(_saved.get("show_inactive", False))
        # 月別配当タブ: 内訳リストの並び順は永続化し、選択月は当月から始める
        # （前回終了時の月を復元しても翌月には陳腐化するため保存しない）。
        self.monthly_sort: str = _saved.get("monthly_sort", "desc")
        self.monthly_month: int = now_jst().date().month
        self._monthly_label: str = "年間配当"
        self._monthly_columns_sized: bool = False
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
        self._apply_dark_title_bar()

        init_empty_files()
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

    def _apply_dark_title_bar(self) -> None:
        """Windows のタイトルバーをダーク化する（DWM API、失敗しても無害）。"""
        try:
            import ctypes

            hwnd = int(self.winId())
            value = ctypes.c_int(1)
            # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（Win10 20H1 以降 / Win11）
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:  # noqa: BLE001 — 非 Windows や旧ビルドでは何もしない
            pass

    # ---- UI 構築 ----
    def _build_ui(self) -> None:
        # アプリ全体をダークに統一（TreemapWidget の濃色背景と整合させ、
        # Win11 既定ライトテーマとの混在を避ける。テーマ切替は対象外＝固定）。
        # 配色・フォント等のトークンと QSS 生成は src/theme.py に集約。
        self.setStyleSheet(build_app_qss())
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
        self._tabs.addTab(self._income_view, "配当詳細")
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
        ("update_market", "株価・配当を取得", "F5", "action_update_market"),
        ("update_fx", "USDJPYを取得", "Ctrl+U", "action_update_fx"),
        ("reload", "再読込", "Ctrl+R", "_reload_all"),
        ("new", "新規登録...", "Ctrl+N", "action_new_holding"),
        ("edit", "編集...", "Ctrl+E", "action_edit"),
        ("delete", "削除", "Ctrl+D", "action_delete"),
        ("toggle_mode", "税引前 / 税引後 の切替", "Ctrl+M", "action_toggle_mode"),
        ("toggle_withdrawal", "収入モードの切替", "Ctrl+I", "action_toggle_withdrawal"),
        ("export_holdings", "保有銘柄...", None, "action_export_holdings"),
        ("export_history", "推移履歴...", None, "action_export_history"),
        ("import_holdings", "保有銘柄...", None, "action_import_holdings"),
        ("import_history", "推移履歴...", None, "action_import_history"),
        ("focus_filter", "絞り込みへ移動", "Ctrl+F", "_focus_filter"),
        ("help_shortcuts", "キーボードショートカット一覧...", "F1", "_show_shortcut_help"),
        ("quit", "終了", "Ctrl+Q", "close"),
    ]
    # タブ切替（Ctrl+1〜5）。ラベルは QTabWidget のタブ名と揃える。
    _TAB_ACTION_LABELS = ["評価額", "配当金", "月別配当", "株価詳細", "配当詳細"]

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

        # 無効銘柄トグルはチェック状態を持たせ、現在値をメニュー上で示す。
        inactive = QAction("無効銘柄も表示", self)
        inactive.setShortcut(QKeySequence("Ctrl+Shift+I"))
        inactive.setCheckable(True)
        inactive.setChecked(self.show_inactive)
        inactive.triggered.connect(lambda _checked=False: self.action_toggle_inactive())
        self.addAction(inactive)
        self._actions["toggle_inactive"] = inactive

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
        export_menu = file_menu.addMenu("エクスポート (CSV)")
        export_menu.addAction(act["export_holdings"])
        export_menu.addAction(act["export_history"])
        import_menu = file_menu.addMenu("インポート (CSV)")
        import_menu.addAction(act["import_holdings"])
        import_menu.addAction(act["import_history"])
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

        view_menu = bar.addMenu("表示(&V)")
        view_menu.addAction(act["toggle_mode"])
        view_menu.addAction(act["toggle_withdrawal"])
        view_menu.addAction(act["toggle_inactive"])
        view_menu.addSeparator()
        view_menu.addAction(act["focus_filter"])
        view_menu.addSeparator()
        for index in range(len(self._TAB_ACTION_LABELS)):
            view_menu.addAction(act[f"tab{index}"])

        help_menu = bar.addMenu("ヘルプ(&H)")
        help_menu.addAction(act["help_shortcuts"])

    def _show_shortcut_help(self) -> None:
        """キーボードショートカットの一覧を表示する（F1）。"""
        QMessageBox.information(
            self,
            "キーボードショートカット",
            "F5: 株価・配当を取得\n"
            "Ctrl+U: USDJPYレートを取得\n"
            "Ctrl+R: 再読込（ローカルCSV）\n"
            "\n"
            "Ctrl+N: 新規登録\n"
            "Ctrl+E: 選択銘柄の編集（ダブルクリック / Enter でも可）\n"
            "Ctrl+D / Delete: 選択銘柄の削除\n"
            "\n"
            "Ctrl+M: 税引前 / 税引後 の切替\n"
            "Ctrl+I: 収入モードの切替（配当 → 定額取崩 → 定率取崩）\n"
            "Ctrl+Shift+I: 無効銘柄の表示切替\n"
            "\n"
            "Ctrl+1〜5: タブ切替（評価額 / 配当金 / 月別配当 / 株価詳細 / 配当詳細）\n"
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

        表示モードと絞り込みは2段目へ分ける。1段に詰めると最小ウィンドウ幅では
        収まらず、あふれた項目（＝右端の絞り込みボックス）が Qt の拡張メニューへ
        追いやられて画面から消えてしまう。
        """
        bar = QToolBar("操作")
        bar.setObjectName("mainToolBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        act = self._actions

        for key in ("update_market", "update_fx", "reload"):
            bar.addAction(act[key])
        bar.addSeparator()
        for key in ("new", "edit", "delete"):
            bar.addAction(act[key])
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
        """表示モードの切替と絞り込みを2段目のツールバーへ配置する。

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

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        bar.addWidget(spacer)

        bar.addWidget(QLabel("絞り込み: "))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("コード / 銘柄名 / 口座")
        self._filter.setClearButtonEnabled(True)
        # 固定幅にすると狭いウィンドウで収まらず拡張メニューへ飛んで消えるため、
        # 下限を決めたうえで縮められるようにする。
        self._filter.setMinimumWidth(150)
        self._filter.setMaximumWidth(260)
        # proxy は後段で生成されるが、textChanged は実行時にしか発火しないため接続順は問題ない。
        self._filter.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self._filter)

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
        head = QHBoxLayout()
        self._monthly_head = QLabel("")
        head.addWidget(self._monthly_head)
        head.addStretch()
        head.addWidget(QLabel("並び替え:"))
        head.addWidget(self._make_combo(
            [("desc", "金額の多い順"), ("asc", "金額の少ない順")],
            self.monthly_sort, self._on_monthly_sort,
        ))
        bottom_v.addLayout(head)
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

    def _on_monthly_sort(self, key: str) -> None:
        self.monthly_sort = key
        self._save_or_notify(lambda: save_settings(monthly_sort=key))
        self._apply_monthly_sort()

    def _apply_monthly_sort(self) -> None:
        # proxy.sort ではなく view 経由で並べ替える（ヘッダのソート矢印も追随させる）。
        order = (
            Qt.SortOrder.AscendingOrder if self.monthly_sort == "asc"
            else Qt.SortOrder.DescendingOrder
        )
        self._monthly_view.sortByColumn(len(MONTHLY_COLUMNS) - 1, order)  # 合計列

    def _make_trend_controls(self, kind: str, line_chart: LineChartWidget) -> QWidget:
        """推移グラフ上部の操作行（期間・Y軸下限、配当タブは優待・取崩）を生成する。

        全てドロップダウン。期間/底辺は self.{asset,income}_trend_{period,baseline}、
        配当の優待/取崩は self.income_{benefit,withdrawal} に保持し設定へ永続化する。
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

        # 配当タブのみ: 系列の優待込み・取崩込みをドロップダウンで切替
        if kind == "income":
            row.addWidget(QLabel("優待:"))
            row.addWidget(self._make_combo(
                [("without", "含まない"), ("with", "含む")],
                self.income_benefit, lambda k: self._on_income_option("benefit", k),
            ))
            row.addWidget(QLabel("取崩:"))
            row.addWidget(self._make_combo(
                [("without", "無し"), ("with", "込み")],
                self.income_withdrawal,
                lambda k: self._on_income_option("withdrawal", k),
            ))
        row.addStretch()

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

    def _on_income_option(self, which: str, key: str) -> None:
        """配当推移グラフの優待/取崩選択を更新し、系列を再合成して再描画する。"""
        if which == "benefit":
            self.income_benefit = key
            self._save_or_notify(lambda: save_settings(income_benefit=key))
        else:
            self.income_withdrawal = key
            self._save_or_notify(lambda: save_settings(income_withdrawal=key))
        self._refresh_trend()

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
        for key in ("update_market", "update_fx", "reload", "new",
                    "import_holdings", "import_history"):
            self._actions[key].setEnabled(idle)
        # 出力対象が無いエクスポートは押せないようにする。
        self._actions["export_holdings"].setEnabled(idle and bool(self.holdings))
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
        self.holdings = load_holdings()
        market_data = load_market_data()
        self.market_data_map = {row.symbol: row for row in market_data}
        fx_rows = load_fx()
        self.fx_rate = fx_rows[-1].rate if fx_rows else None
        if market_data:
            latest = max((row.fetched_at or "") for row in market_data)
            self.market_fetched_at = latest or None
            self.market_source = market_data[0].source
        else:
            self.market_fetched_at = None
            self.market_source = None
        self.history = load_history()
        self._refresh_view()
        # 当日のスナップショットを履歴へ upsert（タブ切替で呼ばれる _refresh_view では
        # 記録せず、再読込・取得完了時のみ記録して CSV の過剰書込を避ける）。
        self._record_history_snapshot()

    def _refresh_view(self) -> None:
        rows, summary = compute_rows(
            self.holdings, self.market_data_map, self.fx_rate, self.dividend_mode,
            self.withdrawal_mode, include_inactive=self.show_inactive,
        )
        # rows と同じ条件・同じ順序で holdings インデックスを組む（表示行 ↔ 保有の対応）。
        visible_indices = [
            i for i, h in enumerate(self.holdings) if h.active or self.show_inactive
        ]
        self._render_table("price", PRICE_COLUMNS, self._price_model, self._price_view, rows, visible_indices)
        income_cols = list(INCOME_COLUMNS)
        if self.withdrawal_mode is not None:
            income_cols = [
                ("年間収入(円)", key) if lbl == "年間配当(円)" else (lbl, key)
                for lbl, key in income_cols
            ]
        self._income_model.setHorizontalHeaderLabels([lbl for lbl, _ in income_cols])
        self._render_table("income", income_cols, self._income_model, self._income_view, rows, visible_indices)
        # 構成グラフは集計と揃えるため常に有効銘柄のみを対象にする
        # （無効銘柄は Summary に含まれず、構成比に混ぜると割合が狂う）。
        chart_rows = [r for r in rows if r.get("active") is not False]
        self._treemap.set_data(chart_rows)
        self._piechart.set_data(chart_rows)
        # 配当額ベース: 面積 = 年間配当（取崩モード時は「年間収入」に上書きされた額）
        div_label = "年間収入" if self.withdrawal_mode is not None else "年間配当"
        div_empty = f"{div_label}データがありません（F5 で取得 / 配当のある銘柄が必要です）"
        self._treemap_div.set_data(
            chart_rows, value_key="annual_dividend_jpy", value_label=div_label,
            empty_message=div_empty,
        )
        self._piechart_div.set_data(
            chart_rows, value_key="annual_dividend_jpy", value_label=div_label,
            empty_message=div_empty,
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
        self._info.setText(
            f"USDJPY {fmt_number(self.fx_rate, 4)} | "
            f"取得 {self._compact_timestamp(self.market_fetched_at)} "
            f"({self.market_source or 'N/A'}){missing_note}"
        )
        income_kpi_label = "年間収入" if self.withdrawal_mode is not None else "年間配当"
        self._cards["年間配当"].set_title(income_kpi_label)
        self._cards["評価額計"].set_value(fmt_number(summary.total_market_value_jpy, 0))
        self._cards["簿価計"].set_value(fmt_number(summary.total_book_value_jpy, 0))
        self._cards["年間配当"].set_value(fmt_number(summary.total_dividend_jpy, 0))
        self._cards["優待"].set_value(fmt_number(summary.total_benefit_jpy, 0))
        self._cards["合計"].set_value(fmt_number(summary.total_numerator_jpy, 0))
        self._cards["簿価利回り"].set_value(fmt_percent(summary.yield_on_cost, 2))
        self._cards["時価利回り"].set_value(fmt_percent(summary.yield_on_value, 2))
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

        評価額タブ＝評価額・簿価の2系列。配当タブ＝優待/取崩の選択に応じて
        combine_income で合成した単一系列。
        """
        hist_dicts = self._history_as_dicts()
        self._asset_line.set_data(
            hist_dicts, ASSET_TREND_SERIES,
            empty_message="評価額の履歴がありません（株価更新か『インポート』で記録されます）",
        )
        with_benefit = self.income_benefit == "with"
        with_withdrawal = self.income_withdrawal == "with"
        income_dicts = [
            {"date": rec["date"],
             "income_value": combine_income(rec, with_benefit, with_withdrawal)}
            for rec in hist_dicts
        ]
        label = self._income_trend_label(with_benefit, with_withdrawal)
        self._income_line.set_data(
            income_dicts, [(label, "income_value", INCOME_TREND_COLOR)],
            empty_message="配当の履歴がありません（株価更新か『インポート』で記録されます）",
        )

    @staticmethod
    def _income_trend_label(with_benefit: bool, with_withdrawal: bool) -> str:
        base = "取崩収入" if with_withdrawal else "配当"
        return f"{base}＋優待" if with_benefit else base

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
                }
            )
        return result

    def _record_history_snapshot(self) -> None:
        """当日のスナップショットを履歴へ upsert して保存する。

        表示モードに依存させないため、税引前・取崩無し（mode="gross",
        withdrawal_mode=None）
        で再計算する。取崩収入は常に定率（"rate"）で別途計算し、取崩込み系列の値とする。
        評価額・配当・優待が全て None（起動直後で株価未取得）の場合は記録しない。
        """
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
        # 取崩込み収入（投信を時価×rate に置換）＝常に定率で決定的に記録。
        _, wd = compute_rows(
            self.holdings, self.market_data_map, self.fx_rate,
            "gross", "rate",
        )
        record = HistoryRecord(
            record_date=today_jst_str(),
            total_market_value_jpy=market,
            total_book_value_jpy=base.total_book_value_jpy,
            total_dividend_jpy=dividend,
            total_benefit_jpy=benefit,
            total_profit_jpy=base.total_profit_jpy,
            total_withdrawal_income_jpy=wd.total_dividend_jpy,
            source="app",
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
        reply = QMessageBox.question(
            self,
            "保有銘柄の取込",
            f"現在の保有銘柄（{len(self.holdings)} 件）を取込内容（{len(imported)} 件）で"
            "置き換えますか？\nこの操作は現在の保有を上書きします"
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

    def _refresh_monthly(self, div_label: str, div_empty: str) -> None:
        """月別タブ（棒グラフ・注記・内訳）を最新の集計で描き直す。"""
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
            MONTHLY_SERIES,
            average=self._monthly.monthly_average_jpy,
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
        parts = [
            "※支払月は権利落ち月からの推定です（JPY 建ては +3ヶ月 / USD 建ては同月）。"
            f"月平均は按分できた{div_label}を12で割った額です。"
        ]
        unknown = (
            self._monthly.unknown_dividend_jpy + self._monthly.unknown_benefit_jpy
        )
        if unknown > 0:
            parts.append(
                f"月不明 ¥{fmt_number(unknown, 0)}"
                f"（{len(self._monthly.unknown_symbols)}銘柄・グラフ未計上）"
            )
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
        total = bucket.total_jpy if bucket is not None else 0.0
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
                cells.append(cell)
            self._monthly_model.appendRow(cells)
        self._apply_monthly_sort()
        # 列幅は「中身が入った最初の1回」だけ調整する。毎回呼ぶと、バーをクリックする
        # たびユーザーが広げた列幅が元へ戻ってしまう（他テーブルの _size_columns_once
        # と同じ方針。ただし永続化はしないのでフラグだけで足りる）。
        if items and not self._monthly_columns_sized:
            self._monthly_columns_sized = True
            self._monthly_view.resizeColumnsToContents()

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
                text, color = _format_cell(row, key)
                item = QStandardItem(text)
                if key in NUMERIC_SORT_COLUMNS:
                    item.setData(safe_float(row.get(key)), Qt.ItemDataRole.UserRole)
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if color is not None:
                    item.setForeground(color)
                items.append(item)
            model.appendRow(items)
            stored_indices.append(holding_indices[i] if holding_indices is not None else i)
        self.row_holding_indices[tab_key] = stored_indices
        view.sortByColumn(sort_col, sort_order)
        self._size_columns_once(tab_key, view)
        self._restore_table_state(tab_key, state)

    def _size_columns_once(self, tab_key: str, view: QTableView) -> None:
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

    def _active_tab_key(self) -> str:
        # タブ構成: [評価額(0), 配当金(1), 月別配当(2), 株価詳細(3), 配当詳細(4)]。
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
            replace(holding), self, market=self.market_data_map.get(holding.symbol)
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            holdings = list(self.holdings)
            holdings[holding_idx] = dlg.result_holding
            if not self._save_or_notify(lambda: save_holdings(holdings)):
                return
            self.holdings = holdings
            self._reload_all()
            self._notify("銘柄を更新しました")

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
        )
        dlg = NewHoldingDialog(holding, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            result = dlg.result_holding
            holdings = [*self.holdings, result]
            if not self._save_or_notify(lambda: save_holdings(holdings)):
                return
            self.holdings = holdings
            self._reload_all()
            self._select_holding_row(len(self.holdings) - 1)
            self._notify("銘柄を追加しました")

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
        self.statusBar().showMessage("株価・配当データを取得中...")
        worker = MarketWorker(self.holdings, self.market_data_map)
        self._start_worker(worker, self._on_market_finished, self._on_market_progress)

    def _on_market_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        self.statusBar().showMessage(label)

    def _on_market_finished(self, result) -> None:
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
        self._refresh_view()
        # 最新株価で評価額が確定した時点で当日スナップショットを記録。
        self._record_history_snapshot()
        if result.errors:
            self._notify(
                f"一部の取得に失敗しました（{len(result.errors)}件）: {result.errors[0]}",
                error=True,
            )
        else:
            self._notify("株価・配当を更新しました")

    def action_update_fx(self) -> None:
        if self._busy:
            self._notify("更新処理が実行中です", error=True)
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # indeterminate
        self.statusBar().showMessage("USDJPYレートを取得中...")
        self._start_worker(FxWorker(), self._on_fx_finished)

    def _on_fx_finished(self, fx) -> None:
        if fx:
            rows = load_fx()
            rows.append(fx)
            if not self._save_or_notify(lambda: save_fx(rows)):
                return
            self.fx_rate = fx.rate
            self._refresh_view()
            self._record_history_snapshot()
            self._notify(f"USDJPY = {fx.rate:.4f} に更新しました")
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
    # QSS の font-family は QPainter 直描画（チャート類の self.font()）へ
    # 反映されないため、アプリ既定フォントも同じ族で揃える。
    app.setFont(QFont(FONT_FAMILY_PRIMARY, FONT_SIZE_PT))
    return app


def main() -> None:
    app = _ensure_app()
    win = build_window()
    win.show()
    sys.exit(app.exec())
