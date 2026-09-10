# -*- coding: utf-8 -*-
"""
進場時點研究：V4.5 訊號「什麼時候買、怎麼掛單」對績效的影響（2y + 5y 雙窗）
═══════════════════════════════════════════════════════════════
起因：2026-09-10 實單 松川精密(7788) 開盤市價買在當日最高 330，現價 307 = -7.0%。
      回測 V4.5 的 +250.6% 是用「隔日開盤價成交」算的（backtest_strategy.py:233 buy_p=Open），
      但 LINE 推播叫用戶掛「收盤×1.008 ~ ×1.02」限價 → 兩者是不同策略，從未被分開驗證。

本測回答三題：
  Q1 進場成交方式：開盤市價 vs 限價協議 vs 收盤買 vs 等回檔 vs 隔兩天買
  Q2 濾網A：訊號日收漲停(+9.5%以上) → 隔日不掛單
  Q3 濾網B：訊號日收盤 < 近10日最高日收 → 不算有效突破（箱型內反彈不買）
另附：事件研究 — 隔日開盤在當日 K 棒 range 的位置分布（直接回答「一天中哪個位置買」）

⚠️ 資料限制：日 K 只有 OHLC，無法模擬「限價只掛 10 分鐘就撤」。
   故限價用兩個版本夾擠真實值：
     LIMIT_OPEN  = 只有開盤集合競價成交（最保守，接近 09:10 撤單）
     LIMIT_DAY   = 限價掛整天（最樂觀，當日 low 觸及就成交）
"""
import sys, os, json, io
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from collections import defaultdict, deque
import backtest_strategy as bs
import backtest_v4_1 as v41
import backtest_v45_candidate as v45
from industry_map_loader import get_industry

ALLOWED = v41.ALLOWED
MIN_MCAP = v41.MIN_MCAP
SCORE_THRESHOLD = 80
RECENT_LOSER_WINDOW = 7
HARD_FLOOR = 0.93
LIMIT_LOW_MULT = 1.008     # 推播的限價低點
LIMIT_HIGH_MULT = 1.02     # 推播的限價高點


# ══════════ 訊號日濾網 ══════════
def sig_filter_ok(mode, r, df, i):
    """訊號日層級的濾網。r=daily_features, df=該股歷史, i=訊號日 index"""
    if mode in ("no_limitup", "both"):
        # 收漲停（台股漲停 +10%，用 +9.5% 容錯；興櫃/特殊不管）
        if r["change_pct"] >= 9.5:
            return False
    if mode in ("valid_breakout", "both"):
        # 訊號日收盤必須是近 10 日最高收盤，否則視為箱型內反彈
        lo = max(0, i - 9)
        recent_max = float(df["Close"].iloc[lo:i + 1].max())
        if r["close"] < recent_max - 1e-9:
            return False
    return True


# ══════════ 進場成交模型 ══════════
def fill_price(mode, df, ni, sig_close):
    """回傳 (成交價 or None, 延後幾天)。None = 沒買到（協議規定撤單不追）"""
    o = float(df["Open"].iloc[ni])
    h = float(df["High"].iloc[ni])
    l = float(df["Low"].iloc[ni])
    c = float(df["Close"].iloc[ni])

    if mode == "OPEN":                 # 現行回測假設＝開盤市價（集合競價）
        return o, 0
    if mode == "CLOSE":                # 隔日收盤買
        return c, 0
    if mode == "LIMIT_OPEN":           # 限價，只在開盤競價成交（保守夾擠）
        hi = sig_close * LIMIT_HIGH_MULT
        return (o, 0) if o <= hi else (None, 0)
    if mode == "LIMIT_DAY":            # 限價掛整天（樂觀夾擠）
        lo_lim = sig_close * LIMIT_LOW_MULT
        hi_lim = sig_close * LIMIT_HIGH_MULT
        if o <= lo_lim:
            return o, 0                # 開盤就低於低點限價 → 開盤成交
        if l <= lo_lim:
            return lo_lim, 0           # 盤中回落觸及低點限價
        if o <= hi_lim:
            return o, 0                # 開盤在區間內 → 改掛高點即成交
        if l <= hi_lim:
            return hi_lim, 0           # 開高後回落觸及高點限價
        return None, 0                 # 跳空超過 +2% 且不回頭 → 放棄不追
    if mode == "DIP1":                 # 等開盤再跌 1% 才買
        tgt = o * 0.99
        return (tgt, 0) if l <= tgt else (None, 0)
    if mode == "DAY2_OPEN":            # 不追，隔兩天開盤買
        return None, 1                 # 由呼叫端處理延後
    raise ValueError(mode)


# ══════════ 主回測 ══════════
def run_variant(name, entry_mode, filt_mode, trade_start, history, mcap, us_chg,
                regime, below20, cums, df_idx, all_dates):
    cash = bs.INITIAL
    positions = {}
    trades = []
    losers = deque(maxlen=400)
    n_signal = 0
    n_filled = 0

    for di, d in enumerate(all_dates):
        if di < 200:
            continue
        d_str = d.strftime("%Y-%m-%d")
        prev_str = all_dates[di - 1].strftime("%Y-%m-%d")
        in_stage2 = bool(regime.get(d_str, False))
        cutoff = d - pd.Timedelta(days=RECENT_LOSER_WINDOW)
        recent_losers = {t for ed, t in losers if pd.Timestamp(ed) >= cutoff}

        cands = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200:
                continue
            f = bs.daily_features(df, i)
            if not f:
                continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            f["_i"] = i
            cands.append(f)
        cur = {r["ticker"]: r for r in cands}
        by_ind = defaultdict(list)
        for r in cands:
            by_ind[r.get("industry") or "未分類"].append(r)
        ind_up = {k: sum(1 for x in v if x["change_pct"] > 0) / max(len(v), 1)
                  for k, v in by_ind.items()}
        for r in cands:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6

        # ── 出場（規則完全不動：收盤破20MA / 峰值-30% / 進場-7% → 隔日開盤賣）──
        for c in list(positions):
            cf = cur.get(c)
            if not cf:
                continue
            pos = positions[c]
            pos["peak"] = max(pos["peak"], cf["close"])
            if (cf["close"] < cf["ma20"] or cf["close"] < pos["peak"] * 0.7
                    or cf["close"] < pos["entry_price"] * HARD_FLOOR):
                nd = all_dates[di + 1] if di + 1 < len(all_dates) else None
                if nd is None:
                    continue
                ni = df_idx[c].get(nd)
                if ni is None:
                    continue
                sp = float(history[c]["Open"].iloc[ni])
                cash += pos["shares"] * sp * (1 - bs.COMMISSION - bs.TAX)
                ret = (sp / pos["entry_price"] - 1) * 100
                trades.append({"ticker": c, "entry_date": pos["entry_date"],
                               "exit_date": str(nd.date()), "entry": pos["entry_price"],
                               "exit": sp, "ret_pct": ret,
                               "hold_days": (nd - pd.Timestamp(pos["entry_date"])).days})
                if ret < 0:
                    losers.append((str(nd.date()), c))
                del positions[c]

        if d_str < trade_start or not in_stage2:
            continue
        if below20.get(d_str, False):        # V4.4 減速器
            continue

        ath = [r for r in cands if r["is_ath"] and r.get("industry") in ALLOWED
               and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP
               and r["ticker"] not in recent_losers]
        for r in ath:
            sc, _ = bs.momentum_score(r)
            sc += v41.us_bonus(r["industry"], prev_str, us_chg)
            r["score"] = min(sc, 100)
        ath_by = defaultdict(list)
        for r in ath:
            ath_by[r["industry"]].append(r)
        strongest = None
        for ind, lst in sorted(ath_by.items(), key=lambda x: -len(x[1])):
            if len(lst) >= 3 and sum(1 for x in lst if x["bullish"]) / len(lst) >= 0.5:
                strongest = ind
                break
        pool = [r for r in (ath_by[strongest] if strongest else ath)
                if r["score"] >= SCORE_THRESHOLD]
        pool = [r for r in pool if not v45.hit_any(cums[r["ticker"]], r["_i"])]   # V4.5 警示濾除
        pool = [r for r in pool
                if sig_filter_ok(filt_mode, r, history[r["ticker"]], r["_i"])]    # 本測新濾網
        pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))
        picks = pool[:5]

        slots = bs.MAX_POS - len(positions)
        if slots <= 0 or not picks:
            continue
        for r in picks[:slots]:
            c = r["ticker"]
            if c in positions:
                continue
            n_signal += 1
            offset = 2 if entry_mode == "DAY2_OPEN" else 1
            nd = all_dates[di + offset] if di + offset < len(all_dates) else None
            if nd is None:
                continue
            ni = df_idx[c].get(nd)
            if ni is None:
                continue
            if entry_mode == "DAY2_OPEN":
                bp = float(history[c]["Open"].iloc[ni])
            else:
                bp, _ = fill_price(entry_mode, history[c], ni, r["close"])
            if bp is None:
                continue                       # 沒成交＝放棄不追
            if cash < bs.PER_POS * 0.5:
                break
            cps = bp * (1 + bs.COMMISSION)
            sh = int(min(bs.PER_POS, cash) / cps / 1000) * 1000
            if sh < 1000:
                continue
            cash -= sh * cps
            n_filled += 1
            positions[c] = {"entry_price": bp, "shares": sh, "peak": bp,
                            "entry_date": str(nd.date())}

    fd = all_dates[-1]
    for c, pos in positions.items():
        i = df_idx[c].get(fd)
        if i is not None:
            cash += pos["shares"] * float(history[c]["Close"].iloc[i]) * (1 - bs.COMMISSION - bs.TAX)
    return cash, trades, n_signal, n_filled


def stats(cash, trades, years):
    n = len(trades)
    if n == 0:
        return {"total_pct": 0, "cagr": 0, "trades": 0, "win_rate": 0,
                "expectancy": 0, "pf": 0, "mdd": 0}
    rets = [t["ret_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    eq = bs.INITIAL
    peak = eq
    mdd = 0.0
    for t in sorted(trades, key=lambda x: x["exit_date"]):
        eq *= (1 + t["ret_pct"] / 100 / bs.MAX_POS)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return {
        "total_pct": round((cash / bs.INITIAL - 1) * 100, 1),
        "cagr": round(((cash / bs.INITIAL) ** (1 / years) - 1) * 100, 1) if cash > 0 else 0,
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "expectancy": round(sum(rets) / n, 2),
        "pf": round(gp / gl, 2) if gl > 0 else 999,
        "mdd": round(mdd * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 1) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 1) if losses else 0,
    }


# ══════════ 事件研究：隔日開盤在 K 棒的哪個位置 ══════════
def event_study(history, mcap, us_chg, regime, below20, cums, df_idx, all_dates, start):
    rows = []
    for di, d in enumerate(all_dates):
        if di < 200 or di + 1 >= len(all_dates):
            continue
        d_str = d.strftime("%Y-%m-%d")
        if d_str < start or not regime.get(d_str, False) or below20.get(d_str, False):
            continue
        prev_str = all_dates[di - 1].strftime("%Y-%m-%d")
        cands = []
        for c, df in history.items():
            i = df_idx[c].get(d)
            if i is None or i < 200:
                continue
            f = bs.daily_features(df, i)
            if not f:
                continue
            f["ticker"] = c
            f["industry"] = get_industry(c)
            f["_i"] = i
            cands.append(f)
        by_ind = defaultdict(list)
        for r in cands:
            by_ind[r.get("industry") or "未分類"].append(r)
        ind_up = {k: sum(1 for x in v if x["change_pct"] > 0) / max(len(v), 1)
                  for k, v in by_ind.items()}
        for r in cands:
            r["industry_strong"] = ind_up.get(r.get("industry") or "未分類", 0) >= 0.6
        ath = [r for r in cands if r["is_ath"] and r.get("industry") in ALLOWED
               and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP]
        for r in ath:
            sc, _ = bs.momentum_score(r)
            sc += v41.us_bonus(r["industry"], prev_str, us_chg)
            r["score"] = min(sc, 100)
        pool = [r for r in ath if r["score"] >= SCORE_THRESHOLD
                and not v45.hit_any(cums[r["ticker"]], r["_i"])]
        pool.sort(key=lambda x: (-x["score"], -x["change_pct"]))
        nd = all_dates[di + 1]
        for r in pool[:5]:
            c = r["ticker"]
            ni = df_idx[c].get(nd)
            if ni is None:
                continue
            df = history[c]
            o, h, l, cl = (float(df["Open"].iloc[ni]), float(df["High"].iloc[ni]),
                           float(df["Low"].iloc[ni]), float(df["Close"].iloc[ni]))
            if h <= l:
                continue
            sig_c = r["close"]
            rows.append({
                "date": d_str, "ticker": c,
                "sig_chg": round(r["change_pct"], 2),
                "limit_up": r["change_pct"] >= 9.5,
                "gap_pct": round((o / sig_c - 1) * 100, 2),
                "open_pos": round((o - l) / (h - l) * 100, 1),   # 開盤在當日 range 位置 %
                "open_to_low": round((l / o - 1) * 100, 2),
                "open_to_high": round((h / o - 1) * 100, 2),
                "open_to_close": round((cl / o - 1) * 100, 2),
                "close_vs_sig": round((cl / sig_c - 1) * 100, 2),
            })
    return rows


def pct(arr, q):
    return round(float(np.percentile(arr, q)), 2) if len(arr) else None


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

    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    cums = v45.precompute_cums(history)

    today = dt.date.today()
    WINDOWS = [("5y", (today - dt.timedelta(days=365 * 5)).isoformat(), 5.0),
               ("2y", (today - dt.timedelta(days=365 * 2)).isoformat(), 2.0)]

    results = {}

    # ── Q1：進場成交方式 ──
    print("\n" + "=" * 70)
    print("Q1 進場成交方式（濾網固定 = 現行 V4.5）")
    print("=" * 70)
    for mode in ["OPEN", "LIMIT_OPEN", "LIMIT_DAY", "DIP1", "CLOSE", "DAY2_OPEN"]:
        for wname, wstart, wyears in WINDOWS:
            cash, tr, nsig, nfil = run_variant(
                mode, mode, "none", wstart, history, mcap, us_chg,
                regime, below20, cums, df_idx, all_dates)
            st = stats(cash, tr, wyears)
            st["signals"] = nsig
            st["filled"] = nfil
            st["fill_rate"] = round(nfil / nsig * 100, 1) if nsig else 0
            results[f"Q1_{mode}_{wname}"] = st
            print(f"  {mode:<11} {wname}: 報酬{st['total_pct']:>8.1f}% CAGR{st['cagr']:>6.1f}% "
                  f"筆{st['trades']:>4} 勝{st['win_rate']:>5.1f}% 期望{st['expectancy']:>6.2f}% "
                  f"PF{st['pf']:>5.2f} MDD{st['mdd']:>6.1f}% 成交率{st['fill_rate']:>5.1f}%")

    # ── Q2/Q3：濾網（進場固定 = OPEN，與現行回測基準可比）──
    print("\n" + "=" * 70)
    print("Q2/Q3 訊號日濾網（進場固定 = 開盤價）")
    print("=" * 70)
    for filt in ["none", "no_limitup", "valid_breakout", "both"]:
        for wname, wstart, wyears in WINDOWS:
            cash, tr, nsig, nfil = run_variant(
                filt, "OPEN", filt, wstart, history, mcap, us_chg,
                regime, below20, cums, df_idx, all_dates)
            st = stats(cash, tr, wyears)
            st["signals"] = nsig
            results[f"Q23_{filt}_{wname}"] = st
            print(f"  {filt:<15} {wname}: 報酬{st['total_pct']:>8.1f}% CAGR{st['cagr']:>6.1f}% "
                  f"筆{st['trades']:>4} 勝{st['win_rate']:>5.1f}% 期望{st['expectancy']:>6.2f}% "
                  f"PF{st['pf']:>5.2f} MDD{st['mdd']:>6.1f}%")

    # ── 事件研究 ──
    print("\n" + "=" * 70)
    print("事件研究：訊號隔日，開盤在當日 K 棒的哪個位置？")
    print("=" * 70)
    ev = event_study(history, mcap, us_chg, regime, below20, cums, df_idx,
                     all_dates, WINDOWS[0][1])
    if ev:
        op = [x["open_pos"] for x in ev]
        gp = [x["gap_pct"] for x in ev]
        otl = [x["open_to_low"] for x in ev]
        otc = [x["open_to_close"] for x in ev]
        oth = [x["open_to_high"] for x in ev]
        print(f"  樣本 {len(ev)} 個訊號（5y，未受持倉上限限制）")
        print(f"  開盤位置(0=當日最低,100=當日最高)：中位 {pct(op,50)}  平均 {round(float(np.mean(op)),1)}")
        print(f"     >70% 的比例（開盤買在偏高處）: "
              f"{round(sum(1 for x in op if x>70)/len(op)*100,1)}%")
        print(f"     <30% 的比例（開盤買在偏低處）: "
              f"{round(sum(1 for x in op if x<30)/len(op)*100,1)}%")
        print(f"  跳空幅度 gap：中位 {pct(gp,50)}%  P25 {pct(gp,25)}%  P75 {pct(gp,75)}%")
        print(f"     跳空 > +2%（限價高點掛不到）的比例: "
              f"{round(sum(1 for x in gp if x>2)/len(gp)*100,1)}%")
        print(f"  開盤→當日最低：中位 {pct(otl,50)}%  P25 {pct(otl,25)}%")
        print(f"  開盤→當日收盤：中位 {pct(otc,50)}%  平均 {round(float(np.mean(otc)),2)}%  "
              f"收高於開的比例 {round(sum(1 for x in otc if x>0)/len(otc)*100,1)}%")
        print(f"  開盤→當日最高：中位 {pct(oth,50)}%")

        lu = [x for x in ev if x["limit_up"]]
        nlu = [x for x in ev if not x["limit_up"]]
        print(f"\n  ▸ 訊號日收漲停 ({len(lu)} 筆) vs 未漲停 ({len(nlu)} 筆)：")
        for lbl, grp in [("漲停", lu), ("未漲停", nlu)]:
            if not grp:
                continue
            print(f"     {lbl:<5}: gap中位 {pct([x['gap_pct'] for x in grp],50)}%  "
                  f"開盤位置中位 {pct([x['open_pos'] for x in grp],50)}  "
                  f"開→收中位 {pct([x['open_to_close'] for x in grp],50)}%  "
                  f"開→低中位 {pct([x['open_to_low'] for x in grp],50)}%")
        results["event_study_n"] = len(ev)
        results["event_open_pos_median"] = pct(op, 50)
        results["event_gap_median"] = pct(gp, 50)
        results["event_open_to_close_mean"] = round(float(np.mean(otc)), 2)
        json.dump(ev, io.open("data/entry_timing_events.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    json.dump(results, io.open("backtest_entry_timing.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n💾 backtest_entry_timing.json + data/entry_timing_events.json")


if __name__ == "__main__":
    main()
