from __future__ import annotations

from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Footer, Header, Input, Button, Checkbox, Label
from textual.screen import ModalScreen
from textual import events

from .io_csv import init_empty_files, load_holdings, save_holdings, load_market_data, save_market_data, load_fx, save_fx
from .models import Holding
from .calc import compute_rows
from .market_data import fetch_market_data, fetch_usdjpy
from .utils import fmt_number, fmt_percent, rjust


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
    CSS = """
    #title { text-align: center; padding: 1 0; }
    #summary { padding: 1; }
    #footer { padding: 1; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        ("q", "quit", "終了"),
        ("r", "reload", "再読み込み"),
        ("m", "toggle_mode", "税引前/後"),
        ("p", "update_market", "株価/配当更新"),
        ("u", "update_fx", "USDJPY更新"),
        ("n", "new_holding", "新規追加"),
        ("e", "edit", "銘柄編集"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.dividend_mode = "gross"
        self.holdings: list[Holding] = []
        self.market_data_map = {}
        self.fx_rate = None
        self.market_source = None
        self.market_fetched_at = None
        self.row_symbols: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="summary")
        yield DataTable(id="table", zebra_stripes=True)
        yield Static(id="footer")
        yield Footer()

    def on_mount(self) -> None:
        init_empty_files()
        self._setup_table()
        self._reload_all()

    def _setup_table(self) -> None:
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        table.add_column("コード")
        table.add_column("銘柄名")
        table.add_column("株数")
        table.add_column("通貨")
        table.add_column("配当/株")
        table.add_column("年間配当(円)")
        table.add_column("優待(円)")
        table.add_column("簿価(円)")
        table.add_column("時価(円)")
        table.add_column("簿価利回り")
        table.add_column("時価利回り")
        table.add_column("損益(円)")
        table.add_column("損益率")
        table.add_column("配当更新日")

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
        table = self.query_one("#table", DataTable)
        table.clear()
        self.row_symbols = []
        for row in rows:
            table.add_row(
                row["symbol"],
                row["name"],
                rjust(fmt_number(row["shares"], 2), 10),
                row["currency"],
                rjust(fmt_number(row["annual_div_ps"], 2), 10),
                rjust(fmt_number(row["annual_dividend_jpy"], 0), 12),
                rjust(fmt_number(row["annual_benefit_jpy"], 0), 10),
                rjust(fmt_number(row["book_value_jpy"], 0), 12),
                rjust(fmt_number(row["market_value_jpy"], 0), 12),
                rjust(fmt_percent(row["yield_on_cost"], 2), 9),
                rjust(fmt_percent(row["yield_on_value"], 2), 9),
                rjust(fmt_number(row["profit_jpy"], 0), 12),
                rjust(fmt_percent(row["profit_rate"], 2), 9),
                row["div_forecast_as_of"] or "",
            )
            self.row_symbols.append(str(row["symbol"]))

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
            f"損益: {fmt_number(summary.total_profit_jpy, 0)}  | "
            f"損益率: {fmt_percent(summary.profit_rate, 2)}"
        )
        self.query_one("#summary", Static).update(summary_text)
        self.query_one("#footer", Static).update("n: 新規追加 / e: 銘柄編集 / p: 株価・配当更新 / u: USDJPY更新")

    def action_reload(self) -> None:
        self._reload_all()

    def action_toggle_mode(self) -> None:
        self.dividend_mode = "net" if self.dividend_mode == "gross" else "gross"
        self._refresh_view()

    def action_update_market(self) -> None:
        result = fetch_market_data(self.holdings, self.market_data_map, force=True)
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

    def action_update_fx(self) -> None:
        fx = fetch_usdjpy()
        if fx:
            rows = load_fx()
            rows.append(fx)
            save_fx(rows)
            self.fx_rate = fx.rate
            self._refresh_view()

    def action_edit(self) -> None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return
        if table.cursor_row >= len(self.row_symbols):
            return
        row_key = self.row_symbols[table.cursor_row]
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
