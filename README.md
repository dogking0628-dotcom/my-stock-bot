# Cloud Bot — 雲端美股策略 LINE 推播

完全免費，跑在 GitHub Actions，**不需要你的電腦開機**。

## 🔗 線上 Dashboard
**https://my-stock-bot-4c2bijuppmsxgtlasobq8h.streamlit.app**

每日 LINE 推播 + Streamlit Cloud App，內容自動同步。

## 功能
- **每日掃描**（週一-週五美股收盤後）：ATH 突破 BUY/SELL 訊號 → LINE
- **每週 Top30 對照**（每週一）：持股 vs 美股市值前 30 → LINE

## 部署步驟（10 分鐘）

### 1. 建立 GitHub repo
```bash
# 在 cloud_bot 資料夾內
git init
git add .
git commit -m "init"
gh repo create my-stock-bot --private --source=. --push
# 或網頁 New repo 後手動 push
```

### 2. 設定 Secrets
GitHub repo 頁面 → **Settings** → **Secrets and variables** → **Actions** → New secret
- `LINE_TOKEN`：你的 Channel Access Token
- `LINE_USER_ID`：你的 User ID（U 開頭）

### 3. 啟用 Actions
- 確認 repo 的 **Actions** 頁籤已啟用
- 點 `Daily ATH Scan` → `Run workflow` 手動測試一次
- 看到綠色勾勾 + LINE 收到訊息 = 成功

### 4. 自動運作
之後排程自動執行：
- 每個交易日 04:30 VN（早晨醒來看訊號）
- 每週一 09:00 VN（Top30 對照）

## 排程時間調整
編輯 `.github/workflows/*.yml` 內的 `cron`（注意是 UTC 時間）。

## 結構
```
cloud_bot/
├── config.py              # 30 檔股票池 + 策略參數
├── daily_scan.py          # 每日 ATH 掃描
├── top30_check.py         # Top30 對照
├── notify_line.py         # LINE 推播
├── state.json             # 持倉狀態（自動生成、commit）
├── holdings.json          # Top30 對照用的持有清單
├── requirements.txt
└── .github/workflows/
    ├── daily.yml          # 每日 cron
    └── weekly_top30.yml   # 每週一 cron
```

## 限制
- 用 yfinance 抓資料，與 moomoo 約 ±0.05% 微幅差異
- GitHub Actions cron 可能延遲 5-15 分鐘（不影響日線策略）
- 免費額度：每月 2000 分鐘 Actions runtime（這個 bot 一個月用不到 30 分鐘）
