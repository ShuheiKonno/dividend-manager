"""保有銘柄一覧を LLM 分析用の Markdown へ整形する（Qt 非依存のロジック層）。

CSV エクスポート（io_csv.export_holdings_csv）は holdings.csv と同一の「取込で
読み戻せる」フォーマットで、入力値（株数・取得単価・上書き値）しか持たない。
対してこちらは compute_rows の計算済み行と Summary を出力する——時価・損益・
利回り・税引後配当が入っていなければ LLM はポートフォリオを分析できない。
往復（import）は目的外なので、書式は機械可読性に振る。

表示層（gui._format_cell）は QColor を返すので使えない。utils.fmt_number も
使わない（カンマ区切りと "N/A" が入る）。整形はこのモジュールが自前で持つ。
"""

from __future__ import annotations

from .calc import Summary
from .utils import safe_float

# 列定義 (ヘッダラベル, row のキー, 書式種別)。
# gui.PRICE_COLUMNS と gui.INCOME_COLUMNS の和集合を1テーブルにしたもの
# （先頭6列は両者で共通なので重複を除く）。2テーブルに分けると symbol が
# 非一意（複数口座・複数取得単価）なため LLM が join できず誤って結合する。
# 率の列にヘッダで (%) を付け、値は ×100 済みの数を入れる——カンマ無しの生数値
# 方針では 0.035 と 3.5 が区別できないため、単位は必ずヘッダ側に書く。
COLUMNS: list[tuple[str, str, str]] = [
    ("コード", "symbol", "text"),
    ("銘柄名", "name", "text"),
    ("口座", "account", "text"),
    ("種別", "asset_type", "asset_type"),
    ("株数", "shares", "num2"),
    ("通貨", "currency", "text"),
    ("購入単価", "avg_cost", "num2"),
    ("現在株価", "last_price", "num2"),
    ("簿価(円)", "book_value_jpy", "num0"),
    ("時価(円)", "market_value_jpy", "num0"),
    ("損益(円)", "profit_jpy", "num0"),
    ("損益率(%)", "profit_rate", "pct"),
    ("配当/株", "annual_div_ps", "num2"),
    ("年間配当(円)", "annual_dividend_jpy", "num0"),
    ("優待(円)", "annual_benefit_jpy", "num0"),
    ("優待内容", "benefit_detail", "text"),
    ("簿価利回り(%)", "yield_on_cost", "pct"),
    ("時価利回り(%)", "yield_on_value", "pct"),
    ("配当更新日", "div_forecast_as_of", "text"),
]
# 無効銘柄が1件でも含まれるときだけ末尾へ足す列。全行「有効」の表に
# 意味の無い1列を常設しない。
STATUS_COLUMN: tuple[str, str, str] = ("状態", "active", "active")

# 資産種別の表示名。gui.ASSET_TYPE_LABELS と同じ対応（gui は Qt 依存なので
# import できない。増やすときは両方に足すこと）。
ASSET_TYPE_LABELS = {"stock": "株式", "fund": "投信"}
# 収入モードの表示名。gui.WITHDRAWAL_LABELS より説明的にする（画面はドロップ
# ダウンの隣に出るが、この文書は単体で読まれる）。
WITHDRAWAL_LABELS = {
    None: "取崩なし（配当のみ）",
    "fixed": "定額取崩",
    "rate": "定率取崩",
}
DIVIDEND_MODE_LABELS = {"gross": "税引前（gross）", "net": "税引後（net）"}


def _cell(value: object) -> str:
    """Markdown テーブルのセルとして安全な1行文字列にする。

    銘柄名・口座・優待内容は CSV 手編集もできる自由文字列で、`|` が入ると
    テーブルがエラーも出さずに崩れる。改行も同様にセルの外へ飛び出す
    （優待内容の入力欄は1行の QLineEdit だが、それ以前の CSV には改行入りの
    値があり得る）。数値列も含め、テーブルへ書くものは例外なくここを通す
    ——列ごとに通す/通さないを分けると、後から足した列で漏れる。
    """
    text = str(value if value is not None else "")
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|").strip()


def _num(value: object, digits: int) -> str:
    """カンマ無しの固定小数で返す。値が無ければ空欄（"N/A" は出さない）。

    safe_float が inf / nan を None に潰すので、除算由来の非有限値も空欄になる。
    """
    number = safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def _pct(value: object) -> str:
    """小数の比率（0.035）を百分率の数（3.50）にする。ヘッダ側に (%) を書く前提。"""
    number = safe_float(value)
    if number is None:
        return ""
    return f"{number * 100:.2f}"


def _format(row: dict, key: str, kind: str) -> str:
    """row の1セルを書式種別に従って文字列化する（エスケープは呼び出し側）。"""
    value = row.get(key)
    if kind == "num0":
        return _num(value, 0)
    if kind == "num2":
        return _num(value, 2)
    if kind == "pct":
        return _pct(value)
    if kind == "asset_type":
        text = str(value or "")
        return ASSET_TYPE_LABELS.get(text, text)
    if kind == "active":
        return "無効" if value is False else "有効"
    return str(value or "")


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """ヘッダ行・区切り行・データ行を組んで Markdown テーブルの行列を返す。"""
    lines = [
        "| " + " | ".join(_cell(h) for h in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return lines


def _columns(withdrawal_mode: str | None, has_inactive: bool) -> list[tuple[str, str, str]]:
    """出力する列定義を返す（収入モードでのラベル入替と状態列の有無を反映）。"""
    columns = list(COLUMNS)
    if withdrawal_mode is not None:
        # 画面（gui._refresh_view）が同じ入替をしている。合わせないと、投信の
        # 取崩額が入った列に「年間配当」という誤ったラベルが付く。
        columns = [
            ("年間収入(円)", key, kind) if label == "年間配当(円)" else (label, key, kind)
            for label, key, kind in columns
        ]
    if has_inactive:
        columns.append(STATUS_COLUMN)
    return columns


def _notes(dividend_mode: str, withdrawal_mode: str | None, has_inactive: bool) -> list[str]:
    """LLM が冷えた状態で読んだとき誤読しうる前提を列挙する。

    この節がこの機能の実質的な本体。数字だけ渡すと、税率・配当の近似・投信の
    口数といったこのアプリ固有の約束事を知らないまま尤もらしい誤った分析が返る。
    """
    notes = [
        "- 空欄は「値が無い（未取得・未設定）」を意味する。0 ではない。",
        "- 金額はすべて円換算後。USD 建て銘柄は上表の USDJPY レートで換算済み。",
        "- 率の列（損益率・簿価利回り・時価利回り）は百分率の数値。"
        "`3.50` は 3.50% を指す。",
        "- 利回りは「配当＋優待」を分子にした値で、配当だけの利回りではない。"
        "サマリーの `合計(円)` も配当＋優待。",
        "- 年間配当は直近約370日の**配当実績の合計**による近似で、会社予想ではない。"
        "四半期・月次配当はこの窓で年換算される。",
        "- 投資信託の `株数` は口数 ÷ 10,000（日本の基準価額が1万口あたりのため）。"
        "他の資産と口数の桁を直接比較しないこと。",
        "- 価格を取得できない投資信託は簿価を時価の代わりに使っている。"
        "その行の損益は 0 になるが、実際に損益が無いという意味ではない。",
        "- サマリーの `評価額計` / `簿価計` / `損益` は、対象銘柄の**全件で値が揃った"
        "ときだけ**数字が出る（1件でも欠けると空欄）。一方 `年間配当` は1件でも既知なら"
        "出るため、集計の母数が異なる。",
    ]
    if dividend_mode == "net":
        notes.append(
            "- 税引後（net）モードの税率は、銘柄ごとの明示指定がなければ"
            "口座 × 通貨で自動判定する: "
            "NISA×日本株 = 0%、NISA×米国株 = 10%、課税口座×日本株 = 20.315%、"
            "課税口座×米国株 ≒ 28.28%。"
        )
        notes.append(
            "  - NISA でも米国株が 0% でないのは、日米租税条約による米国源泉 10% を"
            "NISA では外国税額控除に充てられず、引かれたまま残るため。"
        )
        notes.append(
            "  - 課税口座×米国株が単純加算の 30.315% でないのは、米国 10% を引いた"
            "残額に国内 20.315% が課される二段課税のため（1-(1-0.10)(1-0.20315)）。"
            "確定申告の外国税額控除による還付は他の所得に依存して一意に決まらないので、"
            "「取り戻さない場合」の額を出している。"
        )
    else:
        notes.append(
            "- 税引前（gross）モード。配当は源泉徴収前の額で、手取りではない。"
        )
    if withdrawal_mode is not None:
        notes.append(
            f"- 収入モードが「{WITHDRAWAL_LABELS[withdrawal_mode]}」のため、"
            "投資信託の行の `年間収入(円)` は分配金ではなく取崩による現金収入。"
            "株式・ETF の行は配当のまま（影響を受けない）。"
        )
    if has_inactive:
        notes.append(
            "- `状態` が「無効」の銘柄は保有記録として残しているだけで、"
            "**サマリーの各合計には一切含まれていない**。一覧の行を足し上げても"
            "サマリーとは一致しない。"
        )
    return notes


def build_holdings_markdown(
    rows: list[dict],
    summary: Summary,
    *,
    dividend_mode: str,
    withdrawal_mode: str | None,
    fx_rate: float | None,
    market_fetched_at: str | None,
    market_source: str | None,
    generated_at: str,
) -> str:
    """計算済み行と Summary から、LLM が単体で読める Markdown 文書を組む。

    rows / summary は gui._refresh_view と同じ引数で呼んだ compute_rows の戻り値。
    generated_at を引数で受けるのは、テストが出力全体を固定値で比較できるように
    するため（now_jst_iso を呼ぶのは呼び出し側）。

    優待スイッチ（income_benefit）は受け取らない。あれは構成グラフ・月別・推移の
    数え方を変える設定で、KPI カードにも両テーブルにも効かない＝この出力には
    影響しない。「出力条件」に並べると影響したように読めるので載せない。
    """
    inactive_count = sum(1 for row in rows if row.get("active") is False)
    columns = _columns(withdrawal_mode, inactive_count > 0)
    income_label = "年間収入" if withdrawal_mode is not None else "年間配当"

    count_text = str(len(rows))
    if inactive_count:
        count_text += f"（うち無効 {inactive_count}）"

    lines = ["# 保有銘柄一覧（配当優待管理）", ""]

    lines.append("## 出力条件")
    lines.append("")
    lines += _table(
        ["項目", "値"],
        [
            ["出力日時", generated_at],
            ["配当の税制", DIVIDEND_MODE_LABELS.get(dividend_mode, dividend_mode)],
            ["収入モード", WITHDRAWAL_LABELS.get(withdrawal_mode, str(withdrawal_mode))],
            ["USDJPY", _num(fx_rate, 4)],
            ["株価の取得日時", market_fetched_at or ""],
            ["株価の取得元", market_source or ""],
            ["銘柄数", count_text],
            ["時価を算出できなかった銘柄数", str(summary.missing_market_value_count)],
        ],
    )
    lines.append("")

    lines.append("## サマリー")
    lines.append("")
    lines += _table(
        ["項目", "値"],
        [
            ["評価額計(円)", _num(summary.total_market_value_jpy, 0)],
            ["簿価計(円)", _num(summary.total_book_value_jpy, 0)],
            [f"{income_label}(円)", _num(summary.total_dividend_jpy, 0)],
            ["優待(円)", _num(summary.total_benefit_jpy, 0)],
            ["合計(円)", _num(summary.total_numerator_jpy, 0)],
            ["簿価利回り(%)", _pct(summary.yield_on_cost)],
            ["時価利回り(%)", _pct(summary.yield_on_value)],
            ["損益(円)", _num(summary.total_profit_jpy, 0)],
            ["損益率(%)", _pct(summary.profit_rate)],
        ],
    )
    lines.append("")

    lines.append("## 銘柄一覧")
    lines.append("")
    lines += _table(
        [label for label, _, _ in columns],
        [[_format(row, key, kind) for _, key, kind in columns] for row in rows],
    )
    lines.append("")

    lines.append("## 前提・注意")
    lines.append("")
    lines += _notes(dividend_mode, withdrawal_mode, inactive_count > 0)
    lines.append("")

    return "\n".join(lines)
