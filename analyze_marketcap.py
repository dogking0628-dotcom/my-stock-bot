# -*- coding: utf-8 -*-
"""分析回測交易在不同市值級距的勝率，找最佳市值門檻"""
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import yfinance as yf
from collections import defaultdict

with open("backtest_result.json", encoding="utf-8") as f:
    bt = json.load(f)
trades = bt["trades"]
unique_tickers = sorted(set(t["ticker"] for t in trades))
print(f"分析 {len(trades)} 筆交易，{len(unique_tickers)} 檔不重複股票...")

# 抓市值（current）
mcap = {}
for i, code in enumerate(unique_tickers):
    if i % 20 == 0:
        print(f"  [{i}/{len(unique_tickers)}] 抓市值...")
    try:
        info = yf.Ticker(f"{code}.TW").info
        m = info.get("marketCap")
        if m: mcap[code] = m / 1e8  # 轉成「億」
    except Exception:
        pass
    time.sleep(0.3)

print(f"\n抓到 {len(mcap)} 檔市值\n")

# 市值級距
TIERS = [(0, 50, "< 50億"), (50, 100, "50-100億"),
         (100, 300, "100-300億"), (300, 1000, "300-1000億"),
         (1000, 1e9, ">= 1000億")]

bucket = defaultdict(list)
for t in trades:
    m = mcap.get(t["ticker"])
    if m is None:
        bucket["未知"].append(t); continue
    for lo, hi, name in TIERS:
        if lo <= m < hi:
            bucket[name].append(t); break

print(f"{'市值':<14} {'交易':>4} {'勝率':>8} {'平均':>8} {'平均獲':>8} {'平均虧':>8} {'PF':>6}")
print("─" * 70)
for _, _, name in TIERS:
    ts = bucket.get(name, [])
    if not ts: continue
    n = len(ts); wins = [x for x in ts if x["ret_pct"] > 0]
    losses = [x for x in ts if x["ret_pct"] <= 0]
    wr = len(wins)/n*100
    avg = sum(x["ret_pct"] for x in ts)/n
    avg_w = sum(x["ret_pct"] for x in wins)/len(wins) if wins else 0
    avg_l = sum(x["ret_pct"] for x in losses)/len(losses) if losses else 0
    pf = abs(sum(x["ret_pct"] for x in wins) /
             sum(x["ret_pct"] for x in losses)) if losses else 999
    print(f"{name:<12} {n:>4}  {wr:>6.0f}% {avg:>+7.1f}% {avg_w:>+7.1f}% {avg_l:>+7.1f}% {pf:>5.2f}")

# 累積分析（≥X 億）
print("\n累積分析（保留 >= X 億）:")
print(f"{'門檻':<14} {'交易':>4} {'勝率':>8} {'平均':>8} {'總報酬':>8}")
print("─" * 60)
INITIAL = 1_000_000; PER = INITIAL/5
for thresh in [0, 50, 100, 200, 300, 500, 1000]:
    ts = [t for t in trades if mcap.get(t["ticker"], 0) >= thresh]
    if not ts: continue
    n = len(ts)
    wr = sum(1 for x in ts if x["ret_pct"]>0)/n*100
    avg = sum(x["ret_pct"] for x in ts)/n
    # 累積報酬（按時間順序加總）
    sim_cash = INITIAL
    for x in sorted(ts, key=lambda x: x["entry_date"]):
        sim_cash *= (1 + x["ret_pct"]/100 * (PER/INITIAL))
    total_ret = (sim_cash/INITIAL - 1)*100
    print(f">= {thresh:>4} 億   {n:>4}  {wr:>6.0f}% {avg:>+7.2f}% {total_ret:>+7.1f}%")
