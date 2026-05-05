# -*- coding: utf-8 -*-
"""
用永豐 Shioaji API 全市場掃 2 年新高，按族群統計
不做硬性過濾，純資訊呈現：把所有「接近 2y 高」的股票列出來，標示族群最多的
"""
import sys, io, os
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

import json, datetime as dt
import numpy as np
from collections import defaultdict

import shioaji_data as sd
from tw_breakout_filter import INDUSTRY_GROUPS, get_industry

NEAR_THRESHOLD = 0.95   # 距 2y 高 5% 內
EXACT_THRESHOLD = 0.999

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")


def analyze_one(code, name, exchange):
    # 只抓 2 年（夠算 2y ATH + MA200），避免 5 年資料量過大 timeout
    end = dt.date.today()
    start = end - dt.timedelta(days=365*2 + 30)
    bars = sd.fetch_kbars(code, start_date=start, end_date=end)
    if not bars or len(bars) < 200:
        return None
    closes = np.array([b["close"] for b in bars], dtype=float)
    today = float(closes[-1])
    last_504 = closes[-504:] if len(closes) >= 504 else closes[:-1]
    if len(last_504) < 100:
        return None
    hist_max = float(np.max(last_504))
    if hist_max <= 0:
        return None
    ratio = today / hist_max
    ma20 = closes[-20:].mean()
    ma60 = closes[-60:].mean()
    ma200 = closes[-200:].mean()
    bullish = today > ma20 > ma60 > ma200
    return {
        "ticker": code, "name": name, "exchange": exchange,
        "today": today, "hist_max_2y": hist_max, "ratio": ratio,
        "from_high_pct": (ratio - 1) * 100, "bullish": bullish,
        "industry": get_industry(code),
    }


def main():
    api = sd.get_api()
    if not api:
        print("❌ Shioaji 登入失敗，請確認 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY")
        sys.exit(1)

    print("[1/3] 取全市場股票清單...")
    all_stocks = sd.list_all_stocks()
    print(f"  共 {len(all_stocks)} 檔")

    print("[2/3] 逐檔抓 2y 日 K 計算距高 % ...")
    results = []
    err_samples = []
    for i, (code, name, ex) in enumerate(all_stocks):
        try:
            r = analyze_one(code, name, ex)
            if r:
                results.append(r)
            elif len(err_samples) < 5:
                err_samples.append(f"{code} {name}: 無資料或長度<200")
        except Exception as e:
            if len(err_samples) < 5:
                err_samples.append(f"{code} {name}: {type(e).__name__}: {e}")
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(all_stocks)}] 已分析 {len(results)} 檔")
            if err_samples:
                print(f"    錯誤樣本: {err_samples[:3]}")

    print(f"  完成，共 {len(results)} 檔有效資料")

    # 真正創 2y 高
    exact = [r for r in results if r["ratio"] >= EXACT_THRESHOLD]
    near = [r for r in results if r["ratio"] >= NEAR_THRESHOLD]
    near.sort(key=lambda x: -x["ratio"])
    exact.sort(key=lambda x: -x["ratio"])

    print("\n[3/3] 結果輸出\n")
    print("=" * 60)
    print(f"🔥 真正創 2y 新高（>=99.9%）：{len(exact)} 檔")
    print("=" * 60)
    by_ind = defaultdict(list)
    for r in exact:
        by_ind[r["industry"] or "其他"].append(r)
    for ind, items in sorted(by_ind.items(), key=lambda x: -len(x[1])):
        bull_n = sum(1 for x in items if x["bullish"])
        print(f"  {ind}: {len(items)} 檔（多頭 {bull_n}）")
    print()
    for r in exact[:50]:
        bull = "多頭" if r["bullish"] else "    "
        ind = r["industry"] or "未分類"
        print(f"  {r['ticker']} {r['name']:<10} {ind:<8} {r['from_high_pct']:+.2f}%  ${r['today']:.1f}  {bull}")

    print()
    print("=" * 60)
    print(f"🟡 接近 2y 新高（>=95%）：{len(near)} 檔")
    print("=" * 60)
    by_ind = defaultdict(list)
    for r in near:
        by_ind[r["industry"] or "其他"].append(r)
    ranked = sorted(by_ind.items(), key=lambda x: -len(x[1]))
    print(f"\n📊 族群統計（接近 2y 高 5% 內）：")
    for ind, items in ranked[:15]:
        bull_n = sum(1 for x in items if x["bullish"])
        print(f"  {ind}: {len(items)} 檔（多頭 {bull_n}）")
    if ranked:
        top_ind = ranked[0][0]
        print(f"\n🏆 族群最多：{top_ind}（{len(ranked[0][1])} 檔）")

    print(f"\n📋 Top 30 接近高點個股：")
    for r in near[:30]:
        bull = "多頭" if r["bullish"] else "    "
        ind = r["industry"] or "未分類"
        print(f"  {r['ticker']} {r['name']:<10} {ind:<8} {r['from_high_pct']:+.2f}%  ${r['today']:.1f}  {bull}")

    # 寫 JSON
    out = {
        "timestamp": dt.date.today().isoformat(),
        "exact_ath": exact,
        "near_ath_top30": near[:30],
        "industry_stats": [
            {"industry": ind, "count": len(items),
             "bullish_count": sum(1 for x in items if x["bullish"])}
            for ind, items in ranked
        ],
        "top_industry": ranked[0][0] if ranked else None,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已輸出 {OUTPUT_PATH}")
    sd.logout()


if __name__ == "__main__":
    main()
