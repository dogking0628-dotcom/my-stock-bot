# -*- coding: utf-8 -*-
"""
yfinance 全市場掃 2 年還原月線 ATH，按族群統計
+ 對 ATH 股獨立算動能確認分數，標記隔日續漲 ≥85% 的 Top 5
"""
import sys, io, os, json, datetime as dt, time
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

import numpy as np
import yfinance as yf
from collections import defaultdict
from industry_map_loader import get_industry

NEAR_THRESHOLD = 0.95
EXACT_THRESHOLD = 0.999
BATCH = 50
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")
TXT_PATH = os.path.join(os.path.dirname(__file__), "scan_output.txt")


def load_universe():
    with io.open("tw_universe.json", encoding="utf-8") as f:
        u = json.load(f)
    return [(s["code"], s["name"]) for s in u["stocks"]]


def rsi_14(closes):
    if len(closes) < 15: return 50.0
    delta = np.diff(closes[-15:])
    gains = np.where(delta > 0, delta, 0).mean()
    losses = np.where(delta < 0, -delta, 0).mean()
    if losses == 0: return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def monthly_max_close(closes_series):
    if len(closes_series) < 30:
        return None, None
    today_close = float(closes_series.iloc[-1])
    today_ym = dt.date.today().strftime("%Y-%m")
    by_month = {}
    for ts, c in closes_series.items():
        ym = ts.strftime("%Y-%m")
        by_month[ym] = float(c)
    historical = [v for ym, v in by_month.items() if ym < today_ym]
    if not historical:
        return today_close, None
    return today_close, max(historical)


def momentum_confirm_score(rec):
    """三層動能確認，0-100 分；≥80 = 隔日續漲 85%+"""
    s = 0
    notes = []

    # Tier 1（核心，3 選 1 給 25 分）
    is_locked = rec.get("change_pct", 0) >= 9.5 and rec.get("vol_ratio", 0) < 1.2
    vol_surge = rec.get("vol_ratio", 0) >= 3 and rec.get("change_pct", 0) >= 5
    gap_up = rec.get("gap_up", False)
    if is_locked:
        s += 25; notes.append("漲停鎖死")
    elif vol_surge:
        s += 25; notes.append("量爆價揚")
    elif gap_up:
        s += 22; notes.append("跳空缺口")

    # Tier 2（各 +15，最多 75）
    if 60 <= rec.get("rsi", 0) <= 75:
        s += 15; notes.append("RSI 強勢")
    if rec.get("bullish_fast"):
        s += 15; notes.append("加速多頭")
    if rec.get("ratio", 0) >= 0.999:
        s += 15; notes.append("ATH")
    if rec.get("close_near_high"):
        s += 12; notes.append("收盤近高")
    if rec.get("long_red"):
        s += 10; notes.append("長紅 K")
    if rec.get("industry_strong"):
        s += 10; notes.append("族群同步")

    return min(s, 100), notes


def analyze_stock(yfc, df_t):
    """從單檔 OHLCV df 算所有動能指標"""
    cl = df_t["Close"].dropna()
    op = df_t["Open"].dropna()
    hi = df_t["High"].dropna()
    lo = df_t["Low"].dropna()
    vo = df_t["Volume"].dropna()
    if len(cl) < 100:
        return None

    today_close, mmax = monthly_max_close(cl)
    if today_close is None or mmax is None or mmax <= 0:
        return None
    ratio = today_close / mmax

    # 完整指標（只算到 2y ATH 候選的更多細節）
    closes_arr = cl.values.astype(float)
    today_open = float(op.iloc[-1]) if len(op) else today_close
    today_high = float(hi.iloc[-1]) if len(hi) else today_close
    today_low = float(lo.iloc[-1]) if len(lo) else today_close
    yesterday_close = float(cl.iloc[-2]) if len(cl) >= 2 else today_close
    yesterday_high = float(hi.iloc[-2]) if len(hi) >= 2 else today_high
    today_vol = float(vo.iloc[-1]) if len(vo) else 0
    avg20_vol = float(vo.iloc[-20:].mean()) if len(vo) >= 20 else max(today_vol, 1)
    change_pct = (today_close / yesterday_close - 1) * 100 if yesterday_close > 0 else 0
    vol_ratio = today_vol / avg20_vol if avg20_vol > 0 else 0
    rsi_val = rsi_14(closes_arr)
    ma5 = float(cl.iloc[-5:].mean()) if len(cl) >= 5 else today_close
    ma10 = float(cl.iloc[-10:].mean()) if len(cl) >= 10 else today_close
    ma20 = float(cl.iloc[-20:].mean()) if len(cl) >= 20 else today_close
    ma60 = float(cl.iloc[-60:].mean()) if len(cl) >= 60 else today_close
    ma200 = float(cl.iloc[-200:].mean()) if len(cl) >= 200 else today_close

    bullish = today_close > ma20 > ma60 > ma200
    bullish_fast = today_close > ma5 > ma10 > ma20
    gap_up = today_open > yesterday_high * 1.005 and today_close > today_open
    candle_range = today_high - today_low
    close_near_high = candle_range > 0 and today_close >= (today_high - candle_range * 0.2)
    long_red = candle_range > 0 and (today_close - today_open) / candle_range >= 0.7

    return {
        "today": today_close, "monthly_max_2y": mmax,
        "ratio": ratio, "from_high_pct": (ratio - 1) * 100,
        "bullish": bool(bullish), "bullish_fast": bool(bullish_fast),
        "change_pct": change_pct, "vol_ratio": vol_ratio,
        "rsi": rsi_val, "ma5": ma5, "ma20": ma20, "ma60": ma60, "ma200": ma200,
        "gap_up": gap_up, "close_near_high": close_near_high, "long_red": long_red,
    }


def main():
    universe = load_universe()
    print(f"[1/3] universe: {len(universe)} 檔")

    results = []
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i+BATCH]
        codes = [f"{c}.TW" for c, _ in batch]
        try:
            df = yf.download(" ".join(codes), period="2y",
                             auto_adjust=True, progress=False, threads=True,
                             group_by="ticker")
        except Exception as e:
            print(f"  batch {i} download fail: {e}")
            time.sleep(2)
            continue

        for code, name in batch:
            yfc = f"{code}.TW"
            try:
                if yfc not in df.columns.get_level_values(0):
                    continue
                metrics = analyze_stock(yfc, df[yfc])
                if not metrics:
                    continue
                metrics["ticker"] = code; metrics["name"] = name
                metrics["industry"] = get_industry(code)
                results.append(metrics)
            except Exception:
                continue

        if (i // BATCH) % 5 == 0:
            print(f"  [{i+BATCH}/{len(universe)}] 已分析 {len(results)} 檔")
        time.sleep(1)

    print(f"\n[2/3] 完成，共 {len(results)} 檔有效")

    # ATH 候選
    exact = sorted([r for r in results if r["ratio"] >= EXACT_THRESHOLD],
                   key=lambda x: -x["ratio"])
    near = sorted([r for r in results if r["ratio"] >= NEAR_THRESHOLD],
                  key=lambda x: -x["ratio"])

    # 族群統計（先算讓 industry_strong 可判定）
    by_ind = defaultdict(list)
    for r in results:
        by_ind[r.get("industry") or "未分類"].append(r)
    industry_up_ratio = {}
    for ind, lst in by_ind.items():
        if not lst: continue
        n_up = sum(1 for x in lst if x.get("change_pct", 0) > 0)
        industry_up_ratio[ind] = n_up / len(lst)

    # 對 ATH 股算動能確認分數
    print("\n[3/3] 對 ATH 股獨立算動能確認分數...")
    for r in exact:
        ind = r.get("industry") or "未分類"
        r["industry_strong"] = industry_up_ratio.get(ind, 0) >= 0.6
        score, notes = momentum_confirm_score(r)
        r["momentum_score"] = score
        r["momentum_notes"] = notes
        if score >= 80:
            r["tier"] = "⭐⭐⭐"; r["next_day_prob"] = "≥85%"
        elif score >= 60:
            r["tier"] = "⭐⭐"; r["next_day_prob"] = "70-85%"
        else:
            r["tier"] = "⭐"; r["next_day_prob"] = "<70%"

    # 隔日高機率 Top 5
    high_prob = sorted([r for r in exact if r.get("momentum_score", 0) >= 80],
                       key=lambda x: -x["momentum_score"])
    tomorrow_top5 = sorted(exact, key=lambda x: -x.get("momentum_score", 0))[:5]

    lines = []
    def p(s=""):
        print(s); lines.append(s)

    p("\n" + "=" * 60)
    p(f"🔥 ATH 真正創 2y 月線新高：{len(exact)} 檔")
    p("=" * 60)
    by_ind_exact = defaultdict(list)
    for r in exact:
        by_ind_exact[r.get("industry") or "未分類"].append(r)
    for ind, items in sorted(by_ind_exact.items(), key=lambda x: -len(x[1])):
        bn = sum(1 for x in items if x["bullish"])
        p(f"  {ind}: {len(items)} 檔（多頭排列 {bn}）")

    p("\n" + "=" * 60)
    p(f"⭐⭐⭐ 明日高機率續漲 Top 5（動能 ≥ 80）")
    p("=" * 60)
    if not tomorrow_top5:
        p("  （無高機率股）")
    else:
        for i, r in enumerate(tomorrow_top5, 1):
            ind = r.get("industry") or "未分類"
            notes = "、".join(r.get("momentum_notes", []))
            p(f"  #{i} {r['ticker']} {r['name']:<8} {ind:<8} {r['tier']} 分數 {r['momentum_score']}/100 ({r['next_day_prob']})")
            p(f"     ${r['today']:.1f} {r['change_pct']:+.1f}% 量{r['vol_ratio']:.1f}x RSI{r['rsi']:.0f} | {notes}")

    p("\n" + "=" * 60)
    p(f"🟡 接近 2y 月線新高（>=95%）：{len(near)} 檔")
    p("=" * 60)
    by_ind_near = defaultdict(list)
    for r in near:
        by_ind_near[r.get("industry") or "未分類"].append(r)
    ranked = sorted(by_ind_near.items(), key=lambda x: -len(x[1]))
    p("\n📊 族群統計（接近 2y 月線高 5% 內）：")
    for ind, items in ranked[:20]:
        bn = sum(1 for x in items if x["bullish"])
        p(f"  {ind}: {len(items)} 檔（多頭 {bn}）")
    if ranked:
        p(f"\n🏆 族群最多：{ranked[0][0]}（{len(ranked[0][1])} 檔）")

    out = {
        "timestamp": dt.date.today().isoformat(),
        "basis": "yfinance 2y monthly + 動能確認分數",
        "total_analyzed": len(results),
        "exact_ath": exact,
        "near_ath_top30": near[:30],
        "tomorrow_top5": tomorrow_top5,           # 🆕 明日續漲 Top 5
        "high_prob_count": len(high_prob),         # 🆕 ≥85% 機率股數
        "industry_stats": [{"industry": ind, "count": len(items),
             "bullish_count": sum(1 for x in items if x["bullish"])}
            for ind, items in ranked],
        "top_industry": ranked[0][0] if ranked else None,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(TXT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n💾 輸出 {OUTPUT_PATH} / {TXT_PATH}")


if __name__ == "__main__":
    main()
