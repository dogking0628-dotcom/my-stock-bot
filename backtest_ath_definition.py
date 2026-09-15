# -*- coding: utf-8 -*-
"""
ATH 定義落差回測：回測用的「2y 日線新高」 vs 正式系統用的「2y 月線(月底收盤)新高」
═══════════════════════════════════════════════════════════════
起因（2026-09-10）：74 筆真實推播訊號中 44 筆（59%）不符合回測的 is_ath 定義
  backtest_strategy.py:123  is_ath = close >= max(近504日 日收盤) * 0.999
  industry_ath_yf.py:294    monthly_max_close = close > max(前幾個月的月底收盤)   ← 正式系統
→ +250.6% 驗證的是嚴定義；正式系統跑的是寬定義，從未被回測。

變體（其餘全同 V4.5：V4.4 減速器 + 警示濾除 + 開盤價成交）：
  daily     = 回測原定義（基準，應重現 5y +250.6% / 2y +167.8%）
  monthly   = 正式系統定義（前 24 個月的月底收盤最高；當月不算）
  within5   = 中間值：收盤 ≥ 2y 日線最高 × 0.95（距高點 5% 內即算）
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import backtest_strategy as bs
import backtest_v4_1 as v41
import backtest_v45_candidate as v45
import backtest_entry_timing as et

_ORIG_FEATURES = bs.daily_features
PMM = {}            # id(df) -> np.array 每根 bar 對應「前 24 個月月底收盤最高」
MODE = {"v": "daily"}


def build_prior_month_max(df, months=24):
    cl = df["Close"]
    ym = np.array([t.year * 12 + t.month for t in cl.index])
    # 每個月的月底收盤（該月最後一根）
    last_close_by_ym = {}
    for y, c in zip(ym, cl.values):
        last_close_by_ym[int(y)] = float(c)          # 覆寫 → 留最後一根
    yms_sorted = sorted(last_close_by_ym)
    out = np.full(len(cl), np.nan)
    cache = {}
    for i, y in enumerate(ym):
        y = int(y)
        if y not in cache:
            prior = [last_close_by_ym[k] for k in yms_sorted if (y - months) <= k < y]
            cache[y] = max(prior) if prior else np.nan
        out[i] = cache[y]
    return out


def patched_features(df, idx):
    f = _ORIG_FEATURES(df, idx)
    if not f:
        return f
    mode = MODE["v"]
    if mode == "daily":
        return f
    cl = df["Close"].values
    c = float(cl[idx])
    if mode == "monthly":
        arr = PMM.get(id(df))
        pm = arr[idx] if arr is not None else np.nan
        f["is_ath"] = bool(np.isfinite(pm) and c > pm)
    elif mode == "within5":
        if idx >= 504:
            f["is_ath"] = c >= float(cl[idx - 504:idx].max()) * 0.95
        else:
            f["is_ath"] = False
    return f


def main():
    bs.START_DATE = "2020-08-01"
    bs.END_DATE = dt.date.today().isoformat()
    print(f"[bt] END_DATE = {bs.END_DATE}")
    codes = bs.load_universe()
    mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors()
    regime = v41.fetch_0050()
    below20 = v45.fetch_0050_series()
    history = bs.fetch_history(codes)
    print(f"[bt] 取得 {len(history)} 檔歷史")
    if len(history) < 100:
        print("資料不足")
        return
    for c, df in history.items():
        PMM[id(df)] = build_prior_month_max(df)
    print("[bt] 月底收盤最高 預計算完成")

    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    cums = v45.precompute_cums(history)
    bs.daily_features = patched_features

    today = dt.date.today()
    WINDOWS = [("5y", (today - dt.timedelta(days=365 * 5)).isoformat(), 5.0),
               ("2y", (today - dt.timedelta(days=365 * 2)).isoformat(), 2.0)]
    results = {}
    print("\n" + "=" * 74)
    print("ATH 定義 × 視窗（進場=開盤價，其餘 = 現行 V4.5）")
    print("=" * 74)
    for mode in ["daily", "monthly", "within5"]:
        MODE["v"] = mode
        for wname, wstart, wyears in WINDOWS:
            cash, tr, nsig, nfil = et.run_variant(
                f"{mode}_{wname}", "OPEN", "none", wstart, history, mcap, us_chg,
                regime, below20, cums, df_idx, all_dates)
            st = et.stats(cash, tr, wyears)
            st["signals"] = nsig
            st["filled"] = nfil
            results[f"{mode}_{wname}"] = st
            print(f"  {mode:<9} {wname}: 報酬{st['total_pct']:>8.1f}% CAGR{st['cagr']:>6.1f}% "
                  f"筆{st['trades']:>4} 勝{st['win_rate']:>5.1f}% 期望{st['expectancy']:>6.2f}% "
                  f"PF{st['pf']:>5.2f} MDD{st['mdd']:>6.1f}%  訊號{nsig}")
            # 保留交易明細供後續出場時點研究
            results[f"{mode}_{wname}_trades"] = tr
    json.dump(results, io.open("backtest_ath_definition.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("\n💾 backtest_ath_definition.json")


if __name__ == "__main__":
    main()
