# -*- coding: utf-8 -*-
"""
龍頭補漲 / 落後補漲 回測（analyst_claims #11、#377、#414、#423、#563；2026-09-14 排入）
═════════════════════════════════════════════════
名嘴規則原文：「同族群二線股（華新科/禾伸堂）突破前高、龍頭（國巨）落後 → 龍頭必補一根」（郭哲榮×2）、
             「電子五哥輪漲總會輪到落後補漲股」（杜金龍×2）、「領先創高族群 Q3 回檔後續攻」（沈萬鈞）。
族群定義：product_taxonomy.PRODUCT_TAXONOMY（產品鏈細類，一檔可屬多類）；龍頭 = 族群內市值最大者。

變體：
  A_chase    — 現行 V4.3（ATH 當日動能≥80 追）＝對照組
  L1_lead60  — 族群內 ≥2 檔非龍頭今日創 60 日新高、龍頭距自身 60 日高 ≥5%  → 買龍頭
  L2_lead10  — 同 L1 但龍頭落後 ≥10%（落後更多才買）
  L3_lead20  — 同 L1 但用 20 日高（短週期）
  F1_laggard — 反向：龍頭今日創 60 日高，買族群內落後最多（距 60 日高 10%~40%、收盤>MA200）的成員（杜金龍版）
共同：科技 7 族群、市值≥100億、0050>MA200、7 日黑名單、每檔 20 萬整張、最多 5 檔
出場：同 V4.3（收盤<20MA 或 進場-7% 先到；峰值-30% 保險）；收盤決策→隔日開盤成交
用法：python backtest_leader_catchup.py --window 2y|5y   （紅線：期望≥+8% / PF≥2.5 / MDD≤-30%）
"""
import sys, os, json, io, argparse
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
from product_taxonomy import PRODUCT_TAXONOMY

ALLOWED = v41.ALLOWED
MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80
RECENT_LOSER_WINDOW = 7
HARD_FLOOR = 0.93
MIN_GROUP = 3            # 族群至少 3 檔有資料才算
MIN_HISTORY = 100        # 全市場資料少於此數視為抓取失敗
WINDOWS = {"2y": {"start": "2023-09-01", "trade_start": "2024-08-05"},
           "5y": {"start": "2020-06-01", "trade_start": "2021-06-01"}}

VARIANTS = {
    "A_chase":    {"desc": "V4.3現行(ATH追,動能>=80)"},
    "L1_lead60":  {"desc": "2檔創60日高+龍頭落後>=5%→買龍頭", "n": 60, "lag": 0.05, "mode": "leader"},
    "L2_lead10":  {"desc": "同L1但龍頭落後>=10%",             "n": 60, "lag": 0.10, "mode": "leader"},
    "L3_lead20":  {"desc": "2檔創20日高+龍頭落後>=5%",        "n": 20, "lag": 0.05, "mode": "leader"},
    "F1_laggard": {"desc": "龍頭創60日高→買最落後成員(10~40%)", "n": 60, "lag": 0.10, "mode": "laggard"},
}


def build_groups(history, mcap):
    """{族群名: {"leader": code, "members": [codes]}}，只留資料齊全且 ≥MIN_GROUP 檔的族群。"""
    groups = {}
    for g, codes in PRODUCT_TAXONOMY.items():
        mem = [c for c in dict.fromkeys(codes) if c in history]
        if len(mem) < MIN_GROUP: continue
        with_cap = [(mcap.get(c) or 0, c) for c in mem]
        cap, leader = max(with_cap)
        if cap <= 0: continue
        groups[g] = {"leader": leader, "members": mem}
    return groups


def run_variant(name, history, mcap, us_chg, regime, df_idx, all_dates, pre, groups, trade_start):
    cfg = VARIANTS[name]
    cash = bs.INITIAL; positions = {}; trades = []; losers = deque(maxlen=400)
    signals = 0
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
        ind_up = {k: sum(1 for x in v if x["change_pct"] > 0)/max(len(v), 1) for k, v in by_ind.items()}
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
                trades.append({"ticker": c, "industry": get_industry(c), "group": pos.get("group"),
                               "entry_date": pos["entry_date"], "exit_date": str(nd.date()),
                               "entry": float(pos["entry_price"]), "exit": float(sp), "ret_pct": float(ret),
                               "reason": "跌破20MA" if cf["close"] < cf["ma20"] else ("硬停損-7%" if cf["close"] < pos["entry_price"]*HARD_FLOOR else "峰值-30%"),
                               "hold_days": (nd - pd.Timestamp(pos["entry_date"])).days})
                if ret < 0: losers.append((str(nd.date()), c))
                del positions[c]

        if d_str < trade_start or not in_stage2: continue

        base_ok = lambda r: (r.get("industry") in ALLOWED and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP
                             and r["ticker"] not in recent_losers)
        picks = []
        if name == "A_chase":
            base = [r for r in cands if base_ok(r)]
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
            n = cfg["n"]; key = f"max{n}"
            def new_high(c):
                r = cur.get(c)
                if not r: return False
                hi = pre[c][key].iloc[r["_i"]-1]
                return (not pd.isna(hi)) and hi > 0 and r["close"] >= hi
            def lag(c):
                r = cur.get(c)
                if not r: return None
                hi = pre[c][key].iloc[r["_i"]-1]
                if pd.isna(hi) or hi <= 0: return None
                return 1 - r["close"]/max(hi, r["close"])
            for g, info in groups.items():
                leader = info["leader"]; others = [c for c in info["members"] if c != leader]
                if cfg["mode"] == "leader":
                    n_hi = sum(1 for c in others if new_high(c))
                    if n_hi < 2: continue
                    lg = lag(leader); r = cur.get(leader)
                    if lg is None or lg < cfg["lag"] or not r or not base_ok(r): continue
                    if r["close"] < r["ma20"]: continue            # 龍頭至少站上月線，避免接刀
                    r = dict(r); r["group"] = g; r["_sig"] = n_hi; r["_lag"] = lg
                    picks.append(r); signals += 1
                else:  # laggard
                    if not new_high(leader): continue
                    best = None
                    for c in others:
                        lg = lag(c); r = cur.get(c)
                        if lg is None or not r or not base_ok(r): continue
                        if not (cfg["lag"] <= lg <= 0.40): continue
                        ma200 = history[c]["Close"].iloc[r["_i"]-200:r["_i"]+1].mean()
                        if r["close"] < ma200: continue             # 落後但沒壞掉
                        if best is None or lg > best["_lag"]:
                            best = dict(r); best["group"] = g; best["_lag"] = lg
                    if best: picks.append(best); signals += 1
            # 同一檔可能因多族群重複 → 去重，優先落後幅度大者
            seen = set(); uniq = []
            for r in sorted(picks, key=lambda x: -x.get("_lag", 0)):
                if r["ticker"] in seen: continue
                seen.add(r["ticker"]); uniq.append(r)
            picks = uniq[:5]

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
            positions[c] = {"entry_price": bp, "shares": sh, "peak": bp, "entry_date": str(nd.date()),
                            "group": r.get("group")}
    fd = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(fd)
        if i is not None:
            cash += pos["shares"]*history[c]["Close"].iloc[i]*(1-bs.COMMISSION-bs.TAX)
    return cash, trades, signals


def summarize(results, trade_start, end_date):
    yrs = max((pd.Timestamp(end_date)-pd.Timestamp(trade_start)).days/365.25, 0.1)
    rows = []
    for name, r in results.items():
        t = r["trades"]; n = len(t)
        if n == 0:
            rows.append({"variant": name, "desc": r["desc"], "n": 0}); continue
        w = [x for x in t if x["ret_pct"] > 0]; l = [x for x in t if x["ret_pct"] <= 0]
        tot = (r["final_cash"]/bs.INITIAL-1)*100
        cagr = ((r["final_cash"]/bs.INITIAL)**(1/yrs)-1)*100
        wr = len(w)/n*100; ex = sum(x["ret_pct"] for x in t)/n
        pf = abs(sum(x["ret_pct"] for x in w)/sum(x["ret_pct"] for x in l)) if l and sum(x["ret_pct"] for x in l) != 0 else 99.0
        eq = bs.INITIAL; peak = eq; mdd = 0
        for x in sorted(t, key=lambda z: z["exit_date"]):
            eq *= (1 + x["ret_pct"]/100/bs.MAX_POS)
            peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
        passed = ex >= 8 and pf >= 2.5 and mdd*100 >= -30
        rows.append({"variant": name, "desc": r["desc"], "total": tot, "cagr": cagr, "n": n, "win_rate": wr,
                     "expectancy": ex, "pf": pf, "mdd": mdd*100, "signals": r.get("signals"), "redline": passed})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="2y", choices=list(WINDOWS))
    ap.add_argument("--variants", default=",".join(VARIANTS))
    a = ap.parse_args(argv)
    win = WINDOWS[a.window]
    bs.START_DATE = win["start"]
    bs.END_DATE = dt.date.today().isoformat()
    trade_start = win["trade_start"]
    codes = bs.load_universe(); mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors(); regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < MIN_HISTORY: print("資料不足"); return 1
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print("預計算 20/60 日高...")
    pre = {c: {"max20": df["Close"].rolling(20, min_periods=10).max(),
               "max60": df["Close"].rolling(60, min_periods=30).max()} for c, df in history.items()}
    groups = build_groups(history, mcap)
    print(f"族群 {len(groups)} 個（≥{MIN_GROUP} 檔有資料），龍頭例：" +
          "、".join(f"{g}={i['leader']}" for g, i in list(groups.items())[:6]))

    results = {}
    for name in [v.strip() for v in a.variants.split(",") if v.strip() in VARIANTS]:
        print("\n" + "="*60 + f"\n▶ {name}: {VARIANTS[name]['desc']}\n" + "="*60)
        cash, trades, signals = run_variant(name, history, mcap, us_chg, regime, df_idx, all_dates, pre, groups, trade_start)
        bs.report(cash, trades, label=name, run_stress=False)
        results[name] = {"desc": VARIANTS[name]["desc"], "final_cash": cash, "n": len(trades),
                         "signals": signals, "trades": trades}

    rows = summarize(results, trade_start, bs.END_DATE)
    print("\n" + "="*84); print(f"📊 匯總（{a.window} 窗，交易起 {trade_start}，資料至 {bs.END_DATE}）"); print("="*84)
    print(f"{'變體':<11}{'說明':<34}{'總報酬':>8}{'CAGR':>7}{'筆':>5}{'勝率':>5}{'期望':>7}{'PF':>6}{'MDD':>7} 紅線")
    for r in rows:
        if r["n"] == 0: print(f"{r['variant']:<11}{r['desc']:<34}{'無交易':>8}"); continue
        print(f"{r['variant']:<11}{r['desc']:<34}{r['total']:>+7.1f}%{r['cagr']:>+6.1f}%{r['n']:>5}{r['win_rate']:>4.0f}%"
              f"{r['expectancy']:>+6.2f}%{r['pf']:>6.2f}{r['mdd']:>+6.1f}% {'✅' if r['redline'] else '❌'}")
    out = f"backtest_leader_catchup_{a.window}.json"
    json.dump({"window": a.window, "trade_start": trade_start, "end": bs.END_DATE, "summary": rows,
               "groups": {g: i for g, i in groups.items()}, "results": results},
              io.open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n💾 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
