"""PySide6 ネイティブGUI（配当管理アプリのエントリ）。

ロジック層（io_csv / models / calc / market_data / utils）は無改変で再利用し、
表示層のみを Qt6 で再構築する。レガシー TUI は src/legacy_tui.py に温存。
"""

from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal, QSortFilterProxyModel
from PySide6.QtGui import QStandardItemModel, QStandardItem, QShortcut, QKeySequence, QColor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFormLayout,
    QTabWidget,
    QTableView,
    QLabel,
    QLineEdit,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QProgressBar,
    QAbstractItemView,
    QHeaderView,
)

from .io_csv import (
    init_empty_files,
    load_holdings,
    save_holdings,
    load_market_data,
    save_market_data,
    load_fx,
    save_fx,
)
from .models import Holding
from .calc import compute_rows
from .market_data import fetch_market_data, fetch_usdjpy
from .utils import fmt_number, fmt_percent, safe_float
from .widgets import TreemapWidget, PieChartWidget, KpiCard, profit_text_qcolor


PRICE_COLUMNS = [
    ("コード", "symbol"),
    ("銘柄名", "name"),
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
    ("株数", "shares"),
    ("通貨", "currency"),
    ("配当/株", "annual_div_ps"),
    ("年間配当(円)", "annual_dividend_jpy"),
    ("優待(円)", "annual_benefit_jpy"),
    ("簿価利回り", "yield_on_cost"),
    ("時価利回り", "yield_on_value"),
    ("配当更新日", "div_forecast_as_of"),
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
}


def _format_cell(row: dict, key: str) -> tuple[str, Optional[QColor]]:
    """セル表示文字列と文字色（損益のみ赤青）を返す。"""
    val = row.get(key)
    if key in ("symbol", "name", "currency", "div_forecast_as_of"):
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
        try:
            fx = fetch_usdjpy()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"USDJPY更新に失敗しました: {exc}")
            return
        self.finished.emit(fx)


# ---- 編集 / 新規ダイアログ ----

class EditHoldingDialog(QDialog):
    def __init__(self, holding: Holding, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("銘柄編集")
        self.holding = holding
        self.result_holding: Optional[Holding] = None

        form = QFormLayout()
        self._name = QLineEdit(holding.name)
        self._shares = QLineEdit(str(holding.shares))
        self._avg_cost = QLineEdit(str(holding.avg_cost))
        self._div_gross = QLineEdit(
            "" if holding.annual_div_ps_gross_override is None
            else str(holding.annual_div_ps_gross_override)
        )
        self._div_net = QLineEdit(
            "" if holding.annual_div_ps_net_override is None
            else str(holding.annual_div_ps_net_override)
        )
        self._div_date = QLineEdit(holding.div_forecast_as_of_override or "")
        self._benefit = QLineEdit(str(holding.annual_benefit_jpy))
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        form.addRow("銘柄名", self._name)
        form.addRow("保有株数", self._shares)
        form.addRow("取得単価", self._avg_cost)
        form.addRow("配当(税引前) 上書き", self._div_gross)
        form.addRow("配当(税引後) 上書き", self._div_net)
        form.addRow("配当予想更新日 (YYYY-MM-DD)", self._div_date)
        form.addRow("優待換算額(円/年)", self._benefit)
        form.addRow("", self._active)

        buttons = QDialogButtonBox()
        buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        try:
            name = self._name.text().strip()
            shares = float(self._shares.text() or 0)
            avg_cost = float(self._avg_cost.text() or 0)
            div_gross_text = self._div_gross.text().strip()
            div_net_text = self._div_net.text().strip()
            div_date = self._div_date.text().strip() or None
            benefit = float(self._benefit.text() or 0)
            active = self._active.isChecked()
        except ValueError:
            self.reject()
            return
        self.holding.name = name
        self.holding.shares = max(shares, 0)
        self.holding.avg_cost = max(avg_cost, 0)
        self.holding.annual_div_ps_gross_override = float(div_gross_text) if div_gross_text else None
        self.holding.annual_div_ps_net_override = float(div_net_text) if div_net_text else None
        self.holding.div_forecast_as_of_override = div_date
        self.holding.annual_benefit_jpy = max(benefit, 0)
        self.holding.active = bool(active)
        self.result_holding = self.holding
        self.accept()


class NewHoldingDialog(QDialog):
    def __init__(self, holding: Holding, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新規登録")
        self.holding = holding
        self.result_holding: Optional[Holding] = None

        form = QFormLayout()
        self._symbol = QLineEdit(holding.symbol)
        self._name = QLineEdit(holding.name)
        self._currency = QLineEdit(holding.currency)
        self._shares = QLineEdit(str(holding.shares))
        self._avg_cost = QLineEdit(str(holding.avg_cost))
        self._benefit = QLineEdit(str(holding.annual_benefit_jpy))
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        form.addRow("銘柄コード/ティッカー", self._symbol)
        form.addRow("銘柄名", self._name)
        form.addRow("通貨（JPY/USD）", self._currency)
        form.addRow("保有株数", self._shares)
        form.addRow("取得単価", self._avg_cost)
        form.addRow("優待換算額(円/年)", self._benefit)
        form.addRow("", self._active)

        buttons = QDialogButtonBox()
        buttons.addButton("追加", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        symbol = self._symbol.text().strip()
        name = self._name.text().strip()
        currency = self._currency.text().strip().upper() or "JPY"
        try:
            shares = float(self._shares.text() or 0)
            avg_cost = float(self._avg_cost.text() or 0)
            benefit = float(self._benefit.text() or 0)
            active = self._active.isChecked()
        except ValueError:
            self.reject()
            return
        if not symbol:
            self.reject()
            return
        self.holding.symbol = symbol
        self.holding.name = name
        self.holding.currency = currency if currency in ("JPY", "USD") else "JPY"
        self.holding.shares = max(shares, 0)
        self.holding.avg_cost = max(avg_cost, 0)
        self.holding.annual_benefit_jpy = max(benefit, 0)
        self.holding.active = bool(active)
        self.result_holding = self.holding
        self.accept()


# ---- メインウィンドウ ----

class DividendWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("配当管理")
        self.resize(1180, 760)

        self.dividend_mode = "gross"
        self.holdings: list[Holding] = []
        self.market_data_map: dict = {}
        self.fx_rate: float | None = None
        self.market_source: str | None = None
        self.market_fetched_at: str | None = None
        self.row_symbols: dict[str, list[str]] = {"price": [], "income": []}
        self._busy = False
        self._thread: QThread | None = None
        self._worker: QObject | None = None

        self._build_ui()
        self._register_shortcuts()

        init_empty_files()
        self._reload_all()

    # ---- UI 構築 ----
    def _build_ui(self) -> None:
        # アプリ全体をダークに統一（TreemapWidget の濃色背景と整合させ、
        # Win11 既定ライトテーマとの混在を避ける。テーマ切替は対象外＝固定）。
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #1e1e1e; color: #e0e0e0; }"
            "QTabWidget::pane { border: 1px solid #3a3a3a; }"
            "QTabBar::tab { background: #2a2a2a; color: #c0c0c0; padding: 6px 14px; }"
            "QTabBar::tab:selected { background: #383838; color: #ffffff; }"
            "QTableView { background: #232323; alternate-background-color: #292929;"
            " gridline-color: #3a3a3a; color: #e0e0e0;"
            " selection-background-color: #37474f; }"
            "QHeaderView::section { background: #2d2d2d; color: #d0d0d0;"
            " padding: 4px; border: 0px; border-right: 1px solid #3a3a3a; }"
            "QStatusBar { background: #2a2a2a; color: #cfcfcf; }"
            "QProgressBar { border: 1px solid #3a3a3a; background: #232323;"
            " color: #e0e0e0; text-align: center; }"
            "QProgressBar::chunk { background: #1565c0; }"
            "QLineEdit { background: #2a2a2a; color: #e0e0e0;"
            " border: 1px solid #3a3a3a; padding: 3px; }"
            "QDialog { background: #1e1e1e; color: #e0e0e0; }"
            "QPushButton { background: #2f2f2f; color: #e0e0e0;"
            " border: 1px solid #4a4a4a; padding: 5px 12px; border-radius: 4px; }"
            "QPushButton:hover { background: #3a3a3a; }"
            "QToolTip { background: #2a2a2a; color: #f0f0f0; border: 1px solid #555; }"
        )
        central = QWidget()
        root = QVBoxLayout(central)

        self._info = QLabel("")
        self._info.setStyleSheet("color: #bdbdbd; padding: 4px 2px;")
        root.addWidget(self._info)

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

        self._tabs = QTabWidget()
        # ダッシュボード（ヒートマップ / 円グラフ サブタブ）
        dash = QWidget()
        dash_layout = QVBoxLayout(dash)
        dash_layout.setContentsMargins(0, 0, 0, 0)
        dash_layout.addWidget(QLabel("ポートフォリオ構成（面積=評価額 / 色=損益率）"))
        self._dash_sub = QTabWidget()
        self._dash_sub.tabBar().setExpanding(False)
        self._treemap = TreemapWidget()
        self._piechart = PieChartWidget()
        self._dash_sub.addTab(self._treemap, "ヒートマップ")
        self._dash_sub.addTab(self._piechart, "円グラフ")
        dash_layout.addWidget(self._dash_sub, 1)
        self._tabs.addTab(dash, "ダッシュボード")
        # 株価 / 配当タブ
        self._price_view, self._price_model, self._price_proxy = self._make_table(PRICE_COLUMNS)
        self._income_view, self._income_model, self._income_proxy = self._make_table(INCOME_COLUMNS)
        self._tabs.addTab(self._price_view, "株価関連")
        self._tabs.addTab(self._income_view, "配当関連")
        self._tabs.currentChanged.connect(lambda _i: self._refresh_view())
        root.addWidget(self._tabs, 1)

        self.setCentralWidget(central)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(260)
        self._progress.setVisible(False)
        self.statusBar().addPermanentWidget(self._progress)

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
        return view, model, proxy

    def _register_shortcuts(self) -> None:
        binds = [
            ("Q", self.close),
            ("R", self._reload_all),
            ("M", self.action_toggle_mode),
            ("P", self.action_update_market),
            ("U", self.action_update_fx),
            ("N", self.action_new_holding),
            ("E", self.action_edit),
            ("S", self.action_sort_by_cursor),
            ("1", lambda: self._tabs.setCurrentIndex(0)),
            ("2", lambda: self._tabs.setCurrentIndex(1)),
            ("3", lambda: self._tabs.setCurrentIndex(2)),
        ]
        for key, slot in binds:
            QShortcut(QKeySequence(key), self, activated=slot)

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
        self._refresh_view()

    def _refresh_view(self) -> None:
        rows, summary = compute_rows(
            self.holdings, self.market_data_map, self.fx_rate, self.dividend_mode
        )
        self._render_table("price", PRICE_COLUMNS, self._price_model, self._price_view, rows)
        self._render_table("income", INCOME_COLUMNS, self._income_model, self._income_view, rows)
        self._treemap.set_data(rows)
        self._piechart.set_data(rows)

        self._info.setText(
            f"税引モード: {'税引後' if self.dividend_mode == 'net' else '税引前'}   |   "
            f"USDJPY: {fmt_number(self.fx_rate, 4)}   |   "
            f"株価・配当取得: {self.market_fetched_at or 'N/A'} "
            f"({self.market_source or 'N/A'})   |   "
            f"r:再読込  m:税引前後  p:株価更新  u:USDJPY  n:新規  e:編集  s:列ソート"
        )
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

    def _render_table(self, tab_key, columns, model, view, rows) -> None:
        header = view.horizontalHeader()
        sort_col = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()

        model.setRowCount(0)
        symbols: list[str] = []
        for row in rows:
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
            symbols.append(str(row["symbol"]))
        self.row_symbols[tab_key] = symbols
        view.sortByColumn(sort_col, sort_order)
        view.resizeColumnsToContents()

    # ---- アクション ----
    def action_toggle_mode(self) -> None:
        self.dividend_mode = "net" if self.dividend_mode == "gross" else "gross"
        self._refresh_view()

    def _active_tab_key(self) -> str:
        idx = self._tabs.currentIndex()
        if idx == 1:
            return "price"
        if idx == 2:
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
        symbols = self.row_symbols[tab_key]
        if src_row < 0 or src_row >= len(symbols):
            return
        symbol = symbols[src_row]
        holding = next((h for h in self.holdings if h.symbol == symbol), None)
        if not holding:
            return
        dlg = EditHoldingDialog(holding, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            for i, h in enumerate(self.holdings):
                if h.symbol == dlg.result_holding.symbol:
                    self.holdings[i] = dlg.result_holding
                    break
            save_holdings(self.holdings)
            self._reload_all()
            self._notify("銘柄を更新しました")

    def action_new_holding(self) -> None:
        holding = Holding(
            symbol="", name="", asset_type="stock", currency="JPY",
            shares=0.0, avg_cost=0.0, account="",
            annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
            div_forecast_as_of_override=None, annual_benefit_jpy=0.0, active=True,
        )
        dlg = NewHoldingDialog(holding, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            result = dlg.result_holding
            if any(h.symbol == result.symbol for h in self.holdings):
                self._notify("同じコードの銘柄が既に存在します", error=True)
                return
            self.holdings.append(result)
            save_holdings(self.holdings)
            self._reload_all()
            self._notify("銘柄を追加しました")

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
        save_market_data(result.rows)
        self.market_data_map = {row.symbol: row for row in result.rows}
        self.market_fetched_at = result.fetched_at
        self.market_source = result.source
        if result.name_updates:
            updated = False
            for holding in self.holdings:
                if holding.symbol in result.name_updates and not holding.name:
                    holding.name = result.name_updates[holding.symbol]
                    updated = True
            if updated:
                save_holdings(self.holdings)
        self._refresh_view()
        if result.errors:
            self._notify(f"一部の取得に失敗しました（{len(result.errors)}件）", error=True)
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
            save_fx(rows)
            self.fx_rate = fx.rate
            self._refresh_view()
            self._notify(f"USDJPY = {fx.rate:.4f} に更新しました")
        else:
            self._notify("USDJPYレートを取得できませんでした", error=True)

    def _notify(self, message: str, error: bool = False) -> None:
        self.statusBar().showMessage(message, 6000)


def build_window() -> DividendWindow:
    """ウィンドウを生成して返す（ヘッドレス検証用ファクトリ）。"""
    _ensure_app()
    return DividendWindow()


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def main() -> None:
    app = _ensure_app()
    win = build_window()
    win.show()
    sys.exit(app.exec())
