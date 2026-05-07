# 📡 投資監控 APP（最小骨架）

讀取 `dashboard_data.json` 顯示 V4 策略當日：大盤體制、台股 Top 5、出場警報。

## 部署

1. Push 到 GitHub
2. [https://share.streamlit.io](https://share.streamlit.io) → New app
3. main file 填 `monitor_app/app.py`
4. 拿到網址，手機 Chrome / Safari 可加到主畫面當 PWA

## 本機測試

```bash
pip install -r monitor_app/requirements.txt
streamlit run monitor_app/app.py
```

## 待擴充

- 持股對應警報（讀 `holdings.json`）
- 美股 Top 5 / 出場
- 0050 體制詳細資料 / 0050 進出建議
- 歷史 Top 5 回顧（讀 `top5_history.json`）
- LINE webhook 推送（需另設 secrets）
- Auto refresh（streamlit-autorefresh）

> 詳細策略邏輯見 repo 根目錄的 `STRATEGY.md`。
