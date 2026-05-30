---
name: yf-debug
description: yfinance の取得エラーを診断する。holdings.csv の全ティッカーをテストし、失敗した銘柄と原因を報告する。
---

`data/holdings.csv` の symbol 列を全件読み込み、各ティッカーについて以下を確認する:

1. `yfinance.Ticker(symbol).info` が取得できるか
2. `yfinance.Ticker(symbol).history(period='5d')` が空でないか
3. 日本株で `.T` サフィックスが付いているか（4〜5桁の数字のみのティッカーは `.T` 漏れを疑う）

結果を3列（ティッカー / 状態 / 問題点・修正案）の表にまとめて報告する。
失敗ゼロなら「全銘柄の取得に成功しました」と表示する。
