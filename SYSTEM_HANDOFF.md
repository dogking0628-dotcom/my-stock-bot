# 投資系統交接文件（2026-09-06 定版，供新 session 開發 App 用）

> 本文件 = 原對話（2026/8/17~9/6）的完整系統快照。新 session 讀完此檔即可接手。
> 用戶：李鴻汶（越南 UTC+7，台股投資人）。全程繁體中文。

## 0. 一句話總覽
台股自動選股系統：**雲端（GitHub Actions）每日掃描 → LINE 推播 → 本機排程在對話面板出日報/收盤更新**；雙引擎 = 短線動能（V2+V4.4）＋ 長線基本面（3年翻倍池）。

## 1. 位置與憑證
| 項目 | 位置 |
|---|---|
| 主 repo（雲端 bot）| 本機 `C:\Users\dogki\.claude\sessions\cloud_bot` ↔ GitHub `dogking0628-dotcom/my-stock-bot`（PAT 內嵌在 git remote URL，勿改 remote）|
| 交易日記 repo | `C:\Users\dogki\invest-bot\trade-journal\`（ledger.csv、每日 .md、FREEZE_2026-09.md 含交易憲法）|
| FinMind token | `cloud_bot/finmind_token.txt`（已 .gitignore，600 calls/hr）；讀取順序 env `FINMIND_TOKEN` → 檔案 → 匿名 |
| LINE | GitHub Secrets（LINE_TOKEN/LINE_USER_ID）；`LINE_PAUSED=1` 靜默 |
| 現有 Streamlit 儀表板 | `sessions/streamlit_app/` + `dashboard_data.json`，App URL 見 STRATEGY.md 頂部 → **做新 App 的基礎** |

## 2. 策略現況（一律標版本！用戶鐵律）
| 軌道 | 內容 | 關鍵數字 |
|---|---|---|
| 📡 V2 | 8 條件嚴選（量比≥1.5+RSI55-75+前二強族群…），常空手，金融股常客 | OOS PF 1.83；頻率~每週1檔 |
| 🎯 **V4.4**（9/4 上線）| 創2y月線ATH+動能≥80+科技7族群+市值≥100億+0050>MA200+7日黑名單＋**0050<自身20MA暫停新倉**；出場=收盤破20MA或進場-7%先到 | 5y +247%/CAGR27.7%/PF3.51/MDD-19.8%（vs V4.3 +186%/-28.3%；vs 0050 全持 +253%/-33.8%）|
| 🌀 T2 糾結雷達 | 昨MA5/10/20帶寬<3%+首根漲≥3%量≥2x+距2y高≤15%——資訊層測試軌道非掛單 | 2y +81%/勝率34%；與V4.4僅2/178筆重疊（互補）|
| 🔍 翻倍池 | double_screener.py 量化60分（月營收動能+Quality+估值）×ChatGPT質化40分 | 正式池見 `double_pool_final.md`：S級=智邦/勤誠/緯穎/金像電/貿聯/台積電 |
| 已否決 | 拉回20MA買（+1% vs +73%）、純偏熱>25%停倉、外資金額加分 | 記錄在 STRATEGY.md 決策紀錄 |

## 3. 每日管線（daily.yml，cron UTC 22:33 週日~四 ≈ 越南 06:00 發）
institutional_tracker（T86投信+3主動ETF）→ smart_money_radar（外資/投信5v20日）→ sitc_product_flow（投信×48產品鏈金額）→ industry_ath_yf（全市場ATH掃描＝核心，產出 ath_industry_report.json）→ market_thermometer（量能天險1.5兆+87MA雙指數+權值50創高廣度）→ daily_v2_picker & daily_v41_picker（LINE）→ 收尾 commit。
其他 workflow：intraday.yml（盤中30分，會**覆寫** report=by design）、post_close_review.yml（14:30後，含 tw_only_scan 0050訊號+鄭大87MA脈動）。

## 4. 本機排程（scheduled-tasks，機器時區 UTC+7）
| 任務 | 時間 | 做什麼 |
|---|---|---|
| daily-top5-push | 07:09 每日 | pull + 兩策略訊息貼對話面板 |
| daily-close-update | 16:05 平日 | close_update.py 全管線→盤勢/掛單檢討/投信流向/明日預覽 |
| v43-weekly-review | 週五 17:10 | review_v43.py git-history replay 週/月績效 |
| daily-market-analysis 05:05、daily-macro-handbook 07:30 | | 其他 session 建的宏觀任務 |

## 5. 工程陷阱（都踩過、都修了——新 code 必遵守）
1. **yfinance 台股日K凌晨常缺最新一根**→ industry_ath_yf 以證交所 MI_INDEX 校驗補棒（除權息防呆：落差>15%不補）；report 帶 `data_date/ref_trading_date/patched_bars/trade_date`
2. **證交所會封 IP**（heavy polling→307）→ 回測/指數資料改 FinMind；^TWOII yfinance 曾停更一個月
3. FinMind 財報 long-format 每欄有 `_per` 佔比雙胞胎，**必須過濾 type.endswith("_per")**
4. `backtest_strategy.py` 的 `END_DATE` 寫死 2026-05-06——每個回測 main 必須 `bs.END_DATE=today`
5. stdout 包裝要 isinstance 防呆（被 import 時勿重包）；背景指令 cwd 會重置**每次都要 cd**
6. 本機工作區必須保持乾淨（排程 stash/pull 循環，髒檔會衝突出假資料）；state json 衝突一律 `checkout --theirs`
7. 回測整張限制→歷史樣本最高進場價199元，千金股屬樣本外（picker 已改零股顯示）
8. GitHub cron 延遲 30分~3h；T86 15:00 台北才發布

## 6. 用戶規則（違反=重大錯誤）
- **改策略一律：先回測評估（2y+5y 雙窗）→ 呈數字 → 用戶拍板 → 才上線**。升級紅線：期望≥+8%/PF≥2.5/MDD≤-30%
- 交易憲法 v1（trade-journal/FREEZE_2026-09.md）：只買訊號股、單筆≤50萬、停損不凹、App防呆先行；**翻倍池計畫性買進條款待用戶確認**
- 用戶口報交易→立刻記 ledger.csv+日記+push；違規標 VIOLATE 但不說教過頭
- 部位基準（9/4）：台股=群聯1000股@1990+零股10.5萬≈212萬、現金516萬、美股~$178K、BTC~$21K；大立光誤觸事件已結（-495,876）

## 7. App 開發起點建議
- 資料層全是現成 JSON（repo 根目錄）：`ath_industry_report`（含tangle_breakout/tomorrow_top5/market_regime）、`market_thermometer`、`smart_money_radar`、`sitc_product_flow`、`institutional_signal`、`double_screener_result`、`double_pool_final.md`、`daily_v2_signal`/`daily_v41_signal`、`review_v43_result`、trade-journal ledger
- 現有 streamlit_app 可升級，或改建（用戶說「做一個 app」——需求待新 session 問清：儀表板？下單輔助？手機PWA？）
- 排程與推播勿動；App 唯讀消費 JSON 最安全
- 回測框架：backtest_strategy.py(bs) + backtest_v4_1.py(v41) 為基底，參考 backtest_overheat_decel.py 樣板

## 8. 未完成待辦
- [ ] double_screener v2：FCF_Margin、EPS_CAGR_3Y、Forward_PE proxy、Double_PE、成長持續性欄位（structural_vs_cyclical）、週期股 Normalized EPS 自動標記、S/A/B 自動分級＋排程（每月11日全篩）
- [ ] 憲法 v1.1 翻倍池條款（等用戶說「同意」）
- [ ] 帳務尾巴：環球晶買價、群聯舊倉2175/大立光原倉766出場明細
- [ ] 9/30 復盤（凍結已解除但復盤保留）
- [ ] V4.4 上線後首週實際表現追蹤（9/8 週一首日報）
