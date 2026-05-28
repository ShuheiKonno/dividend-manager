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
    QDoubleSpinBox,
    QMessageBox,
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
)
from .models import Holding
from .calc import compute_rows
# market_data は yfinance / ToushinReader / pandas を芋づる式に読み込み起動を数秒遅らせる。
# 取得は p / u キーのワーカースレッドでしか走らないため、import を run() 内へ遅延させる。
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


# ---- ダイアログ共通ヘルパー ----

def _parse_num(text: str) -> float:
    """カンマ・全角数字・円記号を除去して float に変換。空文字は 0.0 を返す。"""
    cleaned = (
        text.replace(",", "")
        .replace("，", "")
        .replace("¥", "")
        .replace("円", "")
        .strip()
    )
    return float(cleaned) if cleaned else 0.0


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
        self._withdrawal_fixed = QLineEdit(
            "" if holding.withdrawal_fixed_jpy is None
            else str(holding.withdrawal_fixed_jpy)
        )
        self._withdrawal_rate = QLineEdit(
            "" if holding.withdrawal_rate is None
            else str(holding.withdrawal_rate)
        )
        self._tax_rate = QLineEdit(
            "" if holding.tax_rate is None
            else str(holding.tax_rate)
        )
        self._tax_rate.setPlaceholderText("未入力=20.315%（NISA口座は0）")
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        form.addRow("銘柄名", self._name)
        form.addRow("保有株数", self._shares)
        form.addRow("取得単価", self._avg_cost)
        form.addRow("配当(税引前) 上書き", self._div_gross)
        form.addRow("配当(税引後) 上書き", self._div_net)
        form.addRow("配当予想更新日 (YYYY-MM-DD)", self._div_date)
        form.addRow("優待換算額(円/年)", self._benefit)
        form.addRow("定額取崩額(円/年)", self._withdrawal_fixed)
        form.addRow("定率取崩率(例: 0.04)", self._withdrawal_rate)
        form.addRow("源泉税率(例: 0 でNISA)", self._tax_rate)
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
            shares = _parse_num(self._shares.text())
            avg_cost = _parse_num(self._avg_cost.text())
            div_gross_text = self._div_gross.text().strip()
            div_net_text = self._div_net.text().strip()
            div_date = self._div_date.text().strip() or None
            benefit = _parse_num(self._benefit.text())
            withdrawal_fixed_text = self._withdrawal_fixed.text().strip()
            withdrawal_rate_text = self._withdrawal_rate.text().strip()
            div_gross = _parse_num(div_gross_text) if div_gross_text else None
            div_net = _parse_num(div_net_text) if div_net_text else None
            withdrawal_fixed = _parse_num(withdrawal_fixed_text) if withdrawal_fixed_text else None
            withdrawal_rate = _parse_num(withdrawal_rate_text) if withdrawal_rate_text else None
            tax_rate_text = self._tax_rate.text().strip()
            tax_rate = _parse_num(tax_rate_text) if tax_rate_text else None
            active = self._active.isChecked()
        except ValueError:
            QMessageBox.warning(self, "入力エラー", "数値の入力が正しくありません。\n（カンマ区切りはそのまま入力できます）")
            return
        self.holding.name = name
        self.holding.shares = max(shares, 0)
        self.holding.avg_cost = max(avg_cost, 0)
        self.holding.annual_div_ps_gross_override = div_gross
        self.holding.annual_div_ps_net_override = div_net
        self.holding.div_forecast_as_of_override = div_date
        self.holding.annual_benefit_jpy = max(benefit, 0)
        self.holding.withdrawal_fixed_jpy = withdrawal_fixed
        self.holding.withdrawal_rate = withdrawal_rate
        self.holding.tax_rate = tax_rate
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
        self._asset_type = QLineEdit(holding.asset_type)
        self._currency = QLineEdit(holding.currency)
        self._shares = QLineEdit("")
        self._shares.setPlaceholderText("0")
        self._avg_cost = QLineEdit("")
        self._avg_cost.setPlaceholderText("0")
        self._benefit = QLineEdit("")
        self._benefit.setPlaceholderText("0")
        self._withdrawal_fixed = QLineEdit("")
        self._withdrawal_rate = QLineEdit("")
        self._tax_rate = QLineEdit("")
        self._tax_rate.setPlaceholderText("未入力=20.315%（NISA口座は0）")
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        form.addRow("銘柄コード/ティッカー", self._symbol)
        form.addRow("銘柄名", self._name)
        form.addRow("資産種別（stock/fund）", self._asset_type)
        form.addRow("通貨（JPY/USD）", self._currency)
        form.addRow("保有株数", self._shares)
        form.addRow("取得単価", self._avg_cost)
        form.addRow("優待換算額(円/年)", self._benefit)
        form.addRow("定額取崩額(円/年)", self._withdrawal_fixed)
        form.addRow("定率取崩率(例: 0.04)", self._withdrawal_rate)
        form.addRow("源泉税率(例: 0 でNISA)", self._tax_rate)
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
        asset_type = self._asset_type.text().strip().lower() or "stock"
        currency = self._currency.text().strip().upper() or "JPY"
        if not symbol:
            QMessageBox.warning(self, "入力エラー", "銘柄コードを入力してください。")
            return
        try:
            shares = _parse_num(self._shares.text())
            avg_cost = _parse_num(self._avg_cost.text())
            benefit = _parse_num(self._benefit.text())
            withdrawal_fixed_text = self._withdrawal_fixed.text().strip()
            withdrawal_rate_text = self._withdrawal_rate.text().strip()
            withdrawal_fixed = _parse_num(withdrawal_fixed_text) if withdrawal_fixed_text else None
            withdrawal_rate = _parse_num(withdrawal_rate_text) if withdrawal_rate_text else None
            tax_rate_text = self._tax_rate.text().strip()
            tax_rate = _parse_num(tax_rate_text) if tax_rate_text else None
            active = self._active.isChecked()
        except ValueError:
            QMessageBox.warning(self, "入力エラー", "数値の入力が正しくありません。\n（カンマ区切りはそのまま入力できます）")
            return
        self.holding.symbol = symbol
        self.holding.name = name
        self.holding.asset_type = asset_type if asset_type in ("stock", "fund") else "stock"
        self.holding.currency = currency if currency in ("JPY", "USD") else "JPY"
        self.holding.shares = max(shares, 0)
        self.holding.avg_cost = max(avg_cost, 0)
        self.holding.annual_benefit_jpy = max(benefit, 0)
        self.holding.withdrawal_fixed_jpy = withdrawal_fixed
        self.holding.withdrawal_rate = withdrawal_rate
        self.holding.tax_rate = tax_rate
        self.holding.active = bool(active)
        self.result_holding = self.holding
        self.accept()


# ---- メインウィンドウ ----

class DividendWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("配当管理")
        self.resize(1180, 760)

        _saved = load_settings()
        self.dividend_mode: str = _saved.get("dividend_mode", "gross")
        self.withdrawal_mode: str | None = _saved.get("withdrawal_mode", None)
        self.holdings: list[Holding] = []
        self.market_data_map: dict = {}
        self.fx_rate: float | None = None
        self.market_source: str | None = None
        self.market_fetched_at: str | None = None
        self.row_holding_indices: dict[str, list[int]] = {"price": [], "income": []}
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
        self._dash_sub.addTab(self._treemap, "ヒートマップ")

        # 円グラフタブ: 閾値スピンボックス + チャート
        pie_container = QWidget()
        pie_layout = QVBoxLayout(pie_container)
        pie_layout.setContentsMargins(0, 4, 0, 0)
        pie_layout.setSpacing(4)
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("その他にまとめる閾値:"))
        self._pie_threshold = QDoubleSpinBox()
        self._pie_threshold.setRange(0.0, 20.0)
        self._pie_threshold.setValue(2.0)
        self._pie_threshold.setSuffix(" %")
        self._pie_threshold.setSingleStep(0.5)
        self._pie_threshold.setDecimals(1)
        self._pie_threshold.setFixedWidth(100)
        threshold_row.addWidget(self._pie_threshold)
        threshold_row.addStretch()
        pie_layout.addLayout(threshold_row)
        self._piechart = PieChartWidget()
        pie_layout.addWidget(self._piechart, 1)
        self._pie_threshold.valueChanged.connect(self._piechart.set_threshold)
        self._dash_sub.addTab(pie_container, "円グラフ")
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
            ("W", self.action_toggle_withdrawal),
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
            self.holdings, self.market_data_map, self.fx_rate, self.dividend_mode,
            self.withdrawal_mode,
        )
        active_indices = [i for i, h in enumerate(self.holdings) if h.active]
        self._render_table("price", PRICE_COLUMNS, self._price_model, self._price_view, rows, active_indices)
        income_cols = list(INCOME_COLUMNS)
        if self.withdrawal_mode is not None:
            income_cols = [
                ("年間収入(円)", key) if lbl == "年間配当(円)" else (lbl, key)
                for lbl, key in income_cols
            ]
        self._income_model.setHorizontalHeaderLabels([lbl for lbl, _ in income_cols])
        self._render_table("income", income_cols, self._income_model, self._income_view, rows, active_indices)
        self._treemap.set_data(rows)
        self._piechart.set_data(rows)

        withdrawal_label = {None: "配当", "fixed": "定額取崩", "rate": "定率取崩"}[self.withdrawal_mode]
        self._info.setText(
            f"税引: {'後' if self.dividend_mode == 'net' else '前'}  収入: {withdrawal_label}   |   "
            f"USDJPY: {fmt_number(self.fx_rate, 4)}   |   "
            f"株価・配当取得: {self.market_fetched_at or 'N/A'} "
            f"({self.market_source or 'N/A'})   |   "
            f"r:再読込  m:税引前後  w:取崩切替  p:株価更新  u:USDJPY  n:新規  e:編集  s:列ソート"
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

    def _render_table(self, tab_key, columns, model, view, rows, holding_indices: list[int] | None = None) -> None:
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
        view.resizeColumnsToContents()

    # ---- アクション ----
    def action_toggle_mode(self) -> None:
        self.dividend_mode = "net" if self.dividend_mode == "gross" else "gross"
        save_settings(self.dividend_mode, self.withdrawal_mode)
        self._refresh_view()

    def action_toggle_withdrawal(self) -> None:
        cycle: dict[str | None, str | None] = {None: "fixed", "fixed": "rate", "rate": None}
        self.withdrawal_mode = cycle[self.withdrawal_mode]
        save_settings(self.dividend_mode, self.withdrawal_mode)
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
        indices = self.row_holding_indices[tab_key]
        if src_row < 0 or src_row >= len(indices):
            return
        holding_idx = indices[src_row]
        if holding_idx < 0 or holding_idx >= len(self.holdings):
            return
        holding = self.holdings[holding_idx]
        dlg = EditHoldingDialog(holding, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_holding is not None:
            self.holdings[holding_idx] = dlg.result_holding
            save_holdings(self.holdings)
            self._reload_all()
            self._notify("銘柄を更新しました")

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
