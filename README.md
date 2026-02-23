# dividend-tui

## セットアップ
```powershell
pip install -r requirements.txt
```

## 実行
```powershell
python app.py
```

## データ取得
株価・配当・為替は `yfinance` で取得します（APIキー不要）。

## 為替
USDJPYは `exchangerate.host` から取得します。取得できない場合は `u` キーで再試行できます。

## データ
`data/holdings.csv` に銘柄と保有情報を入力します。株価・配当は `p` キーで取得し、`data/market_data.csv` に保存されます。
