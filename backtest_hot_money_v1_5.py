# -*- coding: utf-8 -*-
"""
回測：Hot Money Radar v1.5（族群持續性版）
═════════════════════════════════════════════════
v1 痛點（用戶觀察）：
  - 每天看「rising」族群 → 今天電子明天傳產
  - 沒有族群「持續性買盤」概念
  - 真實熱錢族群會連續多天主導，不是 1 天閃過

v1.5 新增：
  1. 族群必須連續 N 天 rising（trend_days ≥ 2）
     - 1 天閃過的「假升溫」自動排除
  2. 持倉族群鎖定加分
     - 已持倉的族群 → 補位優先（避免換來換去）
  3. 族群分散度限制
     - 同時 5 檔最多分散 2 個族群

出場：同 v1（跌破 20MA / 從峰值 -30%）
"""
import sys, os, json, io, time
import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict, deque

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_strategy as bs
from industry_map_loader import get_industry

# ── 回測期間（過去 3 年）──
START_DATE = "2023-05-01"
END_DATE = "2026-05-22"

# ── 策略參數（與 hot_money_radar.py 一致）──
LOOKBACK_DAYS = 5
RISING_THRESHOLD = 10      # 升溫族群門檻
COOLING_THRESHOLD = -5     # 退潮族群門檻
MIN_BASE = 3
ALLOWED_INDS = None        # None = 不限族群（vs V4 限 7 科技）

# ── 韌性過濾（用戶指標）──
MAX_PULLBACK_3D = -10.0    # 3 日拉回最多 -10%
USE_RESILIENCE = True      # 開關，可關閉做 A/B test

# ── v1.5 族群持續性 ──
MIN_TREND_DAYS = 2         # 族群至少連 N 天 rising (排除 1 天閃過)
MAX_INDUSTRIES = 2         # 同時持倉最多分散 N 個族群
HELD_INDUSTRY_BONUS = 30   # 已持倉族群的個股加分 (避免每天換族群)

# ── 投資組合設定 ──
INITIAL = 1_000_000
MAX_SLOTS = 5              # 同時最多 5 檔
PER_SLOT = INITIAL / MAX_SLOTS  # 每檔 20 萬
MIN_MCAP = 100             # 100 億以上


def load_mcap():
    if not os.path.exists("marketcap_cache.json"):
        return {}
    with open("marketcap_cache.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_0050_regime():
    """0050 vs MA200，stage 2 才進場"""
    try:
        df = yf.download("0050.TW", start="2020-01-01", end=END_DATE,
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


def max_dd_n(closes, n):
    """N 天內 close-to-close 最大回檔"""
    if len(closes) < 2:
        return 0.0
    window = closes[-n:]
    peak = window[0]
    m = 0.0
    for c in window:
        if c > peak:
            peak = c
        dd = (c / peak - 1) * 100
        if dd < m:
            m = dd
    return m


def compute_industry_momentum(heat_history, today_stats):
    """
    計算每族群的 N 日熱度動能 + 連續升溫天數（v1.5）
    heat_history: deque of past N+ days {industry: ath_count}
    today_stats: {industry: ath_count} 今天
    """
    if len(heat_history) < LOOKBACK_DAYS:
        return {ind: {"momentum_pct": None, "status": "new", "today": cnt, "trend_days": 0}
                for ind, cnt in today_stats.items()}

    past = list(heat_history)[-LOOKBACK_DAYS:]
    result = {}
    for ind, today_cnt in today_stats.items():
        past_cnts = [p.get(ind, 0) for p in past]
        avg_n = sum(past_cnts) / len(past_cnts)
        if avg_n < MIN_BASE and today_cnt < MIN_BASE:
            result[ind] = {"momentum_pct": None, "status": "noise",
                          "today": today_cnt, "avg_n": avg_n, "trend_days": 0}
            continue
        mom = (today_cnt / max(avg_n, 1) - 1) * 100
        if mom >= RISING_THRESHOLD:
            status = "rising"
        elif mom <= COOLING_THRESHOLD:
            status = "cooling"
        else:
            status = "steady"

        # 計算連續升溫天數（從今天往回算，差分連續正）
        all_cnts = past_cnts + [today_cnt]
        diffs = [all_cnts[i+1] - all_cnts[i] for i in range(len(all_cnts)-1)]
        trend_days = 0
        if status == "rising":
            for diff in reversed(diffs):
                if diff > 0:
                    trend_days += 1
                else:
                    break

        result[ind] = {"momentum_pct": mom, "status": status,
                       "today": today_cnt, "avg_n": avg_n, "trend_days": trend_days}
    return result


def pick_hot_money(candidates, momentum, mcap, history, df_idx, d, held_industries=None):
    """
    v1.5 從「連續升溫」族群挑真突破：
    - 族群 status == rising AND trend_days >= MIN_TREND_DAYS（核心改動）
    - is_ath + bullish + 市值 ≥ 100 億
    - 3 日拉回 ≥ -10%
    - 持倉族群優先補位（HELD_INDUSTRY_BONUS）
    """
    held_industries = held_industries or set()

    # 只取「連續升溫 ≥ MIN_TREND_DAYS 天」的族群 — 排除 1 天閃過
    qualified_inds = {ind for ind, m in momentum.items()
                      if m["status"] == "rising"
                      and m.get("trend_days", 0) >= MIN_TREND_DAYS}

    # 持倉族群即使 trend_days 不夠，只要還是 rising 就保留（不換掉）
    for ind in held_industries:
        m = momentum.get(ind, {})
        if m.get("status") == "rising":
            qualified_inds.add(ind)

    if not qualified_inds:
        return []

    pool = []
    for c in candidates:
        ind = c.get("industry")
        if ind not in qualified_inds:
            continue
        if not c.get("is_ath"):
            continue
        if not c.get("bullish"):
            continue
        mc = mcap.get(c["ticker"])
        if mc is None or mc < MIN_MCAP:
            continue

        # 韌性
        if USE_RESILIENCE:
            df = history.get(c["ticker"])
            if df is None:
                continue
            i = df_idx[c["ticker"]].get(d)
            if i is None or i < 3:
                continue
            closes = df["Close"].iloc[max(0, i-9):i+1].values
            p3 = max_dd_n(closes, 3)
            if p3 < MAX_PULLBACK_3D:
                continue
            c["_pullback_3d"] = p3

        # 綜合分
        mom_pct = momentum[ind].get("momentum_pct", 0)
        trend = momentum[ind].get("trend_days", 0)
        ratio_score = (c.get("close", 0) / max(c.get("ma20", 1), 0.01) - 1) * 100
        held_bonus = HELD_INDUSTRY_BONUS if ind in held_industries else 0
        score = ratio_score + mom_pct * 0.5 + trend * 10 + held_bonus
        c["_rotation_score"] = score
        pool.append(c)

    pool.sort(key=lambda x: -x["_rotation_score"])

    # 族群分散度限制：同時持倉最多 MAX_INDUSTRIES 個族群
    final = []
    industries_in_picks = set(held_industries)
    for p in pool:
        ind = p.get("industry")
        if ind not in industries_in_picks and len(industries_in_picks) >= MAX_INDUSTRIES:
            continue
        industries_in_picks.add(ind)
        final.append(p)
        if len(final) >= MAX_SLOTS:
            break
    return final


def run_backtest(history, mcap, regime, name="Hot Money Radar v1.5"):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates
                 if pd.Timestamp(START_DATE) <= d <= pd.Timestamp(END_DATE)]
    print(f"[{name}] 回測 {len(all_dates)} 個交易日")

    cash = INITIAL
    positions = {}  # {ticker: {entry_price, shares, peak, entry_date}}
    trades = []
    df_idx = {c: {d: i for i, d in enumerate(df.index)}
              for c, df in history.items()}

    # 族群熱度歷史（rolling window）
    heat_history = deque(maxlen=LOOKBACK_DAYS + 5)

    # 進度
    n_days = len(all_dates)
    for di, d in enumerate(all_dates):
        if di < 200:  # 等 MA200 暖機
            continue
        if di % 50 == 0:
            print(f"  [{di}/{n_days}] {d.strftime('%Y-%m-%d')} "
                  f"持倉 {len(positions)} 累計 {len(trades)} 筆")

        d_str = d.strftime("%Y-%m-%d")
        in_stage2 = regime.get(d_str, False)

        # 算所有股票今日特徵
        candidates = []
        today_stats = defaultdict(int)
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200:
                continue
            f = bs.daily_features(df, i)
            if not f:
                continue
            f["ticker"] = c
            f["industry"] = get_industry(c) or "未分類"
            candidates.append(f)
            if f.get("is_ath"):
                today_stats[f["industry"]] += 1

        # 更新族群動能
        momentum = compute_industry_momentum(heat_history, dict(today_stats))
        heat_history.append(dict(today_stats))

        # 出場（不受體制影響）
        cur = {r["ticker"]: r for r in candidates}
        for c in list(positions.keys()):
            cf = cur.get(c)
            if not cf:
                continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            if cf["close"] < cf["ma20"] or cf["close"] < pos["peak"] * 0.7:
                next_d = all_dates[di+1] if di+1 < len(all_dates) else None
                if next_d is None:
                    continue
                ni = df_idx[c].get(next_d)
                if ni is None:
                    continue
                sell_p = history[c]["Open"].iloc[ni]
                cash += pos["shares"] * sell_p * (1 - bs.COMMISSION - bs.TAX)
                trades.append({
                    "ticker": c, "industry": get_industry(c) or "未分類",
                    "entry_date": pos["entry_date"], "exit_date": str(next_d.date()),
                    "entry": pos["entry_price"], "exit": float(sell_p),
                    "ret_pct": (sell_p / pos["entry_price"] - 1) * 100,
                    "reason": "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%",
                    "hold_days": (next_d - pd.Timestamp(pos["entry_date"])).days,
                })
                del positions[c]

        # 進場
        if not in_stage2:
            continue
        if len(positions) >= MAX_SLOTS:
            continue

        # v1.5: 把已持倉的族群傳給 picker → 同族群補位優先
        held_inds = {get_industry(c) or "未分類" for c in positions.keys()}
        picks = pick_hot_money(candidates, momentum, mcap, history, df_idx, d, held_inds)
        next_d = all_dates[di+1] if di+1 < len(all_dates) else None
        if next_d is None:
            continue

        for r in picks:
            if len(positions) >= MAX_SLOTS:
                break
            c = r["ticker"]
            if c in positions:
                continue
            ni = df_idx[c].get(next_d)
            if ni is None:
                continue
            buy_p = float(history[c]["Open"].iloc[ni])
            if cash < PER_SLOT * 0.5:
                break
            cps = buy_p * (1 + bs.COMMISSION)
            sh = int(min(PER_SLOT, cash) / cps / 1000) * 1000
            if sh < 1000:
                continue
            cash -= sh * cps
            positions[c] = {
                "entry_price": buy_p, "shares": sh,
                "peak": buy_p, "entry_date": str(next_d.date()),
            }

    # 結算剩餘持倉
    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None:
            continue
        last_p = float(history[c]["Close"].iloc[i])
        cash += pos["shares"] * last_p * (1 - bs.COMMISSION - bs.TAX)

    return cash, trades


def main():
    print(f"📅 回測期間：{START_DATE} ~ {END_DATE}")
    print(f"📊 策略：Hot Money Radar")
    print(f"   - 升溫族群 +{RISING_THRESHOLD}%")
    print(f"   - 退潮族群 {COOLING_THRESHOLD}%")
    print(f"   - 韌性過濾：3日拉回 ≥ {MAX_PULLBACK_3D}% (USE={USE_RESILIENCE})")
    print(f"   - 同時 {MAX_SLOTS} 檔")
    print()

    print("[1/4] 載入 universe...")
    # 保留 bs.START_DATE = 2021-01-01 抓完整 5y history（504 天才能算 2y ATH）
    # 只在回測 loop 內限定 START_DATE ~ END_DATE 區間
    bs.END_DATE = END_DATE
    codes = bs.load_universe()
    mcap = load_mcap()
    print(f"      universe: {len(codes)} 檔，市值: {len(mcap)} 檔")
    print(f"      bs.START_DATE = {bs.START_DATE} (5y for ATH)，回測區間 {START_DATE} ~ {END_DATE}")

    print("[2/4] 抓 0050 大盤體制...")
    regime = fetch_0050_regime()
    print(f"      0050 體制 {len(regime)} 天，stage2 {sum(1 for v in regime.values() if v)} 天")

    print("[3/4] 抓全市場歷史（這段約 5-15 分鐘）...")
    t0 = time.time()
    history = bs.fetch_history(codes)
    print(f"      歷史 {len(history)} 檔，耗時 {time.time()-t0:.0f}s")
    if len(history) < 100:
        print("❌ 歷史不足，退出")
        return

    print("[4/4] 開始回測...")
    t0 = time.time()
    cash, trades = run_backtest(history, mcap, regime)
    print(f"      回測完成，耗時 {time.time()-t0:.0f}s")

    print("\n" + "=" * 60)
    print(f"📊 Hot Money Radar 回測結果（{START_DATE} ~ {END_DATE}）")
    print("=" * 60)
    bs.report(cash, trades, label="Hot Money Radar", run_stress=False)

    # 族群別分析
    print("\n📊 族群別獲利分析（Top 10）：")
    by_ind = defaultdict(list)
    for t in trades:
        by_ind[t.get("industry", "未分類")].append(t)
    ind_stats = []
    for ind, ts in by_ind.items():
        n = len(ts)
        wins = sum(1 for t in ts if t["ret_pct"] > 0)
        avg = sum(t["ret_pct"] for t in ts) / n
        total_ret = sum(t["ret_pct"] for t in ts)
        ind_stats.append({"industry": ind, "n": n, "wins": wins,
                         "avg": avg, "total": total_ret})
    ind_stats.sort(key=lambda x: -x["total"])
    print(f"{'族群':<10} {'筆數':>4} {'勝率':>5} {'平均':>7} {'總和':>8}")
    print("-" * 50)
    for s in ind_stats[:10]:
        print(f"{s['industry']:<10} {s['n']:>4} "
              f"{s['wins']/s['n']*100:>4.0f}% "
              f"{s['avg']:>+6.2f}% {s['total']:>+7.1f}%")

    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades),
           "version": "Hot Money Radar v1.5 (族群持續性)",
           "params": {
               "start": START_DATE, "end": END_DATE,
               "lookback": LOOKBACK_DAYS,
               "rising_threshold": RISING_THRESHOLD,
               "cooling_threshold": COOLING_THRESHOLD,
               "max_pullback_3d": MAX_PULLBACK_3D,
               "use_resilience": USE_RESILIENCE,
               "min_trend_days": MIN_TREND_DAYS,
               "max_industries": MAX_INDUSTRIES,
               "held_industry_bonus": HELD_INDUSTRY_BONUS,
               "max_slots": MAX_SLOTS,
               "min_mcap": MIN_MCAP,
           }}
    with open("backtest_hot_money_v1_5.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_hot_money_v1_5.json")


if __name__ == "__main__":
    main()
