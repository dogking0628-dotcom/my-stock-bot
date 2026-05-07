# 📊 強勢股動能掃描器（PWA Android App）

即時監控台股 + 美股 ATH 突破 + 假突破濾網 + 動能評分。

## 🚀 使用流程（手機當 Android App）

### 步驟 1：部署到 Streamlit Cloud（免費，5 分鐘）

1. 把 `streamlit_app/` 整個資料夾上傳到你 GitHub repo
2. 進入 [https://share.streamlit.io](https://share.streamlit.io) 用 GitHub 帳號登入
3. 點 **New app** → 選你的 repo → main 分支 → main file 填 `streamlit_app/app.py`
4. 點 **Deploy** → 等 2-3 分鐘
5. 拿到永久網址，例如 `https://yourname-stockbot.streamlit.app`

### 步驟 2：手機加到主畫面（變成 App）

**Android Chrome：**
1. 用 Chrome 打開那個網址
2. 右上角選單 ⋮ → **加到主畫面**
3. 主畫面就會出現 App 圖示，點開無瀏覽器框架

**iPhone Safari：**
1. Safari 打開網址
2. 分享按鈕 → **加入主畫面**

## 📋 功能

- 🇹🇼 台股 200+ 檔每日掃描
- 🇺🇸 美股 40+ 檔追蹤
- 🟢🟡🔴 三色動能分類
- 📊 還原權值 vs 盤面實價切換
- 📈 大盤體制即時顯示
- 🔄 一鍵清除快取重新掃描

## 🚀 手動觸發 Workflow（手機一鍵）

App 內附 **手動觸發 GitHub Actions Workflow** 按鈕（頁面頂部 expander），可從手機直接重跑：
- 🔥 Daily ATH Scan（會更新 `dashboard_data.json`）
- ⏰ Intraday Scan / 📝 Post-close Review / 🏆 Weekly Top30 / 🏭 Industry ATH

### 一次性設定（GITHUB_TOKEN）
1. GitHub → **Settings** → **Developer settings** → **Personal access tokens**
   - Classic：勾選 `workflow` scope
   - Fine-grained：限定 repo `dogking0628-dotcom/my-stock-bot`，勾選 `Actions: Read and write`、`Contents: Read`
2. 複製 token
3. Streamlit Cloud → 你的 App → **Settings** → **Secrets**，貼上：
   ```toml
   GITHUB_TOKEN = "ghp_xxx..."
   ```
4. App 自動 reload 後按鈕即可使用

按下按鈕 → workflow 在 GitHub Actions 跑（1-3 分鐘）→ 回 App 點「🔄 重新載入」拿到新資料。

## ⚠️ 注意

- yfinance 有 15 分鐘延遲，盤中參考用
- 收盤後資料才完整準確
- 不構成投資建議，僅技術參考
