"""ヘッドレススモーク確認スクリプト（CI/手動検証用、pytest 対象外）。

実ウィンドウを show() せずに構築し、メニュー・タブ・ダイアログの
即時バリデーション・treemap ホバー状態の配線を検証する。

実行: py -3 tests/headless_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.dialogs import (  # noqa: E402
    ACCOUNT_CHOICES,
    EditHoldingDialog,
    MonthCheckboxGroup,
    NewHoldingDialog,
)
from src.gui import build_window  # noqa: E402
from src.models import Holding, MarketData  # noqa: E402


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
                "toggle_mode", "toggle_withdrawal", "toggle_inactive", "quit"):
        assert key in actions, f"アクション {key} が無い: {sorted(actions)}"

    # 一文字ショートカットが残っていないこと（表で銘柄コードを打つと誤爆する）
    for key, action in actions.items():
        for sequence in action.shortcuts():
            text = sequence.toString()
            assert len(text) > 1, f"{key} に一文字キーが残っている: {text!r}"
    assert actions["update_market"].shortcut().toString() == "F5"
    assert actions["edit"].shortcut().toString() == "Ctrl+E"
    assert actions["tab4"].shortcut().toString() == "Ctrl+5", "配当詳細タブのキーが無い"

    original_tab = w._tabs.currentIndex()

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
    w._busy = False
    w._sync_action_states()
    assert actions["update_market"].isEnabled(), "取得完了後に株価更新が戻らない"

    w._tabs.setCurrentIndex(original_tab)  # 設定（last_tab）を元へ戻す


def _check_toolbar(w) -> None:
    """ツールバーのグループ分け・折返し耐性・モード表示の追随を検証する。"""
    separators = [a for a in w._toolbar.actions() if a.isSeparator()]
    assert len(separators) >= 1, f"ツールバーの区切りが無い: {len(separators)}"
    delete_button = w._toolbar.widgetForAction(w._actions["delete"])
    assert delete_button is not None and delete_button.objectName() == "dangerAction"
    primary_button = w._toolbar.widgetForAction(w._actions["update_market"])
    assert primary_button is not None and primary_button.objectName() == "primaryAction"

    # 最小幅でも絞り込みが拡張メニューへ飛ばず画面に残ること
    # （1段に詰め込むと狭い幅であふれ、右端の絞り込みが消える回帰があった）。
    # isVisible() の判定には実際のレイアウトが要るのでここだけ show() する
    # （QT_QPA_PLATFORM=offscreen なら画面には出ない）。最後に hide() で戻す。
    original = w.size()
    w.show()
    for width in (w.minimumWidth(), 1100, 1536):
        w.resize(width, max(620, w.minimumHeight()))
        QApplication.processEvents()
        assert w._filter.isVisible(), f"幅 {width}px で絞り込みが消えている"
        assert w._toolbar.widgetForAction(w._actions["new"]).isVisible(), (
            f"幅 {width}px で新規登録ボタンが消えている"
        )
    w.resize(original)
    w.hide()
    QApplication.processEvents()

    # ドロップダウンが現在のモードを示していること
    assert w._mode_combo.currentData() == w.dividend_mode
    assert w._withdrawal_combo.currentData() == (w.withdrawal_mode or "none")
    # ショートカット経由で切り替えてもドロップダウンが追随すること
    before = w.dividend_mode
    w.action_toggle_mode()
    assert w._mode_combo.currentData() == w.dividend_mode != before
    w.action_toggle_mode()  # 元へ戻す（設定を汚さない）
    assert w.dividend_mode == before


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

    w.resize(original)
    w.hide()
    QApplication.processEvents()


def _check_inactive_toggle(w) -> None:
    """無効銘柄トグルが表示行数とチェック状態へ反映されること。"""
    before_state = w.show_inactive
    before_rows = w._price_proxy.rowCount()
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


def _check_tax_rate_is_untouched() -> None:
    """源泉税率はダイアログに欄が無く、保存しても既存値を書き換えないこと。

    率は calc.resolve_tax_rate が口座・通貨から自動判定する。CSV を手編集して
    例外の率を入れた銘柄が「開いて保存しただけ」で自動判定へ戻ると、エラーも
    出ないまま税引後の額だけが変わるため、代入しないことを固定する。
    """
    dlg = EditHoldingDialog(_holding(tax_rate=0.15))
    assert not hasattr(dlg, "_tax_rate"), "源泉税率の入力欄が残っている"
    dlg._on_save()
    assert dlg.result_holding is not None
    assert dlg.result_holding.tax_rate == 0.15, "保存で手編集の源泉税率が消えた"

    # 新規登録は None のまま（＝自動判定）で追加される
    nd = NewHoldingDialog(_holding(symbol="8306", name="三菱UFJ"))
    assert not hasattr(nd, "_tax_rate"), "新規ダイアログに源泉税率の欄が残っている"
    nd._on_save()
    assert nd.result_holding is not None
    assert nd.result_holding.tax_rate is None


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
    assert "月の配当内訳" in w._monthly_head.text(), w._monthly_head.text()
    assert "推定" in w._monthly_note.text(), "推定である旨の注記が無い"

    original_month = w.monthly_month
    w._monthly_bar.month_clicked.emit(3)
    assert w.monthly_month == 3, "バークリックで対象月が切り替わらない"
    assert w._monthly_head.text().startswith("3月の配当内訳"), w._monthly_head.text()

    # 並び替えは合計列（最終列）を降順/昇順に切り替える
    original_sort = w.monthly_sort
    w._on_monthly_sort("asc")
    header = w._monthly_view.horizontalHeader()
    assert header.sortIndicatorSection() == 5, header.sortIndicatorSection()
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    w._on_monthly_sort("desc")
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    w._on_monthly_sort(original_sort)  # 設定（monthly_sort）を元へ戻す
    w._on_monthly_month(original_month)


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


def main() -> None:
    w = build_window()

    # タブ・メニュー構成
    assert w._tabs.count() == 5, "タブが5つ生成されていない"
    # 並びはグラフ系3枚（評価額 / 配当金 / 月別配当）→ 明細2枚（株価詳細 / 配当詳細）。
    # _active_tab_key が index 3/4 だけを表タブとして拾うので順序ごと固定する。
    assert [w._tabs.tabText(i) for i in range(5)] == [
        "評価額", "配当金", "月別配当", "株価詳細", "配当詳細"
    ], [w._tabs.tabText(i) for i in range(5)]
    menus = [a for a in w.menuBar().actions() if a.menu() is not None]
    assert len(menus) == 4, f"メニューが4つでない: {len(menus)}"
    file_actions = [a.text() for a in menus[0].menu().actions() if a.text()]
    # ファイルメニューはエクスポート/インポートの対称サブメニューを持つ。
    assert any("エクスポート" in t for t in file_actions), f"エクスポートが無い: {file_actions}"
    assert any("インポート" in t for t in file_actions), f"インポートが無い: {file_actions}"
    help_actions = [a.text() for a in menus[3].menu().actions() if a.text()]
    assert any("ショートカット" in t for t in help_actions), help_actions

    # QSS 適用（テーマ）
    assert "QMenuBar" in w.styleSheet() and "#kpiCard" in w.styleSheet()
    assert "QToolBar" in w.styleSheet(), "ツールバーの配色が未定義（Fusion 既定で浮く）"

    # treemap ホバー状態の初期値
    assert w._treemap._hover_tile is None

    _check_actions(w)
    _check_toolbar(w)
    _check_kpi_cards(w)
    _check_inactive_toggle(w)
    _check_monthly_tab(w)

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
    _check_tax_rate_is_untouched()
    _check_month_checkboxes()

    print("headless smoke: OK")


if __name__ == "__main__":
    main()
