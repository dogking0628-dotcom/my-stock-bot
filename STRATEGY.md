# 📊 投資策略系統完整邏輯（V4）

**最後更新**：2026-05-07
**Repo**：https://github.com/dogking0628-dotcom/my-stock-bot
**App**：https://my-stock-bot-4c2bijuppmsxgtlasobq8h.streamlit.app

---

## 🏆 績效摘要（5 年回測）

```
總報酬     +226.8%
年化 CAGR  +24.8%
獲利因子   3.40
勝率       46%
最大回撤   -9.6%   （0050: -33.8%）
抗風險評等 ⭐⭐⭐⭐⭐
```

---

## 📖 目錄

1. [系統架構](#1-系統架構)
2. [策略演進史 V1→V4](#2-策略演進史-v1v4)
3. [V4 完整邏輯（生產版）](#3-v4-完整邏輯生產版)
4. [動能評分公式](#4-動能評分公式)
5. [出場規則](#5-出場規則)
6. [大盤體制濾網](#6-大盤體制濾網)
7. [美股族群連動加分](#7-美股族群連動加分)
8. [台股 0050 進出邏輯](#8-台股-0050-進出邏輯)
9. [美股策略](#9-美股策略)
10. [自動化排程](#10-自動化排程)
11. [輸出通道](#11-輸出通道)
12. [回測歷程與績效](#12-回測歷程與績效)
13. [黑天鵝壓力測試](#13-黑天鵝壓力測試)
14. [自動壓力測試（每次回測必跑）](#14-自動壓力測試每次回測必跑)
15. [檔案結構](#15-檔案結構)
16. [可調參數](#16-可調參數)
17. [改策略決策流程](#17-改策略決策流程)

---

## 1. 系統架構

```
┌──────────────────────────────────────────────────┐
│              GitHub Actions (Cron)                │
├──────────────────────────────────────────────────┤
│ 05:30 daily ─────► daily.yml                     │
│  ├── industry_ath_yf.py  (V4 全市場 ATH)         │
│  ├── daily_scan.py        (台美股 + LINE)        │
│  └── daily_review.py      (昨日選股回顧)         │
│                                                   │
│ 09:00-13:30 每 30 分 ──► intraday.yml            │
│  └── intraday_scan.py     (盤中新訊號 + 去重)    │
│                                                   │
│ 14:30 收盤後 ────────────► post_close_review.yml │
│  └── weekly_review.py     (前一日+前一週檢討)    │
└──────────────────────────────────────────────────┘
                       │
                       ▼
              ┌────────────────┐
              │ dashboard_data │ ◄── Streamlit App (PWA)
              └────────────────┘
                       │
                       ▼
                LINE Messaging API
                  （手機推播）
```

---

## 2. 策略演進史 V1→V4

| 版本 | 描述 | 總報酬 | CAGR | PF | 勝率 | 最大回撤 |
|---|---|---|---|---|---|---|
| V1 | 全市場 ATH | +165.7% | +20.1% | 2.11 | 40% | – |
| V2 | 科技限定（7 族群） | +161.4% | +19.7% | 2.11 | 36% | – |
| V3 | 三濾網（科技+市值+美股加分） | +209.9% | +23.6% | 3.94 | 49% | – |
| **V4** ⭐ | V3 + 0050 體制濾網 | **+226.8%** | **+24.8%** | 3.40 | 46% | **-9.6%** |

### 各版差異對照

| 條件 | V1 | V2 | V3 | V4 |
|---|---|---|---|---|
| 創 2y 月線 ATH | ✅ | ✅ | ✅ | ✅ |
| 多頭排列（>20MA>60MA>200MA） | ✅ | ✅ | ✅ | ✅ |
| 動能 ≥ 80 | ✅ | ✅ | ✅ | ✅ |
| 跌破 20MA 出場 | ✅ | ✅ | ✅ | ✅ |
| 限科技 7 族群 | ❌ | ✅ | ✅ | ✅ |
| 市值 ≥ 100億 | ❌ | ❌ | ✅ | ✅ |
| 美股族群加分 | ❌ | ❌ | ✅ | ✅ |
| **0050 > MA200 才進場** | ❌ | ❌ | ❌ | ✅ |

---

## 3. V4 完整邏輯（生產版）

### 完整篩選流程

```
全市場 1962 檔
    │
    ▼ ① 抓 2 年日 K（yfinance auto_adjust）
    ▼ ② 計算 MA5/20/60/200, RSI, 量比, 月線最高
    ▼ ③ 創 2 年月線 ATH（today >= 24-month max × 0.999）
    │   約 100~200 檔通過
    ▼
    ▼ ④ 🔒 科技限定（7 個族群）
    │   ✅ 半導體 / 電子零組件 / 光電 / 電腦及週邊
    │   ✅ 電子通路 / 通信網路 / 其他電子
    ▼
    ▼ ⑤ 🔒 市值 ≥ 100 億 NT$
    │   砍 <50億 雜訊（PF 1.25 → 6.72）
    ▼
    ▼ ⑥ 動能評分（0~100）詳見§4
    ▼
    ▼ ⑦ 美股族群連動加分 詳見§7
    │   QQQ ↑   → 全 7 族群 +5
    │   SMH ↑0.5%+ → 半導體/其他電子 +10
    │   IGV ↑0.5%+ → 通信網路/電腦及週邊 +10
    ▼
    ▼ ⑧ 找最強族群（ATH 檔數最多 + 多頭比 ≥50%）
    ▼ ⑨ 該族群內挑動能 ≥ 80 的前 5 檔
    │   不足 3 檔 → 從其他科技族群高分股補足
    ▼
    ▼ ⑩ 🚨 V4 大盤體制濾網
    │   0050 > MA200 (Stage 2) → ✅ 推送 Top 5
    │   0050 ≤ MA200 (Stage 4) → ⛔ 空手觀望
    │
    ▼
[每日 Top 5 推薦 / 或空手]
```

### 程式對應

```python
# industry_ath_yf.py 關鍵常數
ALLOWED_INDUSTRIES = {
    "半導體", "電子零組件", "光電", "電腦及週邊",
    "電子通路", "通信網路", "其他電子",
}
MIN_MCAP_BILLIONS = 100
NEAR_THRESHOLD = 0.95
EXACT_THRESHOLD = 0.999  # ATH 判定（距高 0.1% 以內）

US_SECTOR_BOOST = {
    "QQQ": {"industries": list(ALLOWED_INDUSTRIES),
            "threshold": 0.0, "bonus": 5},
    "SMH": {"industries": ["半導體", "其他電子"],
            "threshold": 0.5, "bonus": 10},
    "IGV": {"industries": ["通信網路", "電腦及週邊"],
            "threshold": 0.5, "bonus": 10},
}
```

---

## 4. 動能評分公式

每檔 ATH 股獨立計分（**0~100**，三層加權）。

### 🥇 Tier 1（核心，三選一給 25 分）

| 訊號 | 條件 | 統計續漲率 |
|---|---|---|
| 漲停鎖死 | change ≥ 9.5% AND vol_ratio < 1.2 | **88-93%** |
| 量爆價揚 | vol_ratio ≥ 3 AND change ≥ 5 | 78-85% |
| 跳空缺口 | open > yesterday_high × 1.005 AND close > open | 75-82% |

### 🥈 Tier 2（各 +10~15 分）

| 訊號 | 條件 |
|---|---|
| RSI 強勢區 | 60 ≤ RSI ≤ 75 |
| 加速多頭 | close > MA5 > MA10 > MA20 |
| 創 ATH | today >= 2y_max × 0.999 |
| 收盤近高 | close ≥ (high - range × 0.2) |
| 長紅 K | (close - open) / range ≥ 0.7 |
| 族群同步 | 同產業 ≥60% 今日漲 |

### 🥉 Tier 3（V3 美股加分，+5~10）

詳見 §7

### 評級

| 分數 | 評級 | 隔日續漲率 |
|---|---|---|
| ≥ 80 | ⭐⭐⭐ | ≥ 85% |
| 60-79 | ⭐⭐ | 70-85% |
| < 60 | ⭐ | < 70% |

---

## 5. 出場規則

### 雙重 Trailing Stop

```python
# 持倉中每日檢查
exit_now = False
if close < ma20:                    # 條件 1：跌破 20MA
    exit_now = True
elif close < entry_peak * 0.7:      # 條件 2：從進場後峰值 -30%
    exit_now = True

if exit_now:
    sell_at_next_open()  # 隔日開盤賣
```

**回測統計**：
- 5 年 121 筆出場 100% 都由 20MA 觸發
- -30% 從未啟用 → 20MA 是足夠的 trailing stop

---

## 6. 大盤體制濾網

### Stan Weinstein Stage Analysis

| 階段 | 0050 vs MA200 | 行動 |
|---|---|---|
| Stage 1 底部盤整 | 接近 MA200 震盪 | 不進場 |
| **Stage 2 主升段** | **持續 > MA200** | ⭐ **全力進攻** |
| Stage 3 頂部分配 | 開始跌破 MA200 | 鎖利 |
| Stage 4 主跌段 | 持續 < MA200 | **嚴禁追價** |

### V4 邏輯

```python
# industry_ath_yf.py
in_stage2 = today_0050 > ma200_0050

if not in_stage2:
    tomorrow_top5 = []  # 空手觀望
else:
    tomorrow_top5 = pick_from_strongest_industry()
```

### 5 年回測中觸發次數

- 跳過進場 **270 天 / 1290 天 = 20.9%**
- 主要規避：2022 升息熊市（9 個月）、2025 關稅恐慌（4 月）

---

## 7. 美股族群連動加分

### 邏輯

當美股對應族群昨日上漲 → TW 對應族群隔日**動能加分**

```python
# 昨日漲跌 → 加分
QQQ ↑ 0% 以上     → ALL 7 科技族群 +5
SMH ↑ 0.5% 以上   → 半導體/其他電子 +10
IGV ↑ 0.5% 以上   → 通信網路/電腦及週邊 +10
```

### 實際範例（2026-05-07）

```
QQQ +2.1%、SMH +5.2% → 半導體加 5+10=15 分
原本只有 ATH 50 分的股票 → 升到 65+
原本 85 分的股票 → 升到 100 分（爆滿） ⭐⭐⭐
```

---

## 8. 台股 0050 進出邏輯

獨立於 V4，是「**長期定額**」+「**體制加碼**」邏輯。

```python
ext_pct = (today / ma200 - 1) * 100  # 距 MA200 偏離率

if ext_pct < -10:    action = "🔥 大跌加碼 cash→80%"
elif ext_pct <= 10:  action = "🟢 正常持有 70%"
elif ext_pct <= 30:  action = "🟠 略過熱（持有但不加碼）"
elif ext_pct <= 50:  action = "🔴 過熱（鎖利減碼至 70%）"
else:                action = "🚨 極度過熱（減碼至 50%）"
```

**檔案**：`tw_0050_signal.py`

---

## 9. 美股策略

### S&P 500 ATH 突破策略（簡化版）

```python
# daily_scan.py
universe = S&P 500 + 動態 (503 檔)
for ticker in universe:
    if today >= max(last 504 days) * 0.999:
        BUY  # 創 2y 新高

# 出場
for position in positions:
    if close < ma20:
        SELL  # 跌破 20MA

# 限制
MAX_POSITIONS = 10  # 同時最多 10 檔
```

---

## 10. 自動化排程

| 時間（台北） | Workflow | 內容 |
|---|---|---|
| **09:00-13:30** 每 30 分 | `intraday.yml` | 盤中新訊號（去重） |
| **14:30**（盤後） | `post_close_review.yml` | 前日+前週策略檢討 |
| **05:30**（隔日清晨） | `daily.yml` | 完整日報 + LINE |

### Cron 對應

```yaml
# daily.yml
- cron: '30 21 * * 1-5'       # UTC 21:30 = 台北 05:30 (隔日)

# intraday.yml
- cron: '0,30 1-5 * * 1-5'    # UTC 01:00-05:30 每 30 分

# post_close_review.yml
- cron: '30 6 * * 1-5'        # UTC 06:30 = 台北 14:30
```

---

## 11. 輸出通道

### 11.1 LINE 推播（每日清晨 + 盤中 + 盤後）

#### Daily 訊息（05:30）
```
📊 投資策略日報 yyyy-mm-dd
═════════════════
📊 大盤體制（SPY/0050）
─────────
🇺🇸 美股策略 A（ATH 突破）
─────────
🇹🇼 台股 0050 策略
─────────
🇹🇼 台股突破篩選（統計濾網）
─────────
🎯 動能 Top 5（V4 從最強族群挑）
─────────
🚨 跌破 20MA 出場訊號（持股檢查）
─────────
🌐 全市場創新高族群統計
─────────
📈 昨日 Top 5 回顧
─────────
👉 建議收盤前執行
```

#### 盤後檢討（14:30）
```
📊 策略檢討（盤後）
  昨日 N 檔：x 勝 y 敗（z%）平均 +x.xx%
  過去一週 N 檔：x 勝 y 敗（z%）PF X.XX
  🏆 週最佳：xxxx
  💀 週最差：xxxx
  💡 建議：
    ⚠️ 弱勢族群：xxx
    💎 強勢族群：xxx
    ⭐ 強效訊號：xxx
```

### 11.2 Streamlit App（PWA）

7 個 Tab：
- 🎯 Top5+候選
- 🏆 族群推薦
- 🌐 全市場族群
- 🇹🇼 台股篩選
- 🇹🇼 0050
- 🇺🇸 美股
- ⚠️ 警報

### 11.3 GitHub 紀錄檔（每日 commit）

```
dashboard_data.json       App 單一資料來源
ath_industry_report.json  全市場 ATH + 族群統計
top5_history.json         每日 Top 5 完整歷史（30 天）
daily_review.json         昨日選股回顧
weekly_review.json        週度策略檢討
marketcap_cache.json      1078 檔市值快取
tw_industry_map.json      1968 檔證交所產業對應
backtest_v?.json          各版本回測完整結果
stress_test_*.json        壓力測試結果
```

---

## 12. 回測歷程與績效

### 5 年回測（2021-01 ~ 2026-05，5.3 年）

| 版本 | 總報酬 | CAGR | PF | 勝率 | 交易數 |
|---|---|---|---|---|---|
| V1 | +165.7% | +20.1% | 2.11 | 40% | 170 |
| V2 | +161.4% | +19.7% | 2.11 | 36% | 150 |
| V3 | +209.9% | +23.6% | 3.94 | 49% | 98 |
| **V4** | **+226.8%** | **+24.8%** | 3.40 | 46% | 121 |

### 過去 6 個月（V4）

```
12 筆交易 / 7 勝 5 敗（58%）
平均 +34.41%
最佳：8110 華東 +178.5%
最差：3006 晶豪科 -21.8%
```

### 對標

| 策略 | CAGR |
|---|---|
| **V4 本策略** | **+24.8%** ⭐ |
| 0050 | ~+12% |
| SPY | ~+12% |
| QQQ | ~+15% |

---

## 13. 黑天鵝壓力測試

### 全期最大回撤

| | 5 年報酬 | 最大回撤 | 風報比 |
|---|---|---|---|
| **V4** | +226.8% | **-9.6%** ⭐ | **23.6** |
| 0050 | +258.2% | -33.8% | 7.6 |
| SPY | +110.8% | -24.5% | 4.5 |

### 五大黑天鵝事件實戰

| 事件 | 0050 | V4 行為 |
|---|---|---|
| 🔴 2022 升息熊市（9 個月） | -26.5% | **完全空手** ⭐ |
| 💥 2022 Q4 谷底月 | -3.1% | **完全空手** ⭐ |
| 🏦 2023 Mar SVB 危機 | +1.9% | +35.5%（抓反彈）⭐ |
| 💴 2024 Aug 日圓拆倉 | -8.4% | -6.2%（trailing stop 控損） |
| 💸 2025 Apr 關稅恐慌 | -4.4% | **完全空手** ⭐ |

**核心**：5 次黑天鵝中 **3 次完全規避，1 次抓反彈，1 次小受傷**

---

## 14. 自動壓力測試（每次回測必跑）

> **規則**：未來開發任何新策略版本，回測時都會**自動**跑黑天鵝壓力測試

### 機制

```python
import backtest_strategy as bs
cash, trades = my_new_backtest()
bs.report(cash, trades, label="V5 my_idea")
# ↑ 自動觸發 stress_test_lib.run_stress_test()
```

### 自動產出的壓力測試結果

```
🛡️ 黑天鵝壓力測試 — V5 label

📉 全期最大回撤：策略 vs 0050 vs SPY 對比

💀 五大黑天鵝事件：
  🔴 2022 升息熊市
  💥 2022 Q4 谷底月
  🏦 2023 Mar SVB 危機
  💴 2024 Aug 日圓拆倉
  💸 2025 Apr 關稅恐慌

🔥 風險指標：
  最長連虧次數
  虧損 >10% 的交易佔比

🏆 抗風險評等：
  ⭐⭐⭐⭐⭐ 卓越（≤10%）
  ⭐⭐⭐⭐ 優秀（≤15%）
  ⭐⭐⭐ 中等（≤25%）
  ⭐⭐ 偏弱（>25%）
```

**模組**：`stress_test_lib.py`（被 `backtest_strategy.report()` 自動呼叫）

---

## 15. 檔案結構

```
my-stock-bot/
├── STRATEGY.md                ← 本文件
│
├── 🎯 生產邏輯
│   ├── industry_ath_yf.py     ← V4 主邏輯（核心）
│   ├── daily_scan.py          ← 完整日報整合
│   ├── intraday_scan.py       ← 盤中掃描
│   ├── weekly_review.py       ← 盤後策略檢討
│   ├── daily_review.py        ← 昨日選股回顧
│   ├── tw_0050_signal.py      ← 0050 進出
│   ├── market_regime_alert.py ← SPY/0050 體制警報
│   ├── notify_line.py         ← LINE 推播
│   ├── industry_map_loader.py ← 證交所產業對應
│   └── shioaji_data.py        ← 永豐 API（待 CA 啟用）
│
├── 📊 回測 + 壓力測試
│   ├── backtest_strategy.py   (V1 全市場)
│   ├── backtest_tech_only.py  (V2 科技限定)
│   ├── backtest_v3.py         (V3 三濾網)
│   ├── backtest_v4.py         (V4 + 體制濾網) ⭐
│   ├── stress_test_lib.py     (壓力測試模組)
│   ├── stress_test.py         (壓力測試 standalone)
│   ├── analyze_marketcap.py   (市值門檻分析)
│   └── *.json                 (回測 + 壓力測試結果)
│
├── 🤖 自動化
│   └── .github/workflows/
│       ├── daily.yml             （台北 05:30）
│       ├── intraday.yml          （台北 09:00-13:30 每 30 分）
│       └── post_close_review.yml （台北 14:30 盤後）
│
└── 📱 Streamlit App
    └── streamlit_app/
        ├── app.py                ← 7-tab PWA
        └── requirements.txt
```

---

## 16. 可調參數

修改 `industry_ath_yf.py` 即可調整：

```python
# 篩選族群（可加減）
ALLOWED_INDUSTRIES = {
    "半導體", "電子零組件", "光電", "電腦及週邊",
    "電子通路", "通信網路", "其他電子",
}

# 市值門檻（億 NT$）
MIN_MCAP_BILLIONS = 100        # 50/200/500 各有取捨

# ATH 判定（距 2y 高的容差）
EXACT_THRESHOLD = 0.999        # 0.99 較寬，0.9995 較嚴

# 美股加分權重
US_SECTOR_BOOST = {
    "QQQ": {"bonus": 5, ...},
    "SMH": {"bonus": 10, ...},
    "IGV": {"bonus": 10, ...},
}

# 動能門檻（決定 ⭐⭐⭐ 入選）
TIER1_THRESHOLD = 80           # 75 較寬，85 較嚴

# 最強族群門檻
MIN_INDUSTRY_STOCKS = 3        # 該族群至少 3 檔合格
MIN_BULLISH_RATIO = 0.5        # 多頭比 50%
```

---

## 17. 改策略決策流程

```
[每日 14:30 看 LINE 盤後檢討]
       │
       ▼
週勝率 < 45%？─Yes─► 暫停 1 週 / 提高動能門檻 80→85 / 檢查 0050 體制
       │ No
       ▼
族群連 3 天虧損？─Yes─► 從 ALLOWED_INDUSTRIES 移除
       │ No
       ▼
訊號連 5 次失敗？─Yes─► 降低該訊號 Tier 權重
       │ No
       ▼
重大改動 ─► backtest_v?.py 套新規則回測
       │
       ▼
PF/CAGR 提升 + 最大回撤可接受？
       │
       ├ Yes ─► commit 上線（V5/V6...）
       └ No  ─► 棄用，繼續觀察
```

---

## 📞 常用指令

```bash
# 立刻跑一次 V4
python industry_ath_yf.py

# 跑 V4 回測（會自動跑壓力測試）
python backtest_v4.py

# 跑單獨壓力測試
python stress_test.py

# 抓最新市值
python -c "import json,yfinance as yf,time;u=json.load(open('tw_universe.json',encoding='utf-8'));m={};[m.update({s['code']:yf.Ticker(s['code']+'.TW').info.get('marketCap',0)/1e8}) or time.sleep(0.15) for s in u['stocks']];json.dump(m,open('marketcap_cache.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)"

# 手動觸發 GitHub Actions
# https://github.com/dogking0628-dotcom/my-stock-bot/actions
```

---

## 🎯 接下來的觀察項目

1. ✅ V4 上線運行 1-2 週累積實戰數據
2. ✅ 每日 14:30 看盤後檢討 LINE
3. ⏳ 累積 ≥10 筆實戰交易後評估是否需要 V5
4. ⏳ 永豐 CA 開通後切回 Shioaji（資料更穩定）

---

## 📌 給手機 Claude Code 的指令範本

當你在手機透過 Claude Code 接手繼續開發時，可以這樣開頭：

```
我有一個台股 + 美股自動化交易系統，repo:
https://github.com/dogking0628-dotcom/my-stock-bot

請先讀 STRATEGY.md 了解現有邏輯，目前是 V4 版本，
CAGR +24.8% / 最大回撤 -9.6%。

我想做以下改動：[具體說明]
請先 git pull，改完跑 backtest 確認 PF/CAGR/最大回撤都不退步，
通過再 commit + push。
```

---

## 📡 給「監控 APP 開發」新 session 的指令範本

當你在新 session 開發 / 擴充 `monitor_app/`（手機 PWA 監控介面）時，可以這樣開頭：

```
我要在 https://github.com/dogking0628-dotcom/my-stock-bot
擴充監控 APP，code 在 monitor_app/ 子目錄。

請先 git pull，讀以下檔案了解上下文：
1. STRATEGY.md（V4 策略邏輯，CAGR +24.8% / MDD -9.6%）
2. monitor_app/README.md（監控 APP 現狀與待辦）
3. monitor_app/app.py（目前骨架）
4. dashboard_data.json（資料來源 schema，由 daily_scan.py 產出）

我要新增以下功能：[具體需求，例如：
  - 讀 holdings.json 顯示持股對應出場警報
  - 加美股 Top 5 / 出場 tab
  - 加 0050 進出建議顯示
  - 加 streamlit-autorefresh 自動刷新
  - LINE webhook 推送（用 st.secrets）
]

完成後在 monitor_app/ 內 commit + push 到 [指定分支]。
不要動 monitor_app/ 以外的檔案，除非有明確理由。
```

### 監控 APP 架構約定

| 項目 | 說明 |
|---|---|
| 資料來源 | `https://raw.githubusercontent.com/.../main/dashboard_data.json`（5 分快取） |
| 不直接抓 yfinance | 避免 Streamlit Cloud 觸發 rate limit；資料更新交給 GitHub Actions |
| 不寫策略邏輯 | 監控 APP 只負責「呈現」，策略改動仍走 `industry_ath_yf.py` + `backtest_v?.py` |
| 部署 | Streamlit Cloud，main file 填 `monitor_app/app.py` |
| 主題 | 沿用 `.streamlit/config.toml`（深色） |
