# -*- coding: utf-8 -*-
"""
回測 v2：Hot Money Radar + 漲停續航過濾（2 年回測）
═════════════════════════════════════════════════
v1 (hot_money_radar v1):
  - 升溫族群 +10%
  - 韌性過濾（3 日拉回 ≥ -10%）

v2 新增（用戶觀察）：
  - 過去 25 天有 ≥ 2 次漲停
  - 漲停後隔日續強率 ≥ 70%
  - 排除「沒漲停 = 不是熱錢主流」的個股
  - 排除「漲停常被修正 = 假熱錢」的個股

對照組：
  - V4 五年: +226.8% / CAGR 24.8% / PF 3.40 / 勝率 46%
  - v1 hot_money 3 年: +126.3% / CAGR 30.6% / PF 3.91 / 勝率 52%
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

# ── 回測期間（2 年）──
START_DATE = "2024-05-22"
END_DATE = "2026-05-22"

# ── 策略參數 ──
LOOKBACK_DAYS = 5
RISING_THRESHOLD = 10
COOLING_THRESHOLD = -5
MIN_BASE = 3

# ── 韌性過濾 ──
MAX_PULLBACK_3D = -10.0
USE_RESILIENCE = True

# ── 續航過濾（v2 新增）──
USE_CONTINUATION = True
CONT_LOOKBACK = 25                # 看過去 25 天漲停
CONT_MIN_LIMIT_UPS = 2            # 至少 2 次漲停
CONT_MIN_SCORE = 70.0             # 續強率 ≥ 70%
LIMIT_UP_THRESHOLD = 9.0          # 漲停判定 (≥ 9% 算)
NEXT_DAY_OK_THRESHOLD = -5.0      # 隔日 ≥ -5% 算續強

# ── 投資組合 ──
INITIAL = 1_000_000
MAX_SLOTS = 5
PER_SLOT = INITIAL / MAX_SLOTS
MIN_MCAP = 100


def load_mcap():
    if not os.path.exists("marketcap_cache.json"):
        return {}
    with open("marketcap_cache.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_0050_regime():
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


def compute_continuation_score(df, idx, lookback=CONT_LOOKBACK):
    """
    過去 lookback 天的「漲停 → 隔日表現」續強率
    回傳：(score, n_limit_ups)
    """
    start = max(0, idx - lookback)
    closes = df["Close"].iloc[start:idx+1].values
    if len(closes) < 5:
        return None, 0
    changes = np.diff(closes) / closes[:-1] * 100
    # 找漲停日（in changes 內的 index）
    limit_ups = [i for i, c in enumerate(changes) if c >= LIMIT_UP_THRESHOLD]
    if len(limit_ups) < 1:
        return None, 0
    events = []
    for lu in limit_ups:
        # changes[lu+1] = 隔日漲跌
        if lu + 1 >= len(changes):
            continue
        events.append(changes[lu + 1])
    if not events:
        return None, len(limit_ups)
    positive = sum(1 for e in events if e >= NEXT_DAY_OK_THRESHOLD)
    return positive / len(events) * 100, len(events)


def compute_industry_momentum(heat_history, today_stats):
    if len(heat_history) < LOOKBACK_DAYS:
        return {ind: {"momentum_pct": None, "status": "new", "today": cnt}
                for ind, cnt in today_stats.items()}
    past = list(heat_history)[-LOOKBACK_DAYS:]
    result = {}
    for ind, today_cnt in today_stats.items():
        past_cnts = [p.get(ind, 0) for p in past]
        avg_n = sum(past_cnts) / len(past_cnts)
        if avg_n < MIN_BASE and today_cnt < MIN_BASE:
            result[ind] = {"momentum_pct": None, "status": "noise",
                          "today": today_cnt, "avg_n": avg_n}
            continue
        mom = (today_cnt / max(avg_n, 1) - 1) * 100
        if mom >= RISING_THRESHOLD:
            status = "rising"
        elif mom <= COOLING_THRESHOLD:
            status = "cooling"
        else:
            status = "steady"
        result[ind] = {"momentum_pct": mom, "status": status,
                       "today": today_cnt, "avg_n": avg_n}
    return result


def pick_v2(candidates, momentum, mcap, history, df_idx, d):
    rising_inds = {ind for ind, m in momentum.items() if m["status"] == "rising"}
    if not rising_inds:
        return []

    pool = []
    for c in candidates:
        ind = c.get("industry")
        if ind not in rising_inds:
            continue
        if not c.get("is_ath"):
            continue
        if not c.get("bullish"):
            continue
        mc = mcap.get(c["ticker"])
        if mc is None or mc < MIN_MCAP:
            continue

        df = history.get(c["ticker"])
        if df is None:
            continue
        i = df_idx[c["ticker"]].get(d)
        if i is None or i < 25:
            continue

        # 韌性
        if USE_RESILIENCE:
            closes_recent = df["Close"].iloc[max(0, i-9):i+1].values
            p3 = max_dd_n(closes_recent, 3)
            if p3 < MAX_PULLBACK_3D:
                continue
            c["_pullback_3d"] = p3

        # 續航
        if USE_CONTINUATION:
            cont_score, n_lu = compute_continuation_score(df, i)
            if n_lu < CONT_MIN_LIMIT_UPS:
                continue
            if cont_score is None or cont_score < CONT_MIN_SCORE:
                continue
            c["_cont_score"] = cont_score
            c["_n_limit_ups"] = n_lu

        mom_pct = momentum[ind].get("momentum_pct", 0)
        ratio_score = (c.get("close", 0) / max(c.get("ma20", 1), 0.01) - 1) * 100
        # v2 綜合分加上續航加權
        cont_bonus = c.get("_cont_score", 0) * 0.2 if USE_CONTINUATION else 0
        lu_bonus = c.get("_n_limit_ups", 0) * 2 if USE_CONTINUATION else 0
        score = ratio_score + mom_pct * 0.5 + cont_bonus + lu_bonus
        c["_rotation_score"] = score
        pool.append(c)

    pool.sort(key=lambda x: -x["_rotation_score"])
    return pool[:MAX_SLOTS]


def run_backtest(history, mcap, regime, name="v2"):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates
                 if pd.Timestamp(START_DATE) <= d <= pd.Timestamp(END_DATE)]
    print(f"[{name}] 回測 {len(all_dates)} 個交易日")

    cash = INITIAL
    positions = {}
    trades = []
    df_idx = {c: {d: i for i, d in enumerate(df.index)}
              for c, df in history.items()}
    heat_history = deque(maxlen=LOOKBACK_DAYS + 5)

    n_days = len(all_dates)
    for di, d in enumerate(all_dates):
        if di < 200:
            continue
        if di % 50 == 0:
            print(f"  [{di}/{n_days}] {d.strftime('%Y-%m-%d')} "
                  f"持倉 {len(positions)} 累計 {len(trades)} 筆")

        d_str = d.strftime("%Y-%m-%d")
        in_stage2 = regime.get(d_str, False)

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

        momentum = compute_industry_momentum(heat_history, dict(today_stats))
        heat_history.append(dict(today_stats))

        # 出場
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
                sell_p = float(history[c]["Open"].iloc[ni])
                cash += pos["shares"] * sell_p * (1 - bs.COMMISSION - bs.TAX)
                trades.append({
                    "ticker": c, "industry": get_industry(c) or "未分類",
                    "entry_date": pos["entry_date"], "exit_date": str(next_d.date()),
                    "entry": pos["entry_price"], "exit": sell_p,
                    "ret_pct": (sell_p / pos["entry_price"] - 1) * 100,
                    "reason": "跌破20MA" if cf["close"] < cf["ma20"] else "從峰值-30%",
                    "hold_days": (next_d - pd.Timestamp(pos["entry_date"])).days,
                })
                del positions[c]

        if not in_stage2:
            continue
        if len(positions) >= MAX_SLOTS:
            continue

        picks = pick_v2(candidates, momentum, mcap, history, df_idx, d)
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

    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None:
            continue
        last_p = float(history[c]["Close"].iloc[i])
        cash += pos["shares"] * last_p * (1 - bs.COMMISSION - bs.TAX)

    return cash, trades


def main():
    print(f"📅 v2 回測期間：{START_DATE} ~ {END_DATE}")
    print(f"📊 策略：Hot Money Radar v2")
    print(f"   - 升溫族群 +{RISING_THRESHOLD}%")
    print(f"   - 韌性過濾：3日拉回 ≥ {MAX_PULLBACK_3D}%")
    print(f"   - 續航過濾：≥ {CONT_MIN_LIMIT_UPS} 次漲停 + 續強率 ≥ {CONT_MIN_SCORE}%")
    print(f"   - 同時 {MAX_SLOTS} 檔")
    print()

    bs.START_DATE = START_DATE
    bs.END_DATE = END_DATE
    codes = bs.load_universe()
    mcap = load_mcap()
    print(f"[1/4] universe {len(codes)} 檔，市值 {len(mcap)} 檔")

    print("[2/4] 抓 0050 體制...")
    regime = fetch_0050_regime()

    print("[3/4] 抓歷史（5-15 分鐘）...")
    t0 = time.time()
    history = bs.fetch_history(codes)
    print(f"      歷史 {len(history)} 檔，耗時 {time.time()-t0:.0f}s")
    if len(history) < 100:
        return

    print("[4/4] 開始回測...")
    t0 = time.time()
    cash, trades = run_backtest(history, mcap, regime)
    print(f"      耗時 {time.time()-t0:.0f}s")

    print("\n" + "=" * 60)
    print(f"📊 Hot Money Radar v2 回測（{START_DATE} ~ {END_DATE}）")
    print("=" * 60)
    bs.report(cash, trades, label="v2 +續航", run_stress=False)

    # 對比 V4 五年 / v1 三年
    n = len(trades)
    wins = sum(1 for t in trades if t["ret_pct"] > 0)
    total_ret = (cash / INITIAL - 1) * 100
    from datetime import datetime
    s = datetime.strptime(START_DATE, "%Y-%m-%d")
    e = datetime.strptime(END_DATE, "%Y-%m-%d")
    years = (e - s).days / 365.25
    cagr = ((cash / INITIAL) ** (1 / years) - 1) * 100 if years > 0 else 0
    gains = sum(t["ret_pct"] for t in trades if t["ret_pct"] > 0)
    loss = abs(sum(t["ret_pct"] for t in trades if t["ret_pct"] < 0))
    pf = gains / loss if loss > 0 else 999

    print(f"\n📊 v2 績效：")
    print(f"  總報酬 {total_ret:+.1f}%  CAGR {cagr:+.1f}%")
    print(f"  PF {pf:.2f}  勝率 {wins/n*100:.0f}%  ({wins}/{n})")
    print(f"\n📊 對照組（5/3/2 年）：")
    print(f"  V4 (5年): +226.8% / 24.8% / PF 3.40 / 勝率 46%")
    print(f"  v1 hot_money (3年): +126.3% / 30.6% / PF 3.91 / 勝率 52%")
    print(f"  v2 +續航 (2年): {total_ret:+.1f}% / {cagr:+.1f}% / PF {pf:.2f} / 勝率 {wins/n*100:.0f}%")

    by_ind = defaultdict(list)
    for t in trades:
        by_ind[t.get("industry", "?")].append(t)
    print("\n📊 族群獲利 (Top 8)：")
    ind_stats = []
    for ind, lst in by_ind.items():
        nn = len(lst)
        w = sum(1 for t in lst if t["ret_pct"] > 0)
        total = sum(t["ret_pct"] for t in lst)
        ind_stats.append((ind, nn, w, total))
    ind_stats.sort(key=lambda x: -x[3])
    for ind, nn, w, total in ind_stats[:8]:
        print(f"  {ind:<10} {nn:>3} 筆  勝{w}/{nn}({w/nn*100:.0f}%)  總和 {total:+.1f}%")

    out = {"final_cash": cash, "trades": trades, "n_trades": n,
           "version": "Hot Money Radar v2 (續航過濾)",
           "params": {
               "start": START_DATE, "end": END_DATE,
               "use_resilience": USE_RESILIENCE,
               "use_continuation": USE_CONTINUATION,
               "cont_min_limit_ups": CONT_MIN_LIMIT_UPS,
               "cont_min_score": CONT_MIN_SCORE,
               "max_slots": MAX_SLOTS,
               "min_mcap": MIN_MCAP,
           }}
    with open("backtest_hot_money_v2.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_hot_money_v2.json")


if __name__ == "__main__":
    main()
