#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
us_valuation_lines.py — 美股持股：估值快照 + 出場線建議
═════════════════════════════════════════════════
讀 double_holdings.json 的 us 陣列，對每檔用 yfinance 抓：
  價格：最新收盤、MA20/MA60/MA200、52 週高、距高、20 日低、ATR14
  估值：trailing PE、forward PE、PB、PEG、營收/獲利成長、分析師目標均價
出場線（依 theme，與台股規則同一套語言）：
  記憶體/週期  → 防守線 = MA20（收盤跌破 → 隔日開盤賣）；峰值回落 30% 亦出
  AI 硬體/thesis → 災難線 = 成本 -15%（成本未知時先列 MA200 當體制線）
  其他小部位    → 不設線，只記帳
輸出：us_holdings_lines.json / us_holdings_lines.md
用戶拍板前這只是建議；本檔不下單。
"""
import sys, io, json, math, datetime as dt
from pathlib import Path
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass
import yfinance as yf
import pandas as pd

ROOT = Path(__file__).resolve().parent
H = json.loads((ROOT / "double_holdings.json").read_text(encoding="utf-8"))
OUT_J, OUT_M = ROOT / "us_holdings_lines.json", ROOT / "us_holdings_lines.md"


def f(x, nd=2):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)): return None
        return round(float(x), nd)
    except Exception:
        return None


def one(pos):
    tk = pos["ticker"]
    r = {"ticker": tk, "name": pos.get("name"), "shares": pos.get("shares"), "theme": pos.get("theme"),
         "avg_cost": pos.get("avg_cost")}
    df = yf.download(tk, period="15mo", auto_adjust=False, progress=False, threads=False)
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        r["error"] = "價格不足"; return r
    c = df["Close"]
    last = float(c.iloc[-1]); r["date"] = df.index[-1].strftime("%Y-%m-%d"); r["close"] = f(last)
    r["ma20"] = f(c.rolling(20).mean().iloc[-1]); r["ma60"] = f(c.rolling(60).mean().iloc[-1])
    r["ma200"] = f(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    hi252 = float(df["High"].tail(252).max()); r["high_52w"] = f(hi252); r["from_high_pct"] = f((last / hi252 - 1) * 100, 1)
    r["low_20d"] = f(df["Low"].tail(20).min())
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    r["atr14_pct"] = f(tr.rolling(14).mean().iloc[-1] / last * 100, 1)
    r["ret_20d_pct"] = f((last / float(c.iloc[-21]) - 1) * 100, 1) if len(c) > 21 else None
    r["ret_1d_pct"] = f((last / float(c.iloc[-2]) - 1) * 100, 1)
    try:
        info = yf.Ticker(tk).info or {}
    except Exception:
        info = {}
    for k_out, k_in in (("pe_ttm", "trailingPE"), ("pe_fwd", "forwardPE"), ("pb", "priceToBook"), ("peg", "pegRatio"),
                        ("rev_growth", "revenueGrowth"), ("eps_growth", "earningsGrowth"), ("target_mean", "targetMeanPrice"),
                        ("mcap", "marketCap"), ("beta", "beta")):
        r[k_out] = f(info.get(k_in), 2)
    if r.get("target_mean") and last:
        r["target_upside_pct"] = f((r["target_mean"] / last - 1) * 100, 1)
    # 出場線建議
    theme = pos.get("theme") or ""
    cost = pos.get("avg_cost")
    if theme == "記憶體":
        r["rule"] = "週期股防守線"
        r["line"] = r["ma20"]; r["line_desc"] = "MA20（收盤跌破→隔日開盤賣）；另峰值回落 30% 出"
        r["peak_minus30"] = f(hi252 * 0.7)
        if cost: r["line_cost93"] = f(cost * 0.93)
    elif theme == "AI 硬體":
        r["rule"] = "thesis 災難線"
        if cost:
            r["line"] = f(cost * 0.85); r["line_desc"] = "成本 -15%"
        else:
            r["line"] = r["ma200"]; r["line_desc"] = "成本未知：先列 MA200 當體制線；補成本後改成本 -15%"
    else:
        r["rule"] = "小部位/其他"; r["line"] = None; r["line_desc"] = "不設線，只記帳（用戶可指定）"
    if r.get("line") and last:
        r["line_dist_pct"] = f((last / r["line"] - 1) * 100, 1)
        r["below_line"] = last < r["line"]
    return r


def md(rows, asof):
    L = [f"# 美股持股：估值快照與出場線建議（資料至 {asof}）", "",
         "> 出場線與台股同一套語言：週期股用 MA20 防守線、thesis 股用成本 -15% 災難線、小部位不設線。成本未補的先用 MA200 當體制線。**本表是建議，用戶拍板才生效；出場只看收盤。**", "",
         "| 代號 | 主題 | 收盤 | 1日% | 20日% | 距52週高 | MA20 | MA60 | MA200 | PE(ttm) | PE(fwd) | 營收成長 | 目標均價(上檔) | 規則 | 出場線 | 距線 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("error"):
            L.append(f"| {r['ticker']} | {r.get('theme')} | 抓不到（{r['error']}） |" + " |" * 13); continue
        tgt = f"{r['target_mean']}（{r.get('target_upside_pct'):+.0f}%）" if r.get("target_mean") and r.get("target_upside_pct") is not None else "-"
        rg = f"{r['rev_growth']*100:+.0f}%" if r.get("rev_growth") is not None else "-"
        flag = " ⚠️已破" if r.get("below_line") else ""
        L.append(f"| {r['ticker']} {r.get('name') or ''} | {r.get('theme')} | {r['close']} | {r['ret_1d_pct']:+.1f} | {r['ret_20d_pct']:+.1f} | {r['from_high_pct']:+.1f}% | {r['ma20']} | {r['ma60']} | {r['ma200'] or '-'} | {r.get('pe_ttm') or '-'} | {r.get('pe_fwd') or '-'} | {rg} | {tgt} | {r['rule']} | {r.get('line') or '-'}{flag} | {('%+.1f%%' % r['line_dist_pct']) if r.get('line_dist_pct') is not None else '-'} |")
    L += ["", "說明：距線為正代表還在線上；「⚠️已破」= 最新收盤已低於建議線。記憶體三檔另列峰值回落 30% 價位：" +
          "、".join(f"{r['ticker']} {r.get('peak_minus30')}" for r in rows if r.get("peak_minus30")), ""]
    return "\n".join(L)


def main():
    rows = [one(p) for p in H.get("us", [])]
    asof = max((r.get("date") or "" for r in rows), default=dt.date.today().isoformat())
    OUT_J.write_text(json.dumps({"as_of": asof, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_M.write_text(md(rows, asof), encoding="utf-8")
    print(md(rows, asof))


if __name__ == "__main__":
    main()
