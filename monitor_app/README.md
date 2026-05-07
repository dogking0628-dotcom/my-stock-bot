# 📡 投資監控 APP（最小骨架）

讀取 `dashboard_data.json` 顯示 V4 策略當日：大盤體制、台股 Top 5、出場警報。
**支援從 APP 內按鈕觸發 GitHub Actions workflow（需設定 PAT）**。

## 部署

1. Push 到 GitHub
2. [https://share.streamlit.io](https://share.streamlit.io) → New app
3. main file 填 `monitor_app/app.py`
4. 拿到網址，手機 Chrome / Safari 可加到主畫面當 PWA

## 啟用「從 APP 觸發 workflow」

要按 🚀 立即執行 鈕能跑 GitHub Actions，需要設定 PAT：

### 步驟 1：建立 GitHub Personal Access Token

1. 開 https://github.com/settings/personal-access-tokens/new （fine-grained 推薦）
2. **Token name**：填 `streamlit-monitor`
3. **Expiration**：自選（建議 90 天）
4. **Repository access** → Only select repositories → 勾 `dogking0628-dotcom/my-stock-bot`
5. **Permissions** → Repository permissions → 找這兩個：
   - **Actions**：Read and write
   - **Contents**：Read-only
6. 按 **Generate token** → 複製出現的 `github_pat_xxxx...`（**只會出現一次**）

> 偏好 classic token：scopes 只勾 `workflow` 即可。

### 步驟 2：把 PAT 存進 Streamlit Cloud Secrets

1. 到 https://share.streamlit.io 找你的 app
2. 點 ⋯ → **Settings** → **Secrets**
3. 貼上：
   ```toml
   GITHUB_TOKEN = "github_pat_xxxx你剛複製的"
   ```
4. 按 **Save** → app 會自動重啟

### 步驟 3：使用

APP 內展開 「▶️ 觸發 GitHub Actions」 → 選 workflow → 按 🚀 立即執行 → 出現「已送出觸發請求」就 OK。  
等 5~10 分鐘讓 workflow 跑完，再按 🔄 重新載入資料 看新數據。

## 本機測試

```bash
pip install -r monitor_app/requirements.txt
streamlit run monitor_app/app.py
```

本機要測 trigger，把 PAT 寫到 `~/.streamlit/secrets.toml`：
```toml
GITHUB_TOKEN = "github_pat_xxxx"
```

## 待擴充

- 持股對應警報（讀 `holdings.json`）
- 美股 Top 5 / 出場
- 0050 體制詳細資料 / 0050 進出建議
- 歷史 Top 5 回顧（讀 `top5_history.json`）
- LINE webhook 推送
- Auto refresh（streamlit-autorefresh）

> 詳細策略邏輯見 repo 根目錄的 `STRATEGY.md`。
