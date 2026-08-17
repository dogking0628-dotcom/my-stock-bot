#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投信 × 產品細類 每日資金流向（金額版）
═════════════════════════════════════════════════
只看投信（認養/作帳訊號最乾淨），48 產品細類，金額 = 張數×收盤×1000
資料：t86_3party_cache.json（外資+投信 20 日 cache，由 smart_money_radar 維護）
      不足時補用 t86_history.json（投信 2y 張數）
輸出：sitc_product_flow.json + LINE 區塊 + 完整表
"""
import sys, io, os, json
import datetime as dt
from collections import defaultdict
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception: pass
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from product_taxonomy import PRODUCT_TAXONOMY

OUT = os.path.join(ROOT, "sitc_product_flow.json")
E8 = 1e8


def load_sitc_days(n=20):
    """回傳 [(yyyymmdd, {code: sitc_shares})] 最近 n 交易日；優先 3party cache，缺補 history"""
    days = {}
    p3 = os.path.join(ROOT, "t86_3party_cache.json")
    if os.path.exists(p3):
        for k, v in json.load(io.open(p3, encoding="utf-8")).items():
            if v: days[k.replace("-", "")] = {c: x.get("投信", 0) for c, x in v.items()}
    ph = os.path.join(ROOT, "t86_history.json")
    if os.path.exists(ph):
        for k, v in json.load(io.open(ph, encoding="utf-8")).items():
            if v and k not in days: days[k] = v
    return sorted(days.items())[-n:]


def load_prices(codes, days):
    """近 25 日收盤 {code:{yyyymmdd:close}}"""
    import yfinance as yf
    df = yf.download(" ".join(f"{c}.TW" for c in codes), period="35d", auto_adjust=False,
                     progress=False, threads=True, group_by="ticker")
    px = {}
    for c in codes:
        try:
            s = df[f"{c}.TW"]["Close"].dropna()
            px[c] = {d.strftime("%Y%m%d"): float(v) for d, v in s.items()}
        except Exception: pass
    return px


def price_on(px, c, d):
    m = px.get(c)
    if not m: return None
    if d in m: return m[d]
    prev = [k for k in m if k <= d]
    return m[max(prev)] if prev else None


def main():
    days = load_sitc_days(20)
    if len(days) < 5: print("資料不足"); return
    d5, d20 = days[-5:], days
    codes = sorted({c for cs in PRODUCT_TAXONOMY.values() for c in cs})
    names = {}
    try:
        names = {s["code"]: s["name"] for s in json.load(io.open(os.path.join(ROOT,"tw_universe.json"),encoding="utf-8"))["stocks"]}
    except Exception: pass
    print(f"投信資料 {d20[0][0]}~{d20[-1][0]}（{len(d20)} 日）；抓 {len(codes)} 檔價格...")
    px = load_prices(codes, days)

    def agg(window):
        sh = defaultdict(float); am = defaultdict(float)
        for d, rec in window:
            for c in codes:
                v = rec.get(c, 0)
                if not v: continue
                p = price_on(px, c, d)
                sh[c] += v
                if p: am[c] += v * p
        return sh, am
    sh5, am5 = agg(d5); sh20, am20 = agg(d20)

    rows = []
    for prod, cs in PRODUCT_TAXONOMY.items():
        cs = list(dict.fromkeys(cs))
        a5 = sum(am5.get(c, 0) for c in cs)/E8; a20 = sum(am20.get(c, 0) for c in cs)/E8
        s5 = sum(sh5.get(c, 0) for c in cs)/1000
        items = sorted(((c, names.get(c, "?"), am5.get(c, 0)/E8, sh5.get(c, 0)/1000) for c in cs if c in am5 or c in sh5), key=lambda x: -x[2])
        if a5 > 0 and a5 >= abs(a20)*0.5: mom = "⏫加速買"
        elif a5 < 0 and -a5 >= abs(a20)*0.5: mom = "⏬加速賣"
        elif a20 and (a5 > 0) != (a20 > 0) and abs(a5) > 1: mom = "🔄反轉"
        else: mom = "➡️持平"
        rows.append({"product": prod, "amt5": a5, "amt20": a20, "shares5": s5, "mom": mom,
                     "top_buy": [{"code": c, "name": n, "amt": a, "shares": s} for c, n, a, s in items[:2]],
                     "top_sell": [{"code": c, "name": n, "amt": a, "shares": s} for c, n, a, s in items[-2:][::-1] if a < 0]})
    rows.sort(key=lambda x: -x["amt5"])

    print(f"\n=== 投信 × 產品細類 近5日 淨買賣（億元） [{d5[0][0]}~{d5[-1][0]}] ===")
    print(f"{'產品細類':<14}{'5日金額':>9}{'20日金額':>9}{'5日張':>9}  動能   買最多 / 賣最多")
    for r in rows:
        tb = f"{r['top_buy'][0]['name']}{r['top_buy'][0]['amt']:+.1f}" if r["top_buy"] else "-"
        ts = f"{r['top_sell'][0]['name']}{r['top_sell'][0]['amt']:+.1f}" if r["top_sell"] else "-"
        print(f"{r['product']:<14}{r['amt5']:>+8.1f}億{r['amt20']:>+8.1f}億{r['shares5']:>+8,.0f}  {r['mom']:<7} {tb} / {ts}")

    out = {"date": dt.date.today().isoformat(), "window5": [d5[0][0], d5[-1][0]],
           "window20": [d20[0][0], d20[-1][0]], "products": rows}
    json.dump(out, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n💾 {OUT}")


def build_block(data=None, n=4):
    """LINE 區塊：投信產品買/賣超前 n"""
    if data is None:
        try: data = json.load(io.open(OUT, encoding="utf-8"))
        except Exception: return None
    rows = data.get("products", [])
    buy = [r for r in rows if r["amt5"] > 0][:n]
    sell = [r for r in sorted(rows, key=lambda x: x["amt5"]) if r["amt5"] < 0][:n]
    L = ["🏦 投信產品流向(5日,億)"]
    if buy:  L.append("  📈買: " + "、".join(f"{r['product']}{r['amt5']:+.0f}" for r in buy))
    if sell: L.append("  📉賣: " + "、".join(f"{r['product']}{r['amt5']:+.0f}" for r in sell))
    hot = [r for r in rows if r["mom"] == "⏫加速買"][:3]
    if hot: L.append("  ⏫加速: " + "、".join(r["product"] for r in hot))
    return "\n".join(L) if len(L) > 1 else None


if __name__ == "__main__":
    main()
