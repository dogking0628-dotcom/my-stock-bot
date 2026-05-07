# -*- coding: utf-8 -*-
"""
V4 回測：V3 + 大盤體制濾網（0050 必須在 MA200 之上才進場）
─────────────────────────────────
理論依據：Stan Weinstein Stage Analysis
  Stage 1: 底部盤整     → 不進場
  Stage 2: 主升段       → ⭐ 全力進攻
  Stage 3: 頂部分配     → 鎖利
  Stage 4: 主跌段       → 嚴禁追價

判定：0050 收盤 > MA200 = Stage 2，可進場
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
MIN_MCAP = 100  # 億

US_BOOST = {
    "QQQ": {"industries": list(ALLOWED), "threshold": 0.0, "bonus": 5},
    "SMH": {"industries": ["半導體", "其他電子"], "threshold": 0.5, "bonus": 10},
    "IGV": {"industries": ["通信網路", "電腦及週邊"], "threshold": 0.5, "bonus": 10},
}


def load_mcap():
    if not os.path.exists("marketcap_cache.json"): return {}
    with io.open("marketcap_cache.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_us_sectors():
    out = {}
    for tk in US_BOOST.keys():
        try:
            df = yf.download(tk, start=bs.START_DATE, end=bs.END_DATE,
                             auto_adjust=True, progress=False, threads=False,
                             group_by="column")
            if hasattr(df.columns, "levels"):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            cl = df["Close"].dropna()
            chg = cl.pct_change() * 100
            out[tk] = {d.strftime("%Y-%m-%d"): float(c)
                       for d, c in chg.dropna().items()}
        except Exception: pass
        time.sleep(0.5)
    return out


def fetch_0050():
    """抓 0050 並算 MA200"""
    print("[market] 抓 0050 體制資料...")
    try:
        df = yf.download("0050.TW", start="2020-01-01", end=bs.END_DATE,
                         auto_adjust=True, progress=False, threads=False,
                         group_by="column")
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        cl = df["Close"].dropna()
        ma200 = cl.rolling(200).mean()
        # {date_str: True/False (in stage 2)}
        return {d.strftime("%Y-%m-%d"): bool(c > m)
                for d, c, m in zip(cl.index, cl.values, ma200.values)
                if not pd.isna(m)}
    except Exception as e:
        print(f"  fail: {e}"); return {}


def us_bonus(industry, prev_str, us_chg):
    bonus = 0; notes = []
    for etf, cfg in US_BOOST.items():
        chg = us_chg.get(etf, {}).get(prev_str)
        if chg is None: continue
        if industry in cfg["industries"] and chg >= cfg["threshold"]:
            bonus += cfg["bonus"]; notes.append(f"{etf}+{chg:.1f}%")
    return bonus, notes


def run_v4(history, mcap, us_chg, regime):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    print(f"[v4] {len(all_dates)} 個交易日，0050 體制資料 {len(regime)} 日")

    cash = bs.INITIAL
    positions = {}
    trades = []
    skipped_days = 0
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    for di, d in enumerate(all_dates):
        if di < 200: continue
        d_str = d.strftime("%Y-%m-%d")
        prev_str = all_dates[di-1].strftime("%Y-%m-%d") if di > 0 else None

        # V4 核心：檢查 0050 體制
        in_stage2 = regime.get(d_str, False)

        candidates = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = bs.daily_features(df, i)
            if not f: continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            candidates.append(f)

        by_ind = defaultdict(list)
        for r in candidates:
            ind = r.get("industry") or "未分類"
            by_ind[ind].append(r)
        ind_up = {ind: sum(1 for x in lst if x["change_pct"]>0)/max(len(lst),1)
                  for ind, lst in by_ind.items()}
        for r in candidates:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        ath_set = []
        for r in candidates:
            if not r["is_ath"]: continue
            if r.get("industry") not in ALLOWED: continue
            mc = mcap.get(r["ticker"])
            if mc is None or mc < MIN_MCAP: continue
            ath_set.append(r)

        for r in ath_set:
            sc, _ = bs.momentum_score(r)
            us_b, _ = us_bonus(r["industry"], prev_str, us_chg)
            r["score"] = min(sc + us_b, 100)

        ath_by_ind = defaultdict(list)
        for r in ath_set: ath_by_ind[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by_ind.items(), key=lambda x: -len(x[1])):
            br = sum(1 for x in lst if x["bullish"])/max(len(lst),1)
            if len(lst) >= 3 and br >= 0.5:
                strongest = ind; break
        if strongest:
            pool = sorted(ath_by_ind[strongest], key=lambda x: (-x["score"], -x["change_pct"]))
        else:
            pool = sorted(ath_set, key=lambda x: -x["score"])
        top5 = [r for r in pool if r["score"] >= 80][:5]

        # 出場（出場規則不受體制影響）
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
                    "regime_at_entry": pos.get("regime_stage2", True),
                })
                del positions[c]

        # 進場：V4 額外要求 in_stage2
        if not in_stage2:
            skipped_days += 1
            continue

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
                                    "peak": buy_p, "entry_date": str(next_d.date()),
                                    "regime_stage2": True}

    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None: continue
        last_p = history[c]["Close"].iloc[i]
        cash += pos["shares"]*last_p*(1-bs.COMMISSION-bs.TAX)

    print(f"[v4] 跳過 {skipped_days} 天（0050 < MA200，熊市）")
    return cash, trades, skipped_days


def main():
    codes = bs.load_universe()
    print(f"universe: {len(codes)} 檔")
    mcap = load_mcap()
    print(f"mcap: {len(mcap)} 檔")
    us_chg = fetch_us_sectors()
    regime = fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足"); return

    print("\n" + "=" * 60)
    print("🔬 V4 回測：V3 + 0050 體制濾網")
    print("=" * 60)
    cash, trades, skipped = run_v4(history, mcap, us_chg, regime)

    print("\n" + "=" * 60)
    print("📊 V4 回測結果")
    print("=" * 60)
    bs.report(cash, trades, label="V4")
    print(f"\n跳過進場天數：{skipped} 天（佔回測期 {skipped/1290*100:.1f}%）")

    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades),
           "skipped_days": skipped, "version": "v4 (V3 + 0050 regime)"}
    with open("backtest_v4.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_v4.json")


if __name__ == "__main__":
    main()
