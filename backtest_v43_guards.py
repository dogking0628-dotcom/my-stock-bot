# -*- coding: utf-8 -*-
"""
V4.3 護欄回測（凌華案例驅動，2024-08 ~ 今，T86 真實歷史）
═════════════════════════════════════════════════
同資料 4 變體，隔離每道護欄貢獻：
  V4.2   — 投信加分基準（現上線版）
  V4.3a  — +護欄① 硬停損樓地板：出場 close < max(20MA, 進場價×0.93)
  V4.3b  — +護欄② 獨苗不進場：最強族群達標 <2 檔 → 當日空手
  V4.3   — 兩道護欄全開
"""
import sys, os, json, io
import datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from collections import defaultdict, deque
import backtest_strategy as bs
import backtest_v4_1 as v41
from industry_map_loader import get_industry

TRADE_START = "2024-08-05"
ALLOWED = v41.ALLOWED
MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80
RECENT_LOSER_WINDOW = 7
HARD_FLOOR = 0.93          # 護欄①：進場價 -7% 硬停損
MIN_POOL = 2               # 護欄②：達標股至少 2 檔才進場


def load_t86():
    with io.open("t86_history.json", encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if v}


def run_variant(hard_floor, lone_guard, history, mcap, us_chg, regime,
                t86, df_idx, all_dates):
    cash = bs.INITIAL
    positions, trades = {}, []
    losers_log = deque(maxlen=300)
    lone_skips = 0

    for di, d in enumerate(all_dates):
        if di < 200:
            continue
        d_str = d.strftime("%Y-%m-%d")
        if d_str < TRADE_START:
            continue
        prev_str = all_dates[di - 1].strftime("%Y-%m-%d") if di > 0 else None
        in_stage2 = regime.get(d_str, False)
        sitc_day = t86.get(d.strftime("%Y%m%d"), {})
        cutoff = d - pd.Timedelta(days=RECENT_LOSER_WINDOW)
        recent_losers = {t for ed, t in losers_log if pd.Timestamp(ed) >= cutoff}

        cands = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200:
                continue
            f = bs.daily_features(df, i)
            if not f:
                continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            cands.append(f)
        cur = {r["ticker"]: r for r in cands}
        by_ind = defaultdict(list)
        for r in cands:
            by_ind[r.get("industry") or "未分類"].append(r)
        ind_up = {ind: sum(1 for x in lst if x["change_pct"] > 0) / max(len(lst), 1)
                  for ind, lst in by_ind.items()}
        for r in cands:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # 出場（護欄①在此生效）
        for c in list(positions.keys()):
            cf = cur.get(c)
            if not cf:
                continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            stop_hit = cf["close"] < cf["ma20"] or cf["close"] < pos["peak"] * 0.7
            reason = "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%"
            if hard_floor and cf["close"] < pos["entry_price"] * HARD_FLOOR:
                stop_hit = True
                if cf["close"] >= cf["ma20"]:
                    reason = "硬停損-7%"
            if stop_hit:
                nd = all_dates[di + 1] if di + 1 < len(all_dates) else None
                if nd is None:
                    continue
                ni = df_idx[c].get(nd)
                if ni is None:
                    continue
                sp = history[c]["Open"].iloc[ni]
                cash += pos["shares"] * sp * (1 - bs.COMMISSION - bs.TAX)
                ret = (sp / pos["entry_price"] - 1) * 100
                trades.append({"ticker": c, "industry": get_industry(c),
                               "entry_date": pos["entry_date"],
                               "exit_date": str(nd.date()),
                               "entry": pos["entry_price"], "exit": sp,
                               "ret_pct": ret, "reason": reason,
                               "hold_days": (nd - pd.Timestamp(pos["entry_date"])).days})
                if ret < 0:
                    losers_log.append((str(nd.date()), c))
                del positions[c]

        if not in_stage2:
            continue

        ath = []
        for r in cands:
            if not r["is_ath"]:
                continue
            if r.get("industry") not in ALLOWED:
                continue
            mc = mcap.get(r["ticker"])
            if mc is None or mc < MIN_MCAP:
                continue
            ath.append(r)
        for r in ath:
            sc, _ = bs.momentum_score(r)
            sc += v41.us_bonus(r["industry"], prev_str, us_chg)
            if sitc_day.get(r["ticker"], 0) > 0:
                sc += 10
            r["score"] = min(sc, 100)

        def _pass(r):
            return (r["ticker"] not in recent_losers
                    and r["score"] >= SCORE_THRESHOLD)

        ath_by_ind = defaultdict(list)
        for r in ath:
            ath_by_ind[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by_ind.items(), key=lambda x: -len(x[1])):
            br = sum(1 for x in lst if x["bullish"]) / max(len(lst), 1)
            if len(lst) >= 3 and br >= 0.5:
                strongest = ind
                break
        if strongest:
            pool = [r for r in ath_by_ind[strongest] if _pass(r)]
        else:
            pool = [r for r in ath if _pass(r)]
        pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))

        # 護欄②：達標股不足 → 獨苗 = 動能末端，空手
        if lone_guard and len(pool) < MIN_POOL:
            if pool:
                lone_skips += 1
            continue
        top5 = pool[:5]

        slots = bs.MAX_POS - len(positions)
        if slots <= 0 or not top5:
            continue
        nd = all_dates[di + 1] if di + 1 < len(all_dates) else None
        if nd is None:
            continue
        for r in top5[:slots]:
            c = r["ticker"]
            if c in positions:
                continue
            ni = df_idx[c].get(nd)
            if ni is None:
                continue
            bp = history[c]["Open"].iloc[ni]
            if cash < bs.PER_POS * 0.5:
                break
            cps = bp * (1 + bs.COMMISSION)
            sh = int(min(bs.PER_POS, cash) / cps / 1000) * 1000
            if sh < 1000:
                continue
            cash -= sh * cps
            positions[c] = {"entry_price": bp, "shares": sh, "peak": bp,
                            "entry_date": str(nd.date())}

    fd = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(fd)
        if i is None:
            continue
        cash += pos["shares"] * history[c]["Close"].iloc[i] * (1 - bs.COMMISSION - bs.TAX)
    if lone_guard:
        print(f"  [護欄②] 獨苗跳過 {lone_skips} 個進場日")
    return cash, trades


def main():
    bs.START_DATE = "2023-09-01"
    bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe()
    mcap = v41.load_mcap()
    t86 = load_t86()
    print(f"universe {len(codes)} / mcap {len(mcap)} / T86 {len(t86)} 日")
    us_chg = v41.fetch_us_sectors()
    regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足")
        return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    results = {}
    for key, hf, lg, label in [
            ("v42",  False, False, "V4.2 基準(2y)"),
            ("v43a", True,  False, "V4.3a 硬停損-7%(2y)"),
            ("v43b", False, True,  "V4.3b 獨苗不進場(2y)"),
            ("v43",  True,  True,  "V4.3 雙護欄(2y)")]:
        print("\n" + "=" * 60)
        print(f"▶ {label}")
        print("=" * 60)
        cash, trades = run_variant(hf, lg, history, mcap, us_chg, regime,
                                   t86, df_idx, all_dates)
        bs.report(cash, trades, label=label)
        results[key] = {"final_cash": cash, "n": len(trades), "trades": trades}

    with io.open("backtest_v43_guards.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    print("\n💾 已輸出 backtest_v43_guards.json")


if __name__ == "__main__":
    main()
