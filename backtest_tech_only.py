# -*- coding: utf-8 -*-
"""
回測對照組：只挑半導體 + 電子零組件 + 光電 + 電腦及週邊 + 電子通路 + 通信網路
用同一份歷史資料 + 同一套出場邏輯，但限制產業
"""
import sys, os, json, datetime as dt
import io as _io
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

# 從 backtest_strategy.py 重用所有函式
sys.path.insert(0, os.path.dirname(__file__))
import backtest_strategy as bs
import pandas as pd
from collections import defaultdict
from industry_map_loader import get_industry

ALLOWED = {"半導體", "電子零組件", "光電", "電腦及週邊", "電子通路", "通信網路", "其他電子"}


def run_backtest_filtered(history):
    """同 bs.run_backtest 但只進場 ALLOWED 產業"""
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    print(f"[bt] 回測 {len(all_dates)} 個交易日（限產業：{ALLOWED}）")

    cash = bs.INITIAL
    positions = {}
    trades = []
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    for di, d in enumerate(all_dates):
        if di < 200: continue
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
        ind_up = {ind: sum(1 for x in lst if x["change_pct"] > 0)/max(len(lst),1)
                  for ind, lst in by_ind.items()}
        for r in candidates:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # ATH + 限產業
        ath_set = [r for r in candidates if r["is_ath"]
                   and r.get("industry") in ALLOWED]
        for r in ath_set:
            sc, _ = bs.momentum_score(r); r["score"] = sc

        ath_by_ind = defaultdict(list)
        for r in ath_set:
            ath_by_ind[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by_ind.items(), key=lambda x: -len(x[1])):
            br = sum(1 for x in lst if x["bullish"])/max(len(lst),1)
            if len(lst) >= 5 and br >= 0.5:
                strongest = ind; break
        if strongest:
            pool = sorted(ath_by_ind[strongest],
                          key=lambda x: (-x["score"], -x["change_pct"]))
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
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足"); return

    print("\n" + "=" * 60)
    print("🔬 對照組：只挑科技股族群（半/電零/光電/電腦/電通/通信/其他電子）")
    print("=" * 60)
    cash_filt, trades_filt = run_backtest_filtered(history)

    print("\n" + "=" * 60)
    print("📊 限制版（科技股限定）回測結果")
    print("=" * 60)
    bs.report(cash_filt, trades_filt, label="V2 tech-only")

    out = {"final_cash": cash_filt, "trades": trades_filt,
           "n_trades": len(trades_filt),
           "filter": "tech industries only"}
    with open("backtest_tech_only.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_tech_only.json")


if __name__ == "__main__":
    main()
