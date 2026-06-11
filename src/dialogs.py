"""銘柄の編集・新規登録ダイアログと入力検証ヘルパー。

- 検証ヘルパー（_parse_num / _non_negative / _optional_rate / _optional_iso_date）の
  実体はこのモジュールに置き、gui.py が再エクスポートする
  （tests/test_gui_helpers.py は gui._parse_num 等を参照するため互換を維持）。
- 即時バリデーションは validate_*（「テキスト → エラーメッセージ or None」の
  Qt 非依存ロジック）と _HoldingDialogBase（QSS の [error="true"] 赤枠＋
  ツールチップ＋集約エラーラベル）で行う。
- 保存時の一括検証（QMessageBox）は従来どおりセーフティネットとして温存する。
- _parse_num のカンマ・全角・円記号対応と「空欄=未設定」の意味論を保つため、
  数値入力は QLineEdit のまま（QDoubleSpinBox には置き換えない）。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .models import Holding


# ---- 数値・日付の検証ヘルパー（例外ベース、保存時の一括検証で使用） ----

def _parse_num(text: str) -> float:
    """カンマ・全角数字・円記号を除去して float に変換。空文字は 0.0 を返す。"""
    cleaned = (
        text.replace(",", "")
        .replace("，", "")
        .replace("¥", "")
        .replace("円", "")
        .strip()
    )
    if not cleaned:
        return 0.0
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError("数値の入力が正しくありません。") from exc
    if not math.isfinite(value):
        raise ValueError("nan / inf は入力できません。")
    return value


def _non_negative(value: float, label: str) -> float:
    if value < 0:
        raise ValueError(f"{label}は 0 以上で入力してください。")
    return value


def _optional_rate(value: float | None, label: str) -> float | None:
    if value is not None and not 0 <= value <= 1:
        raise ValueError(f"{label}は 0 以上 1 以下で入力してください。")
    return value


def _optional_iso_date(value: str | None) -> str | None:
    if value is not None:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("配当予想更新日は YYYY-MM-DD 形式で入力してください。") from exc
    return value


# ---- 即時バリデーション用の検証関数（テキスト → エラーメッセージ or None） ----

def validate_required_number(text: str, label: str) -> str | None:
    """非負数値入力を検証する（空欄は 0 扱い＝許容）。"""
    try:
        _non_negative(_parse_num(text), label)
    except ValueError as exc:
        return str(exc)
    return None


def validate_optional_number(text: str, label: str) -> str | None:
    """空欄=未設定を許す非負数値入力を検証する。"""
    if not text.strip():
        return None
    return validate_required_number(text, label)


def validate_optional_rate(text: str, label: str) -> str | None:
    """空欄=未設定を許す 0〜1 の率入力を検証する。"""
    if not text.strip():
        return None
    try:
        _optional_rate(_parse_num(text), label)
    except ValueError as exc:
        return str(exc)
    return None


def validate_optional_date(text: str) -> str | None:
    """空欄=未設定を許す ISO 日付（YYYY-MM-DD）入力を検証する。"""
    if not text.strip():
        return None
    try:
        _optional_iso_date(text.strip())
    except ValueError as exc:
        return str(exc)
    return None


# ---- ダイアログ部品 ----

def _build_section(title: str, rows: list[tuple[str, QWidget]]) -> QGroupBox:
    """（ラベル, ウィジェット）の行リストから QGroupBox セクションを生成する。"""
    box = QGroupBox(title)
    form = QFormLayout(box)
    for label, widget in rows:
        form.addRow(label, widget)
    return box


class _HoldingDialogBase(QDialog):
    """編集/新規ダイアログ共通の即時バリデーション基盤。

    _attach_validator で登録したフィールドは textChanged のたびに検証され、
    エラー時は QSS の QLineEdit[error="true"]（赤枠）とツールチップ、
    フォーム下部の集約エラーラベル（先頭エラーのみ）へ反映される。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._validators: dict[QLineEdit, Callable[[str], str | None]] = {}
        self._error_label = QLabel("")
        self._error_label.setObjectName("dialogError")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)

    def _attach_validator(
        self, edit: QLineEdit, validate: Callable[[str], str | None]
    ) -> None:
        self._validators[edit] = validate
        edit.textChanged.connect(lambda _text, e=edit: self._revalidate(e))

    def _revalidate(self, edit: QLineEdit) -> None:
        message = self._validators[edit](edit.text())
        edit.setProperty("error", bool(message))
        edit.setToolTip(message or "")
        # 動的プロパティの変更を QSS の属性セレクタへ反映させる。
        edit.style().unpolish(edit)
        edit.style().polish(edit)
        self._update_error_label()

    def _update_error_label(self) -> None:
        for edit, validate in self._validators.items():
            message = validate(edit.text())
            if message:
                self._error_label.setText(message)
                self._error_label.setVisible(True)
                return
        self._error_label.setText("")
        self._error_label.setVisible(False)


# ---- 編集 / 新規ダイアログ ----

class EditHoldingDialog(_HoldingDialogBase):
    def __init__(self, holding: Holding, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("銘柄編集")
        self.holding = holding
        self.result_holding: Optional[Holding] = None

        # 基本情報
        self._name = QLineEdit(holding.name)
        self._account = QLineEdit(holding.account)
        self._shares = QLineEdit(str(holding.shares))
        self._avg_cost = QLineEdit(str(holding.avg_cost))
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        # 配当の上書き
        self._div_gross = QLineEdit(
            "" if holding.annual_div_ps_gross_override is None
            else str(holding.annual_div_ps_gross_override)
        )
        self._div_gross.setPlaceholderText("空欄=自動取得値を使用")
        self._div_net = QLineEdit(
            "" if holding.annual_div_ps_net_override is None
            else str(holding.annual_div_ps_net_override)
        )
        self._div_net.setPlaceholderText("空欄=税引前×(1−税率)で自動計算")
        self._div_date = QLineEdit(holding.div_forecast_as_of_override or "")
        self._div_date.setPlaceholderText("YYYY-MM-DD")

        # 優待・取崩・税率
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

        # 即時バリデーション登録
        self._attach_validator(self._shares, lambda t: validate_required_number(t, "保有株数"))
        self._attach_validator(self._avg_cost, lambda t: validate_required_number(t, "取得単価"))
        self._attach_validator(self._div_gross, lambda t: validate_optional_number(t, "配当(税引前)"))
        self._attach_validator(self._div_net, lambda t: validate_optional_number(t, "配当(税引後)"))
        self._attach_validator(self._div_date, validate_optional_date)
        self._attach_validator(self._benefit, lambda t: validate_required_number(t, "優待換算額"))
        self._attach_validator(self._withdrawal_fixed, lambda t: validate_optional_number(t, "定額取崩額"))
        self._attach_validator(self._withdrawal_rate, lambda t: validate_optional_rate(t, "定率取崩率"))
        self._attach_validator(self._tax_rate, lambda t: validate_optional_rate(t, "源泉税率"))

        buttons = QDialogButtonBox()
        buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(_build_section("基本情報", [
            ("銘柄名", self._name),
            ("口座（NISA等）", self._account),
            ("保有株数", self._shares),
            ("取得単価", self._avg_cost),
            ("", self._active),
        ]))
        layout.addWidget(_build_section("配当の上書き", [
            ("配当(税引前) 上書き", self._div_gross),
            ("配当(税引後) 上書き", self._div_net),
            ("配当予想更新日", self._div_date),
        ]))
        layout.addWidget(_build_section("優待・取崩・税率", [
            ("優待換算額(円/年)", self._benefit),
            ("定額取崩額(円/年)", self._withdrawal_fixed),
            ("定率取崩率(例: 0.04)", self._withdrawal_rate),
            ("源泉税率(例: 0 でNISA)", self._tax_rate),
        ]))
        layout.addWidget(self._error_label)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        try:
            name = self._name.text().strip()
            shares = _non_negative(_parse_num(self._shares.text()), "保有株数")
            avg_cost = _non_negative(_parse_num(self._avg_cost.text()), "取得単価")
            div_gross_text = self._div_gross.text().strip()
            div_net_text = self._div_net.text().strip()
            div_date = _optional_iso_date(self._div_date.text().strip() or None)
            benefit = _non_negative(_parse_num(self._benefit.text()), "優待換算額")
            withdrawal_fixed_text = self._withdrawal_fixed.text().strip()
            withdrawal_rate_text = self._withdrawal_rate.text().strip()
            div_gross = (
                _non_negative(_parse_num(div_gross_text), "配当(税引前)")
                if div_gross_text else None
            )
            div_net = (
                _non_negative(_parse_num(div_net_text), "配当(税引後)")
                if div_net_text else None
            )
            withdrawal_fixed = (
                _non_negative(_parse_num(withdrawal_fixed_text), "定額取崩額")
                if withdrawal_fixed_text else None
            )
            withdrawal_rate = _optional_rate(
                _parse_num(withdrawal_rate_text) if withdrawal_rate_text else None,
                "定率取崩率",
            )
            tax_rate_text = self._tax_rate.text().strip()
            tax_rate = _optional_rate(
                _parse_num(tax_rate_text) if tax_rate_text else None, "源泉税率"
            )
            active = self._active.isChecked()
        except ValueError as exc:
            QMessageBox.warning(self, "入力エラー", str(exc))
            return
        self.holding.name = name
        self.holding.account = self._account.text().strip()
        self.holding.shares = shares
        self.holding.avg_cost = avg_cost
        self.holding.annual_div_ps_gross_override = div_gross
        self.holding.annual_div_ps_net_override = div_net
        self.holding.div_forecast_as_of_override = div_date
        self.holding.annual_benefit_jpy = benefit
        self.holding.withdrawal_fixed_jpy = withdrawal_fixed
        self.holding.withdrawal_rate = withdrawal_rate
        self.holding.tax_rate = tax_rate
        self.holding.active = bool(active)
        self.result_holding = self.holding
        self.accept()


class NewHoldingDialog(_HoldingDialogBase):
    def __init__(self, holding: Holding, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新規登録")
        self.holding = holding
        self.result_holding: Optional[Holding] = None

        note = QLabel(
            "投信(fund)は基準価額が1万口単位。保有株数には「口数÷10,000」を入力してください。\n"
            "投信の ISIN コードは基準価額を自動取得します。"
        )
        note.setObjectName("dialogNote")
        note.setWordWrap(True)

        # 基本情報
        self._symbol = QLineEdit(holding.symbol)
        self._symbol.setPlaceholderText("JPY株は .T 不要 / 投信は ISIN 可")
        self._name = QLineEdit(holding.name)
        self._name.setPlaceholderText("空欄=株価更新時に自動取得")
        self._account = QLineEdit(holding.account)
        # 自由入力だった資産種別・通貨はコンボボックスで選択式に（誤入力防止）。
        self._asset_type = QComboBox()
        self._asset_type.addItem("株式 (stock)", "stock")
        self._asset_type.addItem("投資信託 (fund)", "fund")
        if holding.asset_type == "fund":
            self._asset_type.setCurrentIndex(1)
        self._currency = QComboBox()
        self._currency.addItem("JPY", "JPY")
        self._currency.addItem("USD", "USD")
        if holding.currency == "USD":
            self._currency.setCurrentIndex(1)
        self._shares = QLineEdit("")
        self._shares.setPlaceholderText("投信は口数÷10,000")
        self._avg_cost = QLineEdit("")
        self._avg_cost.setPlaceholderText("0")
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        # 優待・取崩・税率
        self._benefit = QLineEdit("")
        self._benefit.setPlaceholderText("0")
        self._withdrawal_fixed = QLineEdit("")
        self._withdrawal_rate = QLineEdit("")
        self._tax_rate = QLineEdit("")
        self._tax_rate.setPlaceholderText("未入力=20.315%（NISA口座は0）")

        # 即時バリデーション登録
        self._attach_validator(self._shares, lambda t: validate_required_number(t, "保有株数"))
        self._attach_validator(self._avg_cost, lambda t: validate_required_number(t, "取得単価"))
        self._attach_validator(self._benefit, lambda t: validate_required_number(t, "優待換算額"))
        self._attach_validator(self._withdrawal_fixed, lambda t: validate_optional_number(t, "定額取崩額"))
        self._attach_validator(self._withdrawal_rate, lambda t: validate_optional_rate(t, "定率取崩率"))
        self._attach_validator(self._tax_rate, lambda t: validate_optional_rate(t, "源泉税率"))

        buttons = QDialogButtonBox()
        buttons.addButton("追加", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(_build_section("基本情報", [
            ("銘柄コード/ティッカー", self._symbol),
            ("銘柄名", self._name),
            ("口座（NISA等）", self._account),
            ("資産種別", self._asset_type),
            ("通貨", self._currency),
            ("保有株数", self._shares),
            ("取得単価", self._avg_cost),
            ("", self._active),
        ]))
        layout.addWidget(_build_section("優待・取崩・税率", [
            ("優待換算額(円/年)", self._benefit),
            ("定額取崩額(円/年)", self._withdrawal_fixed),
            ("定率取崩率(例: 0.04)", self._withdrawal_rate),
            ("源泉税率(例: 0 でNISA)", self._tax_rate),
        ]))
        layout.addWidget(self._error_label)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        symbol = self._symbol.text().strip()
        name = self._name.text().strip()
        account = self._account.text().strip()
        asset_type = str(self._asset_type.currentData())
        currency = str(self._currency.currentData())
        if not symbol:
            QMessageBox.warning(self, "入力エラー", "銘柄コードを入力してください。")
            return
        # コンボボックス化により通常は到達しないが、防御として温存する。
        if asset_type not in ("stock", "fund"):
            QMessageBox.warning(self, "入力エラー", "資産種別は stock または fund を入力してください。")
            return
        if currency not in ("JPY", "USD"):
            QMessageBox.warning(self, "入力エラー", "通貨は JPY または USD を入力してください。")
            return
        try:
            shares = _non_negative(_parse_num(self._shares.text()), "保有株数")
            avg_cost = _non_negative(_parse_num(self._avg_cost.text()), "取得単価")
            benefit = _non_negative(_parse_num(self._benefit.text()), "優待換算額")
            withdrawal_fixed_text = self._withdrawal_fixed.text().strip()
            withdrawal_rate_text = self._withdrawal_rate.text().strip()
            withdrawal_fixed = (
                _non_negative(_parse_num(withdrawal_fixed_text), "定額取崩額")
                if withdrawal_fixed_text else None
            )
            withdrawal_rate = _optional_rate(
                _parse_num(withdrawal_rate_text) if withdrawal_rate_text else None,
                "定率取崩率",
            )
            tax_rate_text = self._tax_rate.text().strip()
            tax_rate = _optional_rate(
                _parse_num(tax_rate_text) if tax_rate_text else None, "源泉税率"
            )
            active = self._active.isChecked()
        except ValueError as exc:
            QMessageBox.warning(self, "入力エラー", str(exc))
            return
        self.holding.symbol = symbol
        self.holding.name = name
        self.holding.account = account
        self.holding.asset_type = asset_type
        self.holding.currency = currency
        self.holding.shares = shares
        self.holding.avg_cost = avg_cost
        self.holding.annual_benefit_jpy = benefit
        self.holding.withdrawal_fixed_jpy = withdrawal_fixed
        self.holding.withdrawal_rate = withdrawal_rate
        self.holding.tax_rate = tax_rate
        self.holding.active = bool(active)
        self.result_holding = self.holding
        self.accept()
