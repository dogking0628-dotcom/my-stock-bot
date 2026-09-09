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

## 影片逐字稿自動下載（論點入庫用）
```bash
pip install yt-dlp                                   # 只裝這個就能抓字幕
python fetch_transcript.py https://youtu.be/xxxx     # 單支影片
python fetch_transcript.py --queue                   # 吃 data/video_queue.txt（一行一支）
python fetch_transcript.py --watch                   # 掃 data/video_sources.json 追蹤頻道最新影片
python fetch_transcript.py --whisper <網址>           # 無字幕才用 faster-whisper 轉錄（需另裝）
```
- 輸出 `data/transcripts/<上傳日>_<影片ID>.md`，每段開頭有 `[mm:ss]` 時間戳；`index.json` 記錄已抓過的影片，重跑不會重抓。
- **自動分析**：`python analyze_transcript.py --new`（需 `ANTHROPIC_API_KEY`）→ 論點追加到 `analyst_claims.md`、報告 `data/transcripts/<影片ID>.analysis.md`、證據候選 `data/evidence_candidates.csv`，有 `--notify` 會推 LINE 摘要。模型只做分流與對照，不給買賣建議。
- **自動對帳與記分板**：`python score_claims.py` 對到期的預測用 FinMind 日K 判定命中/落空，產 `analyst_scorecard.md`（講者、頻道、預測類型命中率 + 回測候選規則彙整）。
- **本機全自動**：`run_transcripts.bat` 一鍵抓＋分析＋對帳＋push；`setup_transcript_scheduler.bat` 建每日 19:30 排程。家用網路不會被 YouTube 要求登入驗證，比雲端穩。
- **回補歷史**：`run_transcripts.bat backfill 20260801 20260831` 抓六個頻道 8 月全部影片並分析、評分。
- 排程 `transcripts.yml` 每天 UTC 11:00（台北 19:00）自動跑頻道＋佇列並 commit；也可在 Actions 頁手動 Run workflow 貼網址。
- GitHub 雲端 IP 若被 YouTube 要求登入驗證，把瀏覽器匯出的 cookies.txt 做 base64 存成 Secret `YT_COOKIES_B64`。

## 限制
- 用 yfinance 抓資料，與 moomoo 約 ±0.05% 微幅差異
- GitHub Actions cron 可能延遲 5-15 分鐘（不影響日線策略）
- 免費額度：每月 2000 分鐘 Actions runtime（這個 bot 一個月用不到 30 分鐘）
