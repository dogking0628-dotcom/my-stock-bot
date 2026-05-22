# -*- coding: utf-8 -*-
"""
熱錢續航力分析（用戶觀察）：
「漲停價買了，隔天修正都很少，或是又漲超過 5%」

量化定義：
- 漲停日 = change_pct >= 9.5%
- 隔日續強 = 收盤 >= 漲停價 × 0.95 (修正 < 5%) 或 >= ×1.05 (續漲)
- 真熱錢 = 出現過漲停 + 隔日續強 + 後續維持
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np

# 候選股（電子零組件熱錢 + 半導體對照組）
candidates = [
    # 電子零組件（系統推薦）
    ("3090", "日電貿", "電子零組件"),
    ("2478", "大毅", "電子零組件"),
    ("2327", "國巨*", "電子零組件"),
    ("6924", "榮惠-KY", "電子零組件"),
    ("2492", "華新科", "電子零組件"),
    ("3026", "禾伸堂", "電子零組件"),
    # 光電 / 電腦及週邊（同樣升溫族群）
    ("3481", "群創", "光電"),
    ("4916", "事欣科", "電腦及週邊"),
    # 半導體對照組（V4 推但 hot money 標假突破）
    ("4919", "新唐", "半導體"),
    ("3532", "台勝科", "半導體"),
    ("3711", "日月光投控", "半導體"),
    ("7769", "鴻勁", "半導體"),
]

tickers = [f"{c[0]}.TW" for c in candidates]
print(f"抓 {len(tickers)} 檔近 20 天日 K + 量...")
data = yf.download(" ".join(tickers), period="25d", group_by="ticker",
                   auto_adjust=True, progress=False, threads=True)


def analyze_continuation(df):
    df = df.dropna(subset=["Close"])
    if len(df) < 5:
        return None
    closes = df["Close"].values
    opens = df["Open"].values
    vols = df["Volume"].values

    # 計算每日漲跌幅
    changes = np.diff(closes) / closes[:-1] * 100
    # 對應日期（第 i+1 天的漲跌 = changes[i]）

    limit_up_days = []  # 漲停日的 idx (in changes)
    for i, c in enumerate(changes):
        if c >= 9.0:  # 接近漲停（含 9% 以上）
            limit_up_days.append(i)

    # 對每個漲停日，看隔日表現
    continuation_events = []
    for idx in limit_up_days:
        # idx 是 changes 的 index = closes[idx+1] 是漲停當天收盤
        limit_up_close = closes[idx + 1]
        if idx + 2 >= len(closes):  # 沒有隔日資料
            continue
        next_day_close = closes[idx + 2]
        change_pct = (next_day_close / limit_up_close - 1) * 100
        # 後續 3 天高點 vs 漲停價
        future_window = closes[idx+2:min(idx+5, len(closes))]
        max_future = max(future_window) if len(future_window) > 0 else next_day_close
        future_max_pct = (max_future / limit_up_close - 1) * 100

        if change_pct >= 5:
            event_type = "🚀 隔日續漲 5%+"
        elif change_pct >= 0:
            event_type = "✅ 隔日小漲/平盤"
        elif change_pct >= -5:
            event_type = "🟡 隔日小修 <5%"
        elif change_pct >= -10:
            event_type = "⚠️ 隔日修 5-10%"
        else:
            event_type = "❌ 隔日重挫 >10%"

        continuation_events.append({
            "limit_up_idx": idx + 1,
            "next_day_change": round(change_pct, 2),
            "future_3d_max": round(future_max_pct, 2),
            "event_type": event_type,
        })

    # 計算續航分數
    if not continuation_events:
        cont_score = 0
        verdict = "📉 近 N 天無漲停（不算熱錢主流）"
    else:
        # 隔日漲幅 × 後續高點
        positive = sum(1 for e in continuation_events if e["next_day_change"] >= -5)
        cont_score = positive / len(continuation_events) * 100
        if cont_score >= 80:
            verdict = "🔥 熱錢續航強（漲停後不修正）"
        elif cont_score >= 50:
            verdict = "✅ 熱錢續航中"
        elif cont_score >= 30:
            verdict = "🟡 續航力一般"
        else:
            verdict = "❌ 漲停後常修正（不是真熱錢）"

    return {
        "today_close": float(closes[-1]),
        "limit_up_count": len(continuation_events),
        "events": continuation_events,
        "continuation_score": round(cont_score, 0),
        "verdict": verdict,
    }


print("\n" + "=" * 80)
print("📊 熱錢續航力分析（漲停後隔日表現）")
print("=" * 80)
print(f"{'代號':<5} {'名稱':<10} {'族群':<8} {'今價':>7} {'漲停數':>5} {'續航分':>5} 評語")
print("-" * 100)

results = []
for code, name, ind in candidates:
    t = f"{code}.TW"
    try:
        df = data[t] if len(tickers) > 1 else data
        r = analyze_continuation(df)
        if r:
            results.append({"code": code, "name": name, "ind": ind, **r})
            print(f"{code:<5} {name:<10} {ind:<8} {r['today_close']:>7.1f} "
                  f"{r['limit_up_count']:>5} {r['continuation_score']:>4.0f}% {r['verdict']}")
            # 詳細事件
            for e in r["events"]:
                print(f"     漲停後第{e['limit_up_idx']}天 → 隔日 {e['next_day_change']:+.2f}% / "
                      f"3日內高 {e['future_3d_max']:+.2f}% {e['event_type']}")
    except Exception as e:
        print(f"{code:<5} ❌ {type(e).__name__}: {str(e)[:50]}")

# 族群續航統計
print("\n" + "=" * 80)
print("📊 族群續航力對比")
print("=" * 80)
from collections import defaultdict
by_ind = defaultdict(list)
for r in results:
    by_ind[r["ind"]].append(r)
for ind, lst in by_ind.items():
    avg_score = sum(x["continuation_score"] for x in lst) / len(lst)
    total_lu = sum(x["limit_up_count"] for x in lst)
    print(f"  {ind:<10}  漲停總數 {total_lu:>2}  平均續航 {avg_score:>4.0f}%  ({len(lst)} 檔)")

print()
print("🎯 真熱錢排行（續航分 ≥ 50% 且有漲停）：")
real = [r for r in results if r["continuation_score"] >= 50 and r["limit_up_count"] >= 1]
real.sort(key=lambda x: (-x["continuation_score"], -x["limit_up_count"]))
for r in real:
    print(f"  {r['code']} {r['name']} ({r['ind']}) "
          f"漲停 {r['limit_up_count']} 次 / 續航 {r['continuation_score']:.0f}%")

with open("continuation_analysis.json", "w", encoding="utf-8") as f:
    json.dump({"results": results}, f, ensure_ascii=False, indent=2, default=str)
print("\n💾 已輸出 continuation_analysis.json")
