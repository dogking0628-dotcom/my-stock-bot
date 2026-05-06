# -*- coding: utf-8 -*-
"""
每日回顧昨日 Top 5 選股 + 改進建議
─────────────────────────────────
1. 讀 top5_history.json 取昨日推薦
2. 用 yfinance 抓今日收盤
3. 計算命中率（漲 +）/失敗率（跌 -）
4. 分析哪個訊號最準
5. 產出改進建議區塊（給 LINE + dashboard）
"""
import sys, io, os, json, datetime as dt
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception: pass

import yfinance as yf
from collections import defaultdict

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "top5_history.json")
REVIEW_PATH = os.path.join(os.path.dirname(__file__), "daily_review.json")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"records": []}
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {"records": []}


def save_history(h):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)


def record_today_top5():
    """把今日的 tomorrow_top5 寫入歷史（供明天回顧）"""
    if not os.path.exists(REPORT_PATH):
        return
    with open(REPORT_PATH, encoding="utf-8") as f:
        rep = json.load(f)
    today = rep.get("timestamp", dt.date.today().isoformat())
    top5 = rep.get("tomorrow_top5", [])
    industry = rep.get("tomorrow_top5_industry")
    if not top5: return

    h = load_history()
    # 同日覆寫（盤中也跑）
    h["records"] = [r for r in h.get("records", []) if r.get("date") != today]
    h["records"].append({
        "date": today,
        "industry": industry,
        "picks": [{"ticker": t["ticker"], "name": t["name"],
                   "industry": t.get("industry"),
                   "rec_close": t["today"],
                   "momentum_score": t.get("momentum_score", 0),
                   "momentum_notes": t.get("momentum_notes", []),
                   "tier": t.get("tier", "⭐"),
                   "rsi": t.get("rsi", 0),
                   "change_pct_at_rec": t.get("change_pct", 0),
                   "vol_ratio_at_rec": t.get("vol_ratio", 0),}
                  for t in top5],
    })
    h["records"] = h["records"][-30:]  # 留 30 天
    save_history(h)
    print(f"[review] 已紀錄 {today} Top 5：{[t['ticker'] for t in top5]}")


def fetch_close(ticker):
    """抓 ticker 最新一筆收盤"""
    try:
        df = yf.download(f"{ticker}.TW", period="5d", auto_adjust=True,
                         progress=False, threads=False)
        if df.empty or "Close" not in df.columns: return None
        return float(df["Close"].dropna().iloc[-1])
    except Exception:
        return None


def review_yesterday():
    """讀昨日 picks，計算今日表現"""
    h = load_history()
    recs = h.get("records", [])
    if len(recs) < 2:
        print("[review] 不足 2 日紀錄，無法回顧")
        return None

    # 找最近一筆「非今日」的紀錄
    today = dt.date.today().isoformat()
    yesterday_rec = next((r for r in reversed(recs) if r["date"] != today), None)
    if not yesterday_rec:
        return None

    print(f"[review] 回顧 {yesterday_rec['date']} 的選股...")
    results = []
    for p in yesterday_rec["picks"]:
        last = fetch_close(p["ticker"])
        if last is None:
            results.append({**p, "today_close": None, "ret_pct": None, "hit": None})
            continue
        ret = (last / p["rec_close"] - 1) * 100
        results.append({**p, "today_close": last, "ret_pct": ret, "hit": ret > 0})
    return {"date": yesterday_rec["date"], "industry": yesterday_rec.get("industry"),
            "results": results}


def analyze_review(review):
    """分析哪個訊號最準 + 改進建議"""
    if not review or not review.get("results"):
        return None
    res = [r for r in review["results"] if r.get("ret_pct") is not None]
    if not res:
        return None

    n = len(res)
    n_hit = sum(1 for r in res if r["hit"])
    avg_ret = sum(r["ret_pct"] for r in res) / n
    best = max(res, key=lambda x: x["ret_pct"])
    worst = min(res, key=lambda x: x["ret_pct"])

    # 訊號 vs 命中率
    signal_stats = defaultdict(lambda: {"total": 0, "hit": 0, "ret_sum": 0})
    for r in res:
        for note in r.get("momentum_notes", []):
            signal_stats[note]["total"] += 1
            if r["hit"]: signal_stats[note]["hit"] += 1
            signal_stats[note]["ret_sum"] += r["ret_pct"]
    signal_perf = []
    for sig, d in signal_stats.items():
        signal_perf.append({"signal": sig, "n": d["total"],
                            "hit_rate": d["hit"] / d["total"] if d["total"] else 0,
                            "avg_ret": d["ret_sum"] / d["total"] if d["total"] else 0})
    signal_perf.sort(key=lambda x: -x["hit_rate"])

    # 改進建議
    suggestions = []
    hit_rate = n_hit / n
    if hit_rate >= 0.8:
        suggestions.append("✅ 命中率 ≥80% — 維持目前策略")
    elif hit_rate >= 0.6:
        suggestions.append("🟡 命中率 60-80% — 策略可用，但可加強動能確認")
    else:
        suggestions.append("🔴 命中率 <60% — 警告：當前盤勢可能轉弱，建議降低部位或暫停進場")
    # 高 RSI 警告
    high_rsi_lost = [r for r in res if r.get("rsi", 0) > 80 and not r["hit"]]
    if len(high_rsi_lost) >= 2:
        suggestions.append("⚠️ RSI > 80 的股票今日多失敗 — 建議排除 RSI > 80")
    # 漲幅過大警告
    high_chg_lost = [r for r in res
                     if r.get("change_pct_at_rec", 0) > 9 and not r["hit"]]
    if len(high_chg_lost) >= 2:
        suggestions.append("⚠️ 推薦時漲幅 >9% 的股票今日多失敗 — 可能是短線過熱")
    # 量縮警告
    low_vol_lost = [r for r in res
                    if r.get("vol_ratio_at_rec", 0) < 1.0 and not r["hit"]]
    if len(low_vol_lost) >= 2:
        suggestions.append("⚠️ 推薦時量比 <1 的股票多失敗 — 建議要求量比 ≥1.2")

    return {
        "date": review["date"], "industry": review.get("industry"),
        "n": n, "n_hit": n_hit, "hit_rate": hit_rate, "avg_ret": avg_ret,
        "best": {"ticker": best["ticker"], "name": best["name"],
                 "ret_pct": best["ret_pct"]},
        "worst": {"ticker": worst["ticker"], "name": worst["name"],
                  "ret_pct": worst["ret_pct"]},
        "results": res,
        "signal_performance": signal_perf,
        "suggestions": suggestions,
    }


def build_review_block(analysis):
    """給 LINE 用的回顧區塊"""
    if not analysis:
        return ""
    a = analysis
    lines = [f"📈 昨日 ({a['date']}) Top 5 回顧",
             f"  族群：{a.get('industry') or '混合'} ｜ 命中 {a['n_hit']}/{a['n']}"
             f" ({a['hit_rate']*100:.0f}%) ｜ 平均 {a['avg_ret']:+.2f}%"]
    lines.append(f"  🏆 最佳：{a['best']['ticker']} {a['best']['name']} "
                 f"{a['best']['ret_pct']:+.2f}%")
    lines.append(f"  💀 最差：{a['worst']['ticker']} {a['worst']['name']} "
                 f"{a['worst']['ret_pct']:+.2f}%")
    lines.append("  📋 個股：")
    for r in a["results"]:
        emoji = "✅" if r["hit"] else "❌"
        lines.append(f"    {emoji} {r['ticker']} {r['name']} "
                     f"{r['ret_pct']:+.2f}%（{r.get('tier','⭐')}）")
    if a["signal_performance"]:
        lines.append("  📊 訊號表現：")
        for s in a["signal_performance"][:3]:
            lines.append(f"    • {s['signal']}: 命中{s['hit_rate']*100:.0f}%"
                         f" 平均{s['avg_ret']:+.1f}% (n={s['n']})")
    if a["suggestions"]:
        lines.append("  💡 改進建議：")
        for s in a["suggestions"]:
            lines.append(f"    {s}")
    return "\n".join(lines)


def main():
    # 1. 先回顧昨日
    review = review_yesterday()
    analysis = analyze_review(review)
    if analysis:
        with open(REVIEW_PATH, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        print(build_review_block(analysis))
    else:
        print("[review] 無昨日資料可回顧")

    # 2. 紀錄今日 Top 5（給明天回顧用）
    record_today_top5()


if __name__ == "__main__":
    main()
