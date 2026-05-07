# -*- coding: utf-8 -*-
"""
每日盤後策略檢討（前一日 + 前一週彙總分析）
─────────────────────────────────────────
用於回答：
  1. 昨日的 Top 5 表現？
  2. 過去一週累積績效？
  3. 哪個訊號最準？
  4. 哪個族群表現最好？
  5. 哪些濾網該強化／該放寬？
"""
import sys, os, json, datetime as dt, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(__file__))

import yfinance as yf
from collections import defaultdict, Counter

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "top5_history.json")
WEEKLY_REVIEW_PATH = os.path.join(os.path.dirname(__file__), "weekly_review.json")


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"records": []}
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"records": []}


def fetch_close(ticker):
    try:
        df = yf.download(f"{ticker}.TW", period="3d",
                         auto_adjust=True, progress=False, threads=False)
        if df.empty: return None
        return float(df["Close"].dropna().iloc[-1])
    except Exception:
        return None


def review_period(records, days):
    """回顧過去 N 個交易日的選股表現"""
    if not records: return None
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=days)
    relevant = [r for r in records
                if dt.date.fromisoformat(r["date"]) >= cutoff
                and dt.date.fromisoformat(r["date"]) < today]
    if not relevant: return None

    print(f"[review] 回顧最近 {days} 天（{len(relevant)} 筆紀錄）")
    all_results = []
    for rec in relevant:
        for p in rec["picks"]:
            last = fetch_close(p["ticker"])
            if last is None: continue
            ret = (last / p["rec_close"] - 1) * 100
            all_results.append({**p, "rec_date": rec["date"],
                                "current": last, "ret_pct": ret, "hit": ret > 0})
    return all_results


def analyze(results, label="期間"):
    """產生分析摘要"""
    if not results:
        return {"label": label, "n": 0, "summary": "無資料"}
    n = len(results)
    wins = [r for r in results if r["hit"]]
    losses = [r for r in results if not r["hit"]]
    avg = sum(r["ret_pct"] for r in results) / n
    wr = len(wins) / n
    avg_w = sum(r["ret_pct"] for r in wins)/len(wins) if wins else 0
    avg_l = sum(r["ret_pct"] for r in losses)/len(losses) if losses else 0
    pf = abs(sum(r["ret_pct"] for r in wins) /
             sum(r["ret_pct"] for r in losses)) if losses else float("inf")

    # 族群表現
    ind_perf = defaultdict(lambda: {"n": 0, "ret_sum": 0, "wins": 0})
    for r in results:
        ind = r.get("industry") or "未分類"
        ind_perf[ind]["n"] += 1
        ind_perf[ind]["ret_sum"] += r["ret_pct"]
        if r["hit"]: ind_perf[ind]["wins"] += 1
    industries = []
    for ind, d in ind_perf.items():
        industries.append({"industry": ind, "n": d["n"],
                           "win_rate": d["wins"]/d["n"],
                           "avg_ret": d["ret_sum"]/d["n"]})
    industries.sort(key=lambda x: -x["avg_ret"])

    # 訊號表現
    sig_perf = defaultdict(lambda: {"n": 0, "ret_sum": 0, "wins": 0})
    for r in results:
        for note in r.get("momentum_notes", []):
            sig_perf[note]["n"] += 1
            sig_perf[note]["ret_sum"] += r["ret_pct"]
            if r["hit"]: sig_perf[note]["wins"] += 1
    signals = []
    for sig, d in sig_perf.items():
        signals.append({"signal": sig, "n": d["n"],
                        "win_rate": d["wins"]/d["n"],
                        "avg_ret": d["ret_sum"]/d["n"]})
    signals.sort(key=lambda x: -x["win_rate"])

    # 最佳/最差
    best = max(results, key=lambda x: x["ret_pct"])
    worst = min(results, key=lambda x: x["ret_pct"])

    return {
        "label": label, "n": n, "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": wr, "avg_ret": avg,
        "avg_win": avg_w, "avg_loss": avg_l, "profit_factor": pf,
        "industry_performance": industries,
        "signal_performance": signals,
        "best": {"ticker": best["ticker"], "name": best["name"],
                 "ret_pct": best["ret_pct"], "industry": best.get("industry")},
        "worst": {"ticker": worst["ticker"], "name": worst["name"],
                  "ret_pct": worst["ret_pct"], "industry": worst.get("industry")},
    }


def generate_critique(daily, weekly):
    """根據績效產生策略檢討建議"""
    suggestions = []
    if not daily and not weekly:
        return ["無足夠資料產生建議"]

    # 1. 整體勝率
    if weekly and weekly["n"] >= 5:
        wr = weekly["win_rate"]
        if wr >= 0.6:
            suggestions.append(f"✅ 週勝率 {wr*100:.0f}% — 策略有效，維持當前濾網")
        elif wr >= 0.45:
            suggestions.append(f"🟡 週勝率 {wr*100:.0f}% — 中等，可考慮收緊條件")
        else:
            suggestions.append(f"🔴 週勝率 {wr*100:.0f}% — 警告：策略失靈，建議：")
            suggestions.append("    - 暫停進場 1 週觀察")
            suggestions.append("    - 檢查大盤體制（0050 是否跌破 MA200）")
            suggestions.append("    - 提高動能門檻 80 → 85")

    # 2. 獲利因子
    if weekly and weekly["n"] >= 10:
        pf = weekly["profit_factor"]
        if pf >= 3:
            suggestions.append(f"⭐ 獲利因子 {pf:.2f} 表現優異")
        elif pf < 1.5:
            suggestions.append(f"⚠️ 獲利因子 {pf:.2f} 偏低，盈虧比不平衡")

    # 3. 族群分析
    if weekly:
        # 找表現最差的族群
        bad_ind = [i for i in weekly["industry_performance"]
                   if i["n"] >= 2 and i["avg_ret"] < -5]
        if bad_ind:
            ind_names = ", ".join(i["industry"] for i in bad_ind[:3])
            suggestions.append(f"⚠️ 弱勢族群（建議排除）：{ind_names}")
        # 找表現最好的族群
        good_ind = [i for i in weekly["industry_performance"]
                    if i["n"] >= 2 and i["avg_ret"] > 10]
        if good_ind:
            ind_names = ", ".join(f"{i['industry']}({i['avg_ret']:+.1f}%)"
                                   for i in good_ind[:3])
            suggestions.append(f"💎 強勢族群（可加碼）：{ind_names}")

    # 4. 訊號分析
    if weekly:
        weak_sigs = [s for s in weekly["signal_performance"]
                     if s["n"] >= 3 and s["win_rate"] < 0.3]
        if weak_sigs:
            names = ", ".join(s["signal"] for s in weak_sigs[:3])
            suggestions.append(f"⚠️ 失效訊號（建議降低權重）：{names}")
        strong_sigs = [s for s in weekly["signal_performance"]
                       if s["n"] >= 3 and s["win_rate"] >= 0.7]
        if strong_sigs:
            names = ", ".join(s["signal"] for s in strong_sigs[:3])
            suggestions.append(f"⭐ 強效訊號（建議加重）：{names}")

    # 5. 昨日 vs 週的比較
    if daily and weekly and daily["n"] >= 3:
        if daily["avg_ret"] < -5 and weekly["avg_ret"] > 0:
            suggestions.append("⚠️ 昨日表現異常差但週均正向 — 可能單日震盪，繼續觀察")
        elif daily["avg_ret"] > 5 and weekly["avg_ret"] < 0:
            suggestions.append("🟢 昨日反彈但週累積負 — 策略開始恢復")

    return suggestions if suggestions else ["✅ 策略表現平穩，無特別建議"]


def build_review_block(daily, weekly, suggestions):
    """產生 LINE / dashboard 用的檢討區塊"""
    lines = ["📊 策略檢討（盤後）"]
    if daily:
        lines.append(f"  昨日（{daily.get('label')}）{daily['n']} 檔："
                     f"{daily['n_wins']}勝/{daily['n_losses']}敗"
                     f" ({daily['win_rate']*100:.0f}%)，平均 {daily['avg_ret']:+.2f}%")
    if weekly:
        lines.append(f"  過去一週 {weekly['n']} 檔："
                     f"{weekly['n_wins']}勝/{weekly['n_losses']}敗"
                     f" ({weekly['win_rate']*100:.0f}%)，平均 {weekly['avg_ret']:+.2f}%"
                     f"，PF {weekly['profit_factor']:.2f}")
        if weekly.get("best"):
            b = weekly["best"]
            lines.append(f"  🏆 週最佳：{b['ticker']} {b['name']} "
                         f"({b.get('industry','?')}) {b['ret_pct']:+.2f}%")
        if weekly.get("worst"):
            w = weekly["worst"]
            lines.append(f"  💀 週最差：{w['ticker']} {w['name']} "
                         f"({w.get('industry','?')}) {w['ret_pct']:+.2f}%")
    if suggestions:
        lines.append("  💡 建議：")
        for s in suggestions:
            lines.append(f"    {s}")
    return "\n".join(lines)


def main():
    h = load_history()
    recs = h.get("records", [])
    if not recs:
        print("無歷史紀錄")
        return

    # 昨日
    daily_results = review_period(recs, days=1)
    daily = analyze(daily_results, "昨日") if daily_results else None
    # 過去 7 天
    weekly_results = review_period(recs, days=7)
    weekly = analyze(weekly_results, "過去 7 日") if weekly_results else None

    suggestions = generate_critique(daily, weekly)
    msg = build_review_block(daily, weekly, suggestions)
    print(msg)

    out = {"timestamp": dt.datetime.now().isoformat(),
           "daily": daily, "weekly": weekly,
           "suggestions": suggestions}
    with open(WEEKLY_REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 已輸出 {WEEKLY_REVIEW_PATH}")


if __name__ == "__main__":
    main()
