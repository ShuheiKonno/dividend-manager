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
    QGridLayout,
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
    QAbstractSpinBox,
    QPushButton,
    QStackedWidget,
    QSplitter,
    QFileDialog,
    QComboBox,
    QMenu,
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
from .calc import compute_rows
from .history import upsert_record, merge_records, combine_income
# market_data は yfinance / ToushinReader / pandas を芋づる式に読み込み起動を数秒遅らせる。
# 取得は p / u キーのワーカースレッドでしか走らないため、import を run() 内へ遅延させる。
from .theme import (
    build_app_qss,
    BG_INPUT,
    FONT_FAMILY_PRIMARY,
    FONT_SIZE_PT,
    TEXT_DISABLED,
    TEXT_STATUS,
    TEXT_STATUS_ERROR,
)
from .utils import fmt_number, fmt_percent, safe_float, today_jst_str
from .widgets import (
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
# 評価額タブの推移系列 (ラベル, history キー, 線色)。評価額＋簿価の2本固定。
ASSET_TREND_SERIES = [
    ("評価額", "total_market_value_jpy", QColor("#42a5f5")),
    ("簿価", "total_book_value_jpy", QColor("#9e9e9e")),
]
# 配当タブの推移は優待/取崩の選択で合成した単一系列（_refresh_trend で構築）。
INCOME_TREND_COLOR = QColor("#66bb6a")
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
        self.row_holding_indices: dict[str, list[int]] = {"price": [], "income": []}
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
        self._build_menubar()
        central = QWidget()
        root = QVBoxLayout(central)

        self._info = QLabel("")
        self._info.setObjectName("infoLabel")
        root.addWidget(self._info)

        self._build_button_bar(root)

        kpi_bar = QWidget()
        grid = QGridLayout(kpi_bar)
        grid.setContentsMargins(0, 0, 0, 0)
        self._cards: dict[str, KpiCard] = {}
        card_defs = [
            "評価額計", "簿価計", "年間配当", "優待", "合計",
            "簿価利回り", "時価利回り", "損益", "損益率",
        ]
        for idx, title in enumerate(card_defs):
            card = KpiCard(title)
            self._cards[title] = card
            grid.addWidget(card, idx // 5, idx % 5)
        root.addWidget(kpi_bar)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("絞り込み:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("コード/銘柄名で絞り込み")
        self._filter.setClearButtonEnabled(True)
        self._filter.setMaximumWidth(320)
        # proxy は後段で生成されるが、textChanged は実行時にしか発火しないため接続順は問題ない。
        self._filter.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter)
        filter_row.addStretch()
        root.addLayout(filter_row)

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
        self._tabs.addTab(self._price_view, "株価関連")
        self._tabs.addTab(self._income_view, "配当関連")
        # 前回終了時のタブを復元してからシグナル接続する（起動時の無駄な保存を避ける）。
        if 0 <= self._initial_tab < self._tabs.count():
            self._tabs.setCurrentIndex(self._initial_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs, 1)

        self.setCentralWidget(central)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(260)
        self._progress.setVisible(False)
        self.statusBar().addPermanentWidget(self._progress)

    def _build_menubar(self) -> None:
        """薄いメニューバーを構築する（低頻度機能とショートカット一覧の置き場）。

        一文字ショートカットは _register_shortcuts の QShortcut（入力中ガード付き）が
        唯一の登録元のため、QAction にはショートカットを設定しない（二重登録すると
        Ambiguous shortcut とガード回避が起きる）。ラベル末尾の (X) は対応キーの表記。
        メニュータイトルの &F 等は Alt 系ニーモニックで一文字キーとは衝突しない。
        """
        bar = self.menuBar()

        def add(menu, label: str, slot):
            action = menu.addAction(label)
            # triggered は checked(bool) を渡すため、引数なしスロットへはラムダで吸収する。
            action.triggered.connect(lambda _checked=False, s=slot: s())
            return action

        file_menu = bar.addMenu("ファイル(&F)")
        export_menu = file_menu.addMenu("エクスポート (CSV)")
        add(export_menu, "保有銘柄...", self.action_export_holdings)
        add(export_menu, "推移履歴...", self.action_export_history)
        import_menu = file_menu.addMenu("インポート (CSV)")
        add(import_menu, "保有銘柄...", self.action_import_holdings)
        add(import_menu, "推移履歴...", self.action_import_history)
        file_menu.addSeparator()
        add(file_menu, "終了 (Q)", self.close)

        data_menu = bar.addMenu("データ(&D)")
        add(data_menu, "株価・配当更新 (P)", self.action_update_market)
        add(data_menu, "USDJPY更新 (U)", self.action_update_fx)
        add(data_menu, "再読込 (R)", self._reload_all)
        data_menu.addSeparator()
        add(data_menu, "新規登録 (N)", self.action_new_holding)
        add(data_menu, "編集 (E)", self.action_edit)
        add(data_menu, "削除 (D)", self.action_delete)

        view_menu = bar.addMenu("表示(&V)")
        add(view_menu, "税引前後の切替 (M)", self.action_toggle_mode)
        add(view_menu, "取崩モードの切替 (W)", self.action_toggle_withdrawal)
        add(view_menu, "無効銘柄も表示", self.action_toggle_inactive)
        view_menu.addSeparator()
        tab_labels = ["評価額 (1)", "配当金 (2)", "株価関連 (3)", "配当関連 (4)"]
        for i, label in enumerate(tab_labels):
            add(view_menu, label, lambda idx=i: self._tabs.setCurrentIndex(idx))

        help_menu = bar.addMenu("ヘルプ(&H)")
        add(help_menu, "キーボードショートカット一覧...", self._show_shortcut_help)

    def _show_shortcut_help(self) -> None:
        """キーボードショートカットの一覧を表示する。"""
        QMessageBox.information(
            self,
            "キーボードショートカット",
            "Q: 終了\n"
            "R: 再読込\n"
            "M: 税引前後の切替\n"
            "W: 取崩モードの切替\n"
            "P: 株価・配当の更新\n"
            "U: USDJPYレートの更新\n"
            "N: 新規登録\n"
            "E: 選択銘柄の編集（ダブルクリックでも可）\n"
            "D: 選択銘柄の削除\n"
            "S: カーソル列でソート\n"
            "1〜4: タブ切替（評価額 / 配当金 / 株価関連 / 配当関連）\n"
            "Ctrl+F: 絞り込みボックスへフォーカス\n"
            "Esc: 絞り込みのクリア\n"
            "\n※テキスト入力中は一文字ショートカットが無効になります。",
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
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        view.horizontalHeader().setStretchLastSection(True)
        view.verticalHeader().setVisible(False)
        view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        view.doubleClicked.connect(lambda _idx: self.action_edit())
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(
            lambda pos, v=view: self._show_table_context_menu(v, pos)
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
        has_row = view.currentIndex().isValid()
        menu = QMenu(self)
        act_edit = menu.addAction("編集 (E)")
        act_delete = menu.addAction("削除 (D)")
        menu.addSeparator()
        act_new = menu.addAction("新規登録 (N)")
        act_edit.setEnabled(has_row)
        act_delete.setEnabled(has_row)
        chosen = menu.exec(view.viewport().mapToGlobal(pos))
        if chosen is act_edit:
            self.action_edit()
        elif chosen is act_delete:
            self.action_delete()
        elif chosen is act_new:
            self.action_new_holding()

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

    def _build_button_bar(self, root: QVBoxLayout) -> None:
        """主要アクションを GUI ボタンとして並べる（キーボードショートカットと等価）。"""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        # (ラベル, スロット) ※ラベル末尾の (X) は対応ショートカット
        defs = [
            ("再読込 (R)", self._reload_all),
            ("税引前後 (M)", self.action_toggle_mode),
            ("取崩切替 (W)", self.action_toggle_withdrawal),
            ("株価更新 (P)", self.action_update_market),
            ("USDJPY (U)", self.action_update_fx),
            ("新規 (N)", self.action_new_holding),
            ("編集 (E)", self.action_edit),
            ("削除 (D)", self.action_delete),
        ]
        for label, slot in defs:
            btn = QPushButton(label)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 一文字ショートカットを奪わない
            # clicked は checked(bool) を渡すため、引数なしスロットへはラムダで吸収する。
            btn.clicked.connect(lambda _checked=False, s=slot: s())
            layout.addWidget(btn)
        layout.addStretch()
        root.addWidget(bar)

    def _make_combo(
        self, defs: list[tuple[str, str]], current_key: str, on_select
    ) -> QComboBox:
        """(キー, ラベル) のリストからドロップダウンを生成する。

        初期選択は current_key に合わせる。activated（ユーザー操作時のみ発火・
        setCurrentIndex では発火しない）で on_select(キー) を呼ぶため、初期化時の
        誤った保存・再描画が起きない。
        """
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

    def _register_shortcuts(self) -> None:
        binds = [
            ("Q", self.close),
            ("R", self._reload_all),
            ("M", self.action_toggle_mode),
            ("W", self.action_toggle_withdrawal),
            ("P", self.action_update_market),
            ("U", self.action_update_fx),
            ("N", self.action_new_holding),
            ("E", self.action_edit),
            ("D", self.action_delete),
            ("S", self.action_sort_by_cursor),
            ("1", lambda: self._tabs.setCurrentIndex(0)),
            ("2", lambda: self._tabs.setCurrentIndex(1)),
            ("3", lambda: self._tabs.setCurrentIndex(2)),
            ("4", lambda: self._tabs.setCurrentIndex(3)),
        ]
        for key, slot in binds:
            QShortcut(QKeySequence(key), self, activated=self._guard_shortcut(slot))
        # 修飾キー付き・Esc は入力中でも安全なためガード無しで直結する。
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_filter)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._on_escape)

    def _focus_filter(self) -> None:
        """絞り込みボックスへフォーカスを移す（Ctrl+F）。"""
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

    def _guard_shortcut(self, slot):
        """テキスト/数値入力中は単一キーショートカットを無効化するラッパを返す。

        絞り込みボックスや閾値スピンボックスに「7203」「Apple」等を入力する際、
        1/2/3（タブ切替）や p/n/e/d 等の一文字アクションが誤発火するのを防ぐ。
        """
        def wrapper() -> None:
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox)):
                return
            slot()
        return wrapper

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
        # 推移グラフ（履歴スナップショット）
        self._refresh_trend()

        withdrawal_label = {None: "配当", "fixed": "定額取崩", "rate": "定率取崩"}[self.withdrawal_mode]
        missing_note = (
            f"   |   評価額未算出: {summary.missing_market_value_count}銘柄"
            if summary.missing_market_value_count else ""
        )
        self._info.setText(
            f"税引: {'後' if self.dividend_mode == 'net' else '前'}  収入: {withdrawal_label}   |   "
            f"USDJPY: {fmt_number(self.fx_rate, 4)}   |   "
            f"株価・配当取得: {self.market_fetched_at or 'N/A'} "
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
    def action_toggle_mode(self) -> None:
        self.dividend_mode = "net" if self.dividend_mode == "gross" else "gross"
        self._save_or_notify(lambda: save_settings(dividend_mode=self.dividend_mode))
        self._refresh_view()

    def action_toggle_withdrawal(self) -> None:
        cycle: dict[str | None, str | None] = {None: "fixed", "fixed": "rate", "rate": None}
        self.withdrawal_mode = cycle[self.withdrawal_mode]
        self._save_or_notify(lambda: save_settings(withdrawal_mode=self.withdrawal_mode))
        self._refresh_view()

    def action_toggle_inactive(self) -> None:
        """無効銘柄をテーブルへ表示するかを切り替える。

        無効銘柄は集計・構成グラフの対象外のまま、テーブルにだけ淡色で並ぶ。
        これが無いと編集ダイアログで「有効」を外した銘柄へ二度と到達できない。
        """
        self.show_inactive = not self.show_inactive
        self._save_or_notify(lambda: save_settings(show_inactive=self.show_inactive))
        self._refresh_view()
        self._notify(
            "無効銘柄を表示します（淡色＝集計対象外）"
            if self.show_inactive
            else "無効銘柄を非表示にしました"
        )

    def _active_tab_key(self) -> str:
        # タブ構成: [評価額(0), 配当金(1), 株価関連(2), 配当関連(3)]。
        # 0/1 はグラフタブで編集・削除・ソート対象外（dashboard 扱い）。
        idx = self._tabs.currentIndex()
        if idx == 2:
            return "price"
        if idx == 3:
            return "income"
        return "dashboard"

    def _view_for_tab(self, tab_key: str) -> QTableView:
        return self._income_view if tab_key == "income" else self._price_view

    def _proxy_for_tab(self, tab_key: str):
        return self._income_proxy if tab_key == "income" else self._price_proxy

    def action_sort_by_cursor(self) -> None:
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return
        view = self._view_for_tab(tab_key)
        idx = view.currentIndex()
        if not idx.isValid():
            return
        col = idx.column()
        header = view.horizontalHeader()
        same = header.sortIndicatorSection() == col
        order = (
            Qt.SortOrder.DescendingOrder
            if same and header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        view.sortByColumn(col, order)

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
        dlg = EditHoldingDialog(replace(holding), self)
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
        thread.start()

    def _on_thread_done(self) -> None:
        self._busy = False
        self._worker = None
        self._thread = None
        self._progress.setVisible(False)

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
        # エラーは警告色＋⚠ で差別化。通常通知のたびに既定色へ戻す。
        color = TEXT_STATUS_ERROR if error else TEXT_STATUS
        self.statusBar().setStyleSheet(f"QStatusBar {{ background: {BG_INPUT}; color: {color}; }}")
        self.statusBar().showMessage(f"⚠ {message}" if error else message, 6000)

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
