from __future__ import annotations

from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Footer, Header, Input, Button, Checkbox, Label, TabbedContent, TabPane, ProgressBar
from textual.screen import ModalScreen
from textual import events, work
from rich.text import Text

from .io_csv import init_empty_files, load_holdings, save_holdings, load_market_data, save_market_data, load_fx, save_fx
from .models import Holding
from .calc import compute_rows
from .market_data import fetch_market_data, fetch_usdjpy
from .utils import fmt_number, fmt_percent, rjust, safe_float


def profit_style(value: float | None) -> str:
    """損益値に応じた表示色を返す（プラス=赤字 / マイナス=青字 / ゼロ・不明=色なし）。"""
    num = safe_float(value)
    if num is None:
        return ""
    if num > 0:
        return "red"
    if num < 0:
        return "blue"
    return ""


# ---- スクエアファイド・ツリーマップ（Bruls/Huizing/van Wijk 2000） ----
# 面積を構成比に比例させ、各矩形がなるべく正方形になるよう配置する。
# 戻り値の矩形は入力 sizes と同じ順序。


def _tm_layout(sizes: list[float], x: float, y: float, dx: float, dy: float):
    rects = []
    covered = sum(sizes)
    if dx >= dy:  # 行（横長領域）に縦積み
        width = covered / dy if dy else 0.0
        cy = y
        for s in sizes:
            h = s / width if width else 0.0
            rects.append((x, cy, width, h))
            cy += h
    else:  # 列（縦長領域）に横並び
        height = covered / dx if dx else 0.0
        cx = x
        for s in sizes:
            w = s / height if height else 0.0
            rects.append((cx, y, w, height))
            cx += w
    return rects


def _tm_leftover(sizes: list[float], x: float, y: float, dx: float, dy: float):
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy if dy else 0.0
        return (x + width, y, dx - width, dy)
    height = covered / dx if dx else 0.0
    return (x, y + height, dx, dy - height)


def _tm_worst(sizes: list[float], dx: float, dy: float) -> float:
    rects = _tm_layout(sizes, 0.0, 0.0, dx, dy)
    ratios = [max(w / h, h / w) for (_, _, w, h) in rects if w > 0 and h > 0]
    return max(ratios) if ratios else float("inf")


def _squarify(sizes: list[float], x: float, y: float, dx: float, dy: float):
    sizes = [float(s) for s in sizes]
    if not sizes:
        return []
    if len(sizes) == 1:
        return _tm_layout(sizes, x, y, dx, dy)
    i = 1
    while i < len(sizes) and _tm_worst(sizes[:i], dx, dy) >= _tm_worst(sizes[: i + 1], dx, dy):
        i += 1
    current, remaining = sizes[:i], sizes[i:]
    return _tm_layout(current, x, y, dx, dy) + _squarify(
        remaining, *_tm_leftover(current, x, y, dx, dy)
    )


class EditHoldingScreen(ModalScreen[Holding | None]):
    def __init__(self, holding: Holding) -> None:
        super().__init__()
        self.holding = holding

    def compose(self) -> ComposeResult:
        yield Static("銘柄編集", id="title")
        with VerticalScroll(id="form"):
            yield Label("銘柄名")
            yield Input(value=self.holding.name, id="name")
            yield Label("保有株数")
            yield Input(value=str(self.holding.shares), id="shares")
            yield Label("取得単価")
            yield Input(value=str(self.holding.avg_cost), id="avg_cost")
            yield Label("配当(税引前) 上書き")
            yield Input(value="" if self.holding.annual_div_ps_gross_override is None else str(self.holding.annual_div_ps_gross_override), id="div_gross")
            yield Label("配当(税引後) 上書き")
            yield Input(value="" if self.holding.annual_div_ps_net_override is None else str(self.holding.annual_div_ps_net_override), id="div_net")
            yield Label("配当予想更新日 上書き (YYYY-MM-DD)")
            yield Input(value=self.holding.div_forecast_as_of_override or "", id="div_date")
            yield Label("優待換算額(円/年)")
            yield Input(value=str(self.holding.annual_benefit_jpy), id="benefit")
            yield Checkbox("有効", value=self.holding.active, id="active")
        with Horizontal():
            yield Button("保存", id="save", variant="success")
            yield Button("キャンセル", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id != "save":
            return
        try:
            name = self.query_one("#name", Input).value.strip()
            shares = float(self.query_one("#shares", Input).value or 0)
            avg_cost = float(self.query_one("#avg_cost", Input).value or 0)
            div_gross_text = self.query_one("#div_gross", Input).value.strip()
            div_net_text = self.query_one("#div_net", Input).value.strip()
            div_date = self.query_one("#div_date", Input).value.strip() or None
            benefit = float(self.query_one("#benefit", Input).value or 0)
            active = self.query_one("#active", Checkbox).value
        except ValueError:
            self.dismiss(None)
            return

        self.holding.name = name
        self.holding.shares = max(shares, 0)
        self.holding.avg_cost = max(avg_cost, 0)
        self.holding.annual_div_ps_gross_override = float(div_gross_text) if div_gross_text else None
        self.holding.annual_div_ps_net_override = float(div_net_text) if div_net_text else None
        self.holding.div_forecast_as_of_override = div_date
        self.holding.annual_benefit_jpy = max(benefit, 0)
        self.holding.active = bool(active)
        self.dismiss(self.holding)


class DividendApp(App):
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

    CSS = """
    #title { text-align: center; padding: 1 0; }
    #summary { padding: 1; }
    #footer { padding: 1; }
    DataTable { height: 1fr; }
    #progress_box { display: none; height: auto; padding: 0 1; background: $panel; }
    #progress_label { width: auto; padding: 0 2 0 0; color: $warning; }
    #progress_bar { width: 1fr; }
    #dashboard_scroll { height: 1fr; }
    #dashboard_summary { padding: 1; }
    #dashboard_heatmap { padding: 0 1 1 1; }
    """

    BINDINGS = [
        ("q", "quit", "終了"),
        ("r", "reload", "再読み込み"),
        ("m", "toggle_mode", "税引前/後"),
        ("p", "update_market", "株価/配当更新"),
        ("u", "update_fx", "USDJPY更新"),
        ("n", "new_holding", "新規追加"),
        ("e", "edit", "銘柄編集"),
        ("s", "sort_by_cursor", "現在列ソート"),
        ("1", "show_dashboard_tab", "ダッシュボード"),
        ("2", "show_price_tab", "株価タブ"),
        ("3", "show_income_tab", "配当タブ"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.dividend_mode = "gross"
        self.holdings: list[Holding] = []
        self.market_data_map = {}
        self.fx_rate = None
        self.market_source = None
        self.market_fetched_at = None
        self.row_symbols = {"price": [], "income": []}
        self.sort_state = {
            "price": {"column": "symbol", "reverse": False},
            "income": {"column": "symbol", "reverse": False},
        }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="summary")
        with Horizontal(id="progress_box"):
            yield Static("", id="progress_label")
            yield ProgressBar(id="progress_bar", show_eta=False)
        with TabbedContent(initial="tab-dashboard", id="tabs"):
            with TabPane("ダッシュボード", id="tab-dashboard"):
                with VerticalScroll(id="dashboard_scroll"):
                    yield Static(id="dashboard_summary")
                    yield Static(id="dashboard_heatmap")
            with TabPane("株価関連", id="tab-price"):
                yield DataTable(id="table_price", zebra_stripes=True)
            with TabPane("配当関連", id="tab-income"):
                yield DataTable(id="table_income", zebra_stripes=True)
        yield Static(id="footer")
        yield Footer()

    def on_mount(self) -> None:
        init_empty_files()
        self._setup_table()
        self._reload_all()

    def _setup_table(self) -> None:
        for table_id, columns in (("table_price", self.PRICE_COLUMNS), ("table_income", self.INCOME_COLUMNS)):
            table = self.query_one(f"#{table_id}", DataTable)
            table.clear(columns=True)
            for label, key in columns:
                table.add_column(label, key=key)

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
        rows, summary = compute_rows(self.holdings, self.market_data_map, self.fx_rate, self.dividend_mode)
        self._render_table("price", rows)
        self._render_table("income", rows)
        self._render_dashboard(rows, summary)

        def _md(text: str, value: float | None) -> str:
            style = profit_style(value)
            return f"[{style}]{text}[/{style}]" if style else text

        profit_jpy_md = _md(fmt_number(summary.total_profit_jpy, 0), summary.total_profit_jpy)
        profit_rate_md = _md(fmt_percent(summary.profit_rate, 2), summary.profit_rate)

        summary_text = (
            f"税引モード: {'税引後' if self.dividend_mode == 'net' else '税引前'}  | "
            f"USDJPY: {fmt_number(self.fx_rate, 4)}  | "
            f"株価・配当取得: {self.market_fetched_at or 'N/A'} ({self.market_source or 'N/A'})\n"
            f"総簿価JPY: {fmt_number(summary.total_book_value_jpy, 0)}  | "
            f"総評価額JPY: {fmt_number(summary.total_market_value_jpy, 0)}  | "
            f"年間配当JPY: {fmt_number(summary.total_dividend_jpy, 0)}  | "
            f"優待JPY: {fmt_number(summary.total_benefit_jpy, 0)}  | "
            f"合計: {fmt_number(summary.total_numerator_jpy, 0)}\n"
            f"簿価利回り: {fmt_percent(summary.yield_on_cost, 2)}  | "
            f"時価利回り: {fmt_percent(summary.yield_on_value, 2)}  | "
            f"損益: {profit_jpy_md}  | "
            f"損益率: {profit_rate_md}"
        )
        self.query_one("#summary", Static).update(summary_text)
        active_tab = self._active_tab_key()
        if active_tab == "dashboard":
            self.query_one("#footer", Static).update(
                "1: ダッシュボード / 2: 株価タブ / 3: 配当タブ / n: 新規追加 / "
                "p: 株価・配当更新 / u: USDJPY更新 | タブ: ダッシュボード"
            )
        else:
            sort_column = str(self.sort_state[active_tab]["column"])
            sort_reverse = bool(self.sort_state[active_tab]["reverse"])
            sort_label = dict(self._columns_for_tab(active_tab)).get(sort_column, "なし")
            sort_order = "降順" if sort_reverse else "昇順"
            active_tab_label = "株価関連" if active_tab == "price" else "配当関連"
            self.query_one("#footer", Static).update(
                f"1: ダッシュボード / 2: 株価タブ / 3: 配当タブ / n: 新規追加 / e: 銘柄編集 / "
                f"p: 株価・配当更新 / u: USDJPY更新 / s: 現在列ソート "
                f"| タブ: {active_tab_label} | ソート: {sort_label}({sort_order})"
            )

    def _columns_for_tab(self, tab_key: str) -> list[tuple[str, str]]:
        return self.PRICE_COLUMNS if tab_key == "price" else self.INCOME_COLUMNS

    def _active_tab_key(self) -> str:
        active = self.query_one("#tabs", TabbedContent).active
        if active == "tab-income":
            return "income"
        if active == "tab-dashboard":
            return "dashboard"
        return "price"

    def _table_for_tab(self, tab_key: str) -> DataTable:
        table_id = "table_income" if tab_key == "income" else "table_price"
        return self.query_one(f"#{table_id}", DataTable)

    def _format_cell(self, row: dict[str, float | str | None], key: str) -> str | Text:
        if key == "symbol":
            return str(row["symbol"])
        if key == "name":
            return str(row["name"])
        if key == "currency":
            return str(row["currency"])
        if key == "shares":
            return rjust(fmt_number(row["shares"], 2), 10)
        if key in {"avg_cost", "last_price", "annual_div_ps"}:
            return rjust(fmt_number(row[key], 2), 10)
        if key == "profit_jpy":
            text = rjust(fmt_number(row[key], 0), 12)
            style = profit_style(row[key])
            return Text(text, style=style) if style else text
        if key in {"annual_dividend_jpy", "annual_benefit_jpy", "book_value_jpy", "market_value_jpy"}:
            return rjust(fmt_number(row[key], 0), 12)
        if key == "profit_rate":
            text = rjust(fmt_percent(row[key], 2), 9)
            style = profit_style(row[key])
            return Text(text, style=style) if style else text
        if key in {"yield_on_cost", "yield_on_value"}:
            return rjust(fmt_percent(row[key], 2), 9)
        if key == "div_forecast_as_of":
            return str(row["div_forecast_as_of"] or "")
        return str(row.get(key) or "")

    def _sort_rows(self, tab_key: str, rows: list[dict[str, float | str | None]]) -> list[dict[str, float | str | None]]:
        sort_column = str(self.sort_state[tab_key]["column"])
        sort_reverse = bool(self.sort_state[tab_key]["reverse"])
        if not sort_column:
            return rows

        present_rows = [row for row in rows if row.get(sort_column) not in (None, "")]
        missing_rows = [row for row in rows if row.get(sort_column) in (None, "")]

        if sort_column in self.NUMERIC_SORT_COLUMNS:
            present_rows.sort(key=lambda row: float(row[sort_column]), reverse=sort_reverse)
        else:
            present_rows.sort(key=lambda row: str(row[sort_column]), reverse=sort_reverse)
        return present_rows + missing_rows

    def _render_table(self, tab_key: str, rows: list[dict[str, float | str | None]]) -> None:
        table = self._table_for_tab(tab_key)
        columns = self._columns_for_tab(tab_key)
        sorted_rows = self._sort_rows(tab_key, rows)
        cursor_row = table.cursor_row if table.cursor_row is not None else 0
        cursor_column = table.cursor_column if table.cursor_column is not None else 0

        table.clear()
        symbols: list[str] = []
        for row in sorted_rows:
            table.add_row(*[self._format_cell(row, key) for _, key in columns])
            symbols.append(str(row["symbol"]))
        self.row_symbols[tab_key] = symbols
        if table.row_count > 0:
            target_row = min(max(cursor_row, 0), table.row_count - 1)
            target_column = min(max(cursor_column, 0), len(columns) - 1)
            table.move_cursor(row=target_row, column=target_column, scroll=False)

    @staticmethod
    def _heatmap_style(profit_rate: float | None) -> str:
        """損益率に応じたタイル背景色（日本式: プラス=赤 / マイナス=青）を3段階で返す。"""
        rate = safe_float(profit_rate)
        if rate is None or rate == 0:
            return "white on grey23"
        magnitude = abs(rate)
        if rate > 0:
            if magnitude >= 0.05:
                return "white on red"
            if magnitude >= 0.02:
                return "white on dark_red"
            return "red on grey15"
        if magnitude >= 0.05:
            return "white on blue"
        if magnitude >= 0.02:
            return "white on dark_blue"
        return "blue on grey15"

    def _render_dashboard(self, rows: list[dict[str, float | str | None]], summary) -> None:
        summary_text = (
            "[b]ポートフォリオ サマリー[/b]\n\n"
            f"評価額計          : {fmt_number(summary.total_market_value_jpy, 0)} 円\n"
            f"簿価計            : {fmt_number(summary.total_book_value_jpy, 0)} 円\n"
            f"配当金計          : {fmt_number(summary.total_dividend_jpy, 0)} 円\n"
            f"配当利回り(簿価)  : {fmt_percent(summary.yield_on_cost, 2)}\n"
            f"配当利回り(評価額): {fmt_percent(summary.yield_on_value, 2)}"
        )
        self.query_one("#dashboard_summary", Static).update(summary_text)

        heatmap = self.query_one("#dashboard_heatmap", Static)
        total = summary.total_market_value_jpy
        if not total:
            heatmap.update("株価未取得 — 'p' で株価・配当を取得してください")
            return

        tiles = [row for row in rows if row.get("market_value_jpy") is not None]
        tiles.sort(key=lambda row: float(row["market_value_jpy"]), reverse=True)
        if not tiles:
            heatmap.update("評価額のある銘柄がありません")
            return

        # キャンバスサイズ（横=端末幅-左右padding、縦=固定28行）
        grid_w = max(40, (self.size.width or 100) - 2)
        grid_h = 28
        aspect = 2.0  # 端末セルは縦:横≈2:1 → 縦を2倍空間で最適化し正方形に見せる
        canvas_h = grid_h * aspect

        weights = [float(r["market_value_jpy"]) / float(total) for r in tiles]
        area = grid_w * canvas_h
        sizes = [w * area for w in weights]
        rects = _squarify(sizes, 0.0, 0.0, float(grid_w), canvas_h)

        neutral = "on grey15"
        cells = [[(" ", neutral)] * grid_w for _ in range(grid_h)]

        for tile, weight, (rx, ry, rw, rh) in zip(tiles, weights, rects):
            x0 = max(0, min(grid_w, int(round(rx))))
            x1 = max(0, min(grid_w, int(round(rx + rw))))
            y0 = max(0, min(grid_h, int(round(ry / aspect))))
            y1 = max(0, min(grid_h, int(round((ry + rh) / aspect))))
            if x1 <= x0:  # 極小タイルでも最低1セルは見せる
                x1 = min(grid_w, x0 + 1)
            if y1 <= y0:
                y1 = min(grid_h, y0 + 1)
            style = self._heatmap_style(tile.get("profit_rate"))
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    cells[yy][xx] = (" ", style)

            cw, ch = x1 - x0, y1 - y0
            symbol = str(tile["symbol"])
            pct = f"{weight * 100:.1f}%"

            def _put(text: str, ry_: int) -> None:
                if not (0 <= ry_ < grid_h) or len(text) > cw:
                    return
                sx = x0 + (cw - len(text)) // 2
                for k, chr_ in enumerate(text):
                    if 0 <= sx + k < grid_w:
                        cells[ry_][sx + k] = (chr_, style)

            if ch >= 2 and cw >= len(symbol):
                mid = y0 + (ch - 2) // 2
                _put(symbol, mid)
                _put(pct, mid + 1)
            elif ch >= 1 and cw >= len(symbol):
                _put(symbol, y0 + ch // 2)

        text = Text()
        for yy in range(grid_h):
            run_chars: list[str] = []
            run_style = cells[yy][0][1]
            for xx in range(grid_w):
                ch_, st_ = cells[yy][xx]
                if st_ == run_style:
                    run_chars.append(ch_)
                else:
                    text.append("".join(run_chars), style=run_style)
                    run_chars = [ch_]
                    run_style = st_
            text.append("".join(run_chars), style=run_style)
            if yy < grid_h - 1:
                text.append("\n")

        heatmap.update(text)

    def _toggle_sort(self, tab_key: str, column_key: str) -> None:
        if column_key not in {key for _, key in self._columns_for_tab(tab_key)}:
            return
        current_column = str(self.sort_state[tab_key]["column"])
        if current_column == column_key:
            self.sort_state[tab_key]["reverse"] = not bool(self.sort_state[tab_key]["reverse"])
        else:
            self.sort_state[tab_key]["column"] = column_key
            self.sort_state[tab_key]["reverse"] = False
        self._refresh_view()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        tab_key = "income" if event.data_table.id == "table_income" else "price"
        self._toggle_sort(tab_key, str(event.column_key))

    def action_sort_by_cursor(self) -> None:
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return
        table = self._table_for_tab(tab_key)
        columns = self._columns_for_tab(tab_key)
        if table.cursor_column is None:
            return
        if table.cursor_column >= len(columns):
            return
        _, column_key = columns[table.cursor_column]
        self._toggle_sort(tab_key, column_key)

    def action_show_price_tab(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-price"
        self._refresh_view()

    def action_show_income_tab(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-income"
        self._refresh_view()

    def action_show_dashboard_tab(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-dashboard"
        self._refresh_view()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._refresh_view()

    def on_resize(self, event: events.Resize) -> None:
        # ツリーマップは端末幅依存のためリサイズで再描画
        try:
            self._refresh_view()
        except Exception:
            pass

    def action_reload(self) -> None:
        self._reload_all()

    def action_toggle_mode(self) -> None:
        self.dividend_mode = "net" if self.dividend_mode == "gross" else "gross"
        self._refresh_view()

    # ---- 進捗表示ヘルパー ----
    def _show_progress(self, label: str, indeterminate: bool = False) -> None:
        self.query_one("#progress_box", Horizontal).display = True
        self.query_one("#progress_label", Static).update(label)
        bar = self.query_one("#progress_bar", ProgressBar)
        if indeterminate:
            bar.update(total=None)
        else:
            bar.update(total=100, progress=0)

    def _set_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self.query_one("#progress_bar", ProgressBar).update(total=total, progress=done)
        self.query_one("#progress_label", Static).update(label)

    def _hide_progress(self) -> None:
        self.query_one("#progress_box", Horizontal).display = False

    def _notify_safe(self, message: str, severity: str = "information") -> None:
        try:
            self.notify(message, severity=severity, timeout=5)
        except Exception:
            pass

    # ---- 株価・配当更新（バックグラウンド実行） ----
    def action_update_market(self) -> None:
        self._show_progress("株価・配当データを取得中...")
        self._run_market_update()

    @work(thread=True, exclusive=True, group="update")
    def _run_market_update(self) -> None:
        def cb(done: int, total: int, label: str) -> None:
            self.call_from_thread(self._set_progress, done, total, label)

        try:
            result = fetch_market_data(
                self.holdings, self.market_data_map, force=True, progress_cb=cb
            )
        except Exception as exc:
            self.call_from_thread(self._hide_progress)
            self.call_from_thread(self._notify_safe, f"更新に失敗しました: {exc}", "error")
            return
        self.call_from_thread(self._apply_market_result, result)

    def _apply_market_result(self, result) -> None:
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
        self._hide_progress()
        if result.errors:
            self._notify_safe(f"一部の取得に失敗しました（{len(result.errors)}件）", "warning")
        else:
            self._notify_safe("株価・配当を更新しました")

    # ---- USDJPY更新（バックグラウンド実行） ----
    def action_update_fx(self) -> None:
        self._show_progress("USDJPYレートを取得中...", indeterminate=True)
        self._run_fx_update()

    @work(thread=True, exclusive=True, group="update")
    def _run_fx_update(self) -> None:
        try:
            fx = fetch_usdjpy()
        except Exception as exc:
            self.call_from_thread(self._hide_progress)
            self.call_from_thread(self._notify_safe, f"USDJPY更新に失敗しました: {exc}", "error")
            return
        self.call_from_thread(self._apply_fx_result, fx)

    def _apply_fx_result(self, fx) -> None:
        if fx:
            rows = load_fx()
            rows.append(fx)
            save_fx(rows)
            self.fx_rate = fx.rate
            self._refresh_view()
            self._notify_safe(f"USDJPY = {fx.rate:.4f} に更新しました")
        else:
            self._notify_safe("USDJPYレートを取得できませんでした", "warning")
        self._hide_progress()

    def action_edit(self) -> None:
        tab_key = self._active_tab_key()
        if tab_key == "dashboard":
            return
        table = self._table_for_tab(tab_key)
        symbols = self.row_symbols[tab_key]
        if table.row_count == 0 or table.cursor_row is None:
            return
        if table.cursor_row >= len(symbols):
            return
        row_key = symbols[table.cursor_row]
        holding = next((h for h in self.holdings if h.symbol == row_key), None)
        if not holding:
            return
        self.push_screen(EditHoldingScreen(holding), self._on_edit_done)

    def _on_edit_done(self, result: Optional[Holding]) -> None:
        if result is None:
            return
        for idx, holding in enumerate(self.holdings):
            if holding.symbol == result.symbol:
                self.holdings[idx] = result
                break
        save_holdings(self.holdings)
        self._reload_all()

    def action_new_holding(self) -> None:
        holding = Holding(
            symbol="",
            name="",
            asset_type="stock",
            currency="JPY",
            shares=0.0,
            avg_cost=0.0,
            account="",
            annual_div_ps_gross_override=None,
            annual_div_ps_net_override=None,
            div_forecast_as_of_override=None,
            annual_benefit_jpy=0.0,
            active=True,
        )
        self.push_screen(NewHoldingScreen(holding), self._on_new_done)

    def _on_new_done(self, result: Optional[Holding]) -> None:
        if result is None:
            return
        if any(h.symbol == result.symbol for h in self.holdings):
            return
        self.holdings.append(result)
        save_holdings(self.holdings)
        self._reload_all()


class NewHoldingScreen(ModalScreen[Holding | None]):
    def __init__(self, holding: Holding) -> None:
        super().__init__()
        self.holding = holding

    def compose(self) -> ComposeResult:
        yield Static("新規登録", id="title")
        with VerticalScroll(id="form"):
            yield Label("銘柄コード/ティッカー")
            yield Input(value=self.holding.symbol, id="symbol")
            yield Label("銘柄名")
            yield Input(value=self.holding.name, id="name")
            yield Label("通貨（JPY/USD）")
            yield Input(value=self.holding.currency, id="currency")
            yield Label("保有株数")
            yield Input(value=str(self.holding.shares), id="shares")
            yield Label("取得単価")
            yield Input(value=str(self.holding.avg_cost), id="avg_cost")
            yield Label("優待換算額(円/年)")
            yield Input(value=str(self.holding.annual_benefit_jpy), id="benefit")
            yield Checkbox("有効", value=self.holding.active, id="active")
        with Horizontal():
            yield Button("追加", id="save", variant="success")
            yield Button("キャンセル", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id != "save":
            return
        symbol = self.query_one("#symbol", Input).value.strip()
        name = self.query_one("#name", Input).value.strip()
        currency = self.query_one("#currency", Input).value.strip().upper() or "JPY"
        try:
            shares = float(self.query_one("#shares", Input).value or 0)
            avg_cost = float(self.query_one("#avg_cost", Input).value or 0)
            benefit = float(self.query_one("#benefit", Input).value or 0)
            active = self.query_one("#active", Checkbox).value
        except ValueError:
            self.dismiss(None)
            return
        if not symbol:
            self.dismiss(None)
            return
        self.holding.symbol = symbol
        self.holding.name = name
        self.holding.currency = currency if currency in ("JPY", "USD") else "JPY"
        self.holding.shares = max(shares, 0)
        self.holding.avg_cost = max(avg_cost, 0)
        self.holding.annual_benefit_jpy = max(benefit, 0)
        self.holding.active = bool(active)
        self.dismiss(self.holding)
