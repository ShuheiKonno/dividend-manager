"""ヘッドレススモーク確認スクリプト（CI/手動検証用、pytest 対象外）。

実ウィンドウを show() せずに構築し、メニュー・タブ・ダイアログの
即時バリデーション・treemap ホバー状態の配線を検証する。

実行: py -3 tests/headless_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.dialogs import (  # noqa: E402
    ACCOUNT_CHOICES,
    EditHoldingDialog,
    NewHoldingDialog,
)
from src.gui import build_window  # noqa: E402
from src.models import Holding  # noqa: E402


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

    original_tab = w._tabs.currentIndex()

    # グラフタブ: 編集・削除・絞り込みは対象が無いので無効（無反応ボタンを作らない）
    w._tabs.setCurrentIndex(0)
    assert not actions["edit"].isEnabled(), "グラフタブで編集が有効のまま"
    assert not actions["delete"].isEnabled(), "グラフタブで削除が有効のまま"
    assert not w._filter.isEnabled(), "グラフタブで絞り込みが有効のまま"

    # 表タブ: 行を選ぶと編集・削除が有効になる
    w._tabs.setCurrentIndex(2)
    assert w._filter.isEnabled(), "表タブで絞り込みが無効のまま"
    if w._price_proxy.rowCount() > 0:
        w._price_view.selectRow(0)
        assert actions["edit"].isEnabled(), "行を選んでも編集が有効にならない"
        assert actions["delete"].isEnabled(), "行を選んでも削除が有効にならない"

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


def main() -> None:
    w = build_window()

    # タブ・メニュー構成
    assert w._tabs.count() == 4, "タブが4つ生成されていない"
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
    _check_inactive_toggle(w)

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

    print("headless smoke: OK")


if __name__ == "__main__":
    main()
