# -*- coding: utf-8 -*-
"""
前哨回測（2y，投信-only）：張數 vs 金額 — 等 5y 外資資料解封前先看方向
變體：
  A base    純技術（V4.3 無籌碼）
  B shares  現行 V4.3：投信 5 日淨買「張數」>0 → +10
  C amount  投信 5 日淨買「金額」>0 → +10（同號，理論上≈B；差在 5 日累計 vs 當日）
  D amt1e   投信 5 日淨買金額 ≥ 1 億 → +10（門檻版）
  E amt_hard投信 5 日淨買金額 ≥ 1 億 硬條件
  F tiered  ≥3億 +15 / ≥1億 +8
"""
import sys, os, json, io
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from collections import defaultdict, deque
import backtest_strategy as bs, backtest_v4_1 as v41
from industry_map_loader import get_industry

ALLOWED = v41.ALLOWED; MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80; RECENT_LOSER_WINDOW = 7; HARD_FLOOR = 0.93; E8 = 1e8
TRADE_START = "2024-08-05"

def load_sitc():
    h = json.load(io.open("t86_history.json", encoding="utf-8"))
    return {k: v for k, v in h.items() if v}   # {yyyymmdd:{code:shares}}

def build_flow(sitc, history):
    """{code:{date_str:(shares5, amt5)}} 5日滾動"""
    out = {}
    for c, df in history.items():
        cl = df["Close"]; ds = [d.strftime("%Y%m%d") for d in df.index]
        sh = np.zeros(len(ds)); am = np.zeros(len(ds))
        for k, d in enumerate(ds):
            v = sitc.get(d, {}).get(c)
            if v: sh[k] = v; am[k] = v * float(cl.iloc[k])
        sh5 = pd.Series(sh).rolling(5, min_periods=1).sum().values
        am5 = pd.Series(am).rolling(5, min_periods=1).sum().values
        out[c] = {d.strftime("%Y-%m-%d"): (float(sh5[k]), float(am5[k])) for k, d in enumerate(df.index)}
    return out

def run(name, history, mcap, us_chg, regime, flow, df_idx, all_dates):
    cash = bs.INITIAL; pos = {}; trades = []; losers = deque(maxlen=400)
    for di, d in enumerate(all_dates):
        if di < 200: continue
        ds = d.strftime("%Y-%m-%d")
        if ds < TRADE_START: continue
        prev = all_dates[di-1].strftime("%Y-%m-%d")
        stage2 = regime.get(ds, False)
        cutoff = d - pd.Timedelta(days=RECENT_LOSER_WINDOW)
        rl = {t for ed, t in losers if pd.Timestamp(ed) >= cutoff}
        cands = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = bs.daily_features(df, i)
            if not f: continue
            f["ticker"] = c; f["industry"] = get_industry(c); cands.append(f)
        cur = {r["ticker"]: r for r in cands}
        by = defaultdict(list)
        for r in cands: by[r.get("industry") or "未分類"].append(r)
        up = {k: sum(1 for x in v if x["change_pct"] > 0)/max(len(v),1) for k, v in by.items()}
        for r in cands: r["industry_strong"] = up.get(r.get("industry") or "未分類", 0) >= 0.6
        for c in list(pos):
            cf = cur.get(c)
            if not cf: continue
            p = pos[c]; p["peak"] = max(p["peak"], cf["close"])
            if cf["close"] < cf["ma20"] or cf["close"] < p["peak"]*0.7 or cf["close"] < p["entry_price"]*HARD_FLOOR:
                nd = all_dates[di+1] if di+1 < len(all_dates) else None
                if nd is None: continue
                ni = df_idx[c].get(nd)
                if ni is None: continue
                sp = history[c]["Open"].iloc[ni]
                cash += p["shares"]*sp*(1-bs.COMMISSION-bs.TAX)
                ret = (sp/p["entry_price"]-1)*100
                trades.append({"ticker": c, "industry": get_industry(c), "entry_date": p["entry_date"],
                               "exit_date": str(nd.date()), "entry": p["entry_price"], "exit": sp,
                               "ret_pct": ret, "reason": "exit", "hold_days": (nd-pd.Timestamp(p["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del pos[c]
        if not stage2: continue
        ath = [r for r in cands if r["is_ath"] and r.get("industry") in ALLOWED and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP]
        for r in ath:
            sc, _ = bs.momentum_score(r); sc += v41.us_bonus(r["industry"], prev, us_chg)
            s5, a5 = flow.get(r["ticker"], {}).get(ds, (0.0, 0.0)); r["_s"], r["_a"] = s5, a5
            if name == "B_shares" and s5 > 0: sc += 10
            elif name == "C_amount" and a5 > 0: sc += 10
            elif name == "D_amt1e" and a5 >= E8: sc += 10
            elif name == "F_tiered":
                if a5 >= 3*E8: sc += 15
                elif a5 >= E8: sc += 8
            r["score"] = min(sc, 100)
        def ok(r):
            if r["ticker"] in rl or r["score"] < SCORE_THRESHOLD: return False
            if name == "E_amt_hard" and r["_a"] < E8: return False
            return True
        ab = defaultdict(list)
        for r in ath: ab[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ab.items(), key=lambda x: -len(x[1])):
            if len(lst) >= 3 and sum(1 for x in lst if x["bullish"])/len(lst) >= 0.5: strongest = ind; break
        pool = [r for r in (ab[strongest] if strongest else ath) if ok(r)]
        pool.sort(key=lambda x: (-x["score"], -x["change_pct"])); top5 = pool[:5]
        slots = bs.MAX_POS - len(pos)
        if slots <= 0 or not top5: continue
        nd = all_dates[di+1] if di+1 < len(all_dates) else None
        if nd is None: continue
        for r in top5[:slots]:
            c = r["ticker"]
            if c in pos: continue
            ni = df_idx[c].get(nd)
            if ni is None: continue
            bp = history[c]["Open"].iloc[ni]
            if cash < bs.PER_POS*0.5: break
            cps = bp*(1+bs.COMMISSION); sh = int(min(bs.PER_POS, cash)/cps/1000)*1000
            if sh < 1000: continue
            cash -= sh*cps; pos[c] = {"entry_price": bp, "shares": sh, "peak": bp, "entry_date": str(nd.date())}
    fd = all_dates[-1]
    for c, p in pos.items():
        i = df_idx[c].get(fd)
        if i is not None: cash += p["shares"]*history[c]["Close"].iloc[i]*(1-bs.COMMISSION-bs.TAX)
    return cash, trades

def main():
    bs.START_DATE = "2023-09-01"; bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap(); sitc = load_sitc()
    print(f"universe {len(codes)} / mcap {len(mcap)} / 投信 {len(sitc)} 日 ({min(sitc)}~{max(sitc)})")
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050(); history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    flow = build_flow(sitc, history)
    V = [("A_base","純技術"),("B_shares","現行:投信張數>0 +10"),("C_amount","投信金額>0 +10"),
         ("D_amt1e","投信金額≥1億 +10"),("E_amt_hard","投信金額≥1億 硬條件"),("F_tiered","≥3億+15/≥1億+8")]
    res = {}
    for n, desc in V:
        print("\n"+"="*60+f"\n▶ {n}: {desc}\n"+"="*60)
        cash, tr = run(n, history, mcap, us_chg, regime, flow, df_idx, all_dates)
        bs.report(cash, tr, label=n, run_stress=False); res[n] = {"desc": desc, "final_cash": cash, "trades": tr}
    yrs = (pd.Timestamp(bs.END_DATE)-pd.Timestamp(TRADE_START)).days/365.25
    print("\n"+"="*74+"\n📊 匯總（2y 投信-only 前哨）\n"+"="*74)
    print(f"{'變體':<11}{'說明':<24}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>7}{'期望':>8}{'PF':>6}{'MDD':>8}")
    from stress_test_lib import _equity_curve, _max_drawdown
    for n, r in res.items():
        t = r["trades"]; k = len(t)
        if not k: print(f"{n:<11}{r['desc']:<24}{'-':>9}"); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100; cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/k*100; ex = sum(x["ret_pct"] for x in t)/k
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
        mdd = _max_drawdown(_equity_curve(t))
        print(f"{n:<11}{r['desc']:<24}{tot:>+8.1f}%{cagr:>+7.1f}%{k:>5}{wr:>6.0f}%{ex:>+7.2f}%{pf:>6.2f}{mdd:>+7.1f}%")
    json.dump(res, io.open("backtest_sitc_amount_2y.json","w",encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_sitc_amount_2y.json")

if __name__ == "__main__": main()
