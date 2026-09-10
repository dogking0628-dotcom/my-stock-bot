# -*- coding: utf-8 -*-
"""
注意股事件研究 ＋「漲到警示線出場/過濾」策略回測（2026-09-09 用戶提案）
═════════════════════════════════════════════════
注意事件以「漲幅條款公式重建」（6日>32% / 30日>100% / 60日>130% / 90日>160%），
非官方名單（官方另有量/週轉/PE 條款）；漲幅條款覆蓋動能股絕大多數觸發原因。

Part A 事件研究：全市場「首次觸發 6日+32%」事件日 → T+1/3/5/10/20 前瞻報酬分佈
Part B 策略回測（V4.4 基礎，5y 窗）：
  A_base    現行 V4.4
  F1_filter 進場日已觸任一注意標準 → 跳過不買（昨日永擎案）
  E1_exit   持倉觸 6日+32% → 隔日開盤獲利了結（先於 20MA）
  E2_exit   持倉觸任一標準 → 隔日開盤了結
  FE_combo  F1 + E2
"""
import sys, os, json, io
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from collections import defaultdict, deque
import backtest_strategy as bs
import backtest_v4_1 as v41
from industry_map_loader import get_industry

ALLOWED = v41.ALLOWED
MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80
RECENT_LOSER_WINDOW = 7
HARD_FLOOR = 0.93
TRADE_START = "2021-08-05"
THRESH = [(6, 32.0), (30, 100.0), (60, 130.0), (90, 160.0)]

VARIANTS = {
    "A_base":    {"desc": "現行V4.4"},
    "F1_filter": {"desc": "進場日已觸注意標準→不買"},
    "E1_exit":   {"desc": "持倉觸6日+32%→隔開了結"},
    "E2_exit":   {"desc": "持倉觸任一標準→隔開了結"},
    "FE_combo":  {"desc": "F1過濾+E2了結"},
}


def precompute_cums(history):
    """{code: {win: pd.Series 累計漲幅%}}"""
    out = {}
    for c, df in history.items():
        cl = df["Close"]
        d = {}
        for win, _ in THRESH:
            d[win] = (cl / cl.shift(win) - 1) * 100
        out[c] = d
    return out


def hit_any(cums_c, i):
    for win, th in THRESH:
        v = cums_c[win].iloc[i]
        if pd.notna(v) and v > th:
            return True, win
    return False, None


def event_study(history, cums, mcap):
    """首次觸發 6日+32%（前一日未觸）→ T+N 前瞻報酬；20日內去重"""
    rows = []
    for c, df in history.items():
        cl = df["Close"].values
        s = cums[c][6]
        last_ev = -99
        for i in range(7, len(cl) - 1):
            v, vp = s.iloc[i], s.iloc[i - 1]
            if pd.isna(v) or pd.isna(vp): continue
            if v > 32 and vp <= 32 and i - last_ev > 20:
                last_ev = i
                fw = {}
                for n in (1, 3, 5, 10, 20):
                    if i + n < len(cl) and cl[i] > 0:
                        fw[n] = (cl[i + n] / cl[i] - 1) * 100
                rows.append({"code": c, "tech": get_industry(c) in ALLOWED and (mcap.get(c) or 0) >= MIN_MCAP, **fw})
    def summarize(sub, label):
        print(f"\n📊 事件研究[{label}]：n={len(sub)}")
        print(f"{'T+N':>5}{'平均':>9}{'中位':>9}{'勝率':>7}{'P10':>9}{'P90':>9}")
        for n in (1, 3, 5, 10, 20):
            vals = [r[n] for r in sub if n in r]
            if not vals: continue
            vals_s = sorted(vals)
            print(f"{n:>5}{np.mean(vals):>+8.2f}%{np.median(vals):>+8.2f}%"
                  f"{sum(1 for x in vals if x > 0)/len(vals)*100:>6.0f}%"
                  f"{vals_s[int(len(vals)*0.1)]:>+8.1f}%{vals_s[int(len(vals)*0.9)]:>+8.1f}%")
    summarize(rows, "全市場")
    summarize([r for r in rows if r["tech"]], "科技+百億(系統池)")
    return rows


def run_variant(name, history, mcap, us_chg, regime, cums, df_idx, all_dates):
    cash = bs.INITIAL; positions = {}; trades = []; losers = deque(maxlen=400)
    n_filtered = 0
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
            i = cf["_i"]
            reason = None
            if cf["close"] < cf["ma20"] or cf["close"] < pos["peak"]*0.7 or cf["close"] < pos["entry_price"]*HARD_FLOOR:
                reason = "exit"
            if reason is None and name in ("E1_exit", "E2_exit", "FE_combo"):
                if name == "E1_exit":
                    v6 = cums[c][6].iloc[i]
                    if pd.notna(v6) and v6 > 32: reason = "注意線了結"
                else:
                    h, _ = hit_any(cums[c], i)
                    if h: reason = "注意線了結"
            if reason:
                nd = all_dates[di+1] if di+1 < len(all_dates) else None
                if nd is None: continue
                ni = df_idx[c].get(nd)
                if ni is None: continue
                sp = history[c]["Open"].iloc[ni]
                cash += pos["shares"]*sp*(1-bs.COMMISSION-bs.TAX)
                ret = (sp/pos["entry_price"]-1)*100
                trades.append({"ticker": c, "entry_date": pos["entry_date"], "exit_date": str(nd.date()),
                               "entry": pos["entry_price"], "exit": sp, "ret_pct": ret, "reason": reason,
                               "hold_days": (nd-pd.Timestamp(pos["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del positions[c]
        if d_str < TRADE_START or not in_stage2: continue

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
        if name in ("F1_filter", "FE_combo"):
            keep = []
            for r in pool:
                h, _ = hit_any(cums[r["ticker"]], r["_i"])
                if h: n_filtered += 1
                else: keep.append(r)
            pool = keep
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
    return cash, trades, n_filtered


def main():
    bs.START_DATE = "2020-08-01"
    bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print("預計算累計漲幅...")
    cums = precompute_cums(history)

    event_study(history, cums, mcap)

    results = {}
    for name, cfg in VARIANTS.items():
        print("\n" + "="*60 + f"\n▶ {name}: {cfg['desc']}\n" + "="*60)
        cash, trades, nf = run_variant(name, history, mcap, us_chg, regime, cums, df_idx, all_dates)
        if nf: print(f"  進場過濾掉 {nf} 檔次")
        bs.report(cash, trades, label=name, run_stress=False)
        results[name] = {"desc": cfg["desc"], "final_cash": cash, "n": len(trades), "trades": trades}

    print("\n" + "="*80); print("📊 匯總（5y 窗 2021-08-05 起）"); print("="*80)
    print(f"{'變體':<11}{'說明':<26}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>6}{'期望':>8}{'PF':>6}{'MDD':>8}")
    yrs = (pd.Timestamp(dt.date.today().isoformat())-pd.Timestamp(TRADE_START)).days/365.25
    for name, r in results.items():
        t = r["trades"]; n = len(t)
        if n == 0: print(f"{name:<11}{r['desc']:<26}{'無交易':>9}"); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100
        cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/n*100; ex = sum(x["ret_pct"] for x in t)/n
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
        eq = bs.INITIAL; peak = eq; mdd = 0
        for x in sorted(t, key=lambda z: z["exit_date"]):
            eq *= (1 + x["ret_pct"]/100/bs.MAX_POS)
            peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
        wexit = [x for x in t if x["reason"] == "注意線了結"]
        extra = f"  (注意線了結 {len(wexit)} 筆/均{sum(x['ret_pct'] for x in wexit)/len(wexit):+.1f}%)" if wexit else ""
        print(f"{name:<11}{r['desc']:<26}{tot:>+8.1f}%{cagr:>+7.1f}%{n:>5}{wr:>5.0f}%{ex:>+7.2f}%{pf:>6.2f}{mdd*100:>+7.1f}%{extra}")
    json.dump(results, io.open("backtest_notice_exit.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_notice_exit.json")


if __name__ == "__main__":
    main()
