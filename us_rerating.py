#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
us_rerating.py — 美股持股 re-rating 素材：分析師 EPS 共識（今年/明年）、成長率、目標價區間、PE 帶
輸出 us_rerating.json（原始素材）；情境本益比與 2027 目標價由分析端決定，不在此寫死。
"""
import sys, io, json, math
from pathlib import Path
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass
import yfinance as yf
import pandas as pd

ROOT = Path(__file__).resolve().parent
H = json.loads((ROOT / "double_holdings.json").read_text(encoding="utf-8"))


def f(x, nd=2):
    try:
        if x is None: return None
        v = float(x)
        return None if math.isnan(v) else round(v, nd)
    except Exception:
        return None


def grab(tk):
    t = yf.Ticker(tk)
    r = {"ticker": tk}
    try:
        info = t.info or {}
    except Exception:
        info = {}
    r["price"] = f(info.get("currentPrice") or info.get("regularMarketPrice"))
    r["eps_ttm"] = f(info.get("trailingEps")); r["eps_fwd"] = f(info.get("forwardEps"))
    r["pe_ttm"] = f(info.get("trailingPE")); r["pe_fwd"] = f(info.get("forwardPE"))
    r["fy_end"] = info.get("lastFiscalYearEnd"); r["currency"] = info.get("currency")
    r["target"] = {k: f(info.get(v)) for k, v in (("low", "targetLowPrice"), ("mean", "targetMeanPrice"),
                                                    ("median", "targetMedianPrice"), ("high", "targetHighPrice"),
                                                    ("n", "numberOfAnalystOpinions"))}
    r["rev_growth"] = f(info.get("revenueGrowth")); r["eps_growth"] = f(info.get("earningsGrowth"))
    r["gross_margin"] = f(info.get("grossMargins")); r["op_margin"] = f(info.get("operatingMargins"))
    # 分析師 EPS 共識：0y=本會計年度, +1y=下一會計年度
    try:
        ee = t.earnings_estimate
        if ee is not None and len(ee):
            r["eps_est"] = {str(idx): {"avg": f(row.get("avg")), "low": f(row.get("low")), "high": f(row.get("high")),
                                       "n": f(row.get("numberOfAnalysts"), 0), "yoy": f(row.get("growth"))}
                            for idx, row in ee.iterrows()}
    except Exception as e:
        r["eps_est_err"] = str(e)[:80]
    try:
        re_ = t.revenue_estimate
        if re_ is not None and len(re_):
            r["rev_est"] = {str(idx): {"avg": f(row.get("avg")), "yoy": f(row.get("growth"))} for idx, row in re_.iterrows()}
    except Exception as e:
        r["rev_est_err"] = str(e)[:80]
    try:
        ge = t.growth_estimates
        if ge is not None and len(ge):
            r["growth_est"] = {str(idx): f(row.get("stockTrend") if "stockTrend" in row else row.iloc[0]) for idx, row in ge.iterrows()}
    except Exception as e:
        r["growth_est_err"] = str(e)[:80]
    # 近 3 年的 PE 帶（用季度 EPS 的 ttm 與價格粗算）
    try:
        q = t.quarterly_income_stmt
        if q is not None and "Diluted EPS" in q.index:
            eps_q = q.loc["Diluted EPS"].dropna().sort_index()
            ttm = eps_q.rolling(4).sum().dropna()
            px = t.history(period="3y")["Close"]
            band = []
            for d, e in ttm.items():
                if e and e > 0:
                    p = px[px.index <= pd.Timestamp(d).tz_localize(px.index.tz) if px.index.tz else px.index <= d]
                    if len(p): band.append(round(float(p.iloc[-1]) / float(e), 1))
            if band:
                r["pe_band_3y"] = {"min": min(band), "max": max(band), "points": band}
    except Exception as e:
        r["pe_band_err"] = str(e)[:80]
    return r


def main():
    out = [grab(p["ticker"]) for p in H.get("us", [])]
    (ROOT / "us_rerating.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # 台股：同一套素材（yfinance 對台股的分析師覆蓋較少，抓得到多少算多少；.TW 失敗改 .TWO）
    tw = []
    for p in H.get("tw", []):
        tk = str(p["ticker"])
        if tk == "0050": continue
        r = grab(tk + ".TW")
        if r.get("price") is None:
            r = grab(tk + ".TWO")
        r["ticker"] = tk; r["name"] = p.get("name"); tw.append(r)
    (ROOT / "tw_rerating.json").write_text(json.dumps(tw, ensure_ascii=False, indent=2), encoding="utf-8")
    out = out + tw
    for r in out:
        print(r["ticker"], r.get("price"), "eps_ttm", r.get("eps_ttm"), "eps_fwd", r.get("eps_fwd"), "est", r.get("eps_est"), "tgt", r.get("target"), "band", r.get("pe_band_3y", {}).get("min"), r.get("pe_band_3y", {}).get("max"))


if __name__ == "__main__":
    main()
