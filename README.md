# dividend-manager-gui

個人向け配当・ポートフォリオ管理アプリ（PySide6 / Qt6 ネイティブ GUI）。
株式・ETF・日本の投資信託（ISIN コード対応）を一括管理し、配当収入・取り崩し収入・損益をリアルタイムで可視化します。

## セットアップ

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

または `run.bat` をダブルクリックで起動。

## キーボードショートカット

| キー | 操作 |
|------|------|
| `N` | 新規銘柄を登録 |
| `E` | 選択行を編集 |
| `P` | 株価・配当・基準価額を取得（yfinance / ToushinReader） |
| `U` | USDJPY レートを取得 |
| `M` | 税引前 / 税引後 モード切替 |
| `W` | 収入モード切替: 配当 → 定額取崩 → 定率取崩 → 配当 |
| `R` | データを再読込 |
| `S` | カーソル列でソート |
| `1` / `2` / `3` / `4` | タブ切替（評価額 / 配当金 / 株価関連 / 配当関連） |
| `Q` | 終了 |

## データ取得

- **株式・ETF**: `yfinance` で株価・配当を取得（API キー不要）
- **日本の投資信託（ISIN コード）**: `ToushinReader` で基準価額を取得
  - `asset_type=fund` かつシンボルが ISIN 形式（例: `JP90C000J569`）の場合に自動で使用
- **USDJPY**: `yfinance`（`USDJPY=X`）で取得

## 投資信託の口数入力

日本の投資信託の基準価額は **1万口あたり** で表示されます。  
`保有株数` フィールドには **口数 ÷ 10,000** を入力してください。

```
例: 2,067,284口 保有 → 保有株数 = 206.7284
    基準価額が 39,764円/万口 → 取得単価 = 39764
```

## 取り崩しモード（W キー）

`W` キーで収入の算出方法を切り替えられます：

| モード | 表示ラベル | 算出方法 |
|--------|-----------|---------|
| 通常 | 年間配当(円) | yfinance 取得配当 / 手動 override |
| 定額取崩 | 年間収入(円) | `withdrawal_fixed_jpy`（設定値）をファンドに適用 |
| 定率取崩 | 年間収入(円) | `時価評価額 × withdrawal_rate` をファンドに適用 |

株式・ETF は取り崩しモードでも配当をそのまま表示します。

## データファイル

| ファイル | 内容 |
|---------|------|
| `data/holdings.csv` | 銘柄マスタ（手動入力） |
| `data/market_data.csv` | 株価・配当キャッシュ（`P` キーで更新） |
| `data/fx.csv` | USDJPY 履歴（最終行が最新レート） |
| `data/backup/` | 書込み前の自動バックアップ |

`data/` ディレクトリは `.gitignore` 済み（ローカル専用）。

## レガシー TUI

旧 Textual TUI を実行する場合のみ、追加依存をインストールしてください。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-legacy.txt
```
