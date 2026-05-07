# -*- coding: utf-8 -*-
"""
回測 V3：科技限定 + 市值 ≥ 100億 + 美股科技昨日同漲加分
─────────────────────────────────
進場 (全滿足):
  1. 創 2y 月線 ATH (today >= max_2y * 0.999)
  2. 屬科技限定 7 族群
  3. 市值 ≥ 100 億 NT$
  4. 動能分數 ≥ 80（含美股連動加分）

美股加分:
  - 昨日 QQQ 漲 → 全科技 +5
  - 昨日 SMH 漲 ≥ 0.5% → 半導體/其他電子 +10
  - 昨日 IGV 漲 ≥ 0.5% → AI/通信網路 +10

出場: 跌破 20MA 或 從峰值 -30%
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
MIN_MCAP_BILLIONS = 100  # 億 NT$

# 美股 → TW 族群對應
US_BOOST = {
    "QQQ": {"industries": list(ALLOWED), "threshold": 0.0, "bonus": 5},
    "SMH": {"industries": ["半導體", "其他電子"], "threshold": 0.5, "bonus": 10},
    "IGV": {"industries": ["通信網路", "電腦及週邊"], "threshold": 0.5, "bonus": 10},
}


def load_marketcaps():
    """讀剛才存的或重抓"""
    cache = "marketcap_cache.json"
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    return {}


def fetch_us_sectors():
    """抓 QQQ/SMH/IGV 5y 歷史"""
    print("[us] 抓美股族群 ETF...")
    out = {}
    for tk in US_BOOST.keys():
        try:
            df = yf.download(tk, start=bs.START_DATE, end=bs.END_DATE,
                             auto_adjust=True, progress=False, threads=False)
            if df.empty: continue
            cl = df["Close"].dropna()
            chg = cl.pct_change() * 100
            # 用日期字串做 key
            out[tk] = {d.strftime("%Y-%m-%d"): float(c)
                       for d, c in chg.dropna().items()}
            print(f"  {tk}: {len(out[tk])} 日")
        except Exception as e:
            print(f"  {tk} fail: {e}")
        time.sleep(0.5)
    return out


def us_bonus_for(industry, prev_date_str, us_chg):
    """計算美股加分（基於昨日漲跌）"""
    bonus = 0; notes = []
    for etf, cfg in US_BOOST.items():
        chg = us_chg.get(etf, {}).get(prev_date_str)
        if chg is None: continue
        if industry in cfg["industries"] and chg >= cfg["threshold"]:
            bonus += cfg["bonus"]
            notes.append(f"{etf}昨+{chg:.1f}%")
    return bonus, notes


def run_backtest_v3(history, mcap, us_chg):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    print(f"[bt v3] {len(all_dates)} 個交易日")

    cash = bs.INITIAL
    positions = {}
    trades = []
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    for di, d in enumerate(all_dates):
        if di < 200: continue
        prev_d_str = all_dates[di-1].strftime("%Y-%m-%d") if di > 0 else None

        candidates = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = bs.daily_features(df, i)
            if not f: continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            candidates.append(f)

        # 族群統計
        by_ind = defaultdict(list)
        for r in candidates:
            ind = r.get("industry") or "未分類"
            by_ind[ind].append(r)
        ind_up = {ind: sum(1 for x in lst if x["change_pct"]>0)/max(len(lst),1)
                  for ind, lst in by_ind.items()}
        for r in candidates:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # 套用三濾網: 科技 + 市值 + ATH
        ath_set = []
        for r in candidates:
            if not r["is_ath"]: continue
            if r.get("industry") not in ALLOWED: continue
            mc = mcap.get(r["ticker"])
            if mc is None or mc < MIN_MCAP_BILLIONS: continue
            ath_set.append(r)

        # 動能分數（加美股加分）
        for r in ath_set:
            sc, notes = bs.momentum_score(r)
            us_b, us_notes = us_bonus_for(r["industry"], prev_d_str, us_chg)
            r["score"] = min(sc + us_b, 100)
            r["momentum_notes"] = notes + us_notes

        # 最強族群
        ath_by_ind = defaultdict(list)
        for r in ath_set: ath_by_ind[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by_ind.items(), key=lambda x: -len(x[1])):
            br = sum(1 for x in lst if x["bullish"])/max(len(lst),1)
            if len(lst) >= 3 and br >= 0.5:  # 放寬到 3 因為市值濾後候選變少
                strongest = ind; break
        if strongest:
            pool = sorted(ath_by_ind[strongest], key=lambda x: (-x["score"], -x["change_pct"]))
        else:
            pool = sorted(ath_set, key=lambda x: -x["score"])
        top5 = [r for r in pool if r["score"] >= 80][:5]

        # 出場
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
                })
                del positions[c]

        # 進場
        slots = bs.MAX_POS - len(positions)
        if slots > 0 and top5:
            buys = [r for r in top5 if r["ticker"] not in positions][:slots]
            next_d = all_dates[di+1] if di+1 < len(all_dates) else None
            if next_d:
                for r in buys:
                    c = r["ticker"]
                    ni = df_idx[c].get(next_d)
                    if ni is None: continue
                    buy_p = history[c]["Open"].iloc[ni]
                    if cash < bs.PER_POS*0.5: break
                    cps = buy_p*(1+bs.COMMISSION)
                    sh = int(min(bs.PER_POS, cash)/cps/1000)*1000
                    if sh < 1000: continue
                    cash -= sh*cps
                    positions[c] = {"entry_price": buy_p, "shares": sh,
                                    "peak": buy_p, "entry_date": str(next_d.date())}

    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None: continue
        last_p = history[c]["Close"].iloc[i]
        cash += pos["shares"]*last_p*(1-bs.COMMISSION-bs.TAX)
    return cash, trades


def main():
    codes = bs.load_universe()
    print(f"universe: {len(codes)} 檔")

    # 1. 抓市值
    mcap = load_marketcaps()
    if not mcap:
        print("[mcap] 重抓全市場市值...")
        mcap = {}
        for i, c in enumerate(codes):
            try:
                m = yf.Ticker(f"{c}.TW").info.get("marketCap")
                if m: mcap[c] = m / 1e8
            except Exception: pass
            if i % 100 == 0: print(f"  [{i}/{len(codes)}]")
            time.sleep(0.2)
        with open("marketcap_cache.json", "w", encoding="utf-8") as f:
            json.dump(mcap, f, ensure_ascii=False, indent=2)
    print(f"[mcap] 已知市值: {len(mcap)} 檔")

    # 2. 抓美股
    us_chg = fetch_us_sectors()

    # 3. 抓 TW 歷史
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足"); return

    print("\n" + "=" * 60)
    print("🔬 V3 回測：科技 + 市值 ≥ 100億 + 美股加分")
    print("=" * 60)
    cash, trades = run_backtest_v3(history, mcap, us_chg)

    print("\n" + "=" * 60)
    print("📊 V3 回測結果")
    print("=" * 60)
    bs.report(cash, trades)

    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades),
           "version": "v3 (tech + mcap + us_boost)"}
    with open("backtest_v3.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_v3.json")


if __name__ == "__main__":
    main()
