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
| 🔍 翻倍池 v1 | double_screener.py 量化60分（月營收動能+Quality+估值）×ChatGPT質化40分 | 正式池見 `double_pool_final.md`：S級=智邦/勤誠/緯穎/金像電/貿聯/台積電 |
| 🔍 **翻倍池 v2**（9/6 建）| `double_screener_v2.py` 100 分制：Quality25+Growth20+Structural20+Revision15+Valuation10+Rerating10；Cycle Pool 自動分流（Normalized EPS/PE）、Double-PE Test、Bear/Base/Bull；質化欄位讀 `double_inputs_v2.json`（缺→proxy＋標「待質化」）；checkpoint 在 `data/checkpoints/s1~s3.json`（schema 版號改了自動失效）；規格原文 `Downloads/3Y_DoubleBagger_APP_v2-2.md` | 輸出 `double_candidates_v2.md/.csv`、`double_screener_v2_result.json`；續跑 `_screener_v2_loop.sh`；Forward EPS 無法人資料時用 tanh 衰減 proxy（標 proxy EPS），`--strict-eps` 則留空（ChatGPT 版原則）；S 級硬性要求已驗證 eps_fy1/fy3，<65 分歸 WATCH；inputs 支援 ChatGPT 版別名 normal_pe / revision_breadth。**v3 研究層（v2.1）**：直接讀 ChatGPT `tw_doublebagger_screener_v3.zip`（Downloads）的四個 CSV（`data/qualitative_research.csv` 分數需附 source_1~3 否則作廢、`data/scenario_inputs.csv` 人工 Bear/Base/Bull EPS×PE、`forward_consensus.csv`、`structural_inputs.csv`）；`--research-pack 30` 產生研究模板（尾端 ref_* 參考欄）；S 級另需有來源質化＋人工 Bear case＋Base≥60%＋R/R≥1.5；**v4 決策層（v2.2）**：`data/evidence_ledger.csv` 證據帳本（一列一論點，需 URL＋180 天內才有效；有新鮮證據即視為有來源）；S 級另需證據覆蓋率≥50% 否則降 A；輸出 `double_dashboard.md`（Top10＋模型組合＋重查佇列）、`double_refresh_queue.csv`、`double_model_portfolio.csv`（單檔≤20%/產業≤35%，候選不足留現金；**排行榜≠投資組合**）。**v5 追蹤層（v2.3）**：`double_holdings.json` 實際持股（9/4 對帳：群聯 1000@1990，防守線 1850）強制納入評分；`data/thesis_updates.csv`（thesis_status BROKEN/FAIL/EXIT 或 governance_red_flag → EXIT）、`data/revision_updates.csv`（1M/3M 同時 <-5% → REDUCE）；Signal Engine 輸出 ADD/HOLD/WATCH/REDUCE/EXIT 到 `double_action_queue.csv` 與儀表板；**ADD 必須有新鮮證據帳本**（比 ChatGPT 版嚴）。API 用量改為跨執行滾動一小時統計（`data/checkpoints/api_usage.json`，上限 590），checkpoint 原子寫入。**v6 Horizon Guard（v2.4）**：`eps_2026~2029` 共識映射 T+1~T+3，2026 執行只有 2029E 才算 horizon_complete，只有 2028E → `verified-T+2`、判定加註「T+2代理」、不得進 S、入重查佇列。`data/consensus_pool.csv` = ChatGPT 2026-09-06 第一版真實候選池（10 檔；法人共識為 ChatGPT 檢索、未驗證；只有台積電有 2029E）。儀表板動作表加「GPT建議」欄供對照。ChatGPT 自家腳本已知問題：缺 forward_consensus.csv 會崩潰、華邦電/南亞科因 PE>10 不會進 CYCLE |
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
- [x] double_screener v2 主體（9/6）：FCF_Margin、ROIC、EPS_CAGR_3Y、Forward_PE、Double_PE、Structural 20 分、Cycle Pool/Normalized EPS、S/A/B 分級
- [ ] v2 後續：把 `double_inputs_v2.json` 種子值（依 pool_final 暫定）逐檔用 ChatGPT/法人資料覆核；填 eps_fy1~fy3；排程每月 11 日全篩；marketcap_cache.json 仍是 5/7 舊值需刷新（群聯 8299 不在快取內）
- [ ] 憲法 v1.1 翻倍池條款（等用戶說「同意」）
- [ ] 帳務尾巴：環球晶買價、群聯舊倉2175/大立光原倉766出場明細
- [ ] 9/30 復盤（凍結已解除但復盤保留）
- [ ] V4.4 上線後首週實際表現追蹤（9/8 週一首日報）

## 9. 影片/論點餵料 SOP（任何 session 收到用戶餵料時必守）
1. YouTube 連結 → `python fetch_transcript.py <網址>`（2026-09-08 起自動化；底層仍是 yt-dlp `--js-runtimes node`，語言序 zh-TW→zh-Hant→zh→zh-Hans→en，人工字幕優先）→ 逐字稿落在 `data/transcripts/<上傳日>_<影片ID>.md`（段首帶 [mm:ss] 時間戳供引用），`data/transcripts/index.json` 去重。無字幕影片預設只標 no_subs，加 `--whisper`（需 `pip install faster-whisper` + ffmpeg）才轉錄，成本高，建議用戶優先給有字幕的連結。
   - 全自動路徑：`data/video_sources.json` 填追蹤頻道（`--watch` 抓最新 N 支）、`data/video_queue.txt` 一行一支佇列（`--queue`）；`.github/workflows/transcripts.yml` 每天 UTC 11:00 跑 `--watch --queue --notify`，手機也能在 Actions 頁 `Run workflow` 貼網址觸發。
   - 已知限制：YouTube 對 GitHub Actions 的雲端 IP 常要求登入驗證（「Sign in to confirm you're not a bot」）；遇到時本機瀏覽器匯出 cookies.txt → base64 → Secret `YT_COOKIES_B64`，或改在本機排程跑 `--watch --queue`。
   - 全自動分析（2026-09-09 起）：`analyze_transcript.py --new` 把新逐字稿丟給 Claude（claude-opus-5，需 `ANTHROPIC_API_KEY`），自動萃取論點 → 對照系統快照（溫度計/ATH 名單/V4.4 訊號/持股/翻倍池）→ 分流（回測候選/到期對帳/證據候選/資訊層/playbook/否決）→ **追加到 `analyst_claims.md` 尾端（編號接續）**、完整報告 `data/transcripts/<影片ID>.analysis.md`、證據候選 `data/evidence_candidates.csv`（不直接進 evidence_ledger，人工確認後才搬）。transcripts.yml 與本機 `run_transcripts.bat`（排程 `setup_transcript_scheduler.bat` 每日 19:30）都會接著跑。模型被 system prompt 鎖死只能分流，不能出現買賣建議；任何參數/策略調整一律標「需回測」。
   - 機器可讀論點庫 `data/claims.jsonl`（一行一條，due_check 附 check 規格：ticker/metric/op/value/window/base_date；backtest 附 rule 規格）→ `score_claims.py` 用 FinMind 日K 自動對帳到期預測（hit/miss 寫回 jsonl），產 `analyst_scorecard.md`：講者×頻道×預測類型命中率、已對帳明細、回測候選規則彙整（關鍵詞聚類）。預測時間基準一律是**影片上傳日**，所以回補舊影片也能直接評分。
   - 回補歷史：`run_transcripts.bat backfill 20260801 20260831`（= `fetch_transcript.py --watch --scan 120 --since --until` + `analyze_transcript.py --new --max 200` + `score_claims.py`）；範圍外影片在 index 標 out_of_range 不重抓。六頻道一個月約 150~200 支，分析費用估 30~60 美元。
2. 摘要論點 → **寫進 `analyst_claims.md`（論點驗證庫）並 commit+push**——落檔才算數，只在對話裡聊過=散失；自動分析產出的列要在下次對話覆核（自動字幕數字可能有誤）
3. 分流：技術規則→回測候選（雙窗+紅線）；籌碼→雷達資訊層；基本面→翻倍池質化/證據帳本；預測→掛到期日待對帳
4. 鐵律：**論點永不直接變成買賣建議**，必須過流水線裁決；用戶「聽了想買」時提醒走進場計畫
5. 心法類內容 → 併入 `playbook.md` 對應情境段
