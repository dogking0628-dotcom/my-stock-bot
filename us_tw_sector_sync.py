# -*- coding: utf-8 -*-
"""
昨日美股大漲族群 → 台股對應族群 → Top 5 選股
─────────────────────────────────
邏輯：
  1. 抓昨日美股各產業 ETF 漲跌幅
  2. 找漲幅最大的 Top 3 美股族群
  3. 對應到台股族群
  4. 該台股族群中找昨日漲幅前 5 + ATH
"""
import sys, io, os, json, datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import yfinance as yf
import numpy as np
import pandas as pd
from collections import defaultdict
from industry_map_loader import get_industry

# 美股 ETF → 台股族群對應
US_TW_MAP = {
    "SMH": {"name": "半導體 ETF", "tw_industries": ["半導體", "其他電子"]},
    "IGV": {"name": "軟體/雲端 ETF", "tw_industries": ["通信網路", "電腦及週邊"]},
    "XLK": {"name": "科技類股 ETF", "tw_industries": ["半導體", "電子零組件",
            "光電", "電腦及週邊", "電子通路", "通信網路", "其他電子"]},
    "SOXL": {"name": "半導體 3x ETF", "tw_industries": ["半導體"]},
    "XLF": {"name": "金融類股 ETF", "tw_industries": ["金融保險"]},
    "XLE": {"name": "能源類股 ETF", "tw_industries": ["油電燃氣"]},
    "XLI": {"name": "工業類股 ETF", "tw_industries": ["電機機械", "鋼鐵"]},
    "XLV": {"name": "生技醫療 ETF", "tw_industries": ["生技醫療"]},
    "XLY": {"name": "可選消費 ETF", "tw_industries": ["汽車", "觀光餐旅"]},
    "XLP": {"name": "必需消費 ETF", "tw_industries": ["食品", "貿易百貨"]},
    "XLU": {"name": "公用事業 ETF", "tw_industries": ["油電燃氣"]},
    "XLB": {"name": "原物料 ETF",   "tw_industries": ["塑膠", "化學工業", "鋼鐵"]},
    "XLC": {"name": "通信服務 ETF", "tw_industries": ["通信網路"]},
    "XLRE": {"name": "REITs ETF",   "tw_industries": ["建材營造"]},
    "QQQ": {"name": "Nasdaq 100",   "tw_industries": ["半導體", "電子零組件",
            "光電", "電腦及週邊", "電子通路", "通信網路", "其他電子"]},
}


def fetch_us_sector_changes():
    """抓昨日美股各 ETF 漲跌幅"""
    out = {}
    for tk in US_TW_MAP.keys():
        try:
            df = yf.download(tk, period="5d", auto_adjust=True,
                             progress=False, threads=False, group_by="column")
            if hasattr(df.columns, "levels"):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            if "Close" not in df.columns: continue
            cl = df["Close"].dropna().values
            if len(cl) < 2: continue
            chg = float((cl[-1] / cl[-2] - 1) * 100)
            out[tk] = chg
        except Exception as e:
            print(f"  {tk} fail: {e}")
    return out


def fetch_tw_universe():
    with io.open("tw_universe.json", encoding="utf-8") as f:
        u = json.load(f)
    return u["stocks"]


def analyze_tw_industries(stocks, target_industries):
    """抓目標族群所有股票昨日表現 + 是否創 ATH"""
    print(f"  抓 {len(stocks)} 檔台股...")
    BATCH = 50
    by_ind = defaultdict(list)
    target_set = set(target_industries)

    for i in range(0, len(stocks), BATCH):
        batch = stocks[i:i+BATCH]
        codes = [f"{s['code']}.TW" for s in batch]
        try:
            df = yf.download(" ".join(codes), period="2y",
                             auto_adjust=True, progress=False, threads=True,
                             group_by="ticker")
        except Exception:
            continue
        for s in batch:
            yfc = f"{s['code']}.TW"
            try:
                if yfc not in df.columns.get_level_values(0):
                    continue
                cl = df[yfc]["Close"].dropna()
                if len(cl) < 100: continue
                # 族群
                ind = get_industry(s["code"])
                if ind not in target_set: continue
                # 昨日漲跌
                today = float(cl.iloc[-1])
                yesterday = float(cl.iloc[-2])
                chg = (today/yesterday - 1) * 100
                # 是否創 2y 月線 ATH
                today_ym = dt.date.today().strftime("%Y-%m")
                by_month = {}
                for ts, c in cl.items():
                    by_month[ts.strftime("%Y-%m")] = float(c)
                hist = [v for ym, v in by_month.items() if ym < today_ym]
                is_ath = bool(hist and today >= max(hist) * 0.999)
                # MA200
                if len(cl) >= 200:
                    ma200 = float(cl.iloc[-200:].mean())
                    bullish = today > ma200
                else:
                    ma200 = today; bullish = False
                by_ind[ind].append({
                    "ticker": s["code"], "name": s["name"],
                    "industry": ind, "today": today, "change_pct": chg,
                    "is_ath": is_ath, "bullish": bullish,
                })
            except Exception:
                continue
    return by_ind


def main():
    today = dt.date.today().isoformat()
    print(f"📅 {today} 昨日美股 → 台股族群同步分析\n")
    print("=" * 70)
    print("[1/3] 抓美股各 ETF 昨日漲跌...")
    us_chg = fetch_us_sector_changes()
    if not us_chg:
        print("⚠️ 美股資料抓不到")
        return

    # 排序 Top 大漲族群
    sorted_us = sorted(us_chg.items(), key=lambda x: -x[1])
    print("\n🇺🇸 昨日美股 ETF 漲幅排名：")
    for tk, chg in sorted_us:
        emoji = "🔥" if chg >= 1 else ("✅" if chg >= 0 else "❌")
        print(f"  {emoji} {tk:<6} ({US_TW_MAP[tk]['name']:<15}) {chg:+6.2f}%")

    # 取漲 ≥ 1% 的族群
    hot_us = [(tk, c) for tk, c in sorted_us if c >= 1.0]
    if not hot_us:
        print("\n⚠️ 昨日美股無大漲族群（≥1%）→ 建議空手觀察")
        return

    print(f"\n🔥 大漲族群 (≥1%)：{len(hot_us)} 個")

    # 收集對應的台股族群（去重）
    target_industries = set()
    industry_us_score = defaultdict(float)  # 累積美股漲幅給對應族群
    for tk, chg in hot_us:
        for ind in US_TW_MAP[tk]["tw_industries"]:
            target_industries.add(ind)
            industry_us_score[ind] += chg

    print(f"\n🇹🇼 對應台股族群（按美股累積漲幅排序）：")
    sorted_tw_ind = sorted(industry_us_score.items(), key=lambda x: -x[1])
    for ind, score in sorted_tw_ind:
        print(f"  • {ind} (美股累積 {score:+.1f}%)")

    print("\n" + "=" * 70)
    print("[2/3] 分析台股對應族群昨日表現...")
    stocks = fetch_tw_universe()
    by_ind = analyze_tw_industries(stocks, list(target_industries))

    print("\n📊 台股族群昨日表現：")
    industry_summary = []
    for ind, lst in by_ind.items():
        if not lst: continue
        avg = sum(s["change_pct"] for s in lst) / len(lst)
        n_up = sum(1 for s in lst if s["change_pct"] > 0)
        n_ath = sum(1 for s in lst if s["is_ath"])
        industry_summary.append({
            "industry": ind, "n": len(lst), "avg_chg": avg,
            "n_up": n_up, "n_ath": n_ath,
            "us_score": industry_us_score.get(ind, 0),
            "stocks": lst,
        })
    industry_summary.sort(key=lambda x: -(x["us_score"] + x["avg_chg"]))

    print(f"\n{'族群':<10} {'股數':>4} {'昨日平均':>8} {'上漲':>6} {'ATH':>5} {'美股對應':>8}")
    print("-" * 60)
    for s in industry_summary:
        print(f"{s['industry']:<10} {s['n']:>4} {s['avg_chg']:>+7.2f}%"
              f" {s['n_up']:>4}/{s['n']:<2} {s['n_ath']:>4} 檔"
              f" {s['us_score']:>+7.1f}%")

    print("\n" + "=" * 70)
    print("[3/3] 從美台同步漲族群挑 Top 5 個股")
    print("=" * 70)

    # 候選池：對應族群中「昨日漲 + ATH」
    candidates = []
    for s in industry_summary:
        for stk in s["stocks"]:
            if stk["change_pct"] > 0 and stk["is_ath"] and stk["bullish"]:
                stk["industry_us_score"] = s["us_score"]
                stk["combined_score"] = (
                    s["us_score"] * 0.4 +
                    stk["change_pct"] * 0.6
                )
                candidates.append(stk)

    candidates.sort(key=lambda x: -x["combined_score"])
    top5 = candidates[:5]

    if not top5:
        print("\n⚠️ 無符合「美股族群連動 + 昨日漲 + ATH」的個股")
        return

    print(f"\n🎯 Top 5 美台同步族群連動股：\n")
    for i, s in enumerate(top5, 1):
        print(f"  #{i} {s['ticker']} {s['name']}（{s['industry']}）")
        print(f"     昨日 {s['change_pct']:+.2f}% ｜ 收 ${s['today']:.1f}"
              f" ｜ 美股族群連動 {s['industry_us_score']:+.1f}%"
              f" ｜ 綜合分 {s['combined_score']:+.1f}")

    # 輸出 JSON
    out = {
        "timestamp": today,
        "us_sectors_yesterday": dict(sorted_us),
        "hot_us_sectors": dict(hot_us),
        "tw_industries_synced": [s["industry"] for s in industry_summary],
        "top5_synced_picks": [
            {"ticker": s["ticker"], "name": s["name"], "industry": s["industry"],
             "change_pct": s["change_pct"], "today": s["today"],
             "us_score": s["industry_us_score"], "combined": s["combined_score"]}
            for s in top5
        ],
    }
    out_path = os.path.join(os.path.dirname(__file__), "us_tw_sync_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已輸出 {out_path}")


if __name__ == "__main__":
    main()
