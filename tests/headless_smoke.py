"""ヘッドレススモーク確認スクリプト（CI/手動検証用、pytest 対象外）。

実ウィンドウを show() せずに構築し、メニュー・タブ・ダイアログの
即時バリデーション・treemap ホバー状態の配線を検証する。

実行: py -3 tests/headless_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dialogs import EditHoldingDialog, NewHoldingDialog  # noqa: E402
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

    # treemap ホバー状態の初期値
    assert w._treemap._hover_tile is None

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

    print("headless smoke: OK")


if __name__ == "__main__":
    main()
