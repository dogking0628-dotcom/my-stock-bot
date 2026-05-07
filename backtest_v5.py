# -*- coding: utf-8 -*-
"""
V5 雙引擎回測：
─────────────────────────────────
70% 資金 V4 主力（最強族群動能 ≥80）+ 30% 資金 US-TW Sync（美台同步族群昨日漲幅）
─────────────────────────────────
分配：
  V4 引擎：3 檔（每檔 23.3 萬）
  Sync 引擎：2 檔（每檔 15 萬）
  共 5 檔同時持倉
規則：
  - 同股出現在兩引擎時優先 V4，Sync 用下一個
  - 出場規則同 V4：跌破 20MA 或從峰值 -30%
  - 大盤體制相同：0050 < MA200 全引擎空手
"""
import sys, os, json, datetime as dt, time, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict
import backtest_strategy as bs
from industry_map_loader import get_industry

ALLOWED = {"半導體", "電子零組件", "光電", "電腦及週邊",
           "電子通路", "通信網路", "其他電子"}
MIN_MCAP = 100

V4_SLOTS = 3        # V4 主力 3 檔
SYNC_SLOTS = 2      # US-TW Sync 2 檔
INITIAL = 1_000_000
V4_BUDGET = INITIAL * 0.6     # 60% 給 V4
SYNC_BUDGET = INITIAL * 0.4   # 40% 給 Sync
V4_PER = V4_BUDGET / V4_SLOTS         # 20 萬/檔
SYNC_PER = SYNC_BUDGET / SYNC_SLOTS   # 20 萬/檔

US_TW_MAP = {
    "SMH":  ["半導體", "其他電子"],
    "IGV":  ["通信網路", "電腦及週邊"],
    "XLK":  list(ALLOWED),
    "SOXL": ["半導體"],
    "QQQ":  list(ALLOWED),
}
US_BOOST_FOR_V4 = {
    "QQQ": {"industries": list(ALLOWED), "threshold": 0.0, "bonus": 5},
    "SMH": {"industries": ["半導體", "其他電子"], "threshold": 0.5, "bonus": 10},
    "IGV": {"industries": ["通信網路", "電腦及週邊"], "threshold": 0.5, "bonus": 10},
}
HOT_THRESHOLD = 1.0


def load_mcap():
    if not os.path.exists("marketcap_cache.json"): return {}
    with io.open("marketcap_cache.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_us_etfs():
    print("[us] 抓美股 ETF 5y...")
    out = {}
    for tk in set(list(US_TW_MAP.keys()) + list(US_BOOST_FOR_V4.keys())):
        try:
            df = yf.download(tk, start=bs.START_DATE, end=bs.END_DATE,
                             auto_adjust=True, progress=False, threads=False,
                             group_by="column")
            if hasattr(df.columns, "levels"):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            cl = df["Close"].dropna()
            chg = cl.pct_change() * 100
            out[tk] = {d.strftime("%Y-%m-%d"): float(c) for d, c in chg.dropna().items()}
        except Exception: pass
        time.sleep(0.5)
    return out


def fetch_0050():
    try:
        df = yf.download("0050.TW", start="2020-01-01", end=bs.END_DATE,
                         auto_adjust=True, progress=False, threads=False,
                         group_by="column")
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        cl = df["Close"].dropna()
        ma200 = cl.rolling(200).mean()
        return {d.strftime("%Y-%m-%d"): bool(c > m)
                for d, c, m in zip(cl.index, cl.values, ma200.values)
                if not pd.isna(m)}
    except Exception:
        return {}


def v4_us_bonus(industry, prev_str, us_chg):
    bonus = 0
    for etf, cfg in US_BOOST_FOR_V4.items():
        chg = us_chg.get(etf, {}).get(prev_str)
        if chg is None: continue
        if industry in cfg["industries"] and chg >= cfg["threshold"]:
            bonus += cfg["bonus"]
    return bonus


def get_synced_industries(prev_str, us_chg):
    if not prev_str: return set()
    target = set()
    for etf, mapping in US_TW_MAP.items():
        chg = us_chg.get(etf, {}).get(prev_str)
        if chg is None: continue
        if chg >= HOT_THRESHOLD:
            target.update(mapping)
    target &= ALLOWED
    return target


def pick_v4(candidates, us_chg, prev_str, mcap):
    """V4 引擎：最強族群 + 動能 ≥80 + 市值 + 美股加分"""
    ath_set = []
    for r in candidates:
        if not r["is_ath"]: continue
        if r.get("industry") not in ALLOWED: continue
        mc = mcap.get(r["ticker"])
        if mc is None or mc < MIN_MCAP: continue
        ath_set.append(r)
    for r in ath_set:
        sc, _ = bs.momentum_score(r)
        sc += v4_us_bonus(r["industry"], prev_str, us_chg)
        r["v4_score"] = min(sc, 100)
    by_ind = defaultdict(list)
    for r in ath_set: by_ind[r["industry"]].append(r)
    strongest = None
    for ind, lst in sorted(by_ind.items(), key=lambda x: -len(x[1])):
        br = sum(1 for x in lst if x["bullish"])/max(len(lst),1)
        if len(lst) >= 3 and br >= 0.5:
            strongest = ind; break
    if strongest:
        pool = sorted(by_ind[strongest], key=lambda x: -x["v4_score"])
    else:
        pool = sorted(ath_set, key=lambda x: -x["v4_score"])
    return [r for r in pool if r["v4_score"] >= 80][:V4_SLOTS]


def pick_sync(candidates, us_chg, prev_str, mcap):
    """Sync 引擎：美台同步族群 + ATH + 市值 + 昨日漲幅最大"""
    target_inds = get_synced_industries(prev_str, us_chg)
    if not target_inds: return []
    pool = []
    for r in candidates:
        if r.get("industry") not in target_inds: continue
        if not r.get("is_ath"): continue
        if not r.get("bullish"): continue
        mc = mcap.get(r["ticker"])
        if mc is None or mc < MIN_MCAP: continue
        pool.append(r)
    pool.sort(key=lambda x: -x["change_pct"])
    return pool[:SYNC_SLOTS]


def run_backtest(history, mcap, us_chg, regime):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    print(f"[v5] {len(all_dates)} 天")

    cash = INITIAL
    positions = {}  # {ticker: {entry_price, shares, peak, entry_date, engine}}
    trades = []
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    for di, d in enumerate(all_dates):
        if di < 200: continue
        d_str = d.strftime("%Y-%m-%d")
        prev_str = all_dates[di-1].strftime("%Y-%m-%d") if di > 0 else None
        in_stage2 = regime.get(d_str, False)

        # 算每日特徵
        candidates = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = bs.daily_features(df, i)
            if not f: continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            candidates.append(f)
        # 族群同步性
        by_ind = defaultdict(list)
        for r in candidates:
            ind = r.get("industry") or "未分類"
            by_ind[ind].append(r)
        ind_up = {ind: sum(1 for x in lst if x["change_pct"]>0)/max(len(lst),1)
                  for ind, lst in by_ind.items()}
        for r in candidates:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # 出場（不受體制影響）
        cur = {r["ticker"]: r for r in candidates}
        for c in list(positions.keys()):
            cf = cur.get(c)
            if not cf: continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            if cf["close"] < cf["ma20"] or cf["close"] < pos["peak"]*0.7:
                next_d = all_dates[di+1] if di+1 < len(all_dates) else None
                if next_d is None: continue
                ni = df_idx[c].get(next_d)
                if ni is None: continue
                sell_p = history[c]["Open"].iloc[ni]
                cash += pos["shares"]*sell_p*(1-bs.COMMISSION-bs.TAX)
                trades.append({
                    "ticker": c, "industry": get_industry(c),
                    "entry_date": pos["entry_date"], "exit_date": str(next_d.date()),
                    "entry": pos["entry_price"], "exit": sell_p,
                    "ret_pct": (sell_p/pos["entry_price"]-1)*100,
                    "reason": "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%",
                    "hold_days": (next_d - pd.Timestamp(pos["entry_date"])).days,
                    "engine": pos.get("engine", "?"),
                })
                del positions[c]

        # 進場（必須 stage 2）
        if not in_stage2: continue

        # V4 引擎挑 3 檔
        v4_picks = pick_v4(candidates, us_chg, prev_str, mcap)
        # Sync 引擎挑 2 檔（排除已被 V4 選的）
        v4_codes = {r["ticker"] for r in v4_picks}
        sync_picks = [r for r in pick_sync(candidates, us_chg, prev_str, mcap)
                      if r["ticker"] not in v4_codes]
        sync_picks = sync_picks[:SYNC_SLOTS]

        next_d = all_dates[di+1] if di+1 < len(all_dates) else None
        if next_d is None: continue

        # V4 進場
        for r in v4_picks:
            c = r["ticker"]
            if c in positions: continue
            n_v4 = sum(1 for p in positions.values() if p.get("engine") == "V4")
            if n_v4 >= V4_SLOTS: break
            ni = df_idx[c].get(next_d)
            if ni is None: continue
            buy_p = history[c]["Open"].iloc[ni]
            if cash < V4_PER * 0.5: break
            cps = buy_p*(1+bs.COMMISSION)
            sh = int(min(V4_PER, cash)/cps/1000)*1000
            if sh < 1000: continue
            cash -= sh*cps
            positions[c] = {"entry_price": buy_p, "shares": sh,
                            "peak": buy_p, "entry_date": str(next_d.date()),
                            "engine": "V4"}

        # Sync 進場
        for r in sync_picks:
            c = r["ticker"]
            if c in positions: continue
            n_sync = sum(1 for p in positions.values() if p.get("engine") == "Sync")
            if n_sync >= SYNC_SLOTS: break
            ni = df_idx[c].get(next_d)
            if ni is None: continue
            buy_p = history[c]["Open"].iloc[ni]
            if cash < SYNC_PER * 0.5: break
            cps = buy_p*(1+bs.COMMISSION)
            sh = int(min(SYNC_PER, cash)/cps/1000)*1000
            if sh < 1000: continue
            cash -= sh*cps
            positions[c] = {"entry_price": buy_p, "shares": sh,
                            "peak": buy_p, "entry_date": str(next_d.date()),
                            "engine": "Sync"}

    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None: continue
        last_p = history[c]["Close"].iloc[i]
        cash += pos["shares"]*last_p*(1-bs.COMMISSION-bs.TAX)

    return cash, trades


def main():
    codes = bs.load_universe()
    mcap = load_mcap()
    print(f"universe: {len(codes)} 檔，市值: {len(mcap)} 檔")
    us_chg = fetch_us_etfs()
    regime = fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100: return

    print("\n" + "=" * 60)
    print("🔬 V5 雙引擎回測：V4 (60%) + US-TW Sync (40%)")
    print("=" * 60)
    cash, trades = run_backtest(history, mcap, us_chg, regime)

    print("\n" + "=" * 60)
    print("📊 V5 雙引擎回測結果")
    print("=" * 60)
    bs.report(cash, trades, label="V5 雙引擎")

    # 引擎別分析
    print("\n📊 各引擎貢獻：")
    for eng in ["V4", "Sync"]:
        ts = [t for t in trades if t.get("engine") == eng]
        if not ts: continue
        n = len(ts); wins = sum(1 for t in ts if t["ret_pct"] > 0)
        avg = sum(t["ret_pct"] for t in ts) / n
        print(f"  {eng}: {n} 筆，{wins}/{n} 勝（{wins/n*100:.0f}%），平均 {avg:+.2f}%")

    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades),
           "version": "V5 dual-engine"}
    with open("backtest_v5.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_v5.json")


if __name__ == "__main__":
    main()
