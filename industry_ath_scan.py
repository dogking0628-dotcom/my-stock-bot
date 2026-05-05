# -*- coding: utf-8 -*-
"""
永豐 Shioaji API 全市場掃 2 年還原月線 ATH，按族群統計
不做硬性過濾，純資訊呈現
"""
import sys, io, os, json, datetime as dt
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

import numpy as np
from collections import defaultdict
import shioaji_data as sd
from tw_breakout_filter import INDUSTRY_GROUPS, get_industry

NEAR_THRESHOLD = 0.95
EXACT_THRESHOLD = 0.999
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")


def monthly_closes(bars):
    """聚合日 K → 每月最後一個交易日收盤"""
    by_month = {}
    for b in bars:
        ym = b["date"][:7]
        by_month[ym] = b["close"]  # 後寫覆蓋 = 該月最後一日
    items = sorted(by_month.items())
    return items  # list of (YYYY-MM, close)


def analyze_one(code, name, exchange):
    end = dt.date.today()
    start = end - dt.timedelta(days=365*2 + 60)  # 多抓 2 個月避免月初空白
    bars = sd.fetch_kbars(code, start_date=start, end_date=end)
    if not bars or len(bars) < 100:
        return None
    monthly = monthly_closes(bars)
    if len(monthly) < 6:  # 至少要 6 個月
        return None
    today_close = float(bars[-1]["close"])
    today_ym = dt.date.today().strftime("%Y-%m")
    # 歷史月線最高（排除當月未收完）
    historical = [c for ym, c in monthly if ym < today_ym]
    if not historical:
        return None
    monthly_max = float(max(historical))
    if monthly_max <= 0:
        return None
    ratio = today_close / monthly_max
    # 多頭排列（用日 K 算 MA）
    closes = np.array([b["close"] for b in bars], dtype=float)
    if len(closes) < 200:
        return {"ticker": code, "name": name, "exchange": exchange,
                "today": today_close, "monthly_max_2y": monthly_max,
                "ratio": ratio, "from_high_pct": (ratio - 1) * 100,
                "bullish": None, "industry": get_industry(code),
                "n_months": len(monthly)}
    ma20, ma60, ma200 = closes[-20:].mean(), closes[-60:].mean(), closes[-200:].mean()
    return {
        "ticker": code, "name": name, "exchange": exchange,
        "today": today_close, "monthly_max_2y": monthly_max,
        "ratio": ratio, "from_high_pct": (ratio - 1) * 100,
        "bullish": bool(today_close > ma20 > ma60 > ma200),
        "industry": get_industry(code), "n_months": len(monthly),
    }


def main():
    api = sd.get_api()
    if not api:
        print("❌ Shioaji 登入失敗")
        sys.exit(1)
    print("[1/3] 取全市場股票清單...")
    all_stocks = sd.list_all_stocks()
    print(f"  共 {len(all_stocks)} 檔")

    print("[2/3] 逐檔抓 2y 日 K → 聚合月線 → 計算距高 % ...")
    results = []
    err_samples = []
    for i, (code, name, ex) in enumerate(all_stocks):
        try:
            r = analyze_one(code, name, ex)
            if r:
                results.append(r)
            elif len(err_samples) < 5:
                err_samples.append(f"{code} {name}: 無資料/月份不足")
        except Exception as e:
            if len(err_samples) < 5:
                err_samples.append(f"{code} {name}: {type(e).__name__}: {str(e)[:80]}")
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(all_stocks)}] 已分析 {len(results)} 檔")
            if err_samples:
                print(f"    錯誤樣本: {err_samples[:3]}")

    print(f"  完成，共 {len(results)} 檔有效\n")

    exact = sorted([r for r in results if r["ratio"] >= EXACT_THRESHOLD],
                   key=lambda x: -x["ratio"])
    near = sorted([r for r in results if r["ratio"] >= NEAR_THRESHOLD],
                  key=lambda x: -x["ratio"])

    print("=" * 60)
    print(f"🔥 真正創 2y 月線新高（>=99.9%）：{len(exact)} 檔")
    print("=" * 60)
    by_ind = defaultdict(list)
    for r in exact:
        by_ind[r["industry"] or "未分類"].append(r)
    for ind, items in sorted(by_ind.items(), key=lambda x: -len(x[1])):
        bn = sum(1 for x in items if x["bullish"])
        print(f"  {ind}: {len(items)} 檔（多頭排列 {bn}）")
    print()
    for r in exact[:50]:
        b = "多頭" if r["bullish"] else "    "
        ind = r["industry"] or "未分類"
        print(f"  {r['ticker']} {r['name']:<10} {ind:<8} {r['from_high_pct']:+6.2f}%  ${r['today']:>8.1f}  {b}")

    print("\n" + "=" * 60)
    print(f"🟡 接近 2y 月線新高（>=95%）：{len(near)} 檔")
    print("=" * 60)
    by_ind = defaultdict(list)
    for r in near:
        by_ind[r["industry"] or "未分類"].append(r)
    ranked = sorted(by_ind.items(), key=lambda x: -len(x[1]))
    print("\n📊 族群統計（接近 2y 月線高 5% 內）：")
    for ind, items in ranked[:20]:
        bn = sum(1 for x in items if x["bullish"])
        print(f"  {ind}: {len(items)} 檔（多頭 {bn}）")
    if ranked:
        print(f"\n🏆 族群最多：{ranked[0][0]}（{len(ranked[0][1])} 檔）")

    print("\n📋 Top 30 接近高點個股：")
    for r in near[:30]:
        b = "多頭" if r["bullish"] else "    "
        ind = r["industry"] or "未分類"
        print(f"  {r['ticker']} {r['name']:<10} {ind:<8} {r['from_high_pct']:+6.2f}%  ${r['today']:>8.1f}  {b}")

    out = {
        "timestamp": dt.date.today().isoformat(),
        "basis": "2y monthly close (Shioaji raw, ~adj)",
        "total_analyzed": len(results),
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
    print(f"\n💾 輸出 {OUTPUT_PATH}")
    sd.logout()


if __name__ == "__main__":
    main()
