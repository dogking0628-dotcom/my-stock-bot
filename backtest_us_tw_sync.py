# -*- coding: utf-8 -*-
"""
美台同步策略回測：
─────────────────────────────────
邏輯：
  1. 每日盤前看昨日美股 ETF（XLK/SMH/IGV/XLF/...）
  2. 找 +1% 大漲的美股族群
  3. 對應到台股族群
  4. 在那個族群內找昨日漲幅最大且 ATH + 多頭 + 市值 ≥ 100億 + 0050>MA200 的前 5 檔
  5. 同樣 20MA 出場
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

# 美股 ETF → TW 族群（含整體 + 子族群）
US_TW_MAP = {
    "SMH":  ["半導體", "其他電子"],
    "IGV":  ["通信網路", "電腦及週邊"],
    "XLK":  list(ALLOWED),
    "SOXL": ["半導體"],
    "QQQ":  list(ALLOWED),
    "XLF":  ["金融保險"],
    "XLY":  ["汽車"],
}

HOT_THRESHOLD = 1.0  # 美股 ≥ +1% 視為大漲族群


def load_mcap():
    if not os.path.exists("marketcap_cache.json"): return {}
    with io.open("marketcap_cache.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_us_etfs():
    """抓所有對照組 ETF 5 年資料"""
    print("[us-tw] 抓美股 ETF 5 年...")
    out = {}
    for tk in US_TW_MAP.keys():
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
        except Exception as e:
            print(f"  {tk} fail: {e}")
        time.sleep(0.5)
    return out


def fetch_0050():
    print("[market] 抓 0050 體制資料...")
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


def get_synced_industries(prev_str, us_chg):
    """根據昨日美股決定今日要關注的台股族群"""
    if not prev_str: return set()
    target = set()
    for etf, mapping in US_TW_MAP.items():
        chg = us_chg.get(etf, {}).get(prev_str)
        if chg is None: continue
        if chg >= HOT_THRESHOLD:
            target.update(mapping)
    target &= ALLOWED  # 只保留科技族群
    return target


def run_backtest(history, mcap, us_chg, regime):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    print(f"[bt] {len(all_dates)} 天")

    cash = bs.INITIAL
    positions = {}
    trades = []
    skip_no_us = 0; skip_regime = 0
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    for di, d in enumerate(all_dates):
        if di < 200: continue
        d_str = d.strftime("%Y-%m-%d")
        prev_str = all_dates[di-1].strftime("%Y-%m-%d") if di > 0 else None
        in_stage2 = regime.get(d_str, False)

        # 出場（不受體制影響）
        candidates_today = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = bs.daily_features(df, i)
            if not f: continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            candidates_today.append(f)
        cur = {r["ticker"]: r for r in candidates_today}
        for c in list(positions.keys()):
            cf = cur.get(c)
            if not cf: continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            if cf["close"] < cf["ma20"] or cf["close"] < pos["peak"] * 0.7:
                next_d = all_dates[di+1] if di+1 < len(all_dates) else None
                if next_d is None: continue
                ni = df_idx[c].get(next_d)
                if ni is None: continue
                sell_p = history[c]["Open"].iloc[ni]
                cash += pos["shares"] * sell_p * (1 - bs.COMMISSION - bs.TAX)
                trades.append({
                    "ticker": c, "industry": get_industry(c),
                    "entry_date": pos["entry_date"], "exit_date": str(next_d.date()),
                    "entry": pos["entry_price"], "exit": sell_p,
                    "ret_pct": (sell_p / pos["entry_price"] - 1) * 100,
                    "reason": "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%",
                    "hold_days": (next_d - pd.Timestamp(pos["entry_date"])).days,
                })
                del positions[c]

        # 進場條件
        if not in_stage2:
            skip_regime += 1; continue

        target_inds = get_synced_industries(prev_str, us_chg)
        if not target_inds:
            skip_no_us += 1; continue

        # 從目標族群挑前 5 檔（昨日漲幅 + ATH + 市值 + 多頭）
        candidates = []
        for r in candidates_today:
            if r.get("industry") not in target_inds: continue
            if not r.get("is_ath"): continue
            if not r.get("bullish"): continue
            mc = mcap.get(r["ticker"])
            if mc is None or mc < MIN_MCAP: continue
            candidates.append(r)
        candidates.sort(key=lambda x: -x["change_pct"])  # 昨日漲幅最大優先
        top5 = candidates[:5]

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
                    if cash < bs.PER_POS * 0.5: break
                    cps = buy_p * (1 + bs.COMMISSION)
                    sh = int(min(bs.PER_POS, cash) / cps / 1000) * 1000
                    if sh < 1000: continue
                    cash -= sh * cps
                    positions[c] = {"entry_price": buy_p, "shares": sh,
                                    "peak": buy_p, "entry_date": str(next_d.date())}

    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None: continue
        last_p = history[c]["Close"].iloc[i]
        cash += pos["shares"] * last_p * (1 - bs.COMMISSION - bs.TAX)

    print(f"\n[bt] 跳過：體制熊市 {skip_regime} 天 / 美股無大漲族群 {skip_no_us} 天")
    return cash, trades


def main():
    codes = bs.load_universe()
    mcap = load_mcap()
    print(f"universe: {len(codes)} 檔，市值快取: {len(mcap)} 檔")
    us_chg = fetch_us_etfs()
    regime = fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足"); return

    print("\n" + "=" * 60)
    print("🔬 US-TW Sync 回測：美股大漲族群 → 台股對應族群挑 Top 5")
    print("=" * 60)
    cash, trades = run_backtest(history, mcap, us_chg, regime)

    print("\n" + "=" * 60)
    print("📊 US-TW Sync 回測結果")
    print("=" * 60)
    bs.report(cash, trades, label="US-TW Sync")

    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades),
           "version": "US-TW sync (V4 base)"}
    with open("backtest_us_tw_sync.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_us_tw_sync.json")


if __name__ == "__main__":
    main()
