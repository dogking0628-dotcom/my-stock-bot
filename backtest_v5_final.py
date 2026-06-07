# -*- coding: utf-8 -*-
"""
V5 最終版回測：V4 + 三大改進
─────────────────────────────────
1. 7 日內虧損股黑名單
2. 族群多樣化（最強 3 檔 + 第二強 2 檔）
3. 過熱日門檻（0050 距 MA200 > 50% → 動能 80→85）
"""
import sys, os, json, datetime as dt, time, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict, deque
import backtest_strategy as bs
from industry_map_loader import get_industry

ALLOWED = {"半導體", "電子零組件", "光電", "電腦及週邊",
           "電子通路", "通信網路", "其他電子"}
MIN_MCAP = 100
US_BOOST = {
    "QQQ": {"industries": list(ALLOWED), "threshold": 0.0, "bonus": 5},
    "SMH": {"industries": ["半導體", "其他電子"], "threshold": 0.5, "bonus": 10},
    "IGV": {"industries": ["通信網路", "電腦及週邊"], "threshold": 0.5, "bonus": 10},
}

# V5 新規則
RECENT_LOSER_WINDOW = 7
OVERHEAT_PCT = 50
NORMAL_THRESHOLD = 80
OVERHEAT_THRESHOLD = 85
TOP_QUOTA = 3
SECOND_QUOTA = 2


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
    print("[market] 抓 0050 體制 + 偏離率...")
    try:
        df = yf.download("0050.TW", start="2020-01-01", end=bs.END_DATE,
                         auto_adjust=True, progress=False, threads=False,
                         group_by="column")
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        cl = df["Close"].dropna()
        ma200 = cl.rolling(200).mean()
        out = {}
        for d, c, m in zip(cl.index, cl.values, ma200.values):
            if pd.isna(m): continue
            ext = (c / m - 1) * 100
            out[d.strftime("%Y-%m-%d")] = {"in_stage2": bool(c > m), "ext_pct": float(ext)}
        return out
    except Exception:
        return {}


def us_bonus(industry, prev_str, us_chg):
    bonus = 0
    for etf, cfg in US_BOOST.items():
        chg = us_chg.get(etf, {}).get(prev_str)
        if chg is None: continue
        if industry in cfg["industries"] and chg >= cfg["threshold"]:
            bonus += cfg["bonus"]
    return bonus


def run_v5(history, mcap, us_chg, regime):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    print(f"[v5] {len(all_dates)} 天")

    cash = bs.INITIAL
    positions = {}
    trades = []
    skip_regime = 0; skip_overheat_blocked = 0
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    # 🆕 V5: 滾動 7 日虧損股黑名單（追蹤剛剛賣出且虧損的）
    recent_losers_log = deque(maxlen=200)  # (exit_date, ticker)

    for di, d in enumerate(all_dates):
        if di < 200: continue
        d_str = d.strftime("%Y-%m-%d")
        prev_str = all_dates[di-1].strftime("%Y-%m-%d") if di > 0 else None
        reg = regime.get(d_str, {"in_stage2": False, "ext_pct": 0})
        in_stage2 = reg["in_stage2"]
        ext_pct = reg["ext_pct"]

        # V5: 過熱判定 → 動能門檻
        is_overheated = ext_pct > OVERHEAT_PCT
        threshold = OVERHEAT_THRESHOLD if is_overheated else NORMAL_THRESHOLD

        # V5: 從 log 找 7 日內虧損股
        cutoff = d - pd.Timedelta(days=RECENT_LOSER_WINDOW)
        recent_losers = {t for ed, t in recent_losers_log
                         if pd.Timestamp(ed) >= cutoff}

        # 收集當日候選
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

        # 族群統計
        by_ind = defaultdict(list)
        for r in candidates_today:
            ind = r.get("industry") or "未分類"
            by_ind[ind].append(r)
        ind_up = {ind: sum(1 for x in lst if x["change_pct"] > 0)/max(len(lst),1)
                  for ind, lst in by_ind.items()}
        for r in candidates_today:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # 出場（不受體制影響）
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
                ret = (sell_p / pos["entry_price"] - 1) * 100
                trades.append({
                    "ticker": c, "industry": get_industry(c),
                    "entry_date": pos["entry_date"], "exit_date": str(next_d.date()),
                    "entry": pos["entry_price"], "exit": sell_p,
                    "ret_pct": ret,
                    "reason": "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%",
                    "hold_days": (next_d - pd.Timestamp(pos["entry_date"])).days,
                })
                # 🆕 V5: 虧損出場 → 記錄入黑名單
                if ret < 0:
                    recent_losers_log.append((str(next_d.date()), c))
                del positions[c]

        # 進場
        if not in_stage2:
            skip_regime += 1; continue

        # 篩出 ATH + 科技 + 市值
        ath_set = []
        for r in candidates_today:
            if not r["is_ath"]: continue
            if r.get("industry") not in ALLOWED: continue
            mc = mcap.get(r["ticker"])
            if mc is None or mc < MIN_MCAP: continue
            ath_set.append(r)

        # 評分 (含美股加分)
        for r in ath_set:
            sc, _ = bs.momentum_score(r)
            sc += us_bonus(r["industry"], prev_str, us_chg)
            r["score"] = min(sc, 100)

        # V5 過濾：黑名單 + 動能門檻
        def _pass(r):
            if r["ticker"] in recent_losers: return False
            if r["score"] < threshold: return False
            return True

        # 找最強族群
        ath_by_ind = defaultdict(list)
        for r in ath_set:
            ath_by_ind[r["industry"]].append(r)
        sorted_inds = sorted(ath_by_ind.items(), key=lambda x: -len(x[1]))

        top_picks = []
        # 最強族群挑 TOP_QUOTA
        for ind, lst in sorted_inds:
            br = sum(1 for x in lst if x["bullish"])/max(len(lst),1)
            if len(lst) >= 3 and br >= 0.5:
                qualified = sorted([r for r in lst if _pass(r)],
                                   key=lambda x: (-x["score"], -x["change_pct"]))
                top_picks = qualified[:TOP_QUOTA]
                used_ind = ind
                break

        # 找第二強族群挑 SECOND_QUOTA
        second_picks = []
        if top_picks:
            existing = {r["ticker"] for r in top_picks}
            for ind, lst in sorted_inds:
                if ind == used_ind: continue
                br = sum(1 for x in lst if x["bullish"])/max(len(lst),1)
                if br >= 0.5:
                    qualified = [r for r in lst if _pass(r) and r["ticker"] not in existing]
                    qualified.sort(key=lambda x: (-x["score"], -x["change_pct"]))
                    second_picks = qualified[:SECOND_QUOTA]
                    if second_picks: break

        top5 = top_picks + second_picks

        # 不足 5 檔從科技族群補
        if len(top5) < 5:
            existing = {r["ticker"] for r in top5}
            extra = [r for r in ath_set if _pass(r) and r["ticker"] not in existing]
            extra.sort(key=lambda x: -x["score"])
            top5 += extra[: 5-len(top5)]

        if not top5:
            if is_overheated: skip_overheat_blocked += 1
            continue

        slots = bs.MAX_POS - len(positions)
        if slots <= 0: continue
        buys = [r for r in top5 if r["ticker"] not in positions][:slots]
        next_d = all_dates[di+1] if di+1 < len(all_dates) else None
        if next_d is None: continue
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

    print(f"[v5] 跳過：體制熊市 {skip_regime} 天 / 過熱濾空 {skip_overheat_blocked} 天")
    return cash, trades


def main():
    codes = bs.load_universe()
    mcap = load_mcap()
    print(f"universe: {len(codes)} 檔，市值快取 {len(mcap)} 檔")
    us_chg = fetch_us_sectors()
    regime = fetch_0050()
    history = bs.fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足"); return

    print("\n" + "=" * 60)
    print("🔬 V5 最終版回測：V4 + 三大改進")
    print("   ① 7 日內虧損股黑名單")
    print("   ② 族群多樣化（最強 3 + 第二強 2）")
    print("   ③ 過熱日動能門檻 80 → 85")
    print("=" * 60)
    cash, trades = run_v5(history, mcap, us_chg, regime)

    print("\n" + "=" * 60)
    print("📊 V5 最終版回測結果")
    print("=" * 60)
    bs.report(cash, trades, label="V5 final")

    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades),
           "version": "v5 final (V4 + recent losers + diversification + overheat)"}
    with open("backtest_v5_final.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_v5_final.json")


if __name__ == "__main__":
    main()
