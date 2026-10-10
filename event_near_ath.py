# -*- coding: utf-8 -*-
"""
事件研究：6日急漲+32% ×「即將ATH」vs「已破ATH」（2026-09-09 用戶提案）
═════════════════════════════════════════════════
事件（20日去重）：當日 6 日累計漲幅首次 >32%，按位置分三組：
  NEAR  距 2y 高 -12%~-0.5%（即將 ATH，未破）
  BREAK 已創 2y 高（ratio>=0.999）
  FAR   距高 >12%（低位急漲）
統計：T+1/3/5/10/20 前瞻報酬、勝率；NEAR 組另計「20日內成功創 2y 高」機率。
全市場與系統池（科技7族群+市值>=100億）分列。
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import backtest_strategy as bs
import backtest_v4_1 as v41
from industry_map_loader import get_industry


def main():
    bs.START_DATE = "2020-08-01"
    bs.END_DATE = dt.date.today().isoformat()
    codes = bs.load_universe(); mcap = v41.load_mcap()
    history = bs.fetch_history(codes)
    print(f"資料 {len(history)} 檔")

    rows = []
    for c, df in history.items():
        cl = df["Close"].dropna()
        if len(cl) < 260: continue
        arr = cl.values
        chg6 = cl / cl.shift(6) - 1
        hist_max = cl.shift(1).rolling(504, min_periods=200).max()   # 不含當日的 2y 高
        tech = get_industry(c) in v41.ALLOWED and (mcap.get(c) or 0) >= v41.MIN_MCAP
        last_ev = -99
        for i in range(210, len(arr) - 1):
            v, vp = chg6.iloc[i], chg6.iloc[i - 1]
            if pd.isna(v) or pd.isna(vp) or pd.isna(hist_max.iloc[i]): continue
            if v > 0.32 and vp <= 0.32 and i - last_ev > 20:
                last_ev = i
                ratio = arr[i] / hist_max.iloc[i]
                if ratio >= 0.999: grp = "BREAK"
                elif ratio >= 0.88: grp = "NEAR"
                else: grp = "FAR"
                ev = {"grp": grp, "tech": tech}
                for n in (1, 3, 5, 10, 20):
                    if i + n < len(arr):
                        ev[n] = (arr[i + n] / arr[i] - 1) * 100
                # NEAR 組：20 日內是否成功創 2y 高
                if grp == "NEAR":
                    fut_end = min(i + 21, len(arr))
                    fut_max = arr[i + 1:fut_end].max() if fut_end > i + 1 else 0
                    ev["ath20"] = bool(fut_max >= hist_max.iloc[i] * 0.999)
                rows.append(ev)

    def table(sub, label):
        print(f"\n📊 {label}")
        print(f"{'組別':<7}{'n':>6}{'T+1勝率':>9}{'T+5均':>8}{'T+5勝':>7}{'T+20均':>9}{'T+20中位':>9}{'T+20勝':>8}")
        for grp in ("NEAR", "BREAK", "FAR"):
            g = [r for r in sub if r["grp"] == grp]
            if not g: continue
            def stat(n):
                vals = [r[n] for r in g if n in r]
                if not vals: return 0, 0, 0
                return np.mean(vals), np.median(vals), sum(1 for x in vals if x > 0) / len(vals) * 100
            m1, _, w1 = stat(1); m5, _, w5 = stat(5); m20, md20, w20 = stat(20)
            print(f"{grp:<7}{len(g):>6}{w1:>8.0f}%{m5:>+7.1f}%{w5:>6.0f}%{m20:>+8.1f}%{md20:>+8.1f}%{w20:>7.0f}%")
        near = [r for r in sub if r["grp"] == "NEAR" and "ath20" in r]
        if near:
            ok = sum(1 for r in near if r["ath20"])
            succ = [r for r in near if r["ath20"]]; fail = [r for r in near if not r["ath20"]]
            s20 = [r[20] for r in succ if 20 in r]; f20 = [r[20] for r in fail if 20 in r]
            print(f"  NEAR→20日內成功創2y高: {ok}/{len(near)} = {ok/len(near)*100:.0f}%")
            if s20: print(f"    成功組 T+20 均 {np.mean(s20):+.1f}% 勝率 {sum(1 for x in s20 if x>0)/len(s20)*100:.0f}%")
            if f20: print(f"    失敗組 T+20 均 {np.mean(f20):+.1f}% 勝率 {sum(1 for x in f20 if x>0)/len(f20)*100:.0f}%")

    table(rows, "全市場")
    table([r for r in rows if r["tech"]], "系統池（科技7族群+百億）")
    json.dump([{k: (bool(v) if isinstance(v, (bool, np.bool_)) else v) for k, v in r.items()} for r in rows],
              io.open("event_near_ath.json", "w", encoding="utf-8"), ensure_ascii=False, default=float)
    print("\n💾 event_near_ath.json")


if __name__ == "__main__":
    main()
