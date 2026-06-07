# -*- coding: utf-8 -*-
"""5 月選股完整檢討"""
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import yfinance as yf
from collections import defaultdict

with open("top5_history.json", encoding="utf-8") as f:
    h = json.load(f)
recs = h.get("records", [])

# 收集所有 ticker 取最新價
all_tickers = sorted(set(p["ticker"] for r in recs for p in r.get("picks", [])))
print(f"5 月選股檢討")
print(f"共 {len(recs)} 個交易日，{len(all_tickers)} 檔不重複股票")
print(f"日期區間：{recs[0]['date']} ~ {recs[-1]['date']}")
print()
print(f"抓 {len(all_tickers)} 檔最新報價...")
prices = {}
yf_codes = " ".join(f"{t}.TW" for t in all_tickers)
try:
    df = yf.download(yf_codes, period="3d", auto_adjust=True,
                    progress=False, threads=True, group_by="ticker")
    for t in all_tickers:
        try:
            yfc = f"{t}.TW"
            sub = df[yfc] if len(all_tickers) > 1 else df
            cl = sub["Close"].dropna()
            if len(cl) > 0:
                prices[t] = float(cl.iloc[-1])
        except Exception:
            pass
except Exception as e:
    print(f"批次失敗，改逐檔: {e}")

print(f"成功抓到 {len(prices)} 檔\n")

# 計算每筆績效
all_picks = []
for r in recs:
    for p in r.get("picks", []):
        latest = prices.get(p["ticker"])
        if latest is None or p.get("rec_close", 0) <= 0:
            continue
        ret = (latest / p["rec_close"] - 1) * 100
        all_picks.append({
            "date": r["date"],
            "ticker": p["ticker"],
            "name": p["name"],
            "industry": p.get("industry") or "?",
            "rec_close": p["rec_close"],
            "current": latest,
            "ret_pct": ret,
            "hit": ret > 0,
            "score": p.get("momentum_score", 0),
            "tier": p.get("tier", "⭐"),
            "notes": p.get("momentum_notes", []),
        })

# 整體統計
n = len(all_picks)
wins = [p for p in all_picks if p["hit"]]
losses = [p for p in all_picks if not p["hit"]]
print("=" * 70)
print(f"📊 整體統計（{recs[0]['date']} ~ 5/12 today）")
print("=" * 70)
print(f"  總推薦次數: {n} 筆")
print(f"  勝率: {len(wins)/n*100:.1f}% ({len(wins)} 勝 / {len(losses)} 敗)")
print(f"  平均報酬: {sum(p['ret_pct'] for p in all_picks)/n:+.2f}%")
if wins:
    print(f"  平均獲利: {sum(p['ret_pct'] for p in wins)/len(wins):+.2f}%")
if losses:
    print(f"  平均虧損: {sum(p['ret_pct'] for p in losses)/len(losses):+.2f}%")
if wins and losses:
    pf = abs(sum(p['ret_pct'] for p in wins) / sum(p['ret_pct'] for p in losses))
    print(f"  獲利因子 PF: {pf:.2f}")

# 最佳/最差
best = max(all_picks, key=lambda x: x["ret_pct"])
worst = min(all_picks, key=lambda x: x["ret_pct"])
print(f"\n🏆 最佳: {best['ticker']} {best['name']} ({best['industry']}) "
      f"{best['ret_pct']:+.2f}% ({best['date']} 推薦)")
print(f"💀 最差: {worst['ticker']} {worst['name']} ({worst['industry']}) "
      f"{worst['ret_pct']:+.2f}% ({worst['date']} 推薦)")

# 各日表現
print("\n" + "=" * 70)
print("📅 各日詳細表現")
print("=" * 70)
for r in recs:
    day = [p for p in all_picks if p["date"] == r["date"]]
    if not day: continue
    dw = sum(1 for p in day if p["hit"])
    davg = sum(p["ret_pct"] for p in day) / len(day)
    print(f"\n{r['date']} ({r.get('industry','?')}) "
          f"勝率 {dw}/{len(day)} ({dw/len(day)*100:.0f}%) 平均 {davg:+.2f}%:")
    for p in sorted(day, key=lambda x: -x["ret_pct"]):
        em = "✅" if p["hit"] else "❌"
        print(f"  {em} {p['ticker']} {p['name']:<8} ({p['industry']:<8}) "
              f"{p['ret_pct']:+6.2f}% 動能{p['score']}/100 {p['tier']}")

# 族群表現
print("\n" + "=" * 70)
print("🏭 各族群表現")
print("=" * 70)
ind_stats = defaultdict(lambda: {"n":0,"wins":0,"ret_sum":0,"stocks":[]})
for p in all_picks:
    ind = p["industry"]
    ind_stats[ind]["n"] += 1
    ind_stats[ind]["ret_sum"] += p["ret_pct"]
    if p["hit"]: ind_stats[ind]["wins"] += 1
    ind_stats[ind]["stocks"].append(p["ticker"])
sorted_ind = sorted(ind_stats.items(), key=lambda x: -x[1]["ret_sum"]/x[1]["n"])
for ind, s in sorted_ind:
    avg = s["ret_sum"]/s["n"]
    wr = s["wins"]/s["n"]*100
    tag = "💎" if avg > 5 else ("✅" if avg > 0 else "⚠️")
    print(f"  {tag} {ind:<10} {s['n']:>2} 檔 ｜ 勝率 {wr:>4.0f}% ｜ 平均 {avg:>+6.2f}%"
          f"  ({', '.join(s['stocks'])})")

# 訊號表現
print("\n" + "=" * 70)
print("⭐ 訊號表現（按勝率）")
print("=" * 70)
sig_stats = defaultdict(lambda: {"n":0,"wins":0,"ret_sum":0})
for p in all_picks:
    for note in p["notes"]:
        sig_stats[note]["n"] += 1
        sig_stats[note]["ret_sum"] += p["ret_pct"]
        if p["hit"]: sig_stats[note]["wins"] += 1
sorted_sig = sorted(sig_stats.items(), key=lambda x: -x[1]["wins"]/max(x[1]["n"],1))
for sig, s in sorted_sig:
    if s["n"] == 0: continue
    avg = s["ret_sum"]/s["n"]
    wr = s["wins"]/s["n"]*100
    print(f"  {sig:<12} {s['n']:>2} 次 ｜ 勝率 {wr:>4.0f}% ｜ 平均 {avg:>+6.2f}%")

# Tier 校準
print("\n" + "=" * 70)
print("📏 Tier 校準（實際勝率 vs 預期）")
print("=" * 70)
tier_stats = defaultdict(lambda: {"n":0,"wins":0,"ret_sum":0})
for p in all_picks:
    t = p["tier"]
    tier_stats[t]["n"] += 1
    tier_stats[t]["ret_sum"] += p["ret_pct"]
    if p["hit"]: tier_stats[t]["wins"] += 1
expected = {"⭐⭐⭐":"85%", "⭐⭐":"70-85%", "⭐":"<70%"}
for t in ["⭐⭐⭐","⭐⭐","⭐"]:
    if t not in tier_stats: continue
    s = tier_stats[t]
    avg = s["ret_sum"]/s["n"]
    wr = s["wins"]/s["n"]*100
    print(f"  {t}: {s['n']:>2} 檔 ｜ 實際勝率 {wr:>4.0f}% ｜ 預期 {expected[t]} ｜ 平均 {avg:>+6.2f}%")
