# -*- coding: utf-8 -*-
"""
正式系統（月線 ATH）定義下的進場成交方式：OPEN / CLOSE / LIMIT_OPEN × 5y/2y
═══════════════════════════════════════════════════════════════
backtest_entry_timing.py 的 CLOSE 優勢是在回測原定義（2y 日線新高）下得到的；
backtest_ath_definition.py 證實正式系統跑的是月線定義（5y +288.0%）。
候選 A「隔日收盤買」必須在正式定義下重驗才算數。
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import backtest_strategy as bs
import backtest_v4_1 as v41
import backtest_v45_candidate as v45
import backtest_entry_timing as et
import backtest_ath_definition as ad


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
        print("資料不足"); return
    for c, df in history.items():
        ad.PMM[id(df)] = ad.build_prior_month_max(df)
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    cums = v45.precompute_cums(history)
    bs.daily_features = ad.patched_features
    ad.MODE["v"] = "monthly"

    today = dt.date.today()
    WINDOWS = [("5y", (today - dt.timedelta(days=365 * 5)).isoformat(), 5.0),
               ("2y", (today - dt.timedelta(days=365 * 2)).isoformat(), 2.0)]
    results = {}
    print("\n" + "=" * 74)
    print("正式系統（月線 ATH）× 進場成交方式")
    print("=" * 74)
    for mode in ["OPEN", "CLOSE", "LIMIT_OPEN"]:
        for wname, wstart, wyears in WINDOWS:
            cash, tr, nsig, nfil = et.run_variant(
                f"m_{mode}_{wname}", mode, "none", wstart, history, mcap, us_chg,
                regime, below20, cums, df_idx, all_dates)
            st = et.stats(cash, tr, wyears)
            st["signals"] = nsig; st["filled"] = nfil
            st["fill_rate"] = round(nfil / nsig * 100, 1) if nsig else 0
            results[f"{mode}_{wname}"] = st
            results[f"{mode}_{wname}_trades"] = tr
            print(f"  {mode:<11} {wname}: 報酬{st['total_pct']:>8.1f}% CAGR{st['cagr']:>6.1f}% "
                  f"筆{st['trades']:>4} 勝{st['win_rate']:>5.1f}% 期望{st['expectancy']:>6.2f}% "
                  f"PF{st['pf']:>5.2f} MDD{st['mdd']:>6.1f}% 成交率{st['fill_rate']:>5.1f}%")
    json.dump(results, io.open("backtest_monthly_entry.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("\n💾 backtest_monthly_entry.json")


if __name__ == "__main__":
    main()
