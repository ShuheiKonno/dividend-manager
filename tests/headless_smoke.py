"""ヘッドレススモーク確認スクリプト（CI/手動検証用、pytest 対象外）。

実ウィンドウを show() せずに構築し、メニュー・タブ・ダイアログの
即時バリデーション・treemap ホバー状態の配線を検証する。

実行: py -3 tests/headless_smoke.py

build_window() は「現在のデータフォルダ」を読み、起動時に履歴の当日行を書き込む。
スクリプト自身が一時フォルダのサンドボックス（実データのコピー＋差し替えた
%APPDATA%）を作ってから走るので、実データのフォルダと config.json には
コピー元として読む以外で触れない（_sandbox が実行前後で突合する）。
空データで走らせるときは環境変数 SMOKE_EMPTY_DATA=1 を付ける（実データの
設定＝税引後・優待あり等に依存せず、既定状態で全項目を通すため）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 座標系のチェックは show() する。PySide6 の import より前に決めないと効かない
# （明示指定があればそちらを優先する）。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

from src.dialogs import (  # noqa: E402
    ACCOUNT_CHOICES,
    EditHoldingDialog,
    MonthCheckboxGroup,
    NewHoldingDialog,
)
from src import app_config, gui, io_csv  # noqa: E402
from src.gui import build_window  # noqa: E402
from src.models import Holding, MarketData  # noqa: E402

# サンドボックスへ写すファイル（データ4本＋画面状態）。settings.json も写すのは、
# 実際に使っている設定（税引後・優待あり・ライトテーマ等）でも各チェックが
# 通ることを見るため。
_SANDBOX_FILES = ("holdings.csv", "market_data.csv", "fx.csv", "history.csv", "settings.json")


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """フォルダ配下の全ファイルの (サイズ, 更新時刻) を取る（無ければ空）。"""
    if not folder.is_dir():
        return {}
    return {
        path.relative_to(folder).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in folder.rglob("*")
        if path.is_file()
    }


@contextmanager
def _sandbox():
    """実データのコピーを置いた一時フォルダへデータフォルダと %APPDATA% を向ける。

    順序が肝: ①元の %APPDATA% のまま実データのフォルダを特定する（読むだけ）
    ②コピー ③%APPDATA% を差し替えて、そちらの config.json をサンドボックスへ向ける
    ④io_csv のパスを張り替える。io_csv の *_PATH は import 時に実データのフォルダで
    確定しているので、④を飛ばすと DATA_DIR だけでなく全パスが実データを指したまま
    になる。③は DividendWindow が設定を読み直したり既定フォルダへ落ちたりしても
    サンドボックスの外へ出ないための二重の囲い。
    """
    real = app_config.load_data_dir()
    before = _snapshot(real)
    root = Path(tempfile.mkdtemp(prefix="dividend_smoke_"))
    data = root / "data"
    data.mkdir()
    if os.environ.get("SMOKE_EMPTY_DATA") != "1":
        for name in _SANDBOX_FILES:
            if (real / name).is_file():
                shutil.copy2(real / name, data / name)
    original_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(root / "appdata")
    try:
        app_config.save_data_dir(data)
        io_csv.set_data_dir(data, persist=False)
        # SMOKE_ASSET_SCOPE=jp_stock,fund で、表示対象を絞って終了した状態の
        # settings.json を再現する（サンドボックス内の設定だけを書き換える）。
        scope = os.environ.get("SMOKE_ASSET_SCOPE")
        if scope:
            io_csv.save_settings(asset_scope=scope.split(","))
            assert io_csv.load_settings().get("asset_scope") == scope.split(","), (
                f"SMOKE_ASSET_SCOPE が不正（{scope}）。ASSET_CLASSES の順で書くこと"
            )
        # 実データのフォルダを指すパスが1つでも残っていれば走らせない。
        for name in ("DATA_DIR", *io_csv._DERIVED_PATHS):
            path = getattr(io_csv, name)
            assert root in path.parents and real not in path.parents and path != real, (
                f"io_csv.{name} がサンドボックスを指していない: {path}"
            )
        assert root in app_config.config_path().parents, app_config.config_path()
        assert app_config.load_data_dir() == data
        print(f"sandbox: {data}（コピー元: "
              f"{'なし（空データ）' if os.environ.get('SMOKE_EMPTY_DATA') == '1' else real}）"
              f"{f' / 対象: {scope}' if scope else ''}")
        yield data
    finally:
        if original_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = original_appdata
        shutil.rmtree(root, ignore_errors=True)
        # 実データへ一切書いていないこと（実行中にアプリ本体が書いた場合も赤くなる）。
        assert _snapshot(real) == before, f"実データのフォルダが変化した: {real}"


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="7203", name="トヨタ", asset_type="stock", currency="JPY",
        shares=100.0, avg_cost=2000.0, account="",
        annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
        div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
    )
    base.update(overrides)
    return Holding(**base)


def _check_actions(w) -> None:
    """QAction 一元化・Ctrl ショートカット・有効/無効の同期を検証する。"""
    actions = w._actions
    for key in ("update_market", "update_fx", "reload", "new", "edit", "delete",
                "toggle_mode", "toggle_withdrawal", "toggle_inactive",
                "theme_dark", "theme_light",
                "export_holdings", "export_history", "export_holdings_md",
                "import_holdings", "import_history",
                "change_data_dir", "open_data_dir", "create_shortcut", "quit",
                "split_apply", "split_dismiss", "split_apply_all",
                "asset_jp_stock", "asset_us_stock", "asset_fund", "asset_all"):
        assert key in actions, f"アクション {key} が無い: {sorted(actions)}"

    # 一文字ショートカットが残っていないこと（表で銘柄コードを打つと誤爆する）
    for key, action in actions.items():
        for sequence in action.shortcuts():
            text = sequence.toString()
            assert len(text) > 1, f"{key} に一文字キーが残っている: {text!r}"
    assert actions["update_market"].shortcut().toString() == "F5"
    # F5 は為替も取るので、メニュー（text）とボタン面（iconText）の両方で分かること。
    assert "為替" in actions["update_market"].text(), actions["update_market"].text()
    assert "為替" in actions["update_market"].iconText(), actions["update_market"].iconText()
    assert actions["update_fx"].text() == "為替取得（USDJPY）", actions["update_fx"].text()
    assert actions["edit"].shortcut().toString() == "Ctrl+E"
    assert actions["tab4"].shortcut().toString() == "Ctrl+5", "配当優待詳細タブのキーが無い"

    original_tab = w._tabs.currentIndex()

    try:
        # グラフタブ: 編集・削除・絞り込みは対象が無いので無効（無反応ボタンを作らない）
        w._tabs.setCurrentIndex(0)
        assert not actions["edit"].isEnabled(), "グラフタブで編集が有効のまま"
        assert not actions["delete"].isEnabled(), "グラフタブで削除が有効のまま"
        assert not w._filter.isEnabled(), "グラフタブで絞り込みが有効のまま"

        # 表タブ（株価詳細=3）: 行を選ぶと編集・削除が有効になる
        w._tabs.setCurrentIndex(3)
        assert w._filter.isEnabled(), "表タブで絞り込みが無効のまま"
        if w._price_proxy.rowCount() > 0:
            w._price_view.selectRow(0)
            assert actions["edit"].isEnabled(), "行を選んでも編集が有効にならない"
            assert actions["delete"].isEnabled(), "行を選んでも削除が有効にならない"

        # 月別配当タブ（=2）: 内訳は読み取り専用なのでグラフタブと同じ扱い
        w._tabs.setCurrentIndex(2)
        assert not actions["edit"].isEnabled(), "月別配当タブで編集が有効のまま"
        assert not actions["delete"].isEnabled(), "月別配当タブで削除が有効のまま"
        assert not w._filter.isEnabled(), "月別配当タブで絞り込みが有効のまま"

        # 取得中は更新系を伏せる（_busy を直接立てて同期だけ確認する）
        w._busy = True
        w._sync_action_states()
        assert not actions["update_market"].isEnabled(), "取得中も株価更新が押せる"
        assert not actions["reload"].isEnabled(), "取得中も再読込が押せる"
        # データフォルダの切替も伏せる（走行中のワーカーが旧フォルダへ書き戻す）。
        # 「開く」は読むだけなので取得中も押せてよい。
        assert not actions["change_data_dir"].isEnabled(), "取得中もデータフォルダを変更できる"
        assert actions["open_data_dir"].isEnabled(), "データフォルダを開くが無効になっている"
        w._busy = False
        w._sync_action_states()
        assert actions["update_market"].isEnabled(), "取得完了後に株価更新が戻らない"

    finally:
        w._busy = False
        w._sync_action_states()
        w._tabs.setCurrentIndex(original_tab)  # 設定（last_tab）を元へ戻す


def _check_toolbar(w) -> None:
    """ツールバーのグループ分け・折返し耐性・モード表示の追随を検証する。"""
    separators = [a for a in w._toolbar.actions() if a.isSeparator()]
    assert len(separators) >= 1, f"ツールバーの区切りが無い: {len(separators)}"
    delete_button = w._toolbar.widgetForAction(w._actions["delete"])
    assert delete_button is not None and delete_button.objectName() == "dangerAction"
    primary_button = w._toolbar.widgetForAction(w._actions["update_market"])
    assert primary_button is not None and primary_button.objectName() == "primaryAction"

    # 絞り込みはツールバーではなくタブ見出しの右端（コーナー）に置く。2段目の
    # ツールバーに置いていた頃は、利回りコンボを足した時点で最小幅 900px に
    # 収まらなくなり（sizeHint 1131px）、絞り込みが拡張メニューへ消えていた。
    corner = w._tabs.cornerWidget(Qt.Corner.TopRightCorner)
    assert corner is not None and corner.isAncestorOf(w._filter), (
        "絞り込みがタブのコーナーに無い"
    )
    for bar in (w._toolbar, w._view_toolbar):
        assert not bar.isAncestorOf(w._filter), "絞り込みがツールバーへ戻っている"

    # あふれの検知は拡張ボタン（»）の可視性で見る。個々のボタンの isVisible だと、
    # 見ていない項目（1段目右端の「分割を反映」等）があふれても気付けない。
    # 実レイアウトが要るのでここだけ show() する（offscreen なら画面には出ない）。
    combos = (w._mode_combo, w._withdrawal_combo, w._benefit_combo, w._asset_scope_button)
    original = w.size()
    original_scope = w.asset_scope
    w.show()
    try:
        # 「対象」ボタンは選択で文言の長さが変わる。全て（最短）と2クラス選択
        # （最長「日本株・米国株」）の両方で、あふれていないことを見る。
        for scope in (("jp_stock", "us_stock", "fund"), ("jp_stock", "us_stock")):
            w._set_asset_scope(scope)
            for width in (w.minimumWidth(), 1024, 1100, 1536):
                w.resize(width, max(620, w.minimumHeight()))
                QApplication.processEvents()
                for bar in (w._toolbar, w._view_toolbar):
                    ext = bar.findChild(QToolButton, "qt_toolbar_ext_button")
                    assert ext is None or not ext.isVisible(), (
                        f"幅 {width}px で {bar.objectName()} があふれている "
                        f"(sizeHint {bar.sizeHint().width()}px > 幅 {bar.width()}px)"
                    )
                assert all(c.isVisible() for c in combos), f"幅 {width}px でコンボが消えている"
                assert w._filter.isVisible(), f"幅 {width}px で絞り込みが消えている"
                # コーナーに幅を取られてタブ見出しが縮む（スクロール矢印が出てタブが
                # 隠れる）のは、絞り込みを移したことで起こり得る別の回帰。
                tab_bar = w._tabs.tabBar()
                assert tab_bar.width() >= tab_bar.sizeHint().width(), (
                    f"幅 {width}px でタブ見出しが縮んでいる "
                    f"({tab_bar.width()}px < {tab_bar.sizeHint().width()}px)"
                )
                assert corner.x() > tab_bar.geometry().right(), (
                    f"幅 {width}px で絞り込みがタブ見出しに重なっている"
                )
    finally:
        w._set_asset_scope(original_scope)  # 設定（asset_scope）を元へ戻す
        w.resize(original)
        w.hide()
        QApplication.processEvents()

    # ドロップダウンが現在のモードを示していること
    assert w._mode_combo.currentData() == w.dividend_mode
    assert w._withdrawal_combo.currentData() == (w.withdrawal_mode or "none")
    # 優待は推移グラフの操作行ではなくツールバー（税引・収入と同じ行）にある
    assert w._benefit_combo.currentData() == w.income_benefit
    # 表示対象の資産も同じ行に並び、ボタン面が現在の対象を示す
    from src.calc import asset_scope_label

    assert w._asset_scope_button.text() == asset_scope_label(w.asset_scope)
    assert not hasattr(w, "yield_scope"), (
        "利回りの「投信除く」が復活している（表示対象の資産に吸収した）"
    )
    assert not hasattr(w, "income_withdrawal"), (
        "取崩の独立スイッチが復活している（収入モードと二重管理になる）"
    )
    # ショートカット経由で切り替えてもドロップダウンが追随すること
    before = w.dividend_mode
    try:
        w.action_toggle_mode()
        assert w._mode_combo.currentData() == w.dividend_mode != before
    finally:
        w._set_dividend_mode(before)  # 元へ戻す（設定を汚さない）
    assert w.dividend_mode == before
    assert w._mode_combo.currentData() == before


def _check_kpi_cards(w) -> None:
    """KPI カードが9枚とも存在し、1行目4枚・2行目5枚の行分けが崩れていないこと。"""
    expected_row1 = ("評価額計", "簿価計", "損益", "損益率")
    expected_row2 = ("年間配当", "優待", "合計", "簿価利回り", "時価利回り")
    assert set(w._cards) == set(expected_row1) | set(expected_row2), (
        f"KPI カードの過不足: {sorted(w._cards)}"
    )

    # 座標判定には実レイアウトが要るので show() する（他の座標系チェックと同じ作法）。
    original = w.size()
    w.show()
    QApplication.processEvents()

    try:
        row1_y = {title: w._cards[title].y() for title in expected_row1}
        row2_y = {title: w._cards[title].y() for title in expected_row2}
        assert len(set(row1_y.values())) == 1, (
            f"1行目のカードが同じ行に並んでいない: {row1_y}"
        )
        assert len(set(row2_y.values())) == 1, (
            f"2行目のカードが同じ行に並んでいない: {row2_y}"
        )
        y1 = next(iter(row1_y.values()))
        y2 = next(iter(row2_y.values()))
        assert y1 < y2, (
            f"「損益率」(1行目, y={y1}) が「合計」(2行目, y={y2}) より下に来ている"
        )

    finally:
        w.resize(original)
        w.hide()
        QApplication.processEvents()


def _check_inactive_toggle(w) -> None:
    """無効銘柄トグルが表示行数とチェック状態へ反映されること。"""
    before_state = w.show_inactive
    before_rows = w._price_proxy.rowCount()
    try:
        w.action_toggle_inactive()
        assert w.show_inactive is not before_state, "トグルで状態が変わらない"
        assert w._actions["toggle_inactive"].isChecked() is w.show_inactive
        after_rows = w._price_proxy.rowCount()
        if w.show_inactive:
            assert after_rows >= before_rows, "表示にしたのに行が減った"
        else:
            assert after_rows <= before_rows, "非表示にしたのに行が増えた"
        w.action_toggle_inactive()  # 元へ戻す（設定を汚さない）
        assert w.show_inactive is before_state
    finally:
        if w.show_inactive is not before_state:
            w.action_toggle_inactive()  # 元へ戻す（設定を汚さない）


def _check_theme_toggle(w) -> None:
    """テーマ切替が QSS・チャート配色・チェック状態へ届くこと。

    色トークンを値で import している箇所があると、QSS だけ切り替わってチャートや
    表の文字色が旧テーマのまま取り残される（エラーは出ない）。切替後に描画側の
    ヘルパーが返す色まで突き合わせて、その退行を捕まえる。
    """
    from src import theme, widgets

    original = w.theme_name
    other = next(t for t in theme.THEMES if t != original)

    try:
        w.action_set_theme(other)
        assert w.theme_name == other and theme.CURRENT_THEME == other
        assert w._actions[f"theme_{other}"].isChecked(), "切替後のテーマにチェックが付かない"
        assert not w._actions[f"theme_{original}"].isChecked(), "旧テーマのチェックが残っている"
        assert theme.PALETTES[other]["BG_WINDOW"] in w.styleSheet(), "QSS が切り替わっていない"
        # 描画時に theme を読んでいるか（値 import していないか）の突合
        assert widgets.profit_text_qcolor(1.0).name() == theme.PALETTES[other]["PROFIT_UP"]
        assert gui.asset_trend_series()[0][2].name() == theme.PALETTES[other]["SERIES_VALUE"]
        assert gui.monthly_series()[0][2].name() == theme.PALETTES[other]["SERIES_DIVIDEND"]

        w.action_set_theme(original)  # 設定（theme）を元へ戻す
        assert w.theme_name == original
        assert theme.PALETTES[original]["BG_WINDOW"] in w.styleSheet()
        assert widgets.profit_text_qcolor(-1.0).name() == theme.PALETTES[original]["PROFIT_DOWN"]
    finally:
        if w.theme_name != original:
            w.action_set_theme(original)  # 設定（theme）を元へ戻す


def _check_fund_share_unit(w) -> None:
    """投信の口数単位が表示・入力だけを変え、保存値を動かさないことを検証する。

    ここが壊れると holdings.csv の口数が 1万倍ずれる（エラーは出ないまま時価と
    配当だけが変わる）ので、往復（開いて保存しただけ）の恒等性まで見る。
    """
    from src.calc import FUND_SHARE_UNITS, FUND_SHARE_UNIT_COLUMN_LABELS

    original = w.fund_share_unit
    for unit in FUND_SHARE_UNITS:
        assert f"fund_unit_{unit}" in w._actions, sorted(w._actions)

    fund = _holding(
        symbol="JP90C000H1T1", name="投信", asset_type="fund",
        shares=206.7284, avg_cost=10_000.0,
    )
    row = {"asset_type": "fund", "shares": 206.7284, "active": True}

    try:
        w.action_set_fund_share_unit("per1")
        assert w.fund_share_unit == "per1"
        assert w._actions["fund_unit_per1"].isChecked()
        assert not w._actions["fund_unit_per10k"].isChecked()
        # 表示（セル・ヘッダ・並び替えの生値）が 1口単位になること
        assert gui._format_cell(row, "shares", "per1")[0] == "2,067,284.00"
        assert gui.cell_sort_value(row, "shares", "per1") == 2_067_284.0
        label = FUND_SHARE_UNIT_COLUMN_LABELS["per1"]
        assert (label, "shares") in w._with_shares_label(gui.PRICE_COLUMNS)
        header = w._price_view.model().headerData(
            [k for _, k in gui.PRICE_COLUMNS].index("shares"), Qt.Orientation.Horizontal
        )
        assert header == label, f"株数列のヘッダが単位に追随しない: {header!r}"
        # 株式は単位設定の影響を受けない
        stock_row = {"asset_type": "stock", "shares": 100.0, "active": True}
        assert gui._format_cell(stock_row, "shares", "per1")[0] == "100.00"

        # 入力欄も 1口単位。開いて保存しただけで保存値が変わらないこと（往復が恒等）
        dlg = EditHoldingDialog(fund, fund_share_unit="per1")
        assert dlg._shares.text() == "2067284", dlg._shares.text()
        dlg._on_save()
        assert dlg.result_holding is not None
        assert dlg.result_holding.shares == 206.7284, dlg.result_holding.shares

        # 万口単位でも同様（既定モード）
        w.action_set_fund_share_unit("per10k")
        assert gui._format_cell(row, "shares", "per10k")[0] == "206.73"
        dlg2 = EditHoldingDialog(
            _holding(symbol="JP90C000H1T1", asset_type="fund", shares=206.7284),
            fund_share_unit="per10k",
        )
        assert dlg2._shares.text() == "206.7284"
        dlg2._on_save()
        assert dlg2.result_holding is not None
        assert dlg2.result_holding.shares == 206.7284

        # 新規登録は「保存時点の資産種別」で単位を解釈する（株式→投信の切替に追随）
        nd = NewHoldingDialog(_holding(symbol="JP90C000H1T1", name="投信"), fund_share_unit="per1")
        nd._shares.setText("2,067,284")
        nd._asset_type.setCurrentIndex(nd._asset_type.findData("fund"))
        assert nd._shares.text() == "2,067,284", "種別を変えたら入力値が書き換えられた"
        assert "口数" in nd._shares_label_widget.text(), nd._shares_label_widget.text()
        nd._on_save()
        assert nd.result_holding is not None
        assert nd.result_holding.shares == 206.7284, nd.result_holding.shares

        w.action_set_fund_share_unit(original)  # 設定（fund_share_unit）を元へ戻す
        assert w.fund_share_unit == original
    finally:
        if w.fund_share_unit != original:
            w.action_set_fund_share_unit(original)  # 設定（fund_share_unit）を元へ戻す


def _check_yield_definitions(w) -> None:
    """優待スイッチが KPI カードと表の利回り列へ届くこと。

    値だけ切り替えてカード名が「簿価利回り」のままだと、画面から今どの定義を見て
    いるのか分からず、優待込みの高い数字を配当だけの利回りとして読んでしまう。
    """
    from src.calc import (
        compute_rows,
        row_yield_keys,
        summary_yields,
        YIELD_BENEFIT_SUFFIX,
    )
    from src.utils import fmt_percent

    original_benefit = w.income_benefit
    rows, summary = compute_rows(
        [h for _, h in w._scoped()], w.market_data_map, w.fx_rate, w.dividend_mode,
        w.withdrawal_mode, include_inactive=w.show_inactive,
    )
    header = w._income_view.horizontalHeader().model()
    yield_col = next(
        i for i, (label, _) in enumerate(gui.INCOME_COLUMNS) if label == "簿価利回り"
    )

    try:
        for benefit in ("without", "with"):
            w._on_income_benefit(benefit)
            with_benefit = benefit == "with"
            suffix = YIELD_BENEFIT_SUFFIX if with_benefit else ""
            assert w._cards["簿価利回り"]._title.text() == f"簿価利回り{suffix}", (
                f"カード名が定義を示していない: {w._cards['簿価利回り']._title.text()}"
            )
            assert w._cards["時価利回り"]._title.text() == f"時価利回り{suffix}"
            yoc, yov = summary_yields(summary, with_benefit)
            assert w._cards["簿価利回り"]._value.text() == fmt_percent(yoc, 2), (
                f"{benefit} の簿価利回りが Summary と食い違う"
            )
            assert w._cards["時価利回り"]._value.text() == fmt_percent(yov, 2)

            # 表の利回り列も同じ定義へ差し替わること（KPI だけ切り替えると、同じ名前の
            # 「簿価利回り」がタブによって別の数字になる）。
            assert (
                header.headerData(yield_col, Qt.Orientation.Horizontal)
                == f"簿価利回り{suffix}"
            ), "表のヘッダが優待の有無を示していない"
            if rows:
                key = row_yield_keys(with_benefit)[0]
                expected, _ = gui._format_cell(rows[0], key)
                actual = w._income_model.item(0, yield_col).text()
                assert actual == expected, (
                    f"表の簿価利回りが {key} と食い違う: {actual} != {expected}"
                )

        # 「含まない」で列幅が確定した直後に「含む」へ切り替える起動時の並びを再現する。
        # 保存済みの列幅（前回終了時が「含む」なら既に広い）に結果が左右されないよう、
        # 短いヘッダに合わせた幅を明示的に当ててから切り替える。
        w._on_income_benefit("without")
        income_header = w._income_view.horizontalHeader()
        income_header.resizeSection(yield_col, income_header.sectionSizeHint(yield_col))
        narrow_width = income_header.sectionSize(yield_col)
        w._on_income_benefit("with")
        assert income_header.sectionSize(yield_col) > narrow_width, (
            "「＋優待」でヘッダが伸びたのに利回り列の幅が確定時のまま "
            f"({narrow_width}px)。接尾辞が切れて定義が読めない"
        )
        assert income_header.sectionSize(yield_col) >= income_header.sectionSizeHint(
            yield_col
        ), "広げたのにヘッダが収まっていない"

        # 逆に、手で広げた幅を切替で縮めないこと（内容幅ちょうどに合わせる実装だと痩せる）。
        widened = income_header.sectionSize(yield_col) + 80
        income_header.resizeSection(yield_col, widened)
        w._on_income_benefit("without")
        assert income_header.sectionSize(yield_col) == widened, (
            "優待を切り替えただけで手動調整した利回り列の幅が巻き戻っている"
        )
        w._on_income_benefit("with")
        assert income_header.sectionSize(yield_col) == widened

        # 接尾辞付きのカード名が通常使う幅で切れないこと・2行目が折り返さないこと・
        # 定義をツールチップで読めることを見る。
        w._on_income_benefit("with")
        original_size = w.size()
        w.show()
        for width in (1100, 1536):
            w.resize(width, max(620, w.minimumHeight()))
            QApplication.processEvents()
            row2 = ("年間配当", "優待", "合計", "簿価利回り", "時価利回り")
            ys = {title: w._cards[title].y() for title in row2}
            assert len(set(ys.values())) == 1, (
                f"幅 {width}px・接尾辞付きの名前で2行目が折り返した: {ys}"
            )
            for title in ("簿価利回り", "時価利回り"):
                card = w._cards[title]
                assert card.isVisible(), f"幅 {width}px で {title} カードが消えている"
                assert card._title.sizeHint().width() <= card._title.width(), (
                    f"幅 {width}px で {card._title.text()} がラベル幅 "
                    f"{card._title.width()}px に収まらない"
                )
        for title in ("簿価利回り", "時価利回り"):
            tip = w._cards[title].toolTip()
            assert "配当＋優待" in tip and "対象:" in tip, (
                f"{title} のツールチップが定義を説明していない: {tip!r}"
            )
        # 表のヘッダにも定義を載せる。列幅はユーザーが手で狭められるので、
        # 幅を取り直すだけでは読めなくなる場合が残る（カードと同じ逃げ道を用意する）。
        header_tip = w._income_model.headerData(
            yield_col, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole
        )
        assert header_tip and "配当＋優待" in header_tip, (
            f"表のヘッダに利回りの定義が無い: {header_tip!r}"
        )
        w.resize(original_size)
        w.hide()
        QApplication.processEvents()

        w._on_income_benefit(original_benefit)  # 設定（income_benefit）を元へ戻す
        assert w.income_benefit == original_benefit
    finally:
        w.hide()
        w._on_income_benefit(original_benefit)


def _check_asset_scope(w) -> None:
    """表示対象の資産（日本株 / 米国株 / 投信）の全組み合わせで画面が揃うこと。

    表の行・表示行 ↔ 保有の対応・KPI・構成グラフ・月別が**同じ部分集合**を見て
    いること、メニューとボタンが同じ QAction を共有し最後の1つを外せないこと、
    設定へ保存されることを見る。行の対応がずれると編集・削除が別の銘柄に当たる。
    """
    from itertools import combinations

    from src.calc import (
        ASSET_CLASSES,
        asset_class,
        asset_scope_label,
        compute_rows,
        monthly_breakdown,
        scope_holdings,
    )
    from src.utils import fmt_number

    original = w.asset_scope
    original_tab = w._tabs.currentIndex()
    original_filter = w._filter.text()

    # 表示メニューのサブメニューとツールバーのボタンが同じ QAction を持つこと。
    view_menu = next(
        a.menu() for a in w.menuBar().actions() if a.text().startswith("表示")
    )
    scope_menu = next(
        a.menu() for a in view_menu.actions()
        if a.menu() is not None and a.text() == "表示対象の資産"
    )
    shared = [w._actions[f"asset_{c}"] for c in ASSET_CLASSES]
    for menu in (scope_menu, w._asset_scope_button.menu()):
        for action in shared:
            assert action in menu.actions(), f"{action.text()} がメニューに無い"

    try:
        w._filter.clear()  # 絞り込み欄の文字で行数が減らないようにする
        w._tabs.setCurrentIndex(3)  # 株価詳細（行選択 → 保有の対応を確かめる）
        subsets = [
            combo for n in range(1, len(ASSET_CLASSES) + 1)
            for combo in combinations(ASSET_CLASSES, n)
        ]
        for scope in subsets:
            w._set_asset_scope(scope)
            assert w.asset_scope == scope
            assert io_csv.load_settings().get("asset_scope") == list(scope), (
                "表示対象が設定へ保存されていない"
            )
            assert w._asset_scope_button.text() == asset_scope_label(scope)
            for cls in ASSET_CLASSES:
                action = w._actions[f"asset_{cls}"]
                assert action.isChecked() is (cls in scope), f"{cls} のチェックがずれた"
                if len(scope) == 1 and cls in scope:
                    assert not action.isEnabled(), "最後の1つを外せる（全画面が空になる）"
            assert w._actions["asset_all"].isEnabled() is (len(scope) < len(ASSET_CLASSES))

            expected = scope_holdings(w.holdings, scope, include_inactive=w.show_inactive)
            assert w.row_holding_indices["price"] == [i for i, _ in expected], (
                "表示行 ↔ 保有の対応が絞り込みとずれている"
            )
            assert w.row_holding_indices["income"] == [i for i, _ in expected]
            assert w._price_proxy.rowCount() == len(expected)
            assert w._income_proxy.rowCount() == len(expected)
            rows, summary = compute_rows(
                [h for _, h in expected], w.market_data_map, w.fx_rate,
                w.dividend_mode, w.withdrawal_mode, include_inactive=w.show_inactive,
            )
            assert w._cards["評価額計"]._value.text() == fmt_number(
                summary.total_market_value_jpy, 0
            ), f"{scope} の評価額計が絞り込みと食い違う"
            assert w._cards["簿価計"]._value.text() == fmt_number(
                summary.total_book_value_jpy, 0
            )
            chart_rows = [r for r in rows if r.get("active") is not False]
            assert w._monthly.known_total_jpy == monthly_breakdown(
                chart_rows
            ).known_total_jpy, f"{scope} の月別が絞り込みと食い違う"
            assert ("対象:" in w._info.text()) is (len(scope) < len(ASSET_CLASSES)), (
                f"ステータスバーが絞り込みを示していない: {w._info.text()!r}"
            )
            if w._price_proxy.rowCount() > 0:
                w._price_view.selectRow(0)
                idx = w._current_holding_index()
                assert idx is not None
                holding = w.holdings[idx]
                assert asset_class(holding.asset_type, holding.currency) in scope, (
                    "選択行が表示対象外の銘柄を指している（編集・削除が別の銘柄に当たる）"
                )

        # 最後の1つを外そうとしても対象は空にならない（QAction 以外の経路の守り）。
        w._set_asset_scope(("jp_stock",))
        w.action_toggle_asset_class("jp_stock", False)
        assert w.asset_scope == ("jp_stock",)
        # 「すべて表示」で全クラスへ戻る。
        w._actions["asset_all"].trigger()
        assert w.asset_scope == ASSET_CLASSES
        # チェックの付け外しは QAction から行える。
        w._actions["asset_fund"].trigger()
        assert w.asset_scope == ("jp_stock", "us_stock")
        w._actions["asset_fund"].trigger()
        assert w.asset_scope == ASSET_CLASSES
    finally:
        w._set_asset_scope(original)  # 設定（asset_scope）を元へ戻す
        w._filter.setText(original_filter)
        w._tabs.setCurrentIndex(original_tab)  # 設定（last_tab）を元へ戻す
    assert w.asset_scope == original


def _check_trend_scope(w) -> None:
    """推移グラフが表示対象の資産に追随し、記録前の期間を推定で埋めないこと。

    履歴はメモリ上の合成データへ差し替える（保存はしない）。1日目は合計列だけ
    （資産別の列を足す前）、2・3日目は資産別の列もある。
    """
    from src.calc import ASSET_CLASSES
    from src.models import HistoryRecord, class_history_key as key

    def record(date, by_class):
        return HistoryRecord(
            record_date=date, total_market_value_jpy=1000.0,
            total_book_value_jpy=800.0, total_dividend_jpy=50.0,
            total_benefit_jpy=5.0, total_profit_jpy=200.0,
            total_withdrawal_income_jpy=70.0,
            total_fixed_withdrawal_income_jpy=90.0, source="app",
            by_class=by_class,
        )

    breakdown = {
        key("jp_stock", "market_value"): 600.0, key("jp_stock", "book_value"): 500.0,
        key("jp_stock", "dividend"): 30.0, key("jp_stock", "benefit"): 5.0,
        key("us_stock", "market_value"): 300.0, key("us_stock", "book_value"): 200.0,
        key("us_stock", "dividend"): 10.0, key("us_stock", "benefit"): 0.0,
        key("fund", "market_value"): 100.0, key("fund", "book_value"): 100.0,
        key("fund", "dividend"): 10.0, key("fund", "benefit"): 0.0,
        key("fund", "withdrawal_income"): 30.0,
        key("fund", "fixed_withdrawal_income"): 50.0,
    }
    synthetic = [
        record("2026-10-01", {}),
        record("2026-10-24", breakdown),
        record("2026-10-25", breakdown),
    ]
    original_history = w.history
    original_scope = w.asset_scope
    original_period = w._asset_line._period
    try:
        w.history = synthetic
        w._asset_line.set_period("all")
        w._income_line.set_period("all")

        w._set_asset_scope(ASSET_CLASSES)
        w._refresh_trend()  # 対象が元から全てだと _set_asset_scope は描き直さない
        market = [r.get("total_market_value_jpy") for r in w._asset_line._visible]
        assert market == [1000.0, 1000.0, 1000.0], f"全選択で合計列を使っていない: {market}"
        assert w._asset_trend_note.text() == ""

        w._set_asset_scope(("jp_stock", "fund"))
        market = [r.get("total_market_value_jpy") for r in w._asset_line._visible]
        assert market == [None, 700.0, 700.0], (
            f"絞った推移が資産別の和になっていない／記録前を埋めている: {market}"
        )
        note = "※資産別の内訳は 2026-10-24 以降のみ記録"
        assert w._asset_trend_note.text() == note, w._asset_trend_note.text()
        assert note in w._income_trend_note.text(), w._income_trend_note.text()
        income = [r.get("income_value") for r in w._income_line._visible]
        expected = 40.0 + (5.0 if w.income_benefit == "with" else 0.0)
        if w.withdrawal_mode == "fixed":
            expected = 80.0 + (5.0 if w.income_benefit == "with" else 0.0)
        elif w.withdrawal_mode == "rate":
            expected = 60.0 + (5.0 if w.income_benefit == "with" else 0.0)
        assert income == [None, expected, expected], f"配当推移が絞れていない: {income}"

        # 資産別の記録が1日も無ければ、空メッセージで理由を出す。
        w.history = synthetic[:1]
        w._refresh_trend()
        assert "内訳はまだ記録がありません" in w._asset_line._empty_message
        assert w._asset_trend_note.text() == ""
    finally:
        w.history = original_history
        w._set_asset_scope(original_scope)  # 設定（asset_scope）を元へ戻す
        w._asset_line.set_period(original_period)
        w._income_line.set_period(w.income_trend_period)
        w._refresh_trend()


def _check_split_flags(w) -> None:
    """株式分割の旗・補正ボタン・メニューが実ウィンドウで配線されていること。

    保有・市場データはメモリ上の合成データへ差し替え、保存と再読込は差し替えて
    実データを汚さない（build_window() は実データを読むので、元へ戻して終える）。
    旗の立て方そのもの（未反映の判定）は tests/test_splits.py が固定する。ここでは
    「旗が両テーブルへ描かれる / 選択に応じてボタンが押せる / 押すと旗が消える /
    テーマ切替で旗の色が追随する」という配線だけを見る。
    """
    from datetime import date, timedelta

    from src import theme

    split_day = (date.today() - timedelta(days=3)).isoformat()

    def market(symbol: str, with_split: bool = True) -> MarketData:
        return MarketData(
            symbol=symbol, currency="JPY", last_price=3737.0, price_as_of=split_day,
            annual_div_ps_gross=100.0, annual_div_ps_net=None, div_forecast_as_of=None,
            source="smoke", fetched_at=None,
            recent_splits=[(split_day, 2.0)] if with_split else [],
        )

    pending = _holding(symbol="9901", name="未反映", account="特定", shares=300.0,
                       avg_cost=2503.0, annual_div_ps_gross_override=108.0)
    done = _holding(symbol="9902", name="反映済み", account="特定",
                    split_applied_through=split_day)
    fund = _holding(symbol="9903", name="投信", asset_type="fund")
    market_map = {s: market(s) for s in ("9901", "9902", "9903")}

    original_holdings = w.holdings
    original_market = w.market_data_map
    original_tab = w._tabs.currentIndex()
    original_theme = w.theme_name
    original_save = gui.save_holdings
    original_question = gui.QMessageBox.question
    saved: dict = {}
    gui.save_holdings = lambda rows: saved.update(rows=rows)
    w._reload_all = w._refresh_view  # 保存後の再読込を、ディスクを読まない再描画へ
    try:
        w.holdings = [pending, done, fund]
        w.market_data_map = market_map
        w._tabs.setCurrentIndex(3)
        w._refresh_view()

        def flag_cells(tab_key, model, columns):
            col = [k for _, k in columns].index("split_flag")
            indices = w.row_holding_indices[tab_key]
            return {indices[r]: model.item(r, col) for r in range(model.rowCount())}

        # 旗は両テーブルに、未反映の行だけ立つ（反映済み・投信には立たない）。
        for tab_key, model, columns in (
            ("price", w._price_model, gui.PRICE_COLUMNS),
            ("income", w._income_model, gui.INCOME_COLUMNS),
        ):
            cells = flag_cells(tab_key, model, columns)
            assert cells[0].text().startswith("⚑ 2:1("), (tab_key, cells[0].text())
            # 反映済みの行は「完了」表示（✓）、投信は分割の対象外なので空。
            assert cells[1].text().startswith("✓ "), (tab_key, cells[1].text())
            assert cells[2].text() == "", tab_key
            assert "→" in cells[0].toolTip() and "分割を反映" in cells[0].toolTip(), tab_key
            assert cells[0].font().bold(), f"{tab_key}: 旗が強調されていない"
            assert cells[0].foreground().color().name() == theme.SPLIT_FLAG, tab_key

        # 補正ボタンは、メニュー・ツールバー・右クリックが同じ QAction を共有する。
        acts = w._actions
        data_menu = [a for a in w.menuBar().actions() if a.text().startswith("データ")][0]
        data_actions = data_menu.menu().actions()
        assert acts["split_apply"] in data_actions, "データメニューに分割を反映が無い"
        assert acts["split_dismiss"] in data_actions
        assert acts["split_apply_all"] in data_actions
        assert acts["split_apply"] in w._toolbar.actions(), "ツールバーに補正ボタンが無い"

        # 押せるのは「旗の立った行を選んでいるとき」だけ（無反応ボタンを作らない）。
        def select_holding(index: int) -> None:
            src_row = w.row_holding_indices["price"].index(index)
            proxy_idx = w._price_proxy.mapFromSource(w._price_model.index(src_row, 0))
            w._price_view.selectRow(proxy_idx.row())

        select_holding(0)
        assert acts["split_apply"].isEnabled(), "旗の行を選んでも補正が押せない"
        assert acts["split_dismiss"].isEnabled()
        assert acts["split_apply_all"].isEnabled()
        select_holding(1)
        assert not acts["split_apply"].isEnabled(), "反映済みの行で補正が押せる"
        assert not acts["split_dismiss"].isEnabled()
        assert acts["split_apply_all"].isEnabled(), "未反映の行が残っているのにまとめて反映が押せない"
        select_holding(0)
        w._busy = True
        w._sync_action_states()
        assert not acts["split_apply"].isEnabled(), "取得中も補正が押せる"
        assert not acts["split_apply_all"].isEnabled()
        w._busy = False
        w._sync_action_states()
        w._tabs.setCurrentIndex(0)
        assert not acts["split_apply"].isEnabled(), "グラフタブで補正が押せる"
        w._tabs.setCurrentIndex(3)
        select_holding(0)

        # 押す → 補正が保存され、旗が消えてボタンも押せなくなる（二度押しの二重適用が無い）。
        gui.QMessageBox.question = staticmethod(
            lambda *a, **k: gui.QMessageBox.StandardButton.Yes
        )
        w.action_apply_split()
        row = saved["rows"][0]
        assert (row.shares, row.avg_cost, row.annual_div_ps_gross_override) == (
            600.0, 1251.5, 54.0
        ), row
        assert row.split_applied_through == split_day
        assert saved["rows"][1:] == [done, fund], "選択していない行を書き換えた"
        # 旗は消さず、別の色の「完了」表示へ変わる（押したことが画面で読める）。
        for tab_key, model, columns in (
            ("price", w._price_model, gui.PRICE_COLUMNS),
            ("income", w._income_model, gui.INCOME_COLUMNS),
        ):
            done_cell = flag_cells(tab_key, model, columns)[0]
            assert done_cell.text().startswith("✓ 2:1("), (tab_key, done_cell.text())
            assert done_cell.foreground().color().name() == theme.SPLIT_DONE, tab_key
            assert theme.SPLIT_DONE != theme.SPLIT_FLAG
            assert not done_cell.font().bold(), f"{tab_key}: 完了表示が旗と同じ強調のまま"
            assert "反映済み" in done_cell.toolTip(), tab_key
        select_holding(0)
        assert not acts["split_apply"].isEnabled(), "補正後も補正ボタンが押せる"
        assert not acts["split_apply_all"].isEnabled()

        # 「反映済みにする」も同じ完了表示になり、数値は変わらない。
        w.holdings = [pending, done, fund]
        w._refresh_view()
        select_holding(0)
        saved.clear()
        w.action_dismiss_split()
        row = saved["rows"][0]
        assert (row.shares, row.avg_cost) == (300.0, 2503.0), "反映済みにしただけで数値が動いた"
        assert flag_cells("price", w._price_model, gui.PRICE_COLUMNS)[0].text().startswith("✓ "), (
            "反映済みにしても完了表示にならない"
        )

        # 旗の色はテーマ切替で追随する（値 import していないこと）。
        w.holdings = [pending, done, fund]
        w._refresh_view()
        other = next(t for t in theme.THEMES if t != original_theme)
        w.action_set_theme(other)
        cell = flag_cells("price", w._price_model, gui.PRICE_COLUMNS)[0]
        assert cell.foreground().color().name() == theme.PALETTES[other]["SPLIT_FLAG"], (
            "テーマ切替後も旗が旧テーマの色のまま"
        )
    finally:
        gui.QMessageBox.question = original_question
        gui.save_holdings = original_save
        del w._reload_all  # インスタンス属性を外し、クラスの _reload_all へ戻す
        w._busy = False
        if w.theme_name != original_theme:
            w.action_set_theme(original_theme)  # 設定（theme）を元へ戻す
        w.holdings = original_holdings
        w.market_data_map = original_market
        w._tabs.setCurrentIndex(original_tab)
        w._refresh_view()
        w._sync_action_states()


def _check_account_combo() -> None:
    """口座が選択式で、既存値・未知値・未設定を取りこぼさないこと。"""
    dlg = EditHoldingDialog(_holding(account="特定"))
    combo = dlg._account
    assert [combo.itemData(i) for i in range(combo.count())] == ACCOUNT_CHOICES
    assert combo.currentData() == "特定", "既存の口座が選択状態にならない"

    # 空欄は「（未設定）」として表示し、保存値は空文字のまま
    empty = EditHoldingDialog(_holding(account=""))
    assert empty._account.currentData() == ""
    assert empty._account.currentText() == "（未設定）"

    # CSV を手編集して一覧に無い値が入っていても、開いただけで消さない
    unknown = EditHoldingDialog(_holding(account="旧口座XYZ"))
    assert unknown._account.currentData() == "旧口座XYZ", "未知の口座が保持されない"

    # 選択 → 保存で holding へ反映されること
    target = _holding(account="")
    save_dlg = EditHoldingDialog(target)
    index = save_dlg._account.findData("NISA（成長）")
    assert index >= 0
    save_dlg._account.setCurrentIndex(index)
    save_dlg._on_save()
    assert save_dlg.result_holding is not None
    assert save_dlg.result_holding.account == "NISA（成長）"


def _check_auto_fields_are_untouched() -> None:
    """自動計算に任せた項目はダイアログに欄が無く、保存で既存値を消さないこと。

    税引後配当は gross × (1 − 税率)、税率は calc.resolve_tax_rate が口座・通貨から
    自動判定する。CSV を手編集して例外値を入れた銘柄が「開いて保存しただけ」で
    自動値へ戻ると、エラーも出ないまま税引後の額だけが変わるため代入しない。
    """
    dlg = EditHoldingDialog(
        _holding(tax_rate=0.15, annual_div_ps_net_override=42.0)
    )
    assert not hasattr(dlg, "_tax_rate"), "源泉税率の入力欄が残っている"
    assert not hasattr(dlg, "_div_net"), "配当(税引後)上書きの入力欄が残っている"
    dlg._on_save()
    assert dlg.result_holding is not None
    assert dlg.result_holding.tax_rate == 0.15, "保存で手編集の源泉税率が消えた"
    assert dlg.result_holding.annual_div_ps_net_override == 42.0, (
        "保存で手編集の税引後上書きが消えた"
    )

    # 分割の反映済み日も同じ扱い。開いて保存しただけで消えると、反映済みの分割へ
    # 旗が再び立ち、補正を押すと二重適用になる（エラーは出ない）。
    dlg = EditHoldingDialog(_holding(split_applied_through="2026-09-29"))
    dlg._on_save()
    assert dlg.result_holding is not None
    assert dlg.result_holding.split_applied_through == "2026-09-29", (
        "保存で分割の反映済み日が消えた"
    )

    # 新規登録は両方 None のまま（＝自動計算に任せる）で追加される
    nd = NewHoldingDialog(_holding(symbol="8306", name="三菱UFJ"))
    assert not hasattr(nd, "_tax_rate"), "新規ダイアログに源泉税率の欄が残っている"
    nd._on_save()
    assert nd.result_holding is not None
    assert nd.result_holding.tax_rate is None
    assert nd.result_holding.annual_div_ps_net_override is None


def _market(**overrides) -> MarketData:
    base = dict(
        symbol="7203", currency="JPY", last_price=3000.0, price_as_of=None,
        annual_div_ps_gross=95.0, annual_div_ps_net=None, div_forecast_as_of=None,
        source="yfinance", fetched_at=None,
    )
    base.update(overrides)
    return MarketData(**base)


def _check_monthly_tab(w) -> None:
    """月別配当タブ: 初期選択月・バークリック配線・並び替えを検証する。"""
    from src.utils import now_jst

    assert w._monthly_bar._selected == now_jst().date().month, "初期選択月が当月でない"
    # 見出しの語は収入モードで「配当」/「収入」に変わるのでどちらも許す
    assert "内訳" in w._monthly_head.text(), w._monthly_head.text()
    assert "推定" in w._monthly_note.text(), "推定である旨の注記が無い"

    original_month = w.monthly_month
    original_benefit = w.income_benefit
    try:
        w._monthly_bar.month_clicked.emit(3)
        assert w.monthly_month == 3, "バークリックで対象月が切り替わらない"
        assert w._monthly_head.text().startswith("3月の"), w._monthly_head.text()

        # 並び替えの導線はヘッダクリックのみ（専用ドロップダウンは持たない）
        assert not hasattr(w, "_on_monthly_sort"), "並び替えドロップダウンが復活している"
        header = w._monthly_view.horizontalHeader()
        assert header.isSortIndicatorShown(), "ヘッダに並び替えの矢印が出ない"
        # 既定は合計列の降順。ただし優待「含まない」では合計列が伏せてあるので配当列。
        expected_default = (
            gui.MONTHLY_COL_TOTAL if w.income_benefit == "with"
            else gui.MONTHLY_COL_DIVIDEND
        )
        assert header.sortIndicatorSection() == expected_default, (
            f"既定の並び替え列が違う: {header.sortIndicatorSection()}"
        )
        assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

        # ヘッダで選んだ並びは月を切り替えても保たれること（再描画で既定へ戻さない）
        w._monthly_view.sortByColumn(gui.MONTHLY_COL_DIVIDEND, Qt.SortOrder.AscendingOrder)
        w._on_monthly_month(5)
        assert header.sortIndicatorSection() == gui.MONTHLY_COL_DIVIDEND, (
            "月を切り替えると並び替えが既定へ戻る"
        )
        assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

        w._on_monthly_month(original_month)
        _check_monthly_benefit_toggle(w)
    finally:
        w._on_monthly_month(original_month)
        w._on_income_benefit(original_benefit)  # 設定（income_benefit）を元へ戻す


def _check_withdrawal_trend_note(w) -> None:
    """収入モードごとに専用の履歴列を描き、記録開始日を注記に出すこと。

    定額と定率で列を兼用すると、定額を選んでいるのに定率の線が出る（値も凡例も
    尤もらしいまま間違う）。取崩の列は後から足したので、それ以前の日は線が引けず、
    理由が画面に無いと「切り替えたらグラフが消えた」に見える。
    """
    original = w.withdrawal_mode
    original_dividend = w.dividend_mode
    hist = w._history_as_dicts()

    try:
        # 税引後では「税引前で記録」の注記が加わる（_check_net_trend_note が見る）。
        # ここは取崩の注記だけを見たいので税引前に固定する。
        w._set_dividend_mode("gross")
        w._set_withdrawal_mode(None)
        assert w._income_trend_note.text() == "", "取崩無しでも取崩の注記が出ている"
        assert w._income_line._series[0][0].startswith("配当"), w._income_line._series[0][0]

        for mode, expected_label in (("fixed", "定額取崩収入"), ("rate", "定率取崩収入")):
            w._set_withdrawal_mode(mode)
            assert w._income_line._series[0][0].startswith(expected_label), (
                w._income_line._series[0][0]
            )
            key = gui.INCOME_BASE_KEYS[mode]
            covered = [r for r in hist if r.get(key) is not None]
            if hist and covered and covered[0]["date"] != hist[0]["date"]:
                assert covered[0]["date"] in w._income_trend_note.text(), (
                    f"{mode} の記録開始日が注記に出ない: {w._income_trend_note.text()!r}"
                )

    finally:
        w._set_withdrawal_mode(original)  # 設定（withdrawal_mode）を元へ戻す
        w._set_dividend_mode(original_dividend)


def _check_net_trend_note(w) -> None:
    """税引後モードでは、推移が税引前の記録である旨を凡例と注記に出すこと。

    履歴は表示モードに依存させないため税引前で記録している。税引後を選んだまま
    黙って税引前の線を描くと、KPI の年間配当とグラフの右端が食い違って見える。
    """
    original = w.withdrawal_mode
    original_dividend = w.dividend_mode
    try:
        w._set_withdrawal_mode(None)
        w._set_dividend_mode("net")
        assert w._income_line._series[0][0].endswith("（税引前）"), (
            w._income_line._series[0][0]
        )
        assert gui.NET_TREND_NOTE in w._income_trend_note.text(), (
            f"税引後なのに税引前で記録の注記が無い: {w._income_trend_note.text()!r}"
        )
        # 取崩の記録開始日の注記とは併記する（片方で上書きしない）。
        hist = w._history_as_dicts()
        for mode in ("fixed", "rate"):
            w._set_withdrawal_mode(mode)
            note = w._income_trend_note.text()
            assert gui.NET_TREND_NOTE in note, note
            assert w._withdrawal_coverage_note(hist, mode) in note, note
            assert w._income_line._series[0][0].endswith("（税引前）")

        w._set_withdrawal_mode(None)
        w._set_dividend_mode("gross")
        assert gui.NET_TREND_NOTE not in w._income_trend_note.text(), (
            "税引前モードでも税引前の注記が残っている"
        )
        assert not w._income_line._series[0][0].endswith("（税引前）")
    finally:
        w._set_withdrawal_mode(original)  # 設定を元へ戻す
        w._set_dividend_mode(original_dividend)


def _check_monthly_benefit_toggle(w) -> None:
    """優待スイッチが月別タブの棒・列構成・並び替え列へ反映されること。"""
    header = w._monthly_view.horizontalHeader()

    w._on_income_benefit("with")
    assert [s[0] for s in w._monthly_bar._series] == ["配当", "優待"], (
        "優待『含む』で棒が積み上げにならない"
    )
    assert not w._monthly_view.isColumnHidden(gui.MONTHLY_COL_BENEFIT)
    assert not w._monthly_view.isColumnHidden(gui.MONTHLY_COL_BENEFIT_DETAIL)
    assert not w._monthly_view.isColumnHidden(gui.MONTHLY_COL_TOTAL)
    # 合計列で並べた状態から優待を外す＝並び替え基準列が伏せられるケース
    w._monthly_view.sortByColumn(gui.MONTHLY_COL_TOTAL, Qt.SortOrder.DescendingOrder)

    w._on_income_benefit("without")
    assert [s[0] for s in w._monthly_bar._series] == ["配当"], (
        "優待『含まない』でも優待が積まれている"
    )
    assert w._monthly_view.isColumnHidden(gui.MONTHLY_COL_BENEFIT), "優待列が伏せられない"
    # 金額を伏せて説明だけ残すと、見出しの合計（配当のみ）と表の見た目が食い違う。
    assert w._monthly_view.isColumnHidden(gui.MONTHLY_COL_BENEFIT_DETAIL), (
        "優待内容列が優待(円) と一緒に伏せられない"
    )
    assert w._monthly_view.isColumnHidden(gui.MONTHLY_COL_TOTAL), "合計列が伏せられない"
    assert header.sortIndicatorSection() == gui.MONTHLY_COL_DIVIDEND, (
        "伏せた合計列で並べ替えており矢印がどこにも出ない"
    )
    # 見出しの金額も優待を落とした額（＝画面に残る配当列の合計）になること
    from src.utils import fmt_number

    bucket = next((b for b in w._monthly.buckets if b.month == w.monthly_month), None)
    expected = bucket.dividend_jpy if bucket is not None else 0.0
    assert f"¥{fmt_number(expected, 0)}" in w._monthly_head.text(), w._monthly_head.text()


def _check_app_icon(w) -> None:
    """アプリアイコンが読み込まれ、タスクバー相当の小サイズまで揃っていること。

    生成は tools/make_app_icon.py（実行時には描かない）。assets/app.ico を消すと
    「タイトルバーだけ既定の Qt アイコンに戻る」という形で静かに退行する。
    """
    assert gui.APP_ICON_PATH.exists(), f"アイコンが無い: {gui.APP_ICON_PATH}"
    icon = QApplication.instance().windowIcon()
    assert not icon.isNull(), "QApplication にウィンドウアイコンが設定されていない"
    available = {s.width() for s in icon.availableSizes()}
    for size in (16, 32, 256):
        assert size in available, f"{size}px のアイコンが .ico に無い: {sorted(available)}"
    # ウィンドウ側は個別指定せずアプリ既定を継承する（設定箇所を1つに保つ）
    assert not w.windowIcon().isNull(), "ウィンドウがアプリのアイコンを継承していない"


def _check_month_checkboxes() -> None:
    """支払月チェックボックス群の自動判定トグルと保存値を検証する。"""
    group = MonthCheckboxGroup([3, 9])
    assert group.months() == [3, 9]
    group.set_months([6, 12])
    assert group.months() == [6, 12]

    # months() は毎回新しいリストを返す（replace の浅いコピーでの共有事故を防ぐ）
    taken = group.months()
    taken.append(1)
    assert group.months() == [6, 12], "months() が内部リストを露出している"

    # 自動判定 ON の間は [] を保存する（自動値を手動値として凍結しない）
    auto = MonthCheckboxGroup([], auto_months=[6, 12])
    assert auto.is_auto() is True
    assert auto.months() == []
    auto._auto.setChecked(False)
    assert auto.months() == [6, 12], "自動を外すと自動値が起点にならない"

    # 手動指定済みの銘柄は自動モードで開かない
    manual = MonthCheckboxGroup([3], auto_months=[6, 12])
    assert manual.is_auto() is False
    assert manual.months() == [3]

    # 自動判定できないときはトグルを出さない（ON なのに全部空、を作らない）
    no_auto = MonthCheckboxGroup([], auto_months=[])
    assert no_auto._auto is None
    assert no_auto.months() == []

    # 編集ダイアログへの組み込み: 権利落ち 3月/9月 → 支払 6月/12月 をプレビュー
    dlg = EditHoldingDialog(
        _holding(), market=_market(div_ex_month_ratios={3: 0.5, 9: 0.5})
    )
    assert dlg._div_months.is_auto() is True
    assert dlg._div_months._auto_months == [6, 12], dlg._div_months._auto_months
    assert dlg._benefit_months._auto is None, "優待に自動判定トグルが付いている"
    dlg._on_save()
    assert dlg.result_holding is not None
    assert dlg.result_holding.dividend_months == [], "自動モードで月が凍結された"

    # 自動を外して手動指定すると保存される
    dlg2 = EditHoldingDialog(
        _holding(), market=_market(div_ex_month_ratios={3: 1.0})
    )
    dlg2._div_months._auto.setChecked(False)
    dlg2._div_months.set_months([6, 12])
    dlg2._benefit_months.set_months([3])
    dlg2._on_save()
    assert dlg2.result_holding is not None
    assert dlg2.result_holding.dividend_months == [6, 12]
    assert dlg2.result_holding.benefit_months == [3]


def _check_benefit_detail() -> None:
    """優待内容の入力欄が両ダイアログにあり、そのまま保存されること。

    自由記述のメモなので検証は付けない（_parse_num を通すと "1,000円分" のような
    入力が数値として壊れる）。カンマは csv 側が引用符で守るため CSV も壊れない。
    """
    dlg = EditHoldingDialog(_holding(benefit_detail="クオカード1,000円分"))
    assert dlg._benefit_detail.text() == "クオカード1,000円分"
    dlg._benefit_detail.setText("  ジェフグルメカード3,000円分  ")
    dlg._on_save()
    assert dlg.result_holding is not None
    assert dlg.result_holding.benefit_detail == "ジェフグルメカード3,000円分", (
        dlg.result_holding.benefit_detail
    )

    nd = NewHoldingDialog(_holding(symbol="9433", name="KDDI"))
    nd._benefit_detail.setText("カタログギフト5,000円分")
    nd._on_save()
    assert nd.result_holding is not None
    assert nd.result_holding.benefit_detail == "カタログギフト5,000円分"

    # 配当優待詳細タブと月別内訳の両方に列がある（列キーは calc の row と揃える）
    assert ("優待内容", "benefit_detail") in gui.INCOME_COLUMNS, gui.INCOME_COLUMNS
    assert ("優待内容", "benefit_detail") in gui.MONTHLY_COLUMNS, gui.MONTHLY_COLUMNS
    # 自由記述なので数値ソート対象にしない（UserRole に生値を入れない列）
    assert "benefit_detail" not in gui.NUMERIC_SORT_COLUMNS

    # 初期幅に上限を当てる。青天井だと長い優待内容1件で右側の数値列が画面外へ出る
    # （この表は setStretchLastSection(False) で内容幅のまま並べるため余白が無い）。
    from PySide6.QtGui import QStandardItemModel
    from PySide6.QtWidgets import QTableView

    view = QTableView()
    view.setModel(QStandardItemModel(1, len(gui.MONTHLY_COLUMNS)))
    detail_col = [k for _, k in gui.MONTHLY_COLUMNS].index("benefit_detail")
    view.setColumnWidth(detail_col, 900)
    view.setColumnWidth(0, 900)  # 自由記述でない列は対象外
    gui.clamp_free_text_columns(view, gui.MONTHLY_COLUMNS)
    assert view.columnWidth(detail_col) == gui.FREE_TEXT_COLUMN_MAX_WIDTH
    assert view.columnWidth(0) == 900, "自由記述でない列まで縮めている"


def main() -> None:
    with _sandbox() as data:
        _run_checks(data)
    print("headless smoke: OK")


def _run_checks(data: Path) -> None:
    from src.calc import ASSET_CLASSES

    w = build_window()
    # 既定フォルダへのフォールバック（DividendWindow.__init__）でサンドボックスの
    # 外へ出ていないこと。
    assert gui.io_csv.DATA_DIR == data, f"データフォルダが外れた: {gui.io_csv.DATA_DIR}"
    # 保存済みの表示対象で起動していること（SMOKE_ASSET_SCOPE で絞った設定を再現）。
    saved_scope = io_csv.load_settings().get("asset_scope")
    if saved_scope:
        assert w.asset_scope == tuple(saved_scope), (w.asset_scope, saved_scope)
    # 以降のチェックの多くは全銘柄を前提に期待値を組んでいる（推移の注記・分割の
    # 旗・月別など）。絞った設定でも同じ前提で見られるよう全クラスへ固定し、
    # 最後に元の対象へ戻す（_check_asset_scope 自体は全組み合わせを回す）。
    original_scope = w.asset_scope
    w._set_asset_scope(ASSET_CLASSES)
    try:
        _run_window_checks(w)
    finally:
        w._set_asset_scope(original_scope)
    assert io_csv.load_settings().get("asset_scope", list(ASSET_CLASSES)) == list(
        original_scope
    ), "スモークの後で表示対象の設定が元に戻っていない"
    _run_dialog_checks()


def _run_window_checks(w) -> None:

    # タブ・メニュー構成
    assert w._tabs.count() == 5, "タブが5つ生成されていない"
    # 並びはグラフ系3枚（評価額 / 配当金 / 月別配当）→ 明細2枚（株価詳細 / 配当優待詳細）。
    # _active_tab_key が index 3/4 だけを表タブとして拾うので順序ごと固定する。
    assert [w._tabs.tabText(i) for i in range(5)] == [
        "評価額", "配当金", "月別配当", "株価詳細", "配当優待詳細"
    ], [w._tabs.tabText(i) for i in range(5)]
    menus = [a for a in w.menuBar().actions() if a.menu() is not None]
    assert len(menus) == 4, f"メニューが4つでない: {len(menus)}"
    file_actions = [a.text() for a in menus[0].menu().actions() if a.text()]
    # ファイルメニューはエクスポート/インポートの対称サブメニューを持つ。
    assert any("エクスポート" in t for t in file_actions), f"エクスポートが無い: {file_actions}"
    assert any("インポート" in t for t in file_actions), f"インポートが無い: {file_actions}"
    # 形式名はサブメニュー名ではなく項目側（エクスポートは CSV と Markdown が並ぶ）。
    # QAction の一時ラッパを掴んだままにする（内包表記の中で捨てると、
    # 取り出した QMenu の C++ 側が先に解放されて RuntimeError になる）。
    file_items = list(menus[0].menu().actions())
    export_menu = next(a.menu() for a in file_items if a.text() == "エクスポート")
    export_items = [a.text() for a in export_menu.actions() if a.text()]
    assert any("Markdown" in t for t in export_items), (
        f"Markdown エクスポートが無い: {export_items}"
    )
    assert all("CSV" in t or "Markdown" in t for t in export_items), (
        f"形式名の無いエクスポート項目がある: {export_items}"
    )
    # データフォルダは「変更（フォルダ選択）」と「開く（エクスプローラー）」の2本立て。
    # ここが消えると、既定以外のフォルダを使っている利用者は設定へ到達できなくなる。
    assert any("データフォルダの変更" in t for t in file_actions), file_actions
    assert any("データフォルダを開く" in t for t in file_actions), file_actions
    # 現在のデータフォルダは io_csv 経由で参照する（値を import して固定しない）。
    assert gui.io_csv.DATA_DIR.exists(), f"データフォルダが無い: {gui.io_csv.DATA_DIR}"
    # ショートカット作成（タスクバーのアイコンを出す唯一の手段）はファイルメニューに置く
    assert any("ショートカット" in t for t in file_actions), (
        f"ショートカット作成が無い: {file_actions}"
    )
    assert w._actions["create_shortcut"].isEnabled() is (sys.platform == "win32"), (
        "ショートカット作成の可否がプラットフォームと合っていない"
    )
    # 表示メニュー: テーマ（ダーク / ライト）はここが唯一の切替導線
    view_actions = [a.text() for a in menus[2].menu().actions() if a.text()]
    assert any("テーマ" in t for t in view_actions), f"表示にテーマが無い: {view_actions}"
    # 投信の口数単位（万口 / 1口）もここが唯一の切替導線
    assert any("口数単位" in t for t in view_actions), (
        f"表示に投信の口数単位が無い: {view_actions}"
    )
    help_actions = [a.text() for a in menus[3].menu().actions() if a.text()]
    assert any("ショートカット" in t for t in help_actions), help_actions

    # QSS 適用（テーマ）
    assert "QMenuBar" in w.styleSheet() and "#kpiCard" in w.styleSheet()
    assert "QToolBar" in w.styleSheet(), "ツールバーの配色が未定義（Fusion 既定で浮く）"

    # treemap ホバー状態の初期値
    assert w._treemap._hover_tile is None

    _check_app_icon(w)

    _check_actions(w)
    _check_toolbar(w)
    _check_kpi_cards(w)
    _check_inactive_toggle(w)
    _check_monthly_tab(w)
    _check_withdrawal_trend_note(w)
    _check_net_trend_note(w)
    _check_theme_toggle(w)
    _check_fund_share_unit(w)
    _check_yield_definitions(w)
    _check_asset_scope(w)
    _check_trend_scope(w)
    _check_split_flags(w)


def _run_dialog_checks() -> None:
    # 編集ダイアログ: 即時バリデーション（赤枠プロパティ＋集約エラーラベル）
    dlg = EditHoldingDialog(_holding())
    dlg._shares.setText("-5")
    assert dlg._shares.property("error") is True, "不正値で error プロパティが立たない"
    assert not dlg._error_label.isHidden(), "集約エラーラベルが表示されない"
    assert dlg._error_label.text(), "エラーメッセージが空"
    dlg._shares.setText("1,000")  # カンマ入力は正当
    assert dlg._shares.property("error") is False, "正当値で error が解除されない"
    assert dlg._error_label.isHidden(), "エラー解消後もラベルが残る"

    # 新規ダイアログ: コンボボックス化
    nd = NewHoldingDialog(_holding(symbol="", name=""))
    assert nd._asset_type.currentData() == "stock"
    assert {nd._asset_type.itemData(i) for i in range(nd._asset_type.count())} == {"stock", "fund"}
    assert {nd._currency.itemData(i) for i in range(nd._currency.count())} == {"JPY", "USD"}

    _check_account_combo()
    _check_auto_fields_are_untouched()
    _check_month_checkboxes()
    _check_benefit_detail()


if __name__ == "__main__":
    main()
