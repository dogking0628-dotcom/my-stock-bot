# -*- coding: utf-8 -*-
"""
停損／出場時機研究：觸發日收盤賣 vs 隔日開盤賣（現行規則）vs 隔日收盤賣
═══════════════════════════════════════════════════════════════
資料：backtest_ath_definition.json 的 monthly_5y_trades（正式月線定義，5y，145 筆）
      每筆有 entry/entry_date/exit（=隔日開盤價）/exit_date。觸發日 = exit_date 前一交易日。
問題：出場規則是「收盤跌破線 → 隔日開盤賣」。若改成觸發日 13:25 就賣（線可在尾盤算出），
      或隔日收盤賣，報酬差多少？隔日開盤在出場日 K 棒的位置如何？
"""
import sys, os, io, json
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    res = json.load(io.open(os.path.join(ROOT, "backtest_ath_definition.json"), encoding="utf-8"))
    trades = res.get("monthly_5y_trades") or []
    print(f"交易 {len(trades)} 筆（monthly 5y）")
    cache = {}
    def hist(tk):
        if tk not in cache:
            df = yf.download(f"{tk}.TW", period="6y", interval="1d", auto_adjust=True,
                             progress=False, threads=False, group_by="column")
            if df is not None and not df.empty:
                if hasattr(df.columns, "levels"):
                    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                df = df.dropna(subset=["Close"]).copy()
                df.index = [t.strftime("%Y-%m-%d") for t in df.index]
            cache[tk] = df
        return cache[tk]

    rows = []
    for t in trades:
        df = hist(t["ticker"])
        if df is None or df.empty or t["exit_date"] not in df.index:
            continue
        i = df.index.get_loc(t["exit_date"])
        if i < 1:
            continue
        e = float(t["entry"])
        trig_close = float(df["Close"].iloc[i - 1])
        nxt_open, nxt_high, nxt_low, nxt_close = (float(df["Open"].iloc[i]), float(df["High"].iloc[i]),
                                                  float(df["Low"].iloc[i]), float(df["Close"].iloc[i]))
        if e <= 0 or nxt_high <= nxt_low:
            continue
        rows.append({
            "ticker": t["ticker"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
            "ret_rule": (nxt_open / e - 1) * 100,           # 現行：隔日開盤
            "ret_trig": (trig_close / e - 1) * 100,         # 觸發日收盤賣
            "ret_nxtc": (nxt_close / e - 1) * 100,          # 隔日收盤賣
            "gap": (nxt_open / trig_close - 1) * 100,       # 出場日跳空
            "open_pos": (nxt_open - nxt_low) / (nxt_high - nxt_low) * 100,
            "o2c": (nxt_close / nxt_open - 1) * 100,
            "bt_exit_check": abs(nxt_open - float(t["exit"])) / float(t["exit"]) * 100,
        })
    n = len(rows)
    print(f"可用 {n} 筆（yfinance 對齊誤差中位 {np.median([r['bt_exit_check'] for r in rows]):.2f}%）")
    f = lambda k: np.array([r[k] for r in rows])
    print("\n" + "=" * 66)
    print("① 三種出場時點的每筆報酬（同一批交易，只換賣點）")
    print("=" * 66)
    for k, lbl in [("ret_trig", "觸發日收盤賣（13:25 掛單）"), ("ret_rule", "隔日開盤賣（現行規則）"), ("ret_nxtc", "隔日收盤賣")]:
        v = f(k)
        print(f"  {lbl:<22} 平均 {v.mean():+6.2f}%  中位 {np.median(v):+6.2f}%  合計 {v.sum():+8.1f}%")
    d1 = f("ret_trig") - f("ret_rule"); d2 = f("ret_nxtc") - f("ret_rule")
    print(f"\n  觸發日收盤 vs 隔日開盤：平均 {d1.mean():+.2f}pp／筆，觸發日較好的比例 {np.mean(d1>0)*100:.0f}%")
    print(f"  隔日收盤 vs 隔日開盤：平均 {d2.mean():+.2f}pp／筆，隔日收盤較好的比例 {np.mean(d2>0)*100:.0f}%")
    print("\n" + "=" * 66)
    print("② 出場日（隔日）的微結構")
    print("=" * 66)
    g = f("gap"); op = f("open_pos"); oc = f("o2c")
    print(f"  跳空（隔日開 vs 觸發日收）：中位 {np.median(g):+.2f}%  平均 {g.mean():+.2f}%  開低比例 {np.mean(g<0)*100:.0f}%")
    print(f"  隔日開盤在當日 range 位置：中位 {np.median(op):.0f}（0=最低 100=最高）  >70 比例 {np.mean(op>70)*100:.0f}%  <30 比例 {np.mean(op<30)*100:.0f}%")
    print(f"  隔日開→收：中位 {np.median(oc):+.2f}%  平均 {oc.mean():+.2f}%  收高於開 {np.mean(oc>0)*100:.0f}%")
    # 依觸發類型分組：用 ret_trig 粗分「深跌（<-5%）」vs 其他
    deep = [r for r in rows if r["ret_trig"] < -5]; rest = [r for r in rows if r["ret_trig"] >= -5]
    print("\n" + "=" * 66)
    print("③ 分組：觸發時已虧 >5%（樓地板型）vs 其他（20MA 型）")
    print("=" * 66)
    for lbl, grp in [("樓地板型", deep), ("20MA 型", rest)]:
        if not grp: continue
        a = np.array([r["ret_trig"] - r["ret_rule"] for r in grp]); gg = np.array([r["gap"] for r in grp])
        print(f"  {lbl}（n={len(grp)}）：觸發日收盤賣 vs 隔日開盤 {a.mean():+.2f}pp／筆；隔日跳空中位 {np.median(gg):+.2f}%")
    json.dump(rows, io.open(os.path.join(ROOT, "data", "exit_timing_study.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n💾 data/exit_timing_study.json")


if __name__ == "__main__":
    main()
