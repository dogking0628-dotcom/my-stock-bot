# -*- coding: utf-8 -*-
"""
V4.2 投信買超條件回測（近 2 年，T86 真實歷史）
═════════════════════════════════════════════════
同一份價格資料跑 3 個變體：
  A. base  — V4.1 原版（基準）
  B. bonus — V4.1 + 投信買超>0 動能+10 分（V4.2 上線版）
  C. hard  — V4.1 + 投信買超>0 硬性條件
時序：day d 收盤後決策（T86 於 d 日 15:00 已發布）→ d+1 開盤進場，無未來函數
"""
import sys, os, json, io, time
import datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from collections import defaultdict, deque
import backtest_strategy as bs
import backtest_v4_1 as v41
from industry_map_loader import get_industry

TRADE_START = "2024-08-05"   # T86 歷史起點
ALLOWED = v41.ALLOWED
MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80
RECENT_LOSER_WINDOW = 7


def load_t86():
    with io.open("t86_history.json", encoding="utf-8") as f:
        h = json.load(f)
    # {yyyymmdd: {code: shares}} → 只留有資料的日
    return {k: v for k, v in h.items() if v}


def run_variant(mode, history, mcap, us_chg, regime, t86, df_idx, all_dates):
    cash = bs.INITIAL
    positions, trades = {}, []
    losers_log = deque(maxlen=300)

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

        # 出場
        for c in list(positions.keys()):
            cf = cur.get(c)
            if not cf:
                continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            if cf["close"] < cf["ma20"] or cf["close"] < pos["peak"] * 0.7:
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
                               "ret_pct": ret,
                               "reason": "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%",
                               "hold_days": (nd - pd.Timestamp(pos["entry_date"])).days})
                if ret < 0:
                    losers_log.append((str(nd.date()), c))
                del positions[c]

        if not in_stage2:
            continue

        # ATH + 科技 + 市值
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
            sitc = sitc_day.get(r["ticker"], 0)
            if mode == "bonus" and sitc > 0:
                sc += 10
            r["score"] = min(sc, 100)
            r["_sitc"] = sitc

        def _pass(r):
            if r["ticker"] in recent_losers:
                return False
            if r["score"] < SCORE_THRESHOLD:
                return False
            if mode == "hard" and r["_sitc"] <= 0:
                return False
            return True

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
            pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))
            top5 = pool[:5]
        else:
            pool = [r for r in ath if _pass(r)]
            pool.sort(key=lambda x: -x["score"])
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
    return cash, trades


def main():
    # 價格資料需要 TRADE_START 前 200 日 → 從 2023-09 抓
    bs.START_DATE = "2023-09-01"
    bs.END_DATE = dt.date.today().isoformat()

    codes = bs.load_universe()
    mcap = v41.load_mcap()
    t86 = load_t86()
    print(f"universe {len(codes)} 檔 / 市值 {len(mcap)} / T86 {len(t86)} 交易日")
    us_chg = v41.fetch_us_sectors()
    regime = v41.fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足")
        return
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    print(f"回測窗 {TRADE_START} ~ {bs.END_DATE}（決策日 {sum(1 for d in all_dates if d.strftime('%Y-%m-%d') >= TRADE_START)} 天）")

    results = {}
    for mode, label in [("base", "V4.1 基準(2y)"),
                        ("bonus", "V4.2 投信加分(2y)"),
                        ("hard", "V4.2h 投信硬條件(2y)")]:
        print("\n" + "=" * 60)
        print(f"▶ {label}")
        print("=" * 60)
        cash, trades = run_variant(mode, history, mcap, us_chg, regime,
                                   t86, df_idx, all_dates)
        bs.report(cash, trades, label=label)
        results[mode] = {"final_cash": cash, "n": len(trades), "trades": trades}

    with io.open("backtest_v42_sitc.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    print("\n💾 已輸出 backtest_v42_sitc.json")


if __name__ == "__main__":
    main()
