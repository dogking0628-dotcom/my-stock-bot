#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchmark_0050.py — 實單持股 vs 0050 對照（週報用）
═════════════════════════════════════════════════
問題只有一個：同樣的錢、同一天，買 0050 現在會是多少？

- 持股來源：double_holdings.json（tw 陣列：ticker/shares/avg_cost/since）
- 每檔：實際成本 = shares×avg_cost；現值 = shares×最新收盤；
        0050 對照 = 成本 × (0050 最新收 / 0050 在 since 當日收)
- 合計：實單累計報酬 vs 0050 對照累計報酬（成本加權，每檔用自己的 since 日）
- 本週：最近 5 個交易日 持股組合 vs 0050 vs 加權指數
- 輸出：weekly_benchmark.json、weekly_benchmark.md，並提供 line_block() 給週報接在後面

用法：python benchmark_0050.py [--md] [--no-fetch]（--no-fetch 用 weekly_benchmark.json 的快取重算）
一年後的判準（DAILY_SOP.md）：整套系統沒贏過 0050 → 主力資金換 0050，動能倉繼續驗證。
"""
import sys, io, os, json, datetime as dt
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
HOLDINGS = ROOT / "double_holdings.json"
OUT_JSON = ROOT / "weekly_benchmark.json"
OUT_MD = ROOT / "weekly_benchmark.md"
BENCH = "0050"
INDEX = "^TWII"
WEEK_DAYS = 5


# ─────────────────────────────────────────────────────────────────────────────
# 價格：回傳 [(date_iso, close)] 由舊到新；上市 .TW 失敗改試上櫃 .TWO
# ─────────────────────────────────────────────────────────────────────────────
def yf_closes(symbol, start):
    import yfinance as yf
    cands = [symbol] if symbol.startswith("^") else [f"{symbol}.TW", f"{symbol}.TWO"]
    for s in cands:
        try:
            df = yf.download(s, start=start, auto_adjust=False, progress=False, threads=False)
            if hasattr(df.columns, "levels"):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df = df.dropna(subset=["Close"])
            if len(df):
                return [(d.strftime("%Y-%m-%d"), float(c)) for d, c in zip(df.index, df["Close"])]
        except Exception as e:
            print(f"  [warn] {s}: {e}", file=sys.stderr)
    return []


def close_on_or_after(series, date_iso):
    for d, c in series:
        if d >= date_iso:
            return d, c
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# 核心計算（純函式，price_fn(symbol, start) -> [(date, close)]，方便離線測試）
# ─────────────────────────────────────────────────────────────────────────────
def compute(positions, price_fn, week_days=WEEK_DAYS):
    if not positions:
        return {"error": "no positions"}
    earliest = min(p["since"] for p in positions)
    start = (dt.date.fromisoformat(earliest) - dt.timedelta(days=10)).isoformat()
    bench = price_fn(BENCH, start)
    index = price_fn(INDEX, start)
    if not bench:
        return {"error": "0050 price unavailable"}
    b_last_d, b_last = bench[-1]
    rows, warnings = [], []
    tot_cost = tot_value = tot_bench = 0.0
    week_pnl = 0.0
    for p in positions:
        tk, sh, cost, since = str(p["ticker"]), float(p["shares"]), float(p["avg_cost"]), p["since"]
        series = price_fn(tk, start)
        if not series:
            warnings.append(f"{tk} 無價格"); continue
        last_d, last = series[-1]
        b_since_d, b_since = close_on_or_after(bench, since)
        if b_since is None:
            warnings.append(f"{tk} 0050 於 {since} 無報價"); continue
        cost_amt = sh * cost
        value = sh * last
        bench_value = cost_amt * (b_last / b_since)
        prev = series[-1 - week_days][1] if len(series) > week_days else series[0][1]
        week_pnl += sh * (last - prev)
        rows.append({
            "ticker": tk, "name": p.get("name", ""), "shares": sh, "avg_cost": cost, "since": since,
            "last_date": last_d, "last": last,
            "cost_amt": round(cost_amt), "value": round(value), "ret_pct": round((last / cost - 1) * 100, 2),
            "bench_since_close": b_since, "bench_value": round(bench_value),
            "bench_ret_pct": round((b_last / b_since - 1) * 100, 2),
            "alpha_pct": round((last / cost - 1) * 100 - (b_last / b_since - 1) * 100, 2),
            "week_pnl": round(sh * (last - prev)),
        })
        tot_cost += cost_amt; tot_value += value; tot_bench += bench_value
    if not rows:
        return {"error": "no priced positions", "warnings": warnings}

    def week_pct(series):
        if len(series) > week_days:
            return round((series[-1][1] / series[-1 - week_days][1] - 1) * 100, 2)
        return None
    prev_value = tot_value - week_pnl
    out = {
        "as_of": b_last_d,
        "baseline": earliest,
        "positions": rows,
        "total": {
            "cost": round(tot_cost), "value": round(tot_value), "pnl": round(tot_value - tot_cost),
            "ret_pct": round((tot_value / tot_cost - 1) * 100, 2),
            "bench_value": round(tot_bench), "bench_pnl": round(tot_bench - tot_cost),
            "bench_ret_pct": round((tot_bench / tot_cost - 1) * 100, 2),
            "vs_bench_amt": round(tot_value - tot_bench),
            "vs_bench_pct": round((tot_value / tot_cost - tot_bench / tot_cost) * 100, 2),
        },
        "week": {
            "portfolio_pnl": round(week_pnl),
            "portfolio_pct": round((tot_value / prev_value - 1) * 100, 2) if prev_value else None,
            "bench_pct": week_pct(bench),
            "index_pct": week_pct(index),
        },
        "warnings": warnings,
    }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 文字輸出
# ─────────────────────────────────────────────────────────────────────────────
def fmt_amt(x):
    return f"{x/10000:+.1f} 萬" if abs(x) >= 10000 else f"{x:+,.0f}"


def line_block(res):
    if "error" in res:
        return f"📈 0050 對照：無法計算（{res['error']}）"
    t, w = res["total"], res["week"]
    verdict = "領先" if t["vs_bench_amt"] >= 0 else "落後"
    L = [f"📈 實單 vs 0050（自 {res['baseline']} 建倉，至 {res['as_of']}）",
         f"  實單 {t['ret_pct']:+.1f}%（{fmt_amt(t['pnl'])}）",
         f"  同錢買 0050 {t['bench_ret_pct']:+.1f}%（{fmt_amt(t['bench_pnl'])}）",
         f"  → {verdict} 0050 {abs(t['vs_bench_pct']):.1f} 個百分點（{fmt_amt(t['vs_bench_amt'])}）",
         f"  本週：持股 {w['portfolio_pct']:+.1f}% ｜ 0050 {w['bench_pct']:+.1f}% ｜ 加權 {w['index_pct']:+.1f}%"
         if None not in (w["portfolio_pct"], w["bench_pct"], w["index_pct"]) else f"  本週：持股 {fmt_amt(w['portfolio_pnl'])}"]
    for r in sorted(res["positions"], key=lambda r: -r["cost_amt"]):
        L.append(f"  {r['name'] or r['ticker']} {r['ret_pct']:+.1f}% vs 0050 {r['bench_ret_pct']:+.1f}%（{r['alpha_pct']:+.1f}）")
    if res.get("warnings"):
        L.append("  ⚠️ " + "；".join(res["warnings"]))
    L.append("  判準：一年後沒贏過 0050 → 主力資金換 0050（DAILY_SOP）")
    return "\n".join(L)


def md_block(res):
    if "error" in res:
        return f"# 實單 vs 0050\n\n無法計算：{res['error']}\n"
    t, w = res["total"], res["week"]
    L = [f"# 實單 vs 0050 對照（至 {res['as_of']}）", "",
         f"> 問題只有一個：同樣的錢、同一天，買 0050 現在是多少。基準日 {res['baseline']}。每檔用自己的建倉日對照。", "",
         "| 合計 | 實單 | 同錢買 0050 | 差 |", "|---|---|---|---|",
         f"| 成本 {t['cost']:,} | {t['value']:,}（{t['ret_pct']:+.2f}%） | {t['bench_value']:,}（{t['bench_ret_pct']:+.2f}%） | {t['vs_bench_amt']:+,}（{t['vs_bench_pct']:+.2f} pp） |", "",
         f"本週（近 {WEEK_DAYS} 個交易日）：持股 {w['portfolio_pct']}% ｜ 0050 {w['bench_pct']}% ｜ 加權 {w['index_pct']}%", "",
         "| 持股 | 股數 | 成本 | 現價 | 報酬 | 0050 同期 | 差 | 本週損益 |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(res["positions"], key=lambda r: -r["cost_amt"]):
        L.append(f"| {r['name']} {r['ticker']} | {r['shares']:.0f} | {r['avg_cost']:,.2f} | {r['last']:,.1f} | {r['ret_pct']:+.2f}% | {r['bench_ret_pct']:+.2f}% | {r['alpha_pct']:+.2f} pp | {r['week_pnl']:+,} |")
    if res.get("warnings"):
        L += ["", "⚠️ " + "；".join(res["warnings"])]
    L += ["", "判準（DAILY_SOP.md）：一年後整套系統沒贏過 0050 → 主力資金換 0050，動能倉繼續驗證。", ""]
    return "\n".join(L)


def load_positions():
    d = json.loads(HOLDINGS.read_text(encoding="utf-8"))
    return [p for p in d.get("tw", []) if p.get("shares") and p.get("avg_cost") and p.get("since")
            and not p.get("exclude_from_benchmark") and str(p.get("ticker")) != BENCH]


def run(write=True, price_fn=None):
    res = compute(load_positions(), price_fn or yf_closes)
    if write:
        OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        OUT_MD.write_text(md_block(res), encoding="utf-8")
    return res


if __name__ == "__main__":
    res = run()
    print(line_block(res))
    sys.exit(1 if "error" in res else 0)
