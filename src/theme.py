"""アプリ全体のデザイントークンと QSS 生成（Qt 非依存の純 Python）。

ダークテーマ固定（テーマ切替は対象外＝意図的）。色・フォント・角丸の定数を
ここに集約し、build_app_qss() が単一のスタイルシート文字列を生成する。
損益カラー（プラス=赤 / マイナス=青）は widgets.py のヘルパーが担当し、
このモジュールでは扱わない。

注意: QComboBox::down-arrow 等の矢印サブコントロールは意図的に定義しない
（画像リソースが必要になるため）。矢印の描画は Fusion スタイルに任せる
（gui.py の _ensure_app() で app.setStyle("Fusion") を適用している）。
"""

from __future__ import annotations

# ---- 配色トークン ----
BG_WINDOW = "#1e1e1e"        # ウィンドウ全体の背景
BG_PANEL = "#252526"         # KPI カード等のパネル背景
BG_INPUT = "#2a2a2a"         # 入力欄・タブ・メニュー・ステータスバーの背景
BG_TABLE = "#232323"         # テーブル背景
BG_TABLE_ALT = "#292929"     # テーブル縞模様
BG_HEADER = "#2d2d2d"        # テーブルヘッダー
BG_BUTTON = "#2f2f2f"        # ボタン背景
BG_HOVER = "#3a3d41"         # ホバー時の背景
BG_PRESSED = "#31363b"       # 押下時の背景
BG_SELECTED = "#37474f"      # テーブル選択行（従来色を踏襲）
BORDER = "#3a3a3a"           # 標準の枠線
BORDER_LIGHT = "#4a4a4a"     # ボタン等のやや明るい枠線
BORDER_FOCUS = "#4f8cc9"     # フォーカスリング（アクセント淡色）
ACCENT = "#1565c0"           # アクセント（選択タブ下線・プログレスバー・チェック）
TEXT_PRIMARY = "#e0e0e0"     # 標準テキスト
TEXT_SECONDARY = "#9e9e9e"   # 補助テキスト（KPI タイトル・注記）
TEXT_BRIGHT = "#f5f5f5"      # 強調テキスト（KPI 値）
TEXT_DISABLED = "#5a5a5a"    # 無効状態
TEXT_STATUS = "#cfcfcf"      # ステータスバー既定色
TEXT_STATUS_ERROR = "#ff8a80"  # ステータスバーのエラー色（ダーク背景向けの淡赤）
ERROR = "#e53935"            # 入力エラーの枠線・文字
CHART_BG = "#1c1c1c"         # チャート描画背景（widgets.py と共有）

# ---- フォント・形状トークン ----
FONT_FAMILY_PRIMARY = "Yu Gothic UI"  # QFont 用（Windows 10/11 標準 UI フォント）
FONT_FAMILY = '"Yu Gothic UI", "Meiryo UI", "MS UI Gothic", sans-serif'  # QSS 用
FONT_SIZE_PT = 10
RADIUS = 4


def build_app_qss() -> str:
    """アプリ全体に適用する QSS を生成して返す。"""
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
    background: {BG_INPUT}; color: #c0c0c0; padding: 7px 16px; margin-right: 2px;
    border: 1px solid {BORDER}; border-bottom: none;
    border-top-left-radius: {RADIUS}px; border-top-right-radius: {RADIUS}px;
}}
QTabBar::tab:hover {{ background: {BG_HOVER}; }}
QTabBar::tab:selected {{
    background: #383838; color: #ffffff;
    padding-bottom: 5px; border-bottom: 2px solid {ACCENT};
}}

/* ---- テーブル ---- */
QTableView {{
    background: {BG_TABLE}; alternate-background-color: {BG_TABLE_ALT};
    gridline-color: {BORDER}; color: {TEXT_PRIMARY}; border: none;
    selection-background-color: {BG_SELECTED}; selection-color: {TEXT_BRIGHT};
}}
QHeaderView::section {{
    background: {BG_HEADER}; color: #d0d0d0; padding: 5px 6px;
    border: 0px; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
}}
QHeaderView::section:hover {{ background: {BG_HOVER}; }}
QTableCornerButton::section {{ background: {BG_HEADER}; border: 0px; }}

/* ---- ボタン ---- */
QPushButton {{
    background: {BG_BUTTON}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT}; padding: 5px 14px; border-radius: {RADIUS}px;
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: #5a5a5a; }}
QPushButton:pressed {{ background: {BG_PRESSED}; }}
QPushButton:disabled {{ background: #282828; color: {TEXT_DISABLED}; border-color: {BORDER}; }}
QPushButton:focus {{ border-color: {BORDER_FOCUS}; outline: none; }}

/* ---- 入力欄 ---- */
QLineEdit {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {BORDER_FOCUS}; }}
QLineEdit:disabled {{ color: {TEXT_DISABLED}; }}
QLineEdit[error="true"] {{ border-color: {ERROR}; }}

/* ---- コンボボックス・スピンボックス ---- */
QComboBox {{
    background: {BG_INPUT}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 3px 8px;
}}
QComboBox:hover {{ border-color: #5a5a5a; }}
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

/* ---- スクロールバー（ボタンレス・ダーク） ---- */
QScrollBar:vertical {{ background: {BG_WINDOW}; width: 12px; margin: 0px; }}
QScrollBar:horizontal {{ background: {BG_WINDOW}; height: 12px; margin: 0px; }}
QScrollBar::handle:vertical {{
    background: {BORDER_LIGHT}; border-radius: 4px; min-height: 24px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_LIGHT}; border-radius: 4px; min-width: 24px; margin: 2px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: #5f5f5f; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- ステータスバー・プログレスバー・ツールチップ ---- */
QStatusBar {{ background: {BG_INPUT}; color: {TEXT_STATUS}; }}
QProgressBar {{
    border: 1px solid {BORDER}; background: {BG_TABLE}; color: {TEXT_PRIMARY};
    text-align: center; border-radius: {RADIUS}px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QToolTip {{
    background: {BG_INPUT}; color: #f0f0f0;
    border: 1px solid #555555; padding: 4px 6px;
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
#infoLabel {{ color: #bdbdbd; padding: 4px 2px; }}
#kpiCard {{ background: {BG_PANEL}; border: 1px solid #3f3f3f; border-radius: 8px; }}
#kpiTitle {{ color: {TEXT_SECONDARY}; font-size: 9pt; }}
#kpiValue {{ color: {TEXT_BRIGHT}; font-size: 13pt; font-weight: bold; }}
#dialogError {{ color: {ERROR}; }}
#dialogNote {{ color: {TEXT_SECONDARY}; font-size: 9pt; }}
"""
