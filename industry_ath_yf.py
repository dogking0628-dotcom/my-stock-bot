# -*- coding: utf-8 -*-
"""
yfinance 全市場掃 2 年還原月線 ATH，按族群統計
（永豐 kbars 需要 CA，先用 yfinance 替代）
"""
import sys, io, os, json, datetime as dt, time
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

import numpy as np
import yfinance as yf
from collections import defaultdict
# 改用證交所官方產業分類（覆蓋全市場 1968 檔）
from industry_map_loader import get_industry

NEAR_THRESHOLD = 0.95
EXACT_THRESHOLD = 0.999
BATCH = 50
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")
TXT_PATH = os.path.join(os.path.dirname(__file__), "scan_output.txt")


def load_universe():
    with io.open("tw_universe.json", encoding="utf-8") as f:
        u = json.load(f)
    return [(s["code"], s["name"]) for s in u["stocks"]]


def monthly_max_close(closes_series):
    """closes_series: pandas Series with DatetimeIndex"""
    if len(closes_series) < 30:
        return None, None
    today_close = float(closes_series.iloc[-1])
    today_ym = dt.date.today().strftime("%Y-%m")
    by_month = {}
    for ts, c in closes_series.items():
        ym = ts.strftime("%Y-%m")
        by_month[ym] = float(c)
    historical = [v for ym, v in by_month.items() if ym < today_ym]
    if not historical:
        return today_close, None
    return today_close, max(historical)


def main():
    universe = load_universe()
    print(f"[1/2] universe: {len(universe)} 檔")

    results = []
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i+BATCH]
        # 先試 .TW，failed 再試 .TWO
        codes = [f"{c}.TW" for c, _ in batch]
        try:
            df = yf.download(" ".join(codes), period="2y",
                             auto_adjust=True, progress=False, threads=True,
                             group_by="ticker")
        except Exception as e:
            print(f"  batch {i} download fail: {e}")
            time.sleep(2)
            continue

        for code, name in batch:
            yfc = f"{code}.TW"
            try:
                if yfc not in df.columns.get_level_values(0):
                    continue
                cl = df[yfc]["Close"].dropna()
                if len(cl) < 100:
                    continue
                today, mmax = monthly_max_close(cl)
                if today is None or mmax is None or mmax <= 0:
                    continue
                ratio = today / mmax
                bullish = None
                if len(cl) >= 200:
                    ma20 = cl.iloc[-20:].mean()
                    ma60 = cl.iloc[-60:].mean()
                    ma200 = cl.iloc[-200:].mean()
                    bullish = bool(today > ma20 > ma60 > ma200)
                results.append({
                    "ticker": code, "name": name,
                    "today": today, "monthly_max_2y": mmax,
                    "ratio": ratio, "from_high_pct": (ratio - 1) * 100,
                    "bullish": bullish, "industry": get_industry(code),
                })
            except Exception:
                continue

        if (i // BATCH) % 5 == 0:
            print(f"  [{i+BATCH}/{len(universe)}] 已分析 {len(results)} 檔")
        time.sleep(1)  # 避免 yfinance rate limit

    print(f"\n[2/2] 完成，共 {len(results)} 檔有效")

    exact = sorted([r for r in results if r["ratio"] >= EXACT_THRESHOLD],
                   key=lambda x: -x["ratio"])
    near = sorted([r for r in results if r["ratio"] >= NEAR_THRESHOLD],
                  key=lambda x: -x["ratio"])

    lines = []
    def p(s=""):
        print(s); lines.append(s)

    p("\n" + "=" * 60)
    p(f"🔥 真正創 2y 月線新高（>=99.9%）：{len(exact)} 檔")
    p("=" * 60)
    by_ind = defaultdict(list)
    for r in exact:
        by_ind[r["industry"] or "未分類"].append(r)
    for ind, items in sorted(by_ind.items(), key=lambda x: -len(x[1])):
        bn = sum(1 for x in items if x["bullish"])
        p(f"  {ind}: {len(items)} 檔（多頭排列 {bn}）")
    p()
    for r in exact[:50]:
        b = "多頭" if r["bullish"] else "    "
        ind = r["industry"] or "未分類"
        p(f"  {r['ticker']} {r['name']:<10} {ind:<8} {r['from_high_pct']:+6.2f}%  ${r['today']:>8.1f}  {b}")

    p("\n" + "=" * 60)
    p(f"🟡 接近 2y 月線新高（>=95%）：{len(near)} 檔")
    p("=" * 60)
    by_ind = defaultdict(list)
    for r in near:
        by_ind[r["industry"] or "未分類"].append(r)
    ranked = sorted(by_ind.items(), key=lambda x: -len(x[1]))
    p("\n📊 族群統計（接近 2y 月線高 5% 內）：")
    for ind, items in ranked[:20]:
        bn = sum(1 for x in items if x["bullish"])
        p(f"  {ind}: {len(items)} 檔（多頭 {bn}）")
    if ranked:
        p(f"\n🏆 族群最多：{ranked[0][0]}（{len(ranked[0][1])} 檔）")

    p("\n📋 Top 30 接近高點個股：")
    for r in near[:30]:
        b = "多頭" if r["bullish"] else "    "
        ind = r["industry"] or "未分類"
        p(f"  {r['ticker']} {r['name']:<10} {ind:<8} {r['from_high_pct']:+6.2f}%  ${r['today']:>8.1f}  {b}")

    out = {
        "timestamp": dt.date.today().isoformat(),
        "basis": "yfinance 2y monthly auto-adjust",
        "total_analyzed": len(results),
        "exact_ath": exact,
        "near_ath_top30": near[:30],
        "industry_stats": [{"industry": ind, "count": len(items),
             "bullish_count": sum(1 for x in items if x["bullish"])}
            for ind, items in ranked],
        "top_industry": ranked[0][0] if ranked else None,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(TXT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n💾 輸出 {OUTPUT_PATH} / {TXT_PATH}")


if __name__ == "__main__":
    main()
