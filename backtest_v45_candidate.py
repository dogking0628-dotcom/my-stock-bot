# -*- coding: utf-8 -*-
"""
V4.5 候選驗證：V4.4（含20MA減速器）+ F1 注意股過濾 疊加測試（2y+5y 雙窗）
═════════════════════════════════════════════════
F1 單獨（vs V4.3 基礎）5y +264.7%/期望+10.5%/PF3.53/MDD-22.3% 已過紅線；
本測驗證與 V4.4 減速器疊加是否仍有增益（兩者可能重疊：減速日常伴隨過熱股）。
變體×兩窗：V44_base（現行）/ V45_F1（V4.4 + 進場日已觸注意漲幅標準→不買）
"""
import sys, os, json, io
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from collections import defaultdict, deque
import yfinance as yf
import backtest_strategy as bs
import backtest_v4_1 as v41
from industry_map_loader import get_industry

ALLOWED = v41.ALLOWED
MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80
RECENT_LOSER_WINDOW = 7
HARD_FLOOR = 0.93
THRESH = [(6, 32.0), (30, 100.0), (60, 130.0), (90, 160.0)]


def fetch_0050_series():
    df = yf.download("0050.TW", start="2020-06-01", end=dt.date.today().isoformat(),
                     auto_adjust=True, progress=False, group_by="column")
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    cl = df["Close"].dropna()
    ma20 = cl.rolling(20).mean()
    return {d.strftime("%Y-%m-%d"): float(cl.iloc[i]) < float(ma20.iloc[i])
            for i, d in enumerate(cl.index) if pd.notna(ma20.iloc[i])}


def precompute_cums(history):
    out = {}
    for c, df in history.items():
        cl = df["Close"]
        out[c] = {win: (cl / cl.shift(win) - 1) * 100 for win, _ in THRESH}
    return out


def hit_any(cums_c, i):
    for win, th in THRESH:
        v = cums_c[win].iloc[i]
        if pd.notna(v) and v > th:
            return True
    return False


def run_variant(name, trade_start, history, mcap, us_chg, regime, below20, cums, df_idx, all_dates):
    cash = bs.INITIAL; positions = {}; trades = []; losers = deque(maxlen=400)
    for di, d in enumerate(all_dates):
        if di < 200: continue
        d_str = d.strftime("%Y-%m-%d")
        prev_str = all_dates[di-1].strftime("%Y-%m-%d")
        in_stage2 = bool(regime.get(d_str, False))
        cutoff = d - pd.Timedelta(days=RECENT_LOSER_WINDOW)
        recent_losers = {t for ed, t in losers if pd.Timestamp(ed) >= cutoff}

        cands = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = bs.daily_features(df, i)
            if not f: continue
            f["ticker"] = c; f["industry"] = get_industry(c); f["_i"] = i
            cands.append(f)
        cur = {r["ticker"]: r for r in cands}
        by_ind = defaultdict(list)
        for r in cands: by_ind[r.get("industry") or "未分類"].append(r)
        ind_up = {k: sum(1 for x in v if x["change_pct"] > 0)/max(len(v),1) for k, v in by_ind.items()}
        for r in cands: r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        for c in list(positions):
            cf = cur.get(c)
            if not cf: continue
            pos = positions[c]; pos["peak"] = max(pos["peak"], cf["close"])
            if (cf["close"] < cf["ma20"] or cf["close"] < pos["peak"]*0.7
                    or cf["close"] < pos["entry_price"]*HARD_FLOOR):
                nd = all_dates[di+1] if di+1 < len(all_dates) else None
                if nd is None: continue
                ni = df_idx[c].get(nd)
                if ni is None: continue
                sp = history[c]["Open"].iloc[ni]
                cash += pos["shares"]*sp*(1-bs.COMMISSION-bs.TAX)
                ret = (sp/pos["entry_price"]-1)*100
                trades.append({"ticker": c, "entry_date": pos["entry_date"], "exit_date": str(nd.date()),
                               "entry": pos["entry_price"], "exit": sp, "ret_pct": ret, "reason": "exit",
                               "hold_days": (nd-pd.Timestamp(pos["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del positions[c]
        if d_str < trade_start or not in_stage2: continue
        if below20.get(d_str, False):   # V4.4 減速器（兩變體皆含）
            continue

        ath = [r for r in cands if r["is_ath"] and r.get("industry") in ALLOWED
               and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP
               and r["ticker"] not in recent_losers]
        for r in ath:
            sc, _ = bs.momentum_score(r)
            sc += v41.us_bonus(r["industry"], prev_str, us_chg)
            r["score"] = min(sc, 100)
        ath_by = defaultdict(list)
        for r in ath: ath_by[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by.items(), key=lambda x: -len(x[1])):
            if len(lst) >= 3 and sum(1 for x in lst if x["bullish"])/len(lst) >= 0.5:
                strongest = ind; break
        pool = [r for r in (ath_by[strongest] if strongest else ath) if r["score"] >= SCORE_THRESHOLD]
        if name == "V45_F1":
            pool = [r for r in pool if not hit_any(cums[r["ticker"]], r["_i"])]
        pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))
        picks = pool[:5]

        slots = bs.MAX_POS - len(positions)
        if slots <= 0 or not picks: continue
        nd = all_dates[di+1] if di+1 < len(all_dates) else None
        if nd is None: continue
        for r in picks[:slots]:
            c = r["ticker"]
            if c in positions: continue
            ni = df_idx[c].get(nd)
            if ni is None: continue
            bp = history[c]["Open"].iloc[ni]
            if cash < bs.PER_POS*0.5: break
            cps = bp*(1+bs.COMMISSION); sh = int(min(bs.PER_POS, cash)/cps/1000)*1000
            if sh < 1000: continue
            cash -= sh*cps
            positions[c] = {"entry_price": bp, "shares": sh, "peak": bp, "entry_date": str(nd.date())}
    fd = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(fd)
        if i is not None:
            cash += pos["shares"]*history[c]["Close"].iloc[i]*(1-bs.COMMISSION-bs.TAX)
    return cash, trades


def main():
    bs.START_DATE = "2020-08-01"
    bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    below20 = fetch_0050_series()
    history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print("預計算累計漲幅...")
    cums = precompute_cums(history)

    print("\n" + "="*84)
    print(f"{'窗口':<14}{'變體':<9}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>6}{'期望':>8}{'PF':>6}{'MDD':>8}")
    results = {}
    for ts, wl in (("2021-08-05", "5y"), ("2024-08-05", "2y")):
        for name in ("V44_base", "V45_F1"):
            cash, trades = run_variant(name, ts, history, mcap, us_chg, regime, below20, cums, df_idx, all_dates)
            n = len(trades)
            w = [x for x in trades if x["ret_pct"] > 0]; l = [x for x in trades if x["ret_pct"] <= 0]
            yrs = (pd.Timestamp(dt.date.today().isoformat())-pd.Timestamp(ts)).days/365.25
            tot = (cash/bs.INITIAL-1)*100
            cagr = ((cash/bs.INITIAL)**(1/yrs)-1)*100 if cash > 0 else 0
            wr = len(w)/n*100 if n else 0
            ex = sum(x["ret_pct"] for x in trades)/n if n else 0
            pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
            eq = bs.INITIAL; peak = eq; mdd = 0
            for x in sorted(trades, key=lambda z: z["exit_date"]):
                eq *= (1 + x["ret_pct"]/100/bs.MAX_POS)
                peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
            print(f"{wl+' '+ts:<14}{name:<9}{tot:>+8.1f}%{cagr:>+7.1f}%{n:>5}{wr:>5.0f}%{ex:>+7.2f}%{pf:>6.2f}{mdd*100:>+7.1f}%")
            results[f"{wl}_{name}"] = {"final_cash": cash, "n": n, "trades": trades}
    json.dump(results, io.open("backtest_v45_candidate.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_v45_candidate.json")


if __name__ == "__main__":
    main()
