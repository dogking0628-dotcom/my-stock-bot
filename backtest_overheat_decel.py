# -*- coding: utf-8 -*-
"""
偏熱減速器 — 2y 同窗對照回測（2026-09-04 總覆盤改進項 #2，用戶核准）
═════════════════════════════════════════════════
問題：0050>MA200 濾網在「頂部初跌段」全程放行 → 2026/7 月 22 筆合計 -181%。
變體（只擋/調「新進場」，出場規則全部不動）：
  A_base   — 現行 V4.3
  D1_ext   — 0050 距 MA200 >25% → 停新倉（最粗暴；預期誤殺 8 月）
  D2_score — 距 MA200 >25% → 分數門檻 80→90（減速不熄火）
  D3_ma20  — 0050 收盤 < 自身 20MA → 停新倉（大盤短期轉弱，不看偏熱）
  D4_combo — 距 MA200 >25% 且 0050<20MA → 停新倉（偏熱＋轉弱雙確認）
驗證重點：能不能砍掉 7 月災難、同時保住 8 月 +439%。
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
TRADE_START = "2024-08-05"
EXT_HOT = 25.0

VARIANTS = {
    "A_base":   {"desc": "現行V4.3"},
    "D1_ext":   {"desc": "距MA200>25% 停新倉"},
    "D2_score": {"desc": "距MA200>25% 門檻80→90"},
    "D3_ma20":  {"desc": "0050<自身20MA 停新倉"},
    "D4_combo": {"desc": "偏熱>25% 且 0050<20MA 停新倉"},
}


def fetch_0050_series():
    """0050 日線 → {date_str: (ext_pct, below_ma20)}"""
    df = yf.download("0050.TW", start="2023-06-01", end=dt.date.today().isoformat(),
                     auto_adjust=True, progress=False, group_by="column")
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    cl = df["Close"].dropna()
    ma200 = cl.rolling(200).mean()
    ma20 = cl.rolling(20).mean()
    out = {}
    for i, d in enumerate(cl.index):
        if pd.isna(ma200.iloc[i]) or pd.isna(ma20.iloc[i]): continue
        out[d.strftime("%Y-%m-%d")] = ((float(cl.iloc[i]) / float(ma200.iloc[i]) - 1) * 100,
                                       float(cl.iloc[i]) < float(ma20.iloc[i]))
    return out


def decel_block_new_entry(name, ext, below20):
    if ext is None:
        return False, SCORE_THRESHOLD
    if name == "D1_ext" and ext > EXT_HOT:
        return True, SCORE_THRESHOLD
    if name == "D2_score" and ext > EXT_HOT:
        return False, 90
    if name == "D3_ma20" and below20:
        return True, SCORE_THRESHOLD
    if name == "D4_combo" and ext > EXT_HOT and below20:
        return True, SCORE_THRESHOLD
    return False, SCORE_THRESHOLD


def run_variant(name, history, mcap, us_chg, regime, etf, df_idx, all_dates):
    cash = bs.INITIAL; positions = {}; trades = []; losers = deque(maxlen=400)
    n_blocked_days = 0
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
            f["ticker"] = c; f["industry"] = get_industry(c)
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
            hit = (cf["close"] < cf["ma20"] or cf["close"] < pos["peak"]*0.7
                   or cf["close"] < pos["entry_price"]*HARD_FLOOR)
            if hit:
                nd = all_dates[di+1] if di+1 < len(all_dates) else None
                if nd is None: continue
                ni = df_idx[c].get(nd)
                if ni is None: continue
                sp = history[c]["Open"].iloc[ni]
                cash += pos["shares"]*sp*(1-bs.COMMISSION-bs.TAX)
                ret = (sp/pos["entry_price"]-1)*100
                trades.append({"ticker": c, "industry": get_industry(c), "entry_date": pos["entry_date"],
                               "exit_date": str(nd.date()), "entry": pos["entry_price"], "exit": sp,
                               "ret_pct": ret, "reason": "exit",
                               "hold_days": (nd-pd.Timestamp(pos["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del positions[c]
        if d_str < TRADE_START or not in_stage2: continue

        ext, below20 = etf.get(d_str, (None, False))
        block, thr = decel_block_new_entry(name, ext, below20)
        if block:
            n_blocked_days += 1
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
        pool = [r for r in (ath_by[strongest] if strongest else ath) if r["score"] >= thr]
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
    return cash, trades, n_blocked_days


def main():
    bs.START_DATE = "2023-09-01"
    bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    etf = fetch_0050_series()
    print(f"0050 series {len(etf)} 日")
    history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    results = {}
    for name, cfg in VARIANTS.items():
        print("\n" + "="*60 + f"\n▶ {name}: {cfg['desc']}\n" + "="*60)
        cash, trades, nb = run_variant(name, history, mcap, us_chg, regime, etf, df_idx, all_dates)
        print(f"  減速器擋掉新倉日: {nb}")
        bs.report(cash, trades, label=name, run_stress=False)
        results[name] = {"desc": cfg["desc"], "final_cash": cash, "n": len(trades),
                         "blocked_days": nb, "trades": trades}

    print("\n" + "="*82); print("📊 匯總（2y 同窗 2024-08-05 起）"); print("="*82)
    print(f"{'變體':<10}{'說明':<28}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>6}{'期望':>8}{'PF':>6}{'MDD':>8}{'擋日':>5}")
    yrs = (pd.Timestamp(dt.date.today().isoformat())-pd.Timestamp(TRADE_START)).days/365.25
    for name, r in results.items():
        t = r["trades"]; n = len(t)
        if n == 0: print(f"{name:<10}{r['desc']:<28}{'無交易':>9}"); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100
        cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/n*100; ex = sum(x["ret_pct"] for x in t)/n
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
        eq = bs.INITIAL; peak = eq; mdd = 0
        for x in sorted(t, key=lambda z: z["exit_date"]):
            eq *= (1 + x["ret_pct"]/100/bs.MAX_POS)
            peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
        print(f"{name:<10}{r['desc']:<28}{tot:>+8.1f}%{cagr:>+7.1f}%{n:>5}{wr:>5.0f}%{ex:>+7.2f}%{pf:>6.2f}{mdd*100:>+7.1f}%{r['blocked_days']:>5}")

    # 2026 年 6-9 月逐月對比（7 月災難有沒有被擋、8 月有沒有被誤殺）
    print("\n📅 2026 關鍵月份逐月（各變體當月平倉合計%）")
    months = ["2026-06", "2026-07", "2026-08", "2026-09"]
    print(f"{'變體':<10}" + "".join(f"{m:>14}" for m in months))
    for name, r in results.items():
        mm = defaultdict(lambda: [0, 0.0])
        for x in r["trades"]:
            k = x["exit_date"][:7]
            mm[k][0] += 1; mm[k][1] += x["ret_pct"]
        print(f"{name:<10}" + "".join(f"{mm[m][0]:>3}筆{mm[m][1]:>+8.0f}%" for m in months))
    json.dump(results, io.open("backtest_overheat_decel.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_overheat_decel.json")


if __name__ == "__main__":
    main()
