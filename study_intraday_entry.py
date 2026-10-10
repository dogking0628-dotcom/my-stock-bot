# -*- coding: utf-8 -*-
"""
盤中進場位置研究：訊號隔日，一天當中哪個時段買最好？
═══════════════════════════════════════════════════════
資料源：top5_history.json（系統近 30 個交易日「實際推播」的訊號）
        × yfinance 15 分鐘線（台股僅回溯 60 天，故只能用近期樣本）

輸出：
 ① 每個 15 分鐘 bar 的價格（以當日開盤=100 標準化）→ 平均盤中路徑
 ② 當日最低點出現在哪個時段的分布
 ③ 「開盤買」vs「各時段買」到收盤/T+5 的報酬差
 ④ 訊號日收漲停 vs 未漲停 的路徑差異
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass

import pandas as pd
import numpy as np
import yfinance as yf
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_signals():
    """從 top5_history 取出 (訊號日, ticker, 訊號日收盤, 訊號日漲幅, 是否漲停)"""
    with io.open(os.path.join(ROOT, "top5_history.json"), encoding="utf-8") as f:
        recs = json.load(f)["records"]
    out = []
    for r in recs:
        for p in r.get("picks", []):
            chg = p.get("change_pct_at_rec")
            out.append({
                "sig_date": r["date"],
                "ticker": p["ticker"],
                "name": p.get("name"),
                "sig_close": p.get("rec_close"),
                "sig_chg": round(chg, 2) if chg is not None else None,
                "limit_up": (chg is not None and chg >= 9.5)
                            or ("漲停鎖死" in (p.get("momentum_notes") or [])),
                "score": p.get("momentum_score"),
            })
    return out


def next_session(intraday_dates, sig_date):
    """訊號日之後的第一個有分鐘資料的交易日"""
    for d in intraday_dates:
        if d > sig_date:
            return d
    return None


def main():
    sigs = load_signals()
    tickers = sorted({s["ticker"] for s in sigs})
    print(f"訊號 {len(sigs)} 筆 / {len(tickers)} 檔  "
          f"（{min(s['sig_date'] for s in sigs)} ~ {max(s['sig_date'] for s in sigs)}）")
    lu = sum(1 for s in sigs if s["limit_up"])
    print(f"其中訊號日收漲停：{lu} 筆 ({lu/len(sigs)*100:.0f}%)")

    rows = []
    skipped = defaultdict(int)
    for tk in tickers:
        try:
            df = yf.download(f"{tk}.TW", period="60d", interval="15m",
                             auto_adjust=True, progress=False, threads=False,
                             group_by="column")
        except Exception as e:
            skipped["download_error"] += 1
            continue
        if df is None or df.empty:
            skipped["empty"] += 1
            continue
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(subset=["Close"])
        if df.empty:
            skipped["empty"] += 1
            continue
        # 轉台北時間並依日期分組
        idx = df.index
        try:
            idx = idx.tz_convert("Asia/Taipei")
        except Exception:
            pass
        df = df.copy()
        df["_d"] = [t.strftime("%Y-%m-%d") for t in idx]
        df["_t"] = [t.strftime("%H:%M") for t in idx]
        sess = {d: g for d, g in df.groupby("_d")}
        sess_dates = sorted(sess.keys())

        for s in [x for x in sigs if x["ticker"] == tk]:
            nd = next_session(sess_dates, s["sig_date"])
            if nd is None:
                skipped["no_next_session"] += 1
                continue
            g = sess[nd]
            if len(g) < 8:                      # 台股一天約 17-18 根 15m bar
                skipped["short_session"] += 1
                continue
            o = float(g["Open"].iloc[0])
            if not np.isfinite(o) or o <= 0:
                continue
            day_close = float(g["Close"].iloc[-1])
            lows = g["Low"].astype(float).values
            times = list(g["_t"])
            low_i = int(np.argmin(lows))
            # T+5 收盤（用日線）
            rows.append({
                "ticker": tk, "name": s["name"], "sig_date": s["sig_date"],
                "entry_date": nd, "limit_up": s["limit_up"], "score": s["score"],
                "sig_close": s["sig_close"], "open": o, "day_close": day_close,
                "open_to_close": round((day_close / o - 1) * 100, 2),
                "gap": round((o / s["sig_close"] - 1) * 100, 2) if s["sig_close"] else None,
                "low_time": times[low_i],
                "low_pct_from_open": round((float(lows[low_i]) / o - 1) * 100, 2),
                # 每根 bar 的收盤（標準化 open=100）
                "path": [{"t": t, "v": round(float(c) / o * 100, 3)}
                         for t, c in zip(times, g["Close"].astype(float).values)],
            })

    print(f"可用樣本 {len(rows)} 筆；略過 {dict(skipped)}")
    if not rows:
        print("無樣本（yfinance 15m 僅回溯 60 天）")
        return

    # ── ① 平均盤中路徑 ──
    by_t = defaultdict(list)
    for r in rows:
        for p in r["path"]:
            by_t[p["t"]].append(p["v"])
    print("\n" + "=" * 62)
    print("① 平均盤中路徑（訊號隔日；開盤 = 100）")
    print("=" * 62)
    print(f"  {'時間':<8}{'中位數':>9}{'平均':>9}{'低於開盤%':>11}{'樣本':>7}")
    for t in sorted(by_t.keys()):
        v = by_t[t]
        if len(v) < max(5, len(rows) * 0.4):
            continue
        below = sum(1 for x in v if x < 100) / len(v) * 100
        print(f"  {t:<8}{np.median(v):>9.2f}{np.mean(v):>9.2f}{below:>10.0f}%{len(v):>7}")

    # ── ② 當日最低出現時段 ──
    print("\n" + "=" * 62)
    print("② 當日最低點出現在哪個時段")
    print("=" * 62)
    cnt = defaultdict(int)
    for r in rows:
        cnt[r["low_time"]] += 1
    for t in sorted(cnt.keys()):
        bar = "█" * int(cnt[t] / max(cnt.values()) * 30)
        print(f"  {t:<8}{cnt[t]:>4} ({cnt[t]/len(rows)*100:>4.0f}%) {bar}")
    早盤 = sum(c for t, c in cnt.items() if t < "10:00")
    print(f"\n  ▸ 09:00-10:00 出現最低的比例：{早盤/len(rows)*100:.0f}%")

    # ── ③ 各時段買 vs 開盤買 ──
    print("\n" + "=" * 62)
    print("③ 在各時段買，到當日收盤的報酬（vs 開盤買）")
    print("=" * 62)
    base = np.mean([r["open_to_close"] for r in rows])
    print(f"  開盤(09:00)買 → 收盤：平均 {base:+.2f}%  "
          f"勝率 {sum(1 for r in rows if r['open_to_close']>0)/len(rows)*100:.0f}%")
    for t in sorted(by_t.keys()):
        vals = []
        for r in rows:
            pm = {p["t"]: p["v"] for p in r["path"]}
            if t not in pm:
                continue
            buy = pm[t]
            end = r["path"][-1]["v"]
            vals.append((end / buy - 1) * 100)
        if len(vals) < max(5, len(rows) * 0.4):
            continue
        print(f"  {t} 買 → 收盤：平均 {np.mean(vals):+6.2f}%  中位 {np.median(vals):+6.2f}%  "
              f"勝率 {sum(1 for x in vals if x>0)/len(vals)*100:>3.0f}%  "
              f"vs開盤 {np.mean(vals)-base:+.2f}pp")

    # ── ④ 漲停 vs 未漲停 ──
    print("\n" + "=" * 62)
    print("④ 訊號日收漲停 vs 未漲停")
    print("=" * 62)
    for lbl, grp in [("訊號日漲停", [r for r in rows if r["limit_up"]]),
                     ("訊號日未漲停", [r for r in rows if not r["limit_up"]])]:
        if not grp:
            print(f"  {lbl}: 無樣本")
            continue
        gaps = [r["gap"] for r in grp if r["gap"] is not None]
        print(f"  {lbl} ({len(grp)} 筆)：")
        print(f"     跳空 gap 中位 {np.median(gaps):+.2f}%" if gaps else "")
        print(f"     開盤→收盤 平均 {np.mean([r['open_to_close'] for r in grp]):+.2f}%  "
              f"勝率 {sum(1 for r in grp if r['open_to_close']>0)/len(grp)*100:.0f}%")
        print(f"     開盤→當日最低 平均 {np.mean([r['low_pct_from_open'] for r in grp]):+.2f}%")

    json.dump(rows, io.open(os.path.join(ROOT, "data", "intraday_entry_study.json"),
                            "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n💾 data/intraday_entry_study.json")


if __name__ == "__main__":
    main()
