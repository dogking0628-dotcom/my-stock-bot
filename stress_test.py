# -*- coding: utf-8 -*-
"""
極端黑天鵝壓力測試（V4 vs 大盤）
─────────────────────────────────
測試期間（5 年內真實事件）：
  1. 2022 Q1-Q3 聯準會升息熊市（NDX -30%）
  2. 2022 Oct 絕對低點（最大恐慌）
  3. 2023 Mar SVB 矽谷銀行倒閉
  4. 2024 Aug 日圓 Carry Trade 解倉（黑色星期一）
  5. 2025 Q1 川普關稅恐慌（如有）

評估：
  - 該期間策略總報酬 vs 0050 / SPY
  - 該期間策略最大連續虧損
  - 持倉多空比 / 跳過天數比
  - 「最壞 60 天」滾動最大跌幅
"""
import sys, os, json, datetime as dt, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pandas as pd
import yfinance as yf
import numpy as np
from collections import defaultdict

# 黑天鵝事件清單（區間）
BLACK_SWAN_PERIODS = [
    ("2022-01-01", "2022-09-30", "🔴 2022 升息熊市"),
    ("2022-10-01", "2022-10-31", "💥 2022 Q4 絕對低點月"),
    ("2023-03-01", "2023-03-31", "🏦 2023 Mar SVB 危機"),
    ("2024-07-15", "2024-08-15", "💴 2024 Aug 日圓拆倉"),
    ("2025-04-01", "2025-04-30", "💸 2025 Apr 關稅恐慌"),
]


def load_v4():
    with open("backtest_v4.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_index(ticker, start, end):
    try:
        df = yf.download(ticker, start=start, end=end,
                         auto_adjust=True, progress=False, threads=False,
                         group_by="column")
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df["Close"].dropna()
    except Exception as e:
        print(f"  {ticker} fail: {e}")
        return None


def trades_in_period(trades, start, end):
    """找在指定期間「進場或出場」的交易"""
    return [t for t in trades
            if (start <= t["entry_date"] <= end)
            or (start <= t["exit_date"] <= end)
            or (t["entry_date"] < start and t["exit_date"] > end)]


def equity_curve(trades, initial=1_000_000, max_pos=5):
    """模擬權益曲線（粗略）"""
    if not trades: return pd.Series([initial])
    per_pos = initial / max_pos
    # 按入場日排序，按 ret_pct 加總
    trades = sorted(trades, key=lambda x: x["entry_date"])
    equity = [(pd.Timestamp(trades[0]["entry_date"]), initial)]
    cash = initial
    for t in trades:
        # 用平倉日記錄損益
        gain = per_pos * (t["ret_pct"] / 100)
        cash += gain
        equity.append((pd.Timestamp(t["exit_date"]), cash))
    s = pd.Series([e[1] for e in equity], index=[e[0] for e in equity])
    return s


def max_drawdown(series):
    """最大回撤（從峰值跌幅）"""
    peak = series.cummax()
    dd = (series - peak) / peak * 100
    return float(dd.min())


def main():
    print("📊 V4 壓力測試 — 5 年內所有黑天鵝事件\n")
    bt = load_v4()
    all_trades = bt["trades"]
    print(f"V4 全期：{len(all_trades)} 筆交易，總報酬 +{(bt['final_cash']/1_000_000-1)*100:.1f}%\n")

    # 抓 0050 + SPY 全期
    full_0050 = fetch_index("0050.TW", "2021-01-01", "2026-05-06")
    full_spy = fetch_index("SPY", "2021-01-01", "2026-05-06")
    print(f"0050 全期報酬: {(full_0050.iloc[-1]/full_0050.iloc[0]-1)*100:+.1f}%")
    print(f"SPY 全期報酬:  {(full_spy.iloc[-1]/full_spy.iloc[0]-1)*100:+.1f}%")

    # 全期最大回撤
    eq = equity_curve(all_trades)
    print(f"\nV4 全期最大回撤: {max_drawdown(eq):.1f}%")
    print(f"0050 全期最大回撤: {max_drawdown(full_0050):.1f}%")
    print(f"SPY 全期最大回撤: {max_drawdown(full_spy):.1f}%")

    # 各黑天鵝區間
    print("\n" + "=" * 70)
    print("💀 各黑天鵝期間表現")
    print("=" * 70)

    for start, end, name in BLACK_SWAN_PERIODS:
        print(f"\n{name}（{start} ~ {end}）")
        # 0050
        sub_0050 = full_0050[(full_0050.index >= start) & (full_0050.index <= end)]
        sub_spy = full_spy[(full_spy.index >= start) & (full_spy.index <= end)]
        if len(sub_0050) >= 2:
            r_0050 = (sub_0050.iloc[-1]/sub_0050.iloc[0] - 1) * 100
            dd_0050 = max_drawdown(sub_0050)
            print(f"  0050: 期間報酬 {r_0050:+.1f}%, 最大回撤 {dd_0050:.1f}%")
        if len(sub_spy) >= 2:
            r_spy = (sub_spy.iloc[-1]/sub_spy.iloc[0] - 1) * 100
            dd_spy = max_drawdown(sub_spy)
            print(f"  SPY:  期間報酬 {r_spy:+.1f}%, 最大回撤 {dd_spy:.1f}%")

        # V4 該期間
        active = trades_in_period(all_trades, start, end)
        if not active:
            print(f"  V4 ⭐: 期間無交易（系統判定空手 = 正確規避）")
            continue
        n = len(active)
        wins = sum(1 for t in active if t["ret_pct"] > 0)
        avg = sum(t["ret_pct"] for t in active) / n
        worst = min(active, key=lambda x: x["ret_pct"])
        # 該期間累積報酬（簡化）
        cum = 1.0
        for t in active:
            cum *= (1 + t["ret_pct"]/100 * 0.2)  # 每檔 20% 倉位
        cum_pct = (cum - 1) * 100
        print(f"  V4 ⭐: 期間 {n} 筆，{wins}勝 ({wins/n*100:.0f}%)，"
              f"平均 {avg:+.1f}%，估累積 {cum_pct:+.1f}%")
        print(f"      最差單筆：{worst['ticker']} {worst['ret_pct']:+.1f}%")

    # 滾動 60 日最大跌幅
    print("\n" + "=" * 70)
    print("📉 滾動 60 日最大跌幅（找最痛的兩個月）")
    print("=" * 70)
    eq_daily = eq.resample("D").ffill()
    rolling_dd = []
    for i in range(60, len(eq_daily)):
        window = eq_daily.iloc[i-60:i+1]
        dd = (window.iloc[-1]/window.max() - 1) * 100
        rolling_dd.append((window.index[-1], dd))
    rolling_dd.sort(key=lambda x: x[1])
    print("\nV4 最痛苦的 5 個 60 日窗口：")
    for ts, dd in rolling_dd[:5]:
        print(f"  {ts.strftime('%Y-%m-%d')}: {dd:+.1f}%")

    # 連續虧損
    print("\n" + "=" * 70)
    print("🔥 連續虧損測試")
    print("=" * 70)
    sorted_t = sorted(all_trades, key=lambda x: x["entry_date"])
    max_consec_loss = 0; cur_consec = 0
    for t in sorted_t:
        if t["ret_pct"] <= 0:
            cur_consec += 1
            max_consec_loss = max(max_consec_loss, cur_consec)
        else:
            cur_consec = 0
    print(f"  最長連續虧損次數: {max_consec_loss} 筆")
    # 找最痛序列
    losses = [t for t in sorted_t if t["ret_pct"] < -10]
    print(f"  虧損 >10% 的交易: {len(losses)} 筆（佔 {len(losses)/len(sorted_t)*100:.0f}%）")
    for t in losses[:5]:
        print(f"    {t['ticker']} {t['entry_date']}~{t['exit_date']} {t['ret_pct']:+.1f}%")

    out = {
        "v4_total_return_5y": (bt['final_cash']/1_000_000-1)*100,
        "v4_max_drawdown": max_drawdown(eq),
        "0050_max_drawdown": max_drawdown(full_0050),
        "spy_max_drawdown": max_drawdown(full_spy),
        "max_consecutive_losses": max_consec_loss,
        "large_losses_count": len(losses),
    }
    with open("stress_test_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n💾 已輸出 stress_test_result.json")


if __name__ == "__main__":
    main()
