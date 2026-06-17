# -*- coding: utf-8 -*-
"""
回測 v2 (真版)：按用戶實際作業流程
═════════════════════════════════════════════════
用戶流程：
  1. 抓 ATH 池（exact_ath，已有）
  2. 計算每族群「當日上漲家數」(change_pct > 0) → 取 Top 2 族群
  3. 該族群內挑「最強線型」：
     - 多頭排列 (close > MA5 > MA20 > MA60 > MA200)
     - 快速多頭 (close > MA5 > MA10 > MA20)
     - 量增 ≥ 1.5x
     - 收長紅 K 或跳空缺口
     - RSI 55-75（強勢未超買）
     - 收盤靠近當日高
  4. 進場（次日開盤）
  5. 出場（跌破 20MA）

對比基準：
  - V4 五年: +226.8% / CAGR 24.8% / PF 3.40 / 勝率 46%
  - v1 (前次 ATH bug): +126.3% (0.9 年有效) / PF 3.91 / 勝率 52%
"""
import sys, os, json, io, time
import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_strategy as bs
from industry_map_loader import get_industry

# ── 回測期間（3 年）──
START_DATE = "2023-05-22"
END_DATE = "2026-05-22"

# ── 策略參數 ──
TOP_INDUSTRIES = 2          # 取上漲家數 Top N 族群
RSI_MIN = 55                # RSI 下限
RSI_MAX = 75                # RSI 上限（避免超買）

# 量增門檻（依市值分層 — 大型藍籌放寬）
VOL_RATIO_MID = 1.5         # 中型股（100-1000 億）
VOL_RATIO_LARGE = 1.2       # 大型股（1000-5000 億）
VOL_RATIO_MEGA = 1.0        # 超大型股（≥ 5000 億，如台積電/聯發科）
LARGE_CAP_THRESHOLD = 1000  # 億
MEGA_CAP_THRESHOLD = 5000   # 億

def required_vol_ratio(mc):
    if mc >= MEGA_CAP_THRESHOLD:
        return VOL_RATIO_MEGA
    elif mc >= LARGE_CAP_THRESHOLD:
        return VOL_RATIO_LARGE
    else:
        return VOL_RATIO_MID

# ── 投資組合 ──
INITIAL = 1_000_000
MAX_SLOTS = 5
PER_SLOT = INITIAL / MAX_SLOTS
MIN_MCAP = 100              # 100 億以上


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


def pick_v2(candidates, mcap):
    """
    按用戶 3 步驟流程：
    1. ATH 池
    2. 計算族群當日上漲家數 → Top 2
    3. 該族群內挑最強線型
    """
    if not candidates:
        return [], []

    # Step 1: ATH 池
    ath_pool = [c for c in candidates if c.get("is_ath")]
    if not ath_pool:
        return [], []

    # Step 2: 計算「全候選」族群當日上漲家數（不限 ATH）
    # 注意：用全候選算，因為「族群整體上漲家數」反映該族群當日資金面
    industry_rising = defaultdict(int)
    industry_total = defaultdict(int)
    for c in candidates:
        ind = c.get("industry", "未分類")
        industry_total[ind] += 1
        if c.get("change_pct", 0) > 0:
            industry_rising[ind] += 1

    # 過濾雜訊（族群總家數 < 5 視為雜訊）
    valid_inds = [(ind, cnt) for ind, cnt in industry_rising.items()
                  if industry_total[ind] >= 5]
    # Top N 族群（按上漲家數絕對值排序）
    top_inds = sorted(valid_inds, key=lambda x: -x[1])[:TOP_INDUSTRIES]
    top_inds_set = {ind for ind, _ in top_inds}

    # Step 3: 該族群內挑最強線型
    picks = []
    for c in ath_pool:
        ind = c.get("industry")
        if ind not in top_inds_set:
            continue
        # 多頭排列（close > MA20 > MA60 > MA200）
        if not c.get("bullish"):
            continue
        # 快速多頭（close > MA5 > MA10 > MA20）
        if not c.get("bullish_fast"):
            continue
        # 市值
        mc = mcap.get(c["ticker"])
        if mc is None or mc < MIN_MCAP:
            continue
        # 量增（依市值分層）— 大型藍籌放寬
        req_vol = required_vol_ratio(mc)
        if c.get("vol_ratio", 0) < req_vol:
            continue
        # 收長紅 K 或跳空（任一）
        if not (c.get("long_red") or c.get("gap_up")):
            continue
        # RSI 55-75
        rsi = c.get("rsi", 0)
        if rsi < RSI_MIN or rsi > RSI_MAX:
            continue
        # 收盤靠近當日高
        if not c.get("close_near_high"):
            continue

        # 綜合分：量 + RSI + 漲幅 + 大型股加分（鼓勵藍籌）
        size_bonus = 5 if mc >= LARGE_CAP_THRESHOLD else 0
        score = (c.get("vol_ratio", 0) * 10 + rsi +
                 c.get("change_pct", 0) * 2 + size_bonus)
        c["_score"] = score
        picks.append(c)

    picks.sort(key=lambda x: -x["_score"])
    return picks[:MAX_SLOTS], top_inds


def run_backtest(history, mcap, regime):
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates
                 if pd.Timestamp(START_DATE) <= d <= pd.Timestamp(END_DATE)]
    print(f"[v2] 回測 {len(all_dates)} 個交易日")

    cash = INITIAL
    positions = {}
    trades = []
    df_idx = {c: {d: i for i, d in enumerate(df.index)}
              for c, df in history.items()}

    n_days = len(all_dates)
    daily_top_inds = []  # 記錄每日 top 族群
    for di, d in enumerate(all_dates):
        if di < 200:
            continue
        if di % 50 == 0:
            print(f"  [{di}/{n_days}] {d.strftime('%Y-%m-%d')} "
                  f"持倉 {len(positions)} 累計 {len(trades)} 筆")

        d_str = d.strftime("%Y-%m-%d")
        in_stage2 = regime.get(d_str, False)

        candidates = []
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

        picks, top_inds = pick_v2(candidates, mcap)
        if top_inds:
            daily_top_inds.append({"date": d_str, "top": top_inds})

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

    return cash, trades, daily_top_inds


def main():
    print(f"📅 v2 (真版) 回測：{START_DATE} ~ {END_DATE}")
    print(f"📊 策略：用戶實際 3 步驟流程")
    print(f"   1. ATH 池")
    print(f"   2. 族群當日上漲家數 Top {TOP_INDUSTRIES}")
    print(f"   3. 最強線型 (多頭+快速多頭+量{VOL_RATIO_MID}/{VOL_RATIO_LARGE}/{VOL_RATIO_MEGA}x (中/大/超大)+長紅/跳空+RSI{RSI_MIN}-{RSI_MAX}+收高)")
    print(f"   出場: 跌破 20MA / 從峰值 -30%")
    print(f"   同時 {MAX_SLOTS} 檔, 市值 ≥ {MIN_MCAP} 億")
    print()

    bs.END_DATE = END_DATE
    codes = bs.load_universe()
    mcap = load_mcap()
    print(f"[1/4] universe {len(codes)} 檔, 市值 {len(mcap)} 檔")
    print(f"      bs.START_DATE = {bs.START_DATE} (5y for ATH)")

    print("[2/4] 抓 0050 體制...")
    regime = fetch_0050_regime()

    print("[3/4] 抓歷史...")
    t0 = time.time()
    history = bs.fetch_history(codes)
    print(f"      歷史 {len(history)} 檔, 耗時 {time.time()-t0:.0f}s")
    if len(history) < 100:
        return

    print("[4/4] 開始回測...")
    t0 = time.time()
    cash, trades, daily_top_inds = run_backtest(history, mcap, regime)
    print(f"      耗時 {time.time()-t0:.0f}s")

    n = len(trades)
    total_ret = (cash / INITIAL - 1) * 100
    from datetime import datetime
    s = datetime.strptime(START_DATE, "%Y-%m-%d")
    e = datetime.strptime(END_DATE, "%Y-%m-%d")
    years = (e - s).days / 365.25
    cagr = ((cash / INITIAL) ** (1 / years) - 1) * 100 if years > 0 else 0

    print("\n" + "=" * 60)
    print(f"📊 v2 (真版) 回測結果")
    print("=" * 60)
    if n > 0:
        wins = sum(1 for t in trades if t["ret_pct"] > 0)
        gains = sum(t["ret_pct"] for t in trades if t["ret_pct"] > 0)
        loss = abs(sum(t["ret_pct"] for t in trades if t["ret_pct"] < 0))
        pf = gains / loss if loss > 0 else 999
        avg = sum(t["ret_pct"] for t in trades) / n
        avg_w = gains / wins if wins else 0
        avg_l = -loss / (n - wins) if (n - wins) else 0
        hd = [t.get("hold_days", 0) for t in trades]

        # 實際交易期
        first = min(t["entry_date"] for t in trades)
        last = max(t["exit_date"] for t in trades)
        active_yrs = (datetime.strptime(last[:10], "%Y-%m-%d") -
                      datetime.strptime(first[:10], "%Y-%m-%d")).days / 365.25
        real_cagr = ((cash / INITIAL) ** (1 / active_yrs) - 1) * 100 if active_yrs > 0 else 0

        print(f"總報酬 {total_ret:+.1f}%  CAGR {cagr:+.1f}%")
        print(f"實際交易期 {first[:10]} ~ {last[:10]} ({active_yrs:.1f}年)")
        print(f"CAGR(實際) {real_cagr:+.1f}%")
        print(f"交易 {n} 筆, 勝率 {wins/n*100:.0f}%, PF {pf:.2f}")
        print(f"平均賺 {avg_w:+.2f}%, 平均賠 {avg_l:+.2f}%, 盈虧比 {abs(avg_w/avg_l):.2f}")
        print(f"平均持倉 {sum(hd)/len(hd):.0f} 天")

        # 族群統計
        by_ind = defaultdict(list)
        for t in trades:
            by_ind[t.get("industry", "?")].append(t)
        ind_stats = sorted(
            [(ind, len(lst), sum(1 for x in lst if x["ret_pct"]>0),
              sum(x["ret_pct"] for x in lst)) for ind, lst in by_ind.items()],
            key=lambda x: -x[3])
        print("\n族群獲利 Top 8:")
        for ind, nn, w, total in ind_stats[:8]:
            print(f"  {ind:<10} {nn:>3}筆  勝{w}/{nn}({w/nn*100:.0f}%)  總 {total:+.1f}%")

        # 月份族群分散度
        by_month = defaultdict(lambda: defaultdict(int))
        for t in trades:
            by_month[t["entry_date"][:7]][t.get("industry","?")] += 1
        print("\n📅 每月族群分散度:")
        for m, inds in sorted(by_month.items()):
            items = sorted(inds.items(), key=lambda x: -x[1])
            summary = ", ".join(f"{i}:{c}" for i, c in items)
            print(f"  {m} ({sum(inds.values())}筆,{len(inds)}族群): {summary}")
    else:
        print("⚠️ 無交易 — 過濾可能過嚴")

    # 對照
    print("\n" + "=" * 60)
    print("📊 對照表")
    print("=" * 60)
    print(f"  V4 (5年): +226.8% / 24.8% CAGR / PF 3.40 / 勝率 46%")
    print(f"  v1 (前次ATH bug,實0.9年): +126.3% / 142.9% CAGR實 / PF 3.91 / 勝率 52%")
    print(f"  v1.5 (族群持續性,實2.2年): +90.2% / 34.1% CAGR實 / PF 1.59 / 勝率 43%")
    if n > 0:
        print(f"  v2 (真版,實{active_yrs:.1f}年): {total_ret:+.1f}% / {real_cagr:+.1f}% CAGR實 / PF {pf:.2f} / 勝率 {wins/n*100:.0f}%")

    out = {"final_cash": cash, "trades": trades, "n_trades": n,
           "version": "v2 真版 (用戶 3 步驟流程)",
           "params": {
               "start": START_DATE, "end": END_DATE,
               "top_industries": TOP_INDUSTRIES,
               "vol_ratio_mid": VOL_RATIO_MID, "vol_ratio_large": VOL_RATIO_LARGE, "vol_ratio_mega": VOL_RATIO_MEGA,
               "rsi_min": RSI_MIN, "rsi_max": RSI_MAX,
               "max_slots": MAX_SLOTS, "min_mcap": MIN_MCAP,
           },
           "daily_top_industries": daily_top_inds[-30:],  # 最後 30 天
           }
    with open("backtest_hot_money_v2.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_hot_money_v2.json")

    # 🛡️ 黑天鵝壓力測試（每次回測必跑）
    if n > 0:
        try:
            from stress_test_lib import run_stress_test
            run_stress_test(trades, label="V2 真版")
        except Exception as e:
            print(f"⚠️ 壓力測試失敗: {e}")


if __name__ == "__main__":
    main()
