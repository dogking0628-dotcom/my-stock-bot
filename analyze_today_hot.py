#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日熱錢族群 + 未來 5 天接棒預測 + 個股韌性排序

加入用戶新指標：
- 3 日拉回 < 5%  = 超強勢（資金黏性最高）
- 3 日拉回 < 10% = 強勢（用戶定義）
- 拉回越小，未來 N 天再漲機率越高

輸出：
- 今日最熱族群（韌性 × 動能）
- 5 天接棒梯度預測
- Top 10 個股（按綜合分排序）
"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── 載入系統訊號 ──
with open(os.path.join(ROOT, "hot_money_signal.json"), "r", encoding="utf-8") as f:
    signal = json.load(f)
with open(os.path.join(ROOT, "ath_industry_report.json"), "r", encoding="utf-8") as f:
    report = json.load(f)

# ── 候選池 ──
# 1. rotation_picks (接棒族群真突破 Top 6)
# 2. V4 tomorrow_top5 (半導體對照組)
# 3. 加上電腦及週邊 / 光電 高分股
candidates = []

# Rotation picks
for p in signal.get("rotation_picks", [])[:6]:
    candidates.append({
        "ticker": p["ticker"], "name": p["name"], "industry": p["industry"],
        "from_today": p["today"], "tag": "🔥 接棒"
    })

# V4 picks (半導體對照)
for p in report.get("tomorrow_top5", []):
    candidates.append({
        "ticker": p["ticker"], "name": p["name"], "industry": p["industry"],
        "from_today": p["today"], "tag": "⚠️ V4(半導體)"
    })

# 加電腦及週邊 / 光電 高分股（從 exact_ath）
seen = {c["ticker"] for c in candidates}
hot_inds = {"電腦及週邊", "光電", "化學工業"}
extras = [r for r in report.get("exact_ath", [])
          if r.get("industry") in hot_inds and r.get("bullish") and r["ticker"] not in seen]
extras.sort(key=lambda x: -x["ratio"])
for r in extras[:8]:
    candidates.append({
        "ticker": r["ticker"], "name": r["name"], "industry": r["industry"],
        "from_today": r["today"], "tag": "🔥 接棒"
    })

print(f"候選池：{len(candidates)} 檔")
for c in candidates:
    print(f"  {c['ticker']} {c['name']:<8} {c['industry']:<6} {c['tag']}")
print()

# ── yfinance 抓最近 15 天日 K ──
tickers_tw = [f"{c['ticker']}.TW" for c in candidates]
print(f"抓 yfinance 最近 15 天...")
data = yf.download(" ".join(tickers_tw), period="20d", group_by="ticker",
                   auto_adjust=True, progress=False, threads=True)

# ── 對每檔算韌性 + 量能 ──
def analyze_resilience(df):
    """回傳：3日拉回%, 5日拉回%, 量增比, 是否破20MA, 連漲天數

    正確「拉回」定義：close-to-close 從區間峰值的最大滑落
    （用戶說「3 天修正不超過 10%」= 收盤跌幅不超過 10%）
    """
    df = df.dropna(subset=["Close"])
    if len(df) < 10:
        return None
    closes = df["Close"].values
    vols = df["Volume"].values
    today = float(closes[-1])

    def max_dd_n(prices, n):
        """N 天內 close-to-close 最大回檔（從區間峰值滑落幅度，負值）"""
        window = prices[-n:]
        peak = window[0]
        max_dd = 0.0
        for c in window:
            if c > peak:
                peak = c
            dd = (c / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd
        return max_dd

    pullback_3d = max_dd_n(closes, 3)
    pullback_5d = max_dd_n(closes, 5)
    pullback_10d = max_dd_n(closes, 10)

    # 從近 10 天最高收盤的距離
    high_10d = max(closes[-10:])
    from_recent_high = (today / high_10d - 1) * 100

    # 5 日量增比 = 近 5 日均量 / 前 5 日均量
    if len(vols) >= 10:
        recent_vol = vols[-5:].mean()
        prior_vol = vols[-10:-5].mean()
        vol_growth = (recent_vol / prior_vol - 1) * 100 if prior_vol > 0 else 0
    else:
        vol_growth = 0

    # MA20
    if len(closes) >= 20:
        ma20 = closes[-20:].mean()
        above_ma20 = today > ma20
    else:
        above_ma20 = True

    # 連續上漲天數
    rising = 0
    for i in range(len(closes)-1, 0, -1):
        if closes[i] > closes[i-1]:
            rising += 1
        else:
            break

    return {
        "today": float(today),
        "pullback_3d": round(pullback_3d, 2),
        "pullback_5d": round(pullback_5d, 2),
        "pullback_10d": round(pullback_10d, 2),
        "from_recent_high": round(from_recent_high, 2),
        "vol_growth_pct": round(vol_growth, 1),
        "above_ma20": bool(above_ma20),
        "consecutive_rising_days": rising,
    }


print("\n=== 分析韌性 ===")
results = []
for c in candidates:
    t = f"{c['ticker']}.TW"
    try:
        if len(tickers_tw) > 1:
            if t not in data.columns.get_level_values(0):
                print(f"  ❌ {c['ticker']} 無資料")
                continue
            df = data[t]
        else:
            df = data
        r = analyze_resilience(df)
        if r:
            results.append({**c, **r})
    except Exception as e:
        print(f"  ❌ {c['ticker']} {type(e).__name__}: {str(e)[:50]}")

# ── 綜合分：韌性 × 量能 ──
def composite_score(r):
    """
    強勢度公式（用戶 + 我系統）：
    - 拉回越小越好（拉回 -5% → 滿分 100，-10% → 50 分，-15% → 0 分）
    - 量能增 → 加分
    - 上 MA20 → +20
    - 連漲 → +10/天 (max 50)
    """
    p3 = r.get("pullback_3d", -100)
    p5 = r.get("pullback_5d", -100)
    vol = r.get("vol_growth_pct", 0)
    above = r.get("above_ma20", False)
    rising = r.get("consecutive_rising_days", 0)

    # 韌性分（拉回小）
    resilience = max(0, 100 + p3 * 10)  # p3=-5% → 50, p3=-10% → 0
    # 中期韌性
    resilience5 = max(0, 100 + p5 * 5)
    score = (resilience * 0.5 + resilience5 * 0.3 +
             min(vol, 100) * 0.1 + (20 if above else 0) + min(rising, 5) * 5)
    return round(score, 1)


for r in results:
    r["score"] = composite_score(r)
results.sort(key=lambda x: -x["score"])

# ── 族群韌性統計 ──
by_ind = {}
for r in results:
    by_ind.setdefault(r["industry"], []).append(r)

print("\n" + "=" * 70)
print("📊 族群韌性對比（按平均強勢分）")
print("=" * 70)
ind_stats = []
for ind, items in by_ind.items():
    avg_score = sum(x["score"] for x in items) / len(items)
    avg_p3 = sum(x["pullback_3d"] for x in items) / len(items)
    avg_p5 = sum(x["pullback_5d"] for x in items) / len(items)
    above_count = sum(1 for x in items if x["above_ma20"])
    ind_stats.append({
        "industry": ind, "n": len(items),
        "avg_score": round(avg_score, 1),
        "avg_pullback_3d": round(avg_p3, 2),
        "avg_pullback_5d": round(avg_p5, 2),
        "above_ma20_ratio": round(above_count / len(items), 2),
    })
ind_stats.sort(key=lambda x: -x["avg_score"])
print(f"{'族群':<10} {'檔數':>3} {'分數':>6} {'3日拉回':>8} {'5日拉回':>8} {'>MA20':>6}")
print("-" * 70)
for s in ind_stats:
    print(f"{s['industry']:<10} {s['n']:>3} {s['avg_score']:>6.1f} "
          f"{s['avg_pullback_3d']:>+7.2f}% {s['avg_pullback_5d']:>+7.2f}% "
          f"{int(s['above_ma20_ratio']*100):>4}%")

print("\n" + "=" * 70)
print("🎯 個股強勢排行 TOP 12")
print("=" * 70)
print(f"{'代號':<5} {'名稱':<10} {'族群':<8} {'分':>5} {'3拉':>6} {'5拉':>6} {'10拉':>6} {'量增':>6} {'連漲':>4} {'標記'}")
print("-" * 100)
for r in results[:15]:
    print(f"{r['ticker']:<5} {r['name']:<10} {r['industry']:<8} "
          f"{r['score']:>5.1f} {r['pullback_3d']:>+5.2f}% {r['pullback_5d']:>+5.2f}% "
          f"{r['pullback_10d']:>+5.2f}% {r['vol_growth_pct']:>+5.0f}% "
          f"{r['consecutive_rising_days']:>3}d {r['tag']}")

# ── 輸出 JSON ──
out = {
    "timestamp": datetime.now().isoformat()[:10],
    "industry_resilience": ind_stats,
    "stocks_ranked": results,
}
with open(os.path.join(ROOT, "hot_money_resilience.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n💾 已輸出 hot_money_resilience.json")
