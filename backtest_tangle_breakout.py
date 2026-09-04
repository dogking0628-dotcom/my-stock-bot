# -*- coding: utf-8 -*-
"""
均線糾結突破 vs 創高追進 — 2y 同窗對照回測（2026-09-04 用戶提案）
═════════════════════════════════════════════════
型態：MA5/10/20 糾結（帶寬小）→ 第一根放量長紅（漲≥3% 出大量）→ 隔日開盤買
  A_chase  — 現行 V4.3（ATH 當日動能≥80 追）＝對照組
  T1_tangle— 昨日帶寬<3% ＋ 今漲≥3% ＋ 量比≥2 ＋ 昨漲<3%(確保第一根) ＋ 收盤突破三線
  T2_high  — T1 ＋ 收盤距 2y 高 ≤15%（高位糾結＝創高前夕，結合兩種思路）
  T3_strict— 帶寬<2% ＋ 今漲≥4% ＋ 量比≥2.5（嚴格版）
共同：科技7族群、市值≥100億、0050>MA200、7日黑名單、每檔20萬整張
出場：同 V4.3（收盤<20MA 或 進場-7% 先到；峰值-30% 保險）；收盤決策→隔日開盤成交
"""
import sys, os, json, io
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
TRADE_START = "2024-08-05"

VARIANTS = {
    "A_chase":   {"desc": "V4.3現行(ATH追,動能>=80)"},
    "T1_tangle": {"desc": "糾結<3%+首日漲3%量2倍"},
    "T2_high":   {"desc": "T1+距2y高<=15%(高位糾結)"},
    "T3_strict": {"desc": "糾結<2%+漲4%量2.5倍"},
}


def run_variant(name, history, mcap, us_chg, regime, df_idx, all_dates, pre):
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

        # 出場（全變體同 V4.3）
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

        base = [r for r in cands if r.get("industry") in ALLOWED
                and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP
                and r["ticker"] not in recent_losers]
        picks = []
        if name == "A_chase":
            ath = [r for r in base if r["is_ath"]]
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
            pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))
            picks = pool[:5]
        else:
            band_max = 0.02 if name == "T3_strict" else 0.03
            chg_min = 4.0 if name == "T3_strict" else 3.0
            vol_min = 2.5 if name == "T3_strict" else 2.0
            for r in base:
                c = r["ticker"]; i = r["_i"]
                if i < 21: continue
                p = pre[c]
                m5y, m10y, m20y = p["ma5"].iloc[i-1], p["ma10"].iloc[i-1], p["ma20"].iloc[i-1]
                if not (m5y and m10y and m20y) or pd.isna(m5y) or pd.isna(m10y) or pd.isna(m20y): continue
                prev_close = float(history[c]["Close"].iloc[i-1])
                if prev_close <= 0: continue
                band = (max(m5y, m10y, m20y) - min(m5y, m10y, m20y)) / prev_close
                if band >= band_max: continue                      # 昨日未糾結
                prev_chg = (prev_close / float(history[c]["Close"].iloc[i-2]) - 1) * 100 if i >= 2 else 0
                if prev_chg >= 3.0: continue                       # 昨天已噴 → 不是第一根
                if r["change_pct"] < chg_min: continue             # 今日首根長紅
                if (r.get("vol_ratio") or 0) < vol_min: continue   # 出大量
                if r["close"] <= max(m5y, m10y, m20y): continue    # 收盤站上糾結帶
                if name == "T2_high":
                    hi2y = p["max2y"].iloc[i-1]
                    if pd.isna(hi2y) or hi2y <= 0 or r["close"] < hi2y * 0.85: continue
                picks.append(r)
            picks.sort(key=lambda x: -(x.get("vol_ratio") or 0))
            picks = picks[:5]

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
    bs.START_DATE = "2023-09-01"
    bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print("預計算 MA5/10/20 + 2y 高...")
    pre = {}
    for c, df in history.items():
        cl = df["Close"]
        pre[c] = {"ma5": cl.rolling(5).mean(), "ma10": cl.rolling(10).mean(),
                  "ma20": cl.rolling(20).mean(), "max2y": cl.rolling(504, min_periods=100).max()}

    results = {}
    for name, cfg in VARIANTS.items():
        print("\n" + "="*60 + f"\n▶ {name}: {cfg['desc']}\n" + "="*60)
        cash, trades = run_variant(name, history, mcap, us_chg, regime, df_idx, all_dates, pre)
        bs.report(cash, trades, label=name, run_stress=False)
        results[name] = {"desc": cfg["desc"], "final_cash": cash, "n": len(trades), "trades": trades}

    print("\n" + "="*78); print("📊 匯總（2y 同窗 2024-08-05 起，資料至今日）"); print("="*78)
    print(f"{'變體':<10}{'說明':<26}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>6}{'期望':>8}{'PF':>6}{'MDD':>8}")
    yrs = (pd.Timestamp(dt.date.today().isoformat())-pd.Timestamp(TRADE_START)).days/365.25
    for name, r in results.items():
        t = r["trades"]; n = len(t)
        if n == 0: print(f"{name:<10}{r['desc']:<26}{'無交易':>9}"); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100
        cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/n*100; ex = sum(x["ret_pct"] for x in t)/n
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
        eq = bs.INITIAL; peak = eq; mdd = 0
        for x in sorted(t, key=lambda z: z["exit_date"]):
            eq *= (1 + x["ret_pct"]/100/bs.MAX_POS)
            peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
        print(f"{name:<10}{r['desc']:<26}{tot:>+8.1f}%{cagr:>+7.1f}%{n:>5}{wr:>5.0f}%{ex:>+7.2f}%{pf:>6.2f}{mdd*100:>+7.1f}%")
    json.dump(results, io.open("backtest_tangle_breakout.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_tangle_breakout.json")


if __name__ == "__main__":
    main()
