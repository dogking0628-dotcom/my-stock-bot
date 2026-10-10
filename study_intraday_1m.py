# -*- coding: utf-8 -*-
"""
1 分鐘線微結構：開盤競價那一口價 vs 開盤後前幾分鐘，哪個更低？
═══════════════════════════════════════════════════════
15m 研究已知：訊號隔日的當日最低有 51% 落在 09:00-09:15 那根。
本測用 1m 線拆開那 15 分鐘，回答「掛單該掛開盤價、還是掛低一點等回落」。

yfinance 1m 僅回溯 7 天 → 樣本必然小，用兩層：
  A) 真訊號：top5_history 近 7 天內的推播訊號（最可信、最少）
  B) 代理樣本：ath_industry_report 的 ATH 名單股，近 7 個交易日中「前一日收盤漲 ≥ +3%」的隔日
     （同族群、同型態的近似，明確標示為代理）
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
CHECK_MIN = [1, 2, 3, 5, 10, 15, 30]


def sessions_1m(tk):
    df = yf.download(f"{tk}.TW", period="7d", interval="1m", auto_adjust=True,
                     progress=False, threads=False, group_by="column")
    if df is None or df.empty:
        return {}
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna(subset=["Close"])
    idx = df.index
    try:
        idx = idx.tz_convert("Asia/Taipei")
    except Exception:
        pass
    df = df.copy()
    df["_d"] = [t.strftime("%Y-%m-%d") for t in idx]
    df["_m"] = [t.hour * 60 + t.minute - 540 for t in idx]        # 09:00 → 0
    out = {}
    for d, g in df.groupby("_d"):
        g = g[(g["_m"] >= 0) & (g["_m"] <= 270)]
        if len(g) >= 60:
            out[d] = g
    return out


def analyze_session(g):
    o = float(g["Open"].iloc[0])                    # 第一根 1m 的開 = 集合競價價
    if not np.isfinite(o) or o <= 0:
        return None
    lows = g["Low"].astype(float).values
    closes = g["Close"].astype(float).values
    mins = g["_m"].values
    day_low = float(lows.min())
    low_min = int(mins[int(np.argmin(lows))])
    r = {"open": o, "day_low_pct": (day_low / o - 1) * 100, "low_minute": low_min,
         "close_pct": (float(closes[-1]) / o - 1) * 100}
    for m in CHECK_MIN:
        sub = lows[mins <= m]
        r[f"minlow_{m}"] = (float(sub.min()) / o - 1) * 100 if len(sub) else None
        at = closes[mins <= m]
        r[f"px_{m}"] = (float(at[-1]) / o - 1) * 100 if len(at) else None
    # 開盤後前 5 分鐘內有沒有跌破開盤價（＝掛開盤價以下的限價有沒有機會成交）
    r["dip_below_open_5m"] = r["minlow_5"] is not None and r["minlow_5"] < -0.05
    r["dip_below_open_15m"] = r["minlow_15"] is not None and r["minlow_15"] < -0.05
    return r


def summarize(rows, title):
    n = len(rows)
    print("\n" + "=" * 66)
    print(f"{title}（n={n}）")
    print("=" * 66)
    if n == 0:
        print("  無樣本")
        return
    f = lambda k: np.array([r[k] for r in rows if r.get(k) is not None])
    print(f"  當日最低 vs 開盤價：中位 {np.median(f('day_low_pct')):+.2f}%  平均 {f('day_low_pct').mean():+.2f}%")
    lm = f("low_minute")
    print(f"  當日最低出現時間：中位 第 {int(np.median(lm))} 分鐘 | ≤1分 {np.mean(lm<=1)*100:.0f}% | ≤5分 {np.mean(lm<=5)*100:.0f}% | ≤15分 {np.mean(lm<=15)*100:.0f}% | ≤30分 {np.mean(lm<=30)*100:.0f}%")
    print(f"  前 5 分鐘內曾跌破開盤價：{np.mean(f('dip_below_open_5m'))*100:.0f}%   前 15 分鐘內：{np.mean(f('dip_below_open_15m'))*100:.0f}%")
    print(f"  開盤買 → 收盤：平均 {f('close_pct').mean():+.2f}%  勝率 {np.mean(f('close_pct')>0)*100:.0f}%")
    print(f"\n  {'分鐘':>4} {'到該分鐘的最低(中位)':>14} {'該分鐘價格(中位)':>14} {'在該分鐘買→收盤':>14}")
    for m in CHECK_MIN:
        ml = f(f"minlow_{m}"); px = f(f"px_{m}")
        if len(px) == 0:
            continue
        ret = np.array([(r["close_pct"] - r[f"px_{m}"]) for r in rows if r.get(f"px_{m}") is not None])
        # 近似：在該分鐘買 → 收盤 = 收盤相對開盤 − 該分鐘相對開盤
        print(f"  {m:>4} {np.median(ml):>+13.2f}% {np.median(px):>+13.2f}% {ret.mean():>+13.2f}%")


def main():
    today = dt.date.today()
    # A) 真訊號
    with io.open(os.path.join(ROOT, "top5_history.json"), encoding="utf-8") as fh:
        recs = json.load(fh)["records"]
    sigs = [(r["date"], p["ticker"], p.get("name")) for r in recs for p in r.get("picks", [])
            if (today - dt.date.fromisoformat(r["date"])).days <= 10]
    # B) 代理：ATH 名單
    with io.open(os.path.join(ROOT, "ath_industry_report.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    ath_tk = sorted({x["ticker"] for x in rep.get("exact_ath", []) if x.get("ticker")})
    print(f"真訊號（近 10 天）{len(sigs)} 筆；ATH 代理池 {len(ath_tk)} 檔")

    cache = {}
    def get(tk):
        if tk not in cache:
            cache[tk] = sessions_1m(tk)
        return cache[tk]

    rows_a, rows_b = [], []
    for sd, tk, nm in sigs:
        ss = get(tk)
        nd = next((d for d in sorted(ss) if d > sd), None)
        if nd:
            r = analyze_session(ss[nd])
            if r:
                r.update(tk=tk, name=nm, sig=sd, day=nd); rows_a.append(r)

    for tk in ath_tk:
        ss = get(tk)
        days = sorted(ss)
        for i in range(1, len(days)):
            prev, cur = ss[days[i - 1]], ss[days[i]]
            pc = float(prev["Close"].iloc[-1]); pp = float(prev["Open"].iloc[0])
            # 前一日「收盤漲幅」用當日開→收近似（無前前日收盤）；退而求其次用 prev 收 vs prev 第一根開
            if pc / pp - 1 >= 0.03:
                r = analyze_session(cur)
                if r:
                    r.update(tk=tk, day=days[i]); rows_b.append(r)

    summarize(rows_a, "A) 真訊號隔日（top5_history，近 10 天）")
    for r in rows_a:
        print(f"     {r['sig']}→{r['day']} {r['tk']} {r['name']}: 開{r['open']:g} 最低{r['day_low_pct']:+.2f}%@第{r['low_minute']}分  收{r['close_pct']:+.2f}%")
    summarize(rows_b, "B) 代理樣本：ATH 池股、前一日強勢(≥+3%)的隔日")
    json.dump({"A": rows_a, "B": rows_b}, io.open(os.path.join(ROOT, "data", "intraday_1m_study.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 data/intraday_1m_study.json")


if __name__ == "__main__":
    main()
