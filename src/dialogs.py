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
from dataclasses import replace
from datetime import date
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .calc import resolve_payment_month_ratios
from .models import Holding, MarketData


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

# 口座の選択肢（CSV 上の値）。空文字＝未設定。表記ゆれ（"NISA" と "ＮＩＳＡ"、
# 全角/半角括弧など）が混ざると同じ口座が別物として集計・絞り込みされるため、
# 自由入力をやめて選択式にする（資産種別・通貨と同じ方針）。
ACCOUNT_CHOICES = ["", "特定", "一般", "NISA（成長）", "NISA（積立）", "旧NISA"]
_ACCOUNT_EMPTY_LABEL = "（未設定）"


def _build_account_combo(current: str | None) -> QComboBox:
    """口座の選択コンボを生成する。

    CSV を手で編集するなどして一覧に無い値が入っていた場合は、その値を選択肢へ
    追加して選択状態にする（編集ダイアログを開いて保存しただけで、既存の口座が
    別の値に化けたり消えたりするのを防ぐ）。
    """
    combo = QComboBox()
    for value in ACCOUNT_CHOICES:
        combo.addItem(value or _ACCOUNT_EMPTY_LABEL, value)
    text = (current or "").strip()
    index = combo.findData(text)
    if index < 0:
        combo.addItem(text, text)
        index = combo.count() - 1
    combo.setCurrentIndex(index)
    return combo


def _build_section(title: str, rows: list[tuple[str, QWidget]]) -> QGroupBox:
    """（ラベル, ウィジェット）の行リストから QGroupBox セクションを生成する。"""
    box = QGroupBox(title)
    form = QFormLayout(box)
    for label, widget in rows:
        form.addRow(label, widget)
    return box


class MonthCheckboxGroup(QWidget):
    """1〜12月のチェックボックス群（3行×4列）。

    auto_months に自動判定された月を渡すと先頭へ「自動判定を使う」チェックが付き、
    ON の間は12個を自動判定値でチェックしたまま無効化して見せる（読み取り専用の
    プレビュー）。この状態で months() は [] を返す＝「未設定＝自動に従う」を保存する。

    このトグルが無いと、ダイアログを開いて保存しただけでその時点の自動判定値が
    手動値として凍結され、以降 F5 で配当実績が変わっても月が更新されなくなる。
    源泉税率欄が「空欄のままにするのが既定運用で、プレースホルダが自動判定値を
    表示する」としているのと同じ意味論を、チェックボックスで表現したもの。

    auto_months が空（＝自動判定できない）ときはトグルを出さない。ON なのに12個
    すべて未チェックのグレーアウト、という無言で無意味な状態を作らないため。
    """

    _AUTO_LABEL = "自動判定を使う（配当実績から推定）"

    def __init__(
        self,
        months: list[int] | None = None,
        auto_months: list[int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._auto_months = sorted(auto_months or [])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._auto: QCheckBox | None = None
        if self._auto_months:
            self._auto = QCheckBox(self._AUTO_LABEL)
            # 月が未設定なら自動に従う（＝既定運用）。
            self._auto.setChecked(not months)
            self._auto.toggled.connect(self._on_auto_toggled)
            outer.addWidget(self._auto)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self._boxes: list[QCheckBox] = []
        for i in range(12):
            box = QCheckBox(f"{i + 1}月")
            grid.addWidget(box, i // 4, i % 4)
            self._boxes.append(box)
        outer.addWidget(grid_host)

        self.set_months(months or [])
        if self._auto is not None:
            self._on_auto_toggled(self._auto.isChecked())

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            # 自動判定値をプレビュー表示する（保存はしない）。
            self._apply(self._auto_months)
        for box in self._boxes:
            box.setEnabled(not checked)

    def _apply(self, months: list[int]) -> None:
        selected = set(months)
        for i, box in enumerate(self._boxes):
            box.setChecked(i + 1 in selected)

    def is_auto(self) -> bool:
        return self._auto is not None and self._auto.isChecked()

    def months(self) -> list[int]:
        """保存する月リスト。自動判定モード中は [] を返す。

        毎回新しいリストを返す。Holding は dataclasses.replace（浅いコピー）で
        複製されるため、元インスタンスと共有したリストを返すと編集をキャンセル
        しても元データが変わってしまう。
        """
        if self.is_auto():
            return []
        return [i + 1 for i, box in enumerate(self._boxes) if box.isChecked()]

    def set_months(self, months: list[int]) -> None:
        if self._auto is not None:
            self._auto.setChecked(not months)
        self._apply(sorted(months or []))


def auto_dividend_months(
    holding: Holding, market: MarketData | None
) -> list[int]:
    """銘柄の手動設定を無視して、自動判定される支払月を求める。

    手動設定済みの銘柄でも「自動ならこうなる」を見せたいので、月を空にした複製で
    解決する。率と同じく判定ロジックは calc に一本化し、表示層へ書き写さない。
    """
    probe = replace(holding, dividend_months=[])
    return sorted(resolve_payment_month_ratios(probe, market) or {})


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
    def __init__(
        self, holding: Holding, parent=None, *, market: MarketData | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("銘柄編集")
        self.holding = holding
        self.result_holding: Optional[Holding] = None

        # 基本情報
        self._name = QLineEdit(holding.name)
        self._account = _build_account_combo(holding.account)
        self._shares = QLineEdit(str(holding.shares))
        self._avg_cost = QLineEdit(str(holding.avg_cost))
        self._active = QCheckBox("有効")
        self._active.setChecked(holding.active)

        # 配当の上書き（税引後は gross × (1 − 税率) で自動計算するため欄を持たない。
        # 一部だけ非課税の分配金など率で表せない例外は holdings.csv の
        # annual_div_ps_net_override 列を手編集する運用）。
        self._div_gross = QLineEdit(
            "" if holding.annual_div_ps_gross_override is None
            else str(holding.annual_div_ps_gross_override)
        )
        self._div_gross.setPlaceholderText("空欄=自動取得値を使用")
        self._div_date = QLineEdit(holding.div_forecast_as_of_override or "")
        self._div_date.setPlaceholderText("YYYY-MM-DD")

        # 支払月・受取月（月別配当タブの按分に使う）。金額と同じセクションへ置く
        # ため、ウィジェットの生成順とレイアウトの並びは一致しない。
        auto_months = auto_dividend_months(holding, market)
        self._div_months = MonthCheckboxGroup(holding.dividend_months, auto_months)
        # 優待には自動判定の材料が無いため常に手動（auto_months を渡さない）。
        self._benefit_months = MonthCheckboxGroup(holding.benefit_months)
        self._months_note = QLabel(
            "配当の支払月は権利落ち月からの推定です"
            "（JPY 建ては +3ヶ月 / USD 建ては同月）。"
            if auto_months
            else "配当実績が無いため支払月を自動判定できません。手動で指定できます。"
        )
        self._months_note.setObjectName("dialogNote")
        self._months_note.setWordWrap(True)

        # 優待・取崩（取崩は配当セクションへ、優待は別セクションへ置くので
        # ウィジェットの生成順とレイアウトの並びは一致しない）。
        # 源泉税率は calc.resolve_tax_rate が口座と通貨から自動判定するため入力欄を
        # 置かない。銘柄ごとの例外（特定口座の源泉徴収なし等）は holdings.csv の
        # tax_rate 列を直接編集する運用で、ここで書き換えず既存値を素通しする。
        self._benefit = QLineEdit(str(holding.annual_benefit_jpy))
        # 優待内容は自由記述のメモなので検証を付けない（_attach_validator も
        # _parse_num も通さない）。1行入力にして CSV へ改行が入る余地を作らない。
        self._benefit_detail = QLineEdit(holding.benefit_detail)
        self._benefit_detail.setPlaceholderText("例: クオカード1,000円分")
        self._withdrawal_fixed = QLineEdit(
            "" if holding.withdrawal_fixed_jpy is None
            else str(holding.withdrawal_fixed_jpy)
        )
        self._withdrawal_rate = QLineEdit(
            "" if holding.withdrawal_rate is None
            else str(holding.withdrawal_rate)
        )
        # 取崩は配当セクションの中に置くので、周りの欄（配当上書き・支払月）が
        # 株式にも効くのに対してここだけ投信専用であることを注記で示す。
        self._withdrawal_note = QLabel(
            "取崩は投資信託(fund)のみに適用されます（株式・ETF では無視されます）。"
        )
        self._withdrawal_note.setObjectName("dialogNote")
        self._withdrawal_note.setWordWrap(True)

        # 即時バリデーション登録
        self._attach_validator(self._shares, lambda t: validate_required_number(t, "保有株数"))
        self._attach_validator(self._avg_cost, lambda t: validate_required_number(t, "取得単価"))
        self._attach_validator(self._div_gross, lambda t: validate_optional_number(t, "配当(税引前)"))
        self._attach_validator(self._div_date, validate_optional_date)
        self._attach_validator(self._benefit, lambda t: validate_required_number(t, "優待換算額"))
        self._attach_validator(self._withdrawal_fixed, lambda t: validate_optional_number(t, "定額取崩額"))
        self._attach_validator(self._withdrawal_rate, lambda t: validate_optional_rate(t, "定率取崩率"))

        buttons = QDialogButtonBox()
        buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(_build_section("基本情報", [
            ("銘柄名", self._name),
            ("口座", self._account),
            ("保有株数", self._shares),
            ("取得単価", self._avg_cost),
            ("", self._active),
        ]))
        # セクションは収入の種類ごとに切る（配当（取崩を含む）/ 優待）。月だけを
        # 別枠へ集めると、性質の違う配当と優待が同じ枠に並び、金額はさらに別の枠に
        # 載る——1銘柄の優待を設定するのに3枠を行き来することになる。
        # 取崩は投信の分配に代わる現金収入なので配当と同じ枠に置く（枠を分けると
        # 「この銘柄からいくら受け取るか」が2枠に割れる）。
        dividend_section = _build_section("配当", [
            ("配当(税引前) 上書き", self._div_gross),
            ("配当予想更新日", self._div_date),
            ("支払月", self._div_months),
        ])
        dividend_form = dividend_section.layout()
        dividend_form.addRow(self._months_note)
        dividend_form.addRow("定額取崩額(円/年)", self._withdrawal_fixed)
        dividend_form.addRow("定率取崩率(例: 0.04)", self._withdrawal_rate)
        dividend_form.addRow(self._withdrawal_note)
        layout.addWidget(dividend_section)
        layout.addWidget(_build_section("優待", [
            ("優待換算額(円/年)", self._benefit),
            ("優待内容", self._benefit_detail),
            ("受取月", self._benefit_months),
        ]))
        layout.addWidget(self._error_label)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        try:
            name = self._name.text().strip()
            shares = _non_negative(_parse_num(self._shares.text()), "保有株数")
            avg_cost = _non_negative(_parse_num(self._avg_cost.text()), "取得単価")
            div_gross_text = self._div_gross.text().strip()
            div_date = _optional_iso_date(self._div_date.text().strip() or None)
            benefit = _non_negative(_parse_num(self._benefit.text()), "優待換算額")
            withdrawal_fixed_text = self._withdrawal_fixed.text().strip()
            withdrawal_rate_text = self._withdrawal_rate.text().strip()
            div_gross = (
                _non_negative(_parse_num(div_gross_text), "配当(税引前)")
                if div_gross_text else None
            )
            withdrawal_fixed = (
                _non_negative(_parse_num(withdrawal_fixed_text), "定額取崩額")
                if withdrawal_fixed_text else None
            )
            withdrawal_rate = _optional_rate(
                _parse_num(withdrawal_rate_text) if withdrawal_rate_text else None,
                "定率取崩率",
            )
            active = self._active.isChecked()
        except ValueError as exc:
            QMessageBox.warning(self, "入力エラー", str(exc))
            return
        self.holding.name = name
        self.holding.account = str(self._account.currentData() or "")
        self.holding.shares = shares
        self.holding.avg_cost = avg_cost
        self.holding.annual_div_ps_gross_override = div_gross
        self.holding.div_forecast_as_of_override = div_date
        self.holding.annual_benefit_jpy = benefit
        self.holding.benefit_detail = self._benefit_detail.text().strip()
        self.holding.withdrawal_fixed_jpy = withdrawal_fixed
        self.holding.withdrawal_rate = withdrawal_rate
        # annual_div_ps_net_override / tax_rate は入力欄が無いので触らない。代入すると
        # CSV を手編集した銘柄が「開いて保存しただけ」で自動計算へ戻ってしまう
        # （エラーも出ないまま税引後の額だけが変わる）。
        self.holding.active = bool(active)
        # months() は毎回新しいリストを返す。replace() の浅いコピーで元インスタンスと
        # 共有されたリストを in-place で書き換えないこと。
        self.holding.dividend_months = self._div_months.months()
        self.holding.benefit_months = self._benefit_months.months()
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
        self._account = _build_account_combo(holding.account)
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

        # 優待・取崩（レイアウトでは別セクション。源泉税率は口座と通貨から
        # 自動判定するため入力欄を持たない）
        self._benefit = QLineEdit("")
        self._benefit.setPlaceholderText("0")
        # 編集ダイアログと同じく自由記述（検証なし）。新規側にも置かないと、
        # 登録直後の銘柄は開き直すまで優待内容を書けない。
        self._benefit_detail = QLineEdit(holding.benefit_detail)
        self._benefit_detail.setPlaceholderText("例: クオカード1,000円分")
        self._withdrawal_fixed = QLineEdit("")
        self._withdrawal_rate = QLineEdit("")

        # 即時バリデーション登録
        self._attach_validator(self._shares, lambda t: validate_required_number(t, "保有株数"))
        self._attach_validator(self._avg_cost, lambda t: validate_required_number(t, "取得単価"))
        self._attach_validator(self._benefit, lambda t: validate_required_number(t, "優待換算額"))
        self._attach_validator(self._withdrawal_fixed, lambda t: validate_optional_number(t, "定額取崩額"))
        self._attach_validator(self._withdrawal_rate, lambda t: validate_optional_rate(t, "定率取崩率"))

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
            ("口座", self._account),
            ("資産種別", self._asset_type),
            ("通貨", self._currency),
            ("保有株数", self._shares),
            ("取得単価", self._avg_cost),
            ("", self._active),
        ]))
        # 編集ダイアログは取崩を配当セクションに含めるが、新規は配当欄そのものを
        # 持たないため（入れる枠が無い）取崩は独立セクションのまま残す。
        # 支払月・受取月は新規では扱わない（登録後に編集ダイアログで指定する）。
        layout.addWidget(_build_section("優待", [
            ("優待換算額(円/年)", self._benefit),
            ("優待内容", self._benefit_detail),
        ]))
        layout.addWidget(_build_section("取崩（投資信託のみ）", [
            ("定額取崩額(円/年)", self._withdrawal_fixed),
            ("定率取崩率(例: 0.04)", self._withdrawal_rate),
        ]))
        layout.addWidget(self._error_label)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        symbol = self._symbol.text().strip()
        name = self._name.text().strip()
        account = str(self._account.currentData() or "")
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
        self.holding.benefit_detail = self._benefit_detail.text().strip()
        self.holding.withdrawal_fixed_jpy = withdrawal_fixed
        self.holding.withdrawal_rate = withdrawal_rate
        # tax_rate は編集ダイアログと同じく触らない（新規は None のまま＝自動判定）。
        self.holding.active = bool(active)
        self.result_holding = self.holding
        self.accept()
