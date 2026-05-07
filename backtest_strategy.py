# -*- coding: utf-8 -*-
"""
回測：「最強族群動能 ≥80 分 Top 5」+「跌破 20MA 出場」策略
─────────────────────────────────────────
邏輯：
  進場條件：
    1. 創 2y 月線 ATH（today >= max_24m * 0.999）
    2. 屬於最強族群（ATH 檔數最多 + 多頭比 ≥50%）
    3. 動能分數 ≥ 80（漲停鎖死/量爆/跳空 + ATH + 族群同步）
    4. 每日最多進場 5 檔（Top 5）
  出場條件：
    1. 收盤跌破 20MA → 隔日開盤賣
    2. 從買進高點回落 30% → 立刻賣
  資金：
    100 萬 NT$，每檔等比例 1/5 倉位
"""
import sys, os, json, datetime as dt, time, io
if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass

import numpy as np
import pandas as pd
import yfinance as yf
from collections import defaultdict
from industry_map_loader import get_industry

INITIAL = 1_000_000      # 起始資金
MAX_POS = 5              # 最大同時持股數
PER_POS = INITIAL / MAX_POS  # 每檔 20 萬
COMMISSION = 0.001425    # 手續費 0.1425%
TAX = 0.003              # 證交稅 0.3%
START_DATE = "2021-01-01"
END_DATE = "2026-05-06"
BATCH = 50

# 動能評分（與 industry_ath_yf.py 一致）
def momentum_score(rec):
    s = 0; notes = []
    is_locked = rec.get("change_pct", 0) >= 9.5 and rec.get("vol_ratio", 0) < 1.2
    vol_surge = rec.get("vol_ratio", 0) >= 3 and rec.get("change_pct", 0) >= 5
    gap_up = rec.get("gap_up", False)
    if is_locked:   s += 25; notes.append("漲停鎖死")
    elif vol_surge: s += 25; notes.append("量爆價揚")
    elif gap_up:    s += 22; notes.append("跳空")
    if rec.get("is_ath"):       s += 15; notes.append("ATH")
    if rec.get("industry_strong"): s += 10; notes.append("族群同步")
    if 60 <= rec.get("rsi", 0) <= 75: s += 15
    if rec.get("bullish_fast"): s += 15
    if rec.get("close_near_high"): s += 12
    if rec.get("long_red"): s += 10
    return min(s, 100), notes


def load_universe():
    with io.open("tw_universe.json", encoding="utf-8") as f:
        u = json.load(f)
    return [s["code"] for s in u["stocks"]]


def fetch_history(codes):
    """批次抓 5y OHLCV，回傳 {code: DataFrame}"""
    print(f"[fetch] 抓 {len(codes)} 檔 5y 歷史...")
    out = {}
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i+BATCH]
        yfc = [f"{c}.TW" for c in batch]
        try:
            df = yf.download(" ".join(yfc), start=START_DATE, end=END_DATE,
                             auto_adjust=True, progress=False, threads=True,
                             group_by="ticker")
        except Exception:
            time.sleep(2); continue
        for c in batch:
            ycode = f"{c}.TW"
            try:
                if ycode not in df.columns.get_level_values(0): continue
                sub = df[ycode].dropna()
                if len(sub) < 250: continue
                out[c] = sub
            except Exception: continue
        if (i // BATCH) % 5 == 0:
            print(f"  [{i+BATCH}/{len(codes)}] 已收 {len(out)} 檔")
        time.sleep(0.8)
    print(f"[fetch] 完成 {len(out)} 檔")
    return out


def daily_features(df, idx):
    """計算第 idx 日的特徵（idx 為 dataframe row index）"""
    if idx < 200: return None
    cl = df["Close"].values
    op = df["Open"].values
    hi = df["High"].values
    lo = df["Low"].values
    vo = df["Volume"].values
    today_close = cl[idx]; today_open = op[idx]
    today_high = hi[idx];  today_low = lo[idx]
    yest_close = cl[idx-1]; yest_high = hi[idx-1]
    today_vol = vo[idx]
    avg20 = vo[idx-20:idx].mean() if idx >= 20 else max(today_vol, 1)
    change_pct = (today_close / yest_close - 1) * 100
    vol_ratio = today_vol / avg20 if avg20 > 0 else 0
    # RSI 14
    delta = np.diff(cl[idx-15:idx+1]) if idx >= 15 else np.array([0])
    gains = np.where(delta > 0, delta, 0).mean()
    losses = np.where(delta < 0, -delta, 0).mean()
    rsi = 100.0 if losses == 0 else 100 - 100/(1 + gains/losses)
    ma5 = cl[idx-5:idx+1].mean()
    ma10 = cl[idx-10:idx+1].mean() if idx >= 10 else ma5
    ma20 = cl[idx-20:idx+1].mean() if idx >= 20 else ma5
    ma60 = cl[idx-60:idx+1].mean() if idx >= 60 else ma20
    ma200 = cl[idx-200:idx+1].mean() if idx >= 200 else ma60
    bullish = today_close > ma20 > ma60 > ma200
    bullish_fast = today_close > ma5 > ma10 > ma20
    gap_up = today_open > yest_high * 1.005 and today_close > today_open
    rng = today_high - today_low
    close_near_high = rng > 0 and today_close >= today_high - rng * 0.2
    long_red = rng > 0 and (today_close - today_open) / rng >= 0.7
    # 月線 ATH（24 個月）
    look = 504  # ~2 年
    if idx >= look:
        max_2y = cl[max(0, idx-look):idx].max()
        is_ath = today_close >= max_2y * 0.999
    else:
        is_ath = False
    return {"close": today_close, "open": today_open, "ma20": ma20,
            "change_pct": change_pct, "vol_ratio": vol_ratio, "rsi": rsi,
            "bullish": bool(bullish), "bullish_fast": bool(bullish_fast),
            "gap_up": gap_up, "close_near_high": close_near_high,
            "long_red": long_red, "is_ath": is_ath}


def run_backtest(history):
    """逐日掃描 → 進場/出場"""
    # 共用日期軸（取所有股票的聯集，按時間排序）
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(START_DATE)]
    print(f"[bt] 回測 {len(all_dates)} 個交易日")

    cash = INITIAL
    positions = {}  # {code: {"entry_price","shares","peak"}}
    trades = []     # 完整交易紀錄

    # 預先把每檔的 row index lookup 建好
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}

    for di, d in enumerate(all_dates):
        if di < 200: continue
        # 每檔今日特徵 + 進場資格
        candidates = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200: continue
            f = daily_features(df, i)
            if not f: continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            candidates.append(f)

        # 族群統計（為 industry_strong）
        by_ind = defaultdict(list)
        for r in candidates:
            ind = r.get("industry") or "未分類"
            by_ind[ind].append(r)
        ind_up_ratio = {ind: sum(1 for x in lst if x["change_pct"] > 0) / max(len(lst),1)
                        for ind, lst in by_ind.items()}
        for r in candidates:
            r["industry_strong"] = ind_up_ratio.get(r.get("industry") or "未分類", 0) >= 0.6

        # ATH + 動能 ≥80
        ath_set = [r for r in candidates if r["is_ath"]]
        for r in ath_set:
            sc, _ = momentum_score(r); r["score"] = sc
        # 最強族群
        ath_by_ind = defaultdict(list)
        for r in ath_set:
            ind = r.get("industry") or "未分類"
            if ind != "未分類": ath_by_ind[ind].append(r)
        strongest = None
        for ind, lst in sorted(ath_by_ind.items(), key=lambda x: -len(x[1])):
            br = sum(1 for x in lst if x["bullish"]) / max(len(lst),1)
            if len(lst) >= 5 and br >= 0.5:
                strongest = ind; break
        if strongest:
            pool = sorted(ath_by_ind[strongest],
                          key=lambda x: (-x["score"], -x["change_pct"]))
        else:
            pool = sorted(ath_set, key=lambda x: -x["score"])
        top5 = [r for r in pool if r["score"] >= 80][:5]

        # ── 出場（隔日開盤）──
        cur_features = {r["ticker"]: r for r in candidates}
        for c in list(positions.keys()):
            cf = cur_features.get(c)
            if not cf: continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            exit_now = False; reason = ""
            if cf["close"] < cf["ma20"]:
                exit_now = True; reason = "跌破20MA"
            elif cf["close"] < pos["peak"] * 0.7:
                exit_now = True; reason = "從峰值-30%"
            if exit_now:
                # 隔日開盤賣
                next_d = all_dates[di+1] if di+1 < len(all_dates) else None
                if next_d is None: continue
                ni = df_idx[c].get(next_d)
                if ni is None: continue
                sell_p = history[c]["Open"].iloc[ni]
                shares = pos["shares"]
                proceeds = shares * sell_p * (1 - COMMISSION - TAX)
                cash += proceeds
                ret_pct = (sell_p / pos["entry_price"] - 1) * 100
                trades.append({
                    "ticker": c, "entry_date": pos["entry_date"], "exit_date": str(next_d.date()),
                    "entry": pos["entry_price"], "exit": sell_p,
                    "ret_pct": ret_pct, "reason": reason,
                    "hold_days": (next_d - pd.Timestamp(pos["entry_date"])).days,
                })
                del positions[c]

        # ── 進場（隔日開盤）──
        slots = MAX_POS - len(positions)
        if slots > 0 and top5:
            buys = [r for r in top5 if r["ticker"] not in positions][:slots]
            next_d = all_dates[di+1] if di+1 < len(all_dates) else None
            if next_d:
                for r in buys:
                    c = r["ticker"]
                    ni = df_idx[c].get(next_d)
                    if ni is None: continue
                    buy_p = history[c]["Open"].iloc[ni]
                    if cash < PER_POS * 0.5: break
                    cost_per_share = buy_p * (1 + COMMISSION)
                    shares = int(min(PER_POS, cash) / cost_per_share / 1000) * 1000
                    if shares < 1000: continue
                    cost = shares * cost_per_share
                    cash -= cost
                    positions[c] = {"entry_price": buy_p, "shares": shares,
                                    "peak": buy_p, "entry_date": str(next_d.date())}

    # 最後清倉（用最後一日收盤）
    final_d = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(final_d)
        if i is None: continue
        last_p = history[c]["Close"].iloc[i]
        cash += pos["shares"] * last_p * (1 - COMMISSION - TAX)

    return cash, trades


def report(final_cash, trades):
    n = len(trades)
    if n == 0:
        print("⚠️ 無交易")
        return
    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] <= 0]
    total_ret = (final_cash / INITIAL - 1) * 100
    avg_win = sum(t["ret_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["ret_pct"] for t in losses) / len(losses) if losses else 0
    avg_hold = sum(t["hold_days"] for t in trades) / n
    win_rate = len(wins) / n
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    pf = (sum(t["ret_pct"] for t in wins) /
          abs(sum(t["ret_pct"] for t in losses))) if losses else float("inf")
    # 年化
    years = (pd.Timestamp(END_DATE) - pd.Timestamp(START_DATE)).days / 365.25
    cagr = (final_cash / INITIAL) ** (1/years) - 1

    print("\n" + "=" * 60)
    print("📊 回測結果")
    print("=" * 60)
    print(f"期間：{START_DATE} ~ {END_DATE} ({years:.1f} 年)")
    print(f"初始：${INITIAL:,.0f}  →  期末：${final_cash:,.0f}")
    print(f"總報酬：{total_ret:+.1f}%  ｜  年化 CAGR：{cagr*100:+.1f}%")
    print()
    print(f"交易次數：{n}")
    print(f"勝率：{win_rate*100:.1f}% ({len(wins)} 勝 / {len(losses)} 敗)")
    print(f"平均獲利：{avg_win:+.2f}%  ｜  平均虧損：{avg_loss:+.2f}%")
    print(f"平均持倉天數：{avg_hold:.0f} 天")
    print(f"期望值（每筆）：{expectancy:+.2f}%")
    print(f"獲利因子 (PF)：{pf:.2f}")

    # 出場原因分布
    by_reason = defaultdict(list)
    for t in trades: by_reason[t["reason"]].append(t)
    print("\n出場原因：")
    for r, ts in by_reason.items():
        ws = sum(1 for x in ts if x["ret_pct"] > 0)
        avg = sum(x["ret_pct"] for x in ts) / len(ts)
        print(f"  {r}: {len(ts)} 次（勝 {ws}，平均 {avg:+.2f}%）")

    # 最大單筆勝/負
    best = max(trades, key=lambda x: x["ret_pct"])
    worst = min(trades, key=lambda x: x["ret_pct"])
    print(f"\n最大單筆獲利：{best['ticker']} {best['ret_pct']:+.1f}% ({best['entry_date']}→{best['exit_date']})")
    print(f"最大單筆虧損：{worst['ticker']} {worst['ret_pct']:+.1f}% ({worst['entry_date']}→{worst['exit_date']})")


def main():
    codes = load_universe()
    print(f"universe: {len(codes)} 檔")
    history = fetch_history(codes)
    if len(history) < 100:
        print("⚠️ 資料不足，無法回測")
        return
    cash, trades = run_backtest(history)
    report(cash, trades)
    # 儲存
    out = {"final_cash": cash, "trades": trades, "n_trades": len(trades)}
    with open("backtest_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 已輸出 backtest_result.json")


if __name__ == "__main__":
    main()
