# -*- coding: utf-8 -*-
"""
ATH 技術線形 × 法人金額買賣超 5 年回測
═════════════════════════════════════════════════
進場基底（V4.3 技術面）：創2y月線ATH + 多頭排列 + 科技7族群 + 市值≥100億
                        + 動能≥80 + 0050>MA200 + 最強族群挑5 + 7日黑名單
出場：收盤跌破20MA 或 進場-7% → 隔日開盤賣

籌碼變體（金額 = 張數×當日收盤×1000，5日累計）：
  A base   — 純技術（V4.3 無投信加分）
  B v43    — V4.3 現行（投信買超>0 +10分）
  C fmt    — 外資 5日淨買金額 > 0 → +10分（外資獨買加分）
  D both   — 外資+投信 5日金額合計 > 0 → +10分
  E hard   — 外資 5日淨買金額 ≥ 1 億 硬性條件（不夠不進）
  F strong — 5日 外資+投信 合計 ≥ 3 億 → +15；≥ 1 億 → +8（分級加分）
時序：T86 於 d 日 15:00 發布，d 日收盤決策、d+1 開盤進 → 無未來函數
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
FLOW_WINDOW = 5
E8 = 1e8
TRADE_START = "2024-08-05"   # 與 V4.3 2y 回測同窗

VARIANTS = {
    "A_base":   {"desc": "純技術(無籌碼)"},
    "B_v43":    {"desc": "V4.3現行(投信張數>0 +10)"},
    "C_fmt":    {"desc": "外資5日金額>0 +10"},
    "D_both":   {"desc": "外資+投信5日金額>0 +10"},
    "E_hard":   {"desc": "外資5日金額≥1億 硬條件"},
    "F_strong": {"desc": "合計≥3億+15 / ≥1億+8 分級"},
}


def load_flow():
    p = "t86_3party_2y.json"
    if not os.path.exists(p):
        print("❌ 缺 t86_3party_5y.json，請先跑 fetch_t86_3party_5y.py（現為 2y 模式）"); sys.exit(1)
    h = json.load(io.open(p, encoding="utf-8"))
    return {k: v for k, v in h.items() if v}   # {yyyymmdd: {code:[f,i]}}


def build_flow_amount(flow, history):
    """每檔每日 外資/投信 金額（元）→ 5日滾動累計。回傳 {code: {date_str: (f_amt5, i_amt5)}}"""
    out = {}
    for c, df in history.items():
        cl = df["Close"]
        dates = [d.strftime("%Y%m%d") for d in df.index]
        f_daily = np.zeros(len(dates)); i_daily = np.zeros(len(dates))
        for k, ds in enumerate(dates):
            rec = flow.get(ds, {}).get(c)
            if rec:
                px = float(cl.iloc[k])
                f_daily[k] = rec[0] * px; i_daily[k] = rec[1] * px
        f5 = pd.Series(f_daily).rolling(FLOW_WINDOW, min_periods=1).sum().values
        i5 = pd.Series(i_daily).rolling(FLOW_WINDOW, min_periods=1).sum().values
        out[c] = {d.strftime("%Y-%m-%d"): (float(f5[k]), float(i5[k])) for k, d in enumerate(df.index)}
    return out


def run_variant(name, history, mcap, us_chg, regime, flow_amt, df_idx, all_dates):
    cash = bs.INITIAL; positions = {}; trades = []; losers = deque(maxlen=400)
    for di, d in enumerate(all_dates):
        if di < 200: continue
        d_str = d.strftime("%Y-%m-%d")
        if d_str < TRADE_START: continue
        prev_str = all_dates[di-1].strftime("%Y-%m-%d")
        if not regime.get(d_str, False):
            in_stage2 = False
        else:
            in_stage2 = True
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

        # 出場
        for c in list(positions):
            cf = cur.get(c)
            if not cf: continue
            pos = positions[c]; pos["peak"] = max(pos["peak"], cf["close"])
            hit = cf["close"] < cf["ma20"] or cf["close"] < pos["peak"]*0.7 or cf["close"] < pos["entry_price"]*HARD_FLOOR
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
                               "ret_pct": ret, "reason": "exit", "hold_days": (nd-pd.Timestamp(pos["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del positions[c]
        if not in_stage2: continue

        ath = [r for r in cands if r["is_ath"] and r.get("industry") in ALLOWED
               and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP]
        for r in ath:
            sc, _ = bs.momentum_score(r)
            sc += v41.us_bonus(r["industry"], prev_str, us_chg)
            fa, ia = flow_amt.get(r["ticker"], {}).get(d_str, (0.0, 0.0))
            r["_f"], r["_i"] = fa, ia
            if name == "B_v43":
                # 現行：投信「張數」>0；用金額>0 等價（同號）
                if ia > 0: sc += 10
            elif name == "C_fmt":
                if fa > 0: sc += 10
            elif name == "D_both":
                if fa + ia > 0: sc += 10
            elif name == "F_strong":
                tot = fa + ia
                if tot >= 3*E8: sc += 15
                elif tot >= 1*E8: sc += 8
            r["score"] = min(sc, 100)

        def _pass(r):
            if r["ticker"] in recent_losers: return False
            if r["score"] < SCORE_THRESHOLD: return False
            if name == "E_hard" and r["_f"] < 1*E8: return False
            return True

        ath_by = defaultdict(list)
        for r in ath: ath_by[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by.items(), key=lambda x: -len(x[1])):
            if len(lst) >= 3 and sum(1 for x in lst if x["bullish"])/len(lst) >= 0.5:
                strongest = ind; break
        pool = [r for r in (ath_by[strongest] if strongest else ath) if _pass(r)]
        pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))
        top5 = pool[:5]
        slots = bs.MAX_POS - len(positions)
        if slots <= 0 or not top5: continue
        nd = all_dates[di+1] if di+1 < len(all_dates) else None
        if nd is None: continue
        for r in top5[:slots]:
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
    bs.START_DATE = "2023-09-01"   # 2y 窗需前推 200 日暖機; bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap()
    flow = load_flow()
    print(f"universe {len(codes)} / mcap {len(mcap)} / T86 {len(flow)} 交易日 ({min(flow)}~{max(flow)})")
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100: print("資料不足"); return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print("建 5 日金額流量表..."); flow_amt = build_flow_amount(flow, history)

    results = {}
    for name, cfg in VARIANTS.items():
        print("\n" + "="*60 + f"\n▶ {name}: {cfg['desc']}\n" + "="*60)
        cash, trades = run_variant(name, history, mcap, us_chg, regime, flow_amt, df_idx, all_dates)
        bs.report(cash, trades, label=f"{name}", run_stress=False)
        results[name] = {"desc": cfg["desc"], "final_cash": cash, "n": len(trades), "trades": trades}

    # 匯總表 + 對 A 的增益 + 每檔黑天鵝只跑一次最佳版
    print("\n" + "="*72); print("📊 匯總（2y 同 V4.3 窗）"); print("="*72)
    print(f"{'變體':<10}{'說明':<26}{'總報酬':>9}{'CAGR':>8}{'筆':>5}{'勝率':>7}{'期望':>8}{'PF':>6}")
    yrs = (pd.Timestamp(bs.END_DATE)-pd.Timestamp(TRADE_START)).days/365.25
    for name, r in results.items():
        t = r["trades"]; n = len(t)
        if n == 0: print(f"{name:<10}{r['desc']:<26}{'-':>9}"); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100
        cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/n*100; ex = sum(x["ret_pct"] for x in t)/n
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l else 99
        print(f"{name:<10}{r['desc']:<26}{tot:>+8.1f}%{cagr:>+7.1f}%{n:>5}{wr:>6.0f}%{ex:>+7.2f}%{pf:>6.2f}")
    best = max(results, key=lambda k: results[k]["final_cash"])
    print(f"\n🏆 最佳: {best}  → 跑黑天鵝壓測")
    try:
        from stress_test_lib import run_stress_test
        run_stress_test(results[best]["trades"], label=best)
    except Exception as e: print("stress fail", e)
    json.dump(results, io.open("backtest_ath_smartmoney.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\n💾 backtest_ath_smartmoney.json")


if __name__ == "__main__":
    main()
