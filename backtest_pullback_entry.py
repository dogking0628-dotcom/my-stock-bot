# -*- coding: utf-8 -*-
"""
拉回買進 vs 創高追進 — 2y 同窗對照回測
═════════════════════════════════════════════════
動機（2026-09-04 非凡《股市週報》方法論 + 近期實單痛點）：
  節目：「鎖定已創新高的個股，回測月線且基本面沒變 = 好的短期進場點」
  實單：V4.3 創高日追進在偏熱段連續受傷（嘉基追高 -10%、盟立 -5.6%、台虹樣本外）
變體：
  A_chase  — 現行 V4.3（ATH 當日動能≥80 → 隔日開盤追）＝對照組
  P1_pull  — 近 14 曆日內曾創 2y 月線 ATH ＋ 今收落在 20MA -2%~+3% ＋ 20MA 上揚
             ＋ 收盤>60MA ＋ 回檔量縮(vol_ratio<2) → 隔日開盤買；出場同 V4.3
  P2_buf   — P1 進場 ＋ 出場 20MA×0.98 緩衝（避免貼線進場隔日即洗出）
共同：科技7族群、市值≥100億、0050>MA200、7日虧損黑名單、每檔20萬整張、-7%樓地板
時序：全部收盤決策 → 隔日開盤成交，無未來函數
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
ATH_MEMORY_DAYS = 14        # 近 14 曆日（≈10 交易日）內曾創高才算「創高股」
PULL_LO, PULL_HI = -0.02, 0.03   # 收盤落在 20MA 的 -2% ~ +3%
TRADE_START = "2024-08-05"

VARIANTS = {
    "A_chase": {"desc": "V4.3現行(ATH當日追,動能>=80)"},
    "P1_pull": {"desc": "創高後回測20MA買(量縮,MA上揚)"},
    "P2_buf":  {"desc": "P1 + 出場20MA緩衝2%"},
}


def run_variant(name, history, mcap, us_chg, regime, df_idx, all_dates, ma20s, ma60s):
    cash = bs.INITIAL; positions = {}; trades = []; losers = deque(maxlen=400)
    last_ath = {}          # ticker -> 最後一次創高日 (pd.Timestamp)
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
            if f["is_ath"]:
                last_ath[c] = d
        cur = {r["ticker"]: r for r in cands}
        by_ind = defaultdict(list)
        for r in cands: by_ind[r.get("industry") or "未分類"].append(r)
        ind_up = {k: sum(1 for x in v if x["change_pct"] > 0)/max(len(v),1) for k, v in by_ind.items()}
        for r in cands: r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # ── 出場 ──
        buf = 0.98 if name == "P2_buf" else 1.0
        for c in list(positions):
            cf = cur.get(c)
            if not cf: continue
            pos = positions[c]; pos["peak"] = max(pos["peak"], cf["close"])
            hit = (cf["close"] < cf["ma20"] * buf or cf["close"] < pos["peak"]*0.7
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
                               "ret_pct": ret, "hold_days": (nd-pd.Timestamp(pos["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del positions[c]
        if d_str < TRADE_START or not in_stage2: continue

        # ── 進場候選 ──
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
            for r in base:
                c = r["ticker"]
                la = last_ath.get(c)
                if la is None or (d - la).days > ATH_MEMORY_DAYS: continue
                if r["is_ath"]: continue                      # 創高當天不買，等拉回
                ma20 = r["ma20"]; i = r["_i"]
                if not ma20 or ma20 <= 0: continue
                gap = r["close"]/ma20 - 1
                if not (PULL_LO <= gap <= PULL_HI): continue
                m5 = ma20s[c]
                if i < 5 or not (m5.iloc[i] > m5.iloc[i-5]): continue   # 20MA 上揚
                m60 = ma60s[c].iloc[i]
                if not (m60 and r["close"] > m60): continue
                if (r.get("vol_ratio") or 0) >= 2: continue           # 回檔要量縮
                r["_fresh"] = (d - la).days
                r["_gap"] = abs(gap)
                picks.append(r)
            picks.sort(key=lambda x: (x["_fresh"], x["_gap"]))
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
    codes = bs.load_universe(); mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print("預計算 20/60MA...")
    ma20s = {c: df["Close"].rolling(20).mean() for c, df in history.items()}
    ma60s = {c: df["Close"].rolling(60).mean() for c, df in history.items()}

    results = {}
    for name, cfg in VARIANTS.items():
        print("\n" + "="*60 + f"\n▶ {name}: {cfg['desc']}\n" + "="*60)
        cash, trades = run_variant(name, history, mcap, us_chg, regime, df_idx, all_dates, ma20s, ma60s)
        bs.report(cash, trades, label=name, run_stress=False)
        results[name] = {"desc": cfg["desc"], "final_cash": cash, "n": len(trades), "trades": trades}

    print("\n" + "="*76); print("📊 匯總（2y 同 V4.3 窗 2024-08-05 起）"); print("="*76)
    print(f"{'變體':<9}{'說明':<30}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>6}{'期望':>8}{'PF':>6}{'MDD':>8}")
    yrs = (pd.Timestamp(dt.date.today().isoformat())-pd.Timestamp(TRADE_START)).days/365.25
    for name, r in results.items():
        t = r["trades"]; n = len(t)
        if n == 0: print(f"{name:<9}{r['desc']:<30}{'無交易':>9}"); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100
        cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/n*100; ex = sum(x["ret_pct"] for x in t)/n
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
        eq = bs.INITIAL; peak = eq; mdd = 0
        for x in sorted(t, key=lambda z: z["exit_date"]):
            eq *= (1 + x["ret_pct"]/100/bs.MAX_POS)
            peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
        print(f"{name:<9}{r['desc']:<30}{tot:>+8.1f}%{cagr:>+7.1f}%{n:>5}{wr:>5.0f}%{ex:>+7.2f}%{pf:>6.2f}{mdd*100:>+7.1f}%")
    json.dump(results, io.open("backtest_pullback_entry.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_pullback_entry.json")


if __name__ == "__main__":
    main()
