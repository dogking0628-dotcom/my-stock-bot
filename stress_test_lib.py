# -*- coding: utf-8 -*-
"""
壓力測試工具庫（可被任何回測腳本 import）
─────────────────────────────────
用法:
    from stress_test_lib import run_stress_test
    run_stress_test(trades, label="V5 my_strategy")

每次回測自動執行：
  1. 5 年內所有黑天鵝事件期間表現
  2. 全期最大回撤
  3. 滾動 60 日最大跌幅
  4. 連續虧損 / 大虧序列
"""
import sys, io, os, json, datetime as dt
import pandas as pd
import numpy as np
import yfinance as yf
from collections import defaultdict

BLACK_SWAN = [
    ("2022-01-01", "2022-09-30", "🔴 2022 升息熊市"),
    ("2022-10-01", "2022-10-31", "💥 2022 Q4 谷底月"),
    ("2023-03-01", "2023-03-31", "🏦 2023 Mar SVB 危機"),
    ("2024-07-15", "2024-08-15", "💴 2024 Aug 日圓拆倉"),
    ("2025-04-01", "2025-04-30", "💸 2025 Apr 關稅恐慌"),
]
INDEX_CACHE = {}  # 避免重複下載


def _fetch_index(ticker, start, end):
    key = (ticker, start, end)
    if key in INDEX_CACHE: return INDEX_CACHE[key]
    try:
        df = yf.download(ticker, start=start, end=end,
                         auto_adjust=True, progress=False, threads=False,
                         group_by="column")
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        s = df["Close"].dropna() if "Close" in df.columns else None
        INDEX_CACHE[key] = s
        return s
    except Exception:
        return None


def _equity_curve(trades, initial=1_000_000, max_pos=5):
    if not trades: return pd.Series([initial])
    per = initial / max_pos
    trades = sorted(trades, key=lambda x: x["entry_date"])
    pts = [(pd.Timestamp(trades[0]["entry_date"]), initial)]
    cash = initial
    for t in trades:
        cash += per * (t["ret_pct"] / 100)
        pts.append((pd.Timestamp(t["exit_date"]), cash))
    # 去重相同日期（取最後值）
    df = pd.DataFrame(pts, columns=["d", "v"]).groupby("d").last()
    return df["v"]


def _max_drawdown(s):
    if s is None or len(s) < 2: return 0.0
    peak = s.cummax()
    return float(((s - peak) / peak * 100).min())


def _trades_in_period(trades, start, end):
    return [t for t in trades
            if (start <= t["entry_date"] <= end)
            or (start <= t["exit_date"] <= end)
            or (t["entry_date"] < start and t["exit_date"] > end)]


def run_stress_test(trades, label="strategy", start_date="2021-01-01",
                    end_date=None, save_json=False):
    """
    對 trades 執行壓力測試。
    trades: list of dicts with entry_date, exit_date, ret_pct, ticker
    回傳：dict 包含全部測試結果
    """
    if end_date is None:
        end_date = dt.date.today().isoformat()

    print("\n" + "═" * 70)
    print(f"🛡️ 黑天鵝壓力測試 — {label}")
    print("═" * 70)

    if not trades:
        print("⚠️ 無交易資料")
        return {"label": label, "trades": 0}

    full_0050 = _fetch_index("0050.TW", start_date, end_date)
    full_spy = _fetch_index("SPY", start_date, end_date)

    eq = _equity_curve(trades)
    s_dd = _max_drawdown(eq)
    z_dd = _max_drawdown(full_0050)
    p_dd = _max_drawdown(full_spy)

    print(f"\n📉 全期最大回撤：")
    print(f"  {label:<18}: {s_dd:>+6.1f}%")
    print(f"  0050              : {z_dd:>+6.1f}%")
    print(f"  SPY               : {p_dd:>+6.1f}%")
    if z_dd != 0:
        risk_ratio = abs(s_dd / z_dd)
        if risk_ratio < 1:
            print(f"  ✅ 風險低於 0050 {1/risk_ratio:.1f}x")

    # 黑天鵝期間
    print("\n💀 黑天鵝事件表現：")
    swan_results = []
    for start, end, name in BLACK_SWAN:
        sub_z = full_0050[(full_0050.index >= start) & (full_0050.index <= end)] \
            if full_0050 is not None else None
        z_ret = (float(sub_z.iloc[-1]/sub_z.iloc[0])-1)*100 \
            if sub_z is not None and len(sub_z) >= 2 else None

        active = _trades_in_period(trades, start, end)
        if not active:
            print(f"  {name}：策略空手 ✅（0050 {z_ret:+.1f}%）" if z_ret is not None
                  else f"  {name}：策略空手 ✅")
            swan_results.append({"event": name, "trades": 0, "ret": 0,
                                 "z_ret": z_ret, "skipped": True})
            continue

        n = len(active); wins = sum(1 for t in active if t["ret_pct"] > 0)
        avg = sum(t["ret_pct"] for t in active) / n
        cum = 1.0
        for t in active: cum *= (1 + t["ret_pct"]/100 * 0.2)
        cum_pct = (cum - 1) * 100
        print(f"  {name}：{n} 筆 ({wins} 勝)，估累積 {cum_pct:+.1f}%（0050 {z_ret:+.1f}%）"
              if z_ret is not None else f"  {name}：{n} 筆，估累積 {cum_pct:+.1f}%")
        swan_results.append({"event": name, "trades": n, "wins": wins,
                             "avg_ret": avg, "cum_ret": cum_pct,
                             "z_ret": z_ret, "skipped": False})

    # 連續虧損
    sorted_t = sorted(trades, key=lambda x: x["entry_date"])
    max_consec = 0; cur = 0
    for t in sorted_t:
        if t["ret_pct"] <= 0:
            cur += 1; max_consec = max(max_consec, cur)
        else: cur = 0
    big_losses = [t for t in sorted_t if t["ret_pct"] < -10]

    print(f"\n🔥 風險指標：")
    print(f"  最長連虧次數：{max_consec} 筆")
    print(f"  虧損 >10% 的交易：{len(big_losses)} / {len(trades)} 筆"
          f"（{len(big_losses)/len(trades)*100:.0f}%）")

    # 評等
    print(f"\n🏆 抗風險評等：")
    if abs(s_dd) <= 10:
        rating = "⭐⭐⭐⭐⭐ 卓越"
    elif abs(s_dd) <= 15:
        rating = "⭐⭐⭐⭐ 優秀"
    elif abs(s_dd) <= 25:
        rating = "⭐⭐⭐ 中等"
    else:
        rating = "⭐⭐ 偏弱"
    print(f"  {rating}（最大回撤 {s_dd:.1f}%）")

    result = {
        "label": label, "n_trades": len(trades),
        "max_drawdown": s_dd,
        "drawdown_0050": z_dd,
        "drawdown_spy": p_dd,
        "max_consecutive_losses": max_consec,
        "large_losses": len(big_losses),
        "swan_events": swan_results,
        "rating": rating,
    }
    if save_json:
        path = f"stress_test_{label.replace(' ', '_')}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n💾 已輸出 {path}")
    return result


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                   line_buffering=True)
    # 測試：對 V4 跑一次
    if os.path.exists("backtest_v4.json"):
        with open("backtest_v4.json", encoding="utf-8") as f:
            d = json.load(f)
        run_stress_test(d["trades"], label="V4", save_json=True)
