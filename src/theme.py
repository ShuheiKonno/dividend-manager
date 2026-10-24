"""アプリ全体のデザイントークンと QSS 生成（Qt 非依存の純 Python）。

ダーク / ライトの 2 パレットを持ち、`set_theme()` が「現在のテーマ」を切り替える。
色トークンはモジュール属性（`theme.BG_WINDOW` 等）として公開し、set_theme() が
その属性を張り替える——`io_csv.DATA_DIR` と同じ「定数ではなく現在値」方式。

**利用側は `from .theme import BG_WINDOW` と値で import しないこと。**
値で import すると切替後も旧テーマの色を描き続け、エラーも出ないまま
一部のウィジェットだけ配色が取り残される。必ず `from . import theme` して
`theme.BG_WINDOW` のように描画時（＝関数の中）で参照する。同じ理由で、
モジュール読込み時に `QColor(theme.X)` を作って使い回さないこと。

損益カラー（プラス=赤 / マイナス=青）の出し分けは widgets.py のヘルパーが
担当し、このモジュールは値（PROFIT_UP / PROFIT_DOWN）だけを持つ。

注意: QComboBox::down-arrow 等の矢印サブコントロールは意図的に定義しない
（画像リソースが必要になるため）。矢印の描画は Fusion スタイルに任せる
（gui.py の _ensure_app() で app.setStyle("Fusion") を適用している）。
"""

from __future__ import annotations

# ---- テーマ定義 ----
DEFAULT_THEME = "dark"
THEME_LABELS = {"dark": "ダーク", "light": "ライト"}

# 現在のテーマ名（set_theme が更新する）。読み取りは theme.CURRENT_THEME。
CURRENT_THEME = DEFAULT_THEME

# 配色トークン。2 つのパレットは**同じキー集合**を持つこと（片方に足し忘れると
# そのテーマだけ AttributeError になる。tests/test_theme.py が突合して固定する）。
_DARK = {
    # ---- 面と枠 ----
    "BG_WINDOW": "#1e1e1e",        # ウィンドウ全体の背景
    "BG_PANEL": "#252526",         # KPI カード等のパネル背景
    "BG_INPUT": "#2a2a2a",         # 入力欄・メニュー・ステータスバーの背景
    "BG_TAB": "#2a2a2a",           # 非選択タブの背景
    "BG_TABLE": "#232323",         # テーブル背景
    "BG_TABLE_ALT": "#292929",     # テーブル縞模様
    "BG_HEADER": "#2d2d2d",        # テーブルヘッダー
    "BG_BUTTON": "#2f2f2f",        # ボタン背景
    "BG_HOVER": "#3a3d41",         # ホバー時の背景
    "BG_PRESSED": "#31363b",       # 押下時の背景
    "BG_SELECTED": "#37474f",      # テーブル選択行（従来色を踏襲）
    "BG_DISABLED": "#282828",      # 無効ボタンの背景
    "BG_TAB_SELECTED": "#383838",  # 選択中タブの背景
    "BORDER": "#3a3a3a",           # 標準の枠線
    "BORDER_LIGHT": "#4a4a4a",     # ボタン等のやや明るい枠線
    "BORDER_HOVER": "#5a5a5a",     # ホバー時の枠線
    "BORDER_FOCUS": "#4f8cc9",     # フォーカスリング（アクセント淡色）
    "PANEL_BORDER": "#3f3f3f",     # KPI カードの枠線
    "SCROLL_HANDLE": "#4a4a4a",    # スクロールバーのつまみ
    "SCROLL_HANDLE_HOVER": "#5f5f5f",
    # ---- アクセント ----
    "ACCENT": "#1565c0",           # アクセント（選択タブ下線・プログレスバー・チェック）
    "ACCENT_HOVER": "#1976d2",     # アクセントボタンのホバー
    "ACCENT_PRESSED": "#0d47a1",   # 同・押下
    "ACCENT_TEXT": "#ffffff",      # アクセント塗り上の文字
    "ACCENT_FOCUS": "#bbdefb",     # アクセント塗り上のフォーカスリング
    # ---- 文字 ----
    "TEXT_PRIMARY": "#e0e0e0",     # 標準テキスト
    "TEXT_SECONDARY": "#9e9e9e",   # 補助テキスト（KPI タイトル・注記）
    "TEXT_BRIGHT": "#f5f5f5",      # 強調テキスト（KPI 値・選択行）
    "TEXT_DISABLED": "#5a5a5a",    # 無効状態
    "TEXT_STATUS": "#cfcfcf",      # ステータスバー既定色
    "TEXT_STATUS_ERROR": "#ff8a80",  # ステータスバーのエラー色
    "TEXT_INFO": "#bdbdbd",        # ステータスバー右側の恒久情報
    "TEXT_HEADER": "#d0d0d0",      # テーブルヘッダーの文字
    "TEXT_TAB": "#c0c0c0",         # 非選択タブの文字
    "TEXT_TAB_SELECTED": "#ffffff",  # 選択中タブの文字
    "TEXT_TOOLTIP": "#f0f0f0",     # ツールチップの文字
    "BORDER_TOOLTIP": "#555555",   # ツールチップの枠線
    "ERROR": "#e53935",            # 入力エラーの枠線・文字
    # ---- 損益カラー（日本式: プラス=赤 / マイナス=青） ----
    # ダーク背景（BG_TABLE / CHART_BG）でも読めるよう、彩度より明度を優先した
    # 淡めの赤青。ライトでは逆に濃い側へ振る。
    "PROFIT_UP": "#e53935",        # 含み益（プラス）
    "PROFIT_DOWN": "#42a5f5",      # 含み損（マイナス）
    # ---- チャート（widgets.py の描画） ----
    "CHART_BG": "#1c1c1c",         # チャート描画背景
    "CHART_GRID": "#2f2f2f",       # 横グリッド線
    "CHART_AXIS_TEXT": "#9e9e9e",  # 軸ラベル・空メッセージ
    "CHART_LABEL_TEXT": "#d0d0d0",  # 棒の上の金額・凡例
    "CHART_CURSOR": "#6f6f6f",     # ホバーの縦線・月平均の破線
    "CHART_SELECTION_BG": "#282828",  # 選択中の月の帯（棒より先に塗る＝不透明）
    # ---- treemap タイル（3 段階濃淡） ----
    "TILE_BORDER": "#7f7f7f",      # タイル枠線
    "TILE_HOVER_OUTLINE": "#f5f5f5",  # ホバー中タイルの上描き枠
    "TILE_NEUTRAL_BG": "#3a3a3a",  # 損益 0 / 不明
    "TILE_WEAK_BG": "#262626",     # |損益率| < 2%（文字色で赤青を示す）
    "TILE_UP_STRONG": "#c62828",   # 損益率 >= +5%
    "TILE_UP_MID": "#8e2424",      # +2% 〜 +5%
    "TILE_DOWN_STRONG": "#1565c0",  # 損益率 <= -5%
    "TILE_DOWN_MID": "#1a3a6b",    # -5% 〜 -2%
    "ON_FILL_LIGHT": "#f5f5f5",    # 濃い塗りの上の文字
    "ON_FILL_DARK": "#1c1c1c",     # 淡い塗りの上の文字
    "CATEGORICAL_OTHER": "#757575",  # 円グラフ「その他」のスライス
    # ---- チャートの系列色 ----
    "SERIES_VALUE": "#42a5f5",     # 評価額推移
    "SERIES_BOOK": "#9e9e9e",      # 簿価推移
    "SERIES_INCOME": "#66bb6a",    # 配当（収入）推移
    "SERIES_DIVIDEND": "#1565c0",  # 月別: 配当の棒
    "SERIES_BENEFIT": "#ffa726",   # 月別: 優待の棒（配当推移の緑と取り違えない橙）
    # ---- 表の注意喚起 ----
    "SPLIT_FLAG": "#ffca28",       # 株式分割の未反映フラグ（損益の赤青と取り違えない黄）
    "SPLIT_DONE": "#81c784",       # 株式分割の反映済み表示（未反映の黄と見分ける緑）
}

_LIGHT = {
    # ---- 面と枠 ----
    "BG_WINDOW": "#f4f5f7",
    "BG_PANEL": "#ffffff",
    "BG_INPUT": "#ffffff",
    "BG_TAB": "#e6e8ec",
    "BG_TABLE": "#ffffff",
    "BG_TABLE_ALT": "#f6f7f9",
    "BG_HEADER": "#eceef1",
    "BG_BUTTON": "#fbfbfc",
    "BG_HOVER": "#e2e8ee",
    "BG_PRESSED": "#d3dbe3",
    "BG_SELECTED": "#cfe3ef",
    "BG_DISABLED": "#ececec",
    "BG_TAB_SELECTED": "#ffffff",
    "BORDER": "#d3d6da",
    "BORDER_LIGHT": "#c2c6cb",
    "BORDER_HOVER": "#9aa0a6",
    "BORDER_FOCUS": "#1565c0",
    "PANEL_BORDER": "#dcdfe3",
    "SCROLL_HANDLE": "#c2c6cb",
    "SCROLL_HANDLE_HOVER": "#a2a7ad",
    # ---- アクセント ----
    "ACCENT": "#1565c0",
    "ACCENT_HOVER": "#1976d2",
    "ACCENT_PRESSED": "#0d47a1",
    "ACCENT_TEXT": "#ffffff",
    "ACCENT_FOCUS": "#bbdefb",
    # ---- 文字 ----
    "TEXT_PRIMARY": "#202124",
    "TEXT_SECONDARY": "#5f6368",
    "TEXT_BRIGHT": "#111111",
    "TEXT_DISABLED": "#a0a4a8",
    "TEXT_STATUS": "#3c4043",
    "TEXT_STATUS_ERROR": "#c62828",
    "TEXT_INFO": "#5f6368",
    "TEXT_HEADER": "#3c4043",
    "TEXT_TAB": "#5f6368",
    "TEXT_TAB_SELECTED": "#111111",
    "TEXT_TOOLTIP": "#202124",
    "BORDER_TOOLTIP": "#c2c6cb",
    "ERROR": "#c62828",
    # ---- 損益カラー ----
    # 白背景では淡い赤青がほとんど読めないため、ダークより濃い側を使う。
    "PROFIT_UP": "#c62828",
    "PROFIT_DOWN": "#1565c0",
    # ---- チャート ----
    "CHART_BG": "#ffffff",
    "CHART_GRID": "#e6e8ea",
    "CHART_AXIS_TEXT": "#757575",
    "CHART_LABEL_TEXT": "#3c4043",
    "CHART_CURSOR": "#9aa0a6",
    "CHART_SELECTION_BG": "#eceff1",
    # ---- treemap タイル ----
    "TILE_BORDER": "#ffffff",
    "TILE_HOVER_OUTLINE": "#202124",
    "TILE_NEUTRAL_BG": "#e0e0e0",
    "TILE_WEAK_BG": "#f2f2f2",
    "TILE_UP_STRONG": "#c62828",
    "TILE_UP_MID": "#ef9a9a",
    "TILE_DOWN_STRONG": "#1565c0",
    "TILE_DOWN_MID": "#90caf9",
    "ON_FILL_LIGHT": "#f5f5f5",
    "ON_FILL_DARK": "#202124",
    # 白背景で白文字が乗らない明るさにする（#9e9e9e は輝度 0.62 で
    # _readable_text_color の境目に落ち、白文字とのコントラストが足りない）。
    "CATEGORICAL_OTHER": "#bdbdbd",
    # ---- チャートの系列色 ----
    "SERIES_VALUE": "#1976d2",
    "SERIES_BOOK": "#757575",
    "SERIES_INCOME": "#2e7d32",
    "SERIES_DIVIDEND": "#1565c0",
    "SERIES_BENEFIT": "#ef6c00",
    "SPLIT_FLAG": "#b26a00",
    "SPLIT_DONE": "#2e7d32",
}

PALETTES = {"dark": _DARK, "light": _LIGHT}
THEMES = tuple(PALETTES)
# 色トークン名の一覧（テスト・パレット突合用）
TOKEN_NAMES = tuple(sorted(_DARK))

# ---- フォント・形状トークン（テーマ非依存） ----
FONT_FAMILY_PRIMARY = "Yu Gothic UI"  # QFont 用（Windows 10/11 標準 UI フォント）
FONT_FAMILY = '"Yu Gothic UI", "Meiryo UI", "MS UI Gothic", sans-serif'  # QSS 用
FONT_SIZE_PT = 10
RADIUS = 4


def set_theme(name: str) -> str:
    """現在のテーマを切り替え、色トークンのモジュール属性を張り替える。

    未知の名前は既定（ダーク）へ落とす（設定ファイルの手編集で起動不能に
    しないため）。戻り値は実際に適用したテーマ名。
    """
    global CURRENT_THEME
    if name not in PALETTES:
        name = DEFAULT_THEME
    CURRENT_THEME = name
    globals().update(PALETTES[name])
    return name


def is_dark() -> bool:
    """現在のテーマが暗色系か（Windows タイトルバーの切替に使う）。"""
    return CURRENT_THEME == "dark"


def theme_label(name: str) -> str:
    """テーマ名の日本語ラベル（メニュー表示用）。"""
    return THEME_LABELS.get(name, name)


# モジュール読込み時に既定テーマを適用する（以降の theme.BG_WINDOW 等が有効になる）。
set_theme(DEFAULT_THEME)


def build_app_qss() -> str:
    """現在のテーマの QSS を生成して返す（テーマ切替のたびに呼び直す）。"""
    return f"""
/* ---- 基本 ---- */
QMainWindow, QWidget {{
    background: {BG_WINDOW}; color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY}; font-size: {FONT_SIZE_PT}pt;
}}
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; spacing: 6px; }}

/* ---- メニュー ---- */
QMenuBar {{
    background: {BG_WINDOW}; color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {BG_HOVER}; border-radius: {RADIUS}px; }}
QMenu {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT}; padding: 4px;
}}
QMenu::item {{ background: transparent; padding: 5px 24px 5px 12px; border-radius: {RADIUS}px; }}
QMenu::item:selected {{ background: {BG_HOVER}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

/* ---- タブ ---- */
QTabWidget::pane {{ border: 1px solid {BORDER}; top: -1px; }}
QTabBar::tab {{
    background: {BG_TAB}; color: {TEXT_TAB}; padding: 7px 16px; margin-right: 2px;
    border: 1px solid {BORDER}; border-bottom: none;
    border-top-left-radius: {RADIUS}px; border-top-right-radius: {RADIUS}px;
}}
QTabBar::tab:hover {{ background: {BG_HOVER}; }}
QTabBar::tab:selected {{
    background: {BG_TAB_SELECTED}; color: {TEXT_TAB_SELECTED};
    padding-bottom: 5px; border-bottom: 2px solid {ACCENT};
}}

/* ---- テーブル ---- */
QTableView {{
    background: {BG_TABLE}; alternate-background-color: {BG_TABLE_ALT};
    gridline-color: {BORDER}; color: {TEXT_PRIMARY}; border: none;
    selection-background-color: {BG_SELECTED}; selection-color: {TEXT_BRIGHT};
}}
QHeaderView::section {{
    background: {BG_HEADER}; color: {TEXT_HEADER}; padding: 5px 6px;
    border: 0px; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
}}
QHeaderView::section:hover {{ background: {BG_HOVER}; }}
QTableCornerButton::section {{ background: {BG_HEADER}; border: 0px; }}

/* ---- ボタン ---- */
QPushButton {{
    background: {BG_BUTTON}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT}; padding: 5px 14px; border-radius: {RADIUS}px;
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: {BORDER_HOVER}; }}
QPushButton:pressed {{ background: {BG_PRESSED}; }}
QPushButton:disabled {{ background: {BG_DISABLED}; color: {TEXT_DISABLED}; border-color: {BORDER}; }}
QPushButton:focus {{ border-color: {BORDER_FOCUS}; outline: none; }}

/* ---- 入力欄 ---- */
QLineEdit {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT};
}}
QLineEdit:focus {{ border-color: {BORDER_FOCUS}; }}
QLineEdit:disabled {{ color: {TEXT_DISABLED}; }}
QLineEdit[error="true"] {{ border-color: {ERROR}; }}

/* ---- コンボボックス・スピンボックス ---- */
QComboBox {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 3px 8px;
}}
QComboBox:hover {{ border-color: {BORDER_HOVER}; }}
QComboBox:focus {{ border-color: {BORDER_FOCUS}; }}
QComboBox QAbstractItemView {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT}; selection-background-color: {BG_HOVER};
    outline: none;
}}
QAbstractSpinBox {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 3px 6px;
}}
QAbstractSpinBox:focus {{ border-color: {BORDER_FOCUS}; }}

/* ---- チェックボックス（チェック画像レス: ACCENT 塗りで表現） ---- */
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {BORDER_LIGHT};
    border-radius: 3px; background: {BG_INPUT};
}}
QCheckBox::indicator:hover {{ border-color: {BORDER_FOCUS}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
/* 無効（外せない最後の1つ・自動判定中の月など）。定義が無いと ACCENT 塗りのまま
   有効と見分けが付かない。hover より後ろに置いて枠色の上書きに勝たせる。 */
QCheckBox:disabled {{ color: {TEXT_DISABLED}; }}
QCheckBox::indicator:disabled {{ background: {BG_DISABLED}; border-color: {BORDER}; }}
QCheckBox::indicator:checked:disabled {{
    background: {TEXT_DISABLED}; border-color: {TEXT_DISABLED};
}}

/* ---- スクロールバー（ボタンレス） ---- */
QScrollBar:vertical {{ background: {BG_WINDOW}; width: 12px; margin: 0px; }}
QScrollBar:horizontal {{ background: {BG_WINDOW}; height: 12px; margin: 0px; }}
QScrollBar::handle:vertical {{
    background: {SCROLL_HANDLE}; border-radius: 4px; min-height: 24px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {SCROLL_HANDLE}; border-radius: 4px; min-width: 24px; margin: 2px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {SCROLL_HANDLE_HOVER};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- ツールバー ---- */
/* Fusion 既定のツールバーは背景から浮くため、明示的に配色を与える。
   ツールボタンは QPushButton と同系の見え方に揃える。 */
QToolBar {{
    background: {BG_WINDOW}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 4px 6px; spacing: 4px;
}}
QToolBar::separator {{
    background: {BORDER}; width: 1px; margin: 4px 8px;
}}
QToolBar QToolButton {{
    background: {BG_BUTTON}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT}; padding: 5px 12px; border-radius: {RADIUS}px;
}}
QToolBar QToolButton:hover {{ background: {BG_HOVER}; border-color: {BORDER_HOVER}; }}
QToolBar QToolButton:pressed {{ background: {BG_PRESSED}; }}
QToolBar QToolButton:disabled {{
    background: {BG_DISABLED}; color: {TEXT_DISABLED}; border-color: {BORDER};
}}
QToolBar QToolButton:focus {{ border-color: {BORDER_FOCUS}; outline: none; }}
/* 取り消せない操作（削除）はホバー時に赤枠で他と区別する。 */
QToolBar QToolButton#dangerAction:hover {{
    border-color: {ERROR}; color: {TEXT_STATUS_ERROR};
}}
/* 最頻出の操作（株価・配当・為替を取得）はアクセント塗りで見つけやすくする。 */
QToolBar QToolButton#primaryAction {{
    background: {ACCENT}; color: {ACCENT_TEXT}; border: 1px solid {ACCENT};
}}
QToolBar QToolButton#primaryAction:hover {{
    background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER};
}}
QToolBar QToolButton#primaryAction:pressed {{
    background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED};
}}
/* id セレクタは既存の QToolBar QToolButton:disabled に特異度で勝つため、
   無効時の見え方をここで明示する（取得中に青のままだと押せそうに見える）。 */
QToolBar QToolButton#primaryAction:disabled {{
    background: {BG_DISABLED}; color: {TEXT_DISABLED}; border-color: {BORDER};
}}
/* 既定の BORDER_FOCUS は青塗りの上では青on青でほぼ見えないため淡色を使う。 */
QToolBar QToolButton#primaryAction:focus {{ border-color: {ACCENT_FOCUS}; outline: none; }}
QToolBar QLabel, #filterBox QLabel {{ color: {TEXT_SECONDARY}; padding: 0px 2px; }}

/* ---- ステータスバー・プログレスバー・ツールチップ ---- */
QStatusBar {{ background: {BG_INPUT}; color: {TEXT_STATUS}; }}
/* エラー通知は動的プロパティで切り替える（_notify が setProperty +
   unpolish/polish を呼ぶ）。QStatusBar 全体を setStyleSheet で組み直すと
   このファイルと配色が二重管理になるため、状態はここで定義する。 */
QStatusBar[error="true"] {{ color: {TEXT_STATUS_ERROR}; }}
QStatusBar QLabel {{ color: {TEXT_STATUS}; }}
QProgressBar {{
    border: 1px solid {BORDER}; background: {BG_TABLE}; color: {TEXT_PRIMARY};
    text-align: center; border-radius: {RADIUS}px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QToolTip {{
    background: {BG_INPUT}; color: {TEXT_TOOLTIP};
    border: 1px solid {BORDER_TOOLTIP}; padding: 4px 6px;
}}

/* ---- ダイアログ・グループボックス・スプリッタ ---- */
QDialog {{ background: {BG_WINDOW}; color: {TEXT_PRIMARY}; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    margin-top: 10px; padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 8px; padding: 0px 4px; color: {TEXT_SECONDARY};
}}
QMessageBox QLabel {{ color: {TEXT_PRIMARY}; }}
QMessageBox QPushButton {{ min-width: 80px; }}
QSplitter::handle {{ background: {BORDER}; }}

/* ---- objectName 指定（個別ウィジェット） ---- */
#infoLabel {{ color: {TEXT_INFO}; padding: 4px 2px; }}
#kpiCard {{ background: {BG_PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 8px; }}
#kpiTitle {{ color: {TEXT_SECONDARY}; font-size: 9pt; }}
#kpiValue {{ color: {TEXT_BRIGHT}; font-size: 13pt; font-weight: bold; }}
#dialogError {{ color: {ERROR}; }}
#dialogNote {{ color: {TEXT_SECONDARY}; font-size: 9pt; }}
"""
