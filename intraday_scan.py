# -*- coding: utf-8 -*-
"""
盤中掃描（簡單版）：
  1. 跑 industry_ath_yf.py 取得當下即時 ATH + 動能分數（yfinance 延遲約 15 分鐘）
  2. 推 LINE：只推「⭐⭐⭐ 明日高機率 Top 5」+「跌破 20MA 出場」
  3. 不重複每日整套訊息（簡化）
"""
import sys, io, os, json, datetime as dt, subprocess
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

import notify_line

REPORT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")


def run_market_scan():
    """跑一次 industry_ath_yf.py（會更新 ath_industry_report.json）"""
    print("[intraday] 啟動盤中掃描...")
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "industry_ath_yf.py")]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"[intraday] scan failed: {proc.stderr[:500]}")
        return False
    return True


def build_intraday_msg():
    if not os.path.exists(REPORT_PATH):
        return None
    with open(REPORT_PATH, encoding="utf-8") as f:
        r = json.load(f)
    tomorrow = r.get("tomorrow_top5", [])
    high_n = r.get("high_prob_count", 0)
    exact = r.get("exact_ath", [])
    industry_stats = r.get("industry_stats", [])

    now = dt.datetime.now().strftime("%H:%M")
    today = dt.date.today().strftime("%Y-%m-%d")

    lines = [f"⏱ 盤中掃描 {today} {now}",
             "═" * 22,
             f"🌐 全市場創 2y 月線新高：{len(exact)} 檔",
             f"⭐⭐⭐ 高機率股（≥80 分）：{high_n} 檔"]

    # 強勢族群 Top 3
    classified = sorted(
        [s for s in industry_stats if s["industry"] != "未分類"],
        key=lambda x: -x["count"])
    if classified:
        lines.append("─" * 22)
        lines.append("🏆 強勢族群 Top 3：")
        for s in classified[:3]:
            lines.append(f"  • {s['industry']} {s['count']} 檔（多頭 {s['bullish_count']}）")

    # 明日高機率 Top 5
    if tomorrow:
        lines.append("─" * 22)
        lines.append("⭐⭐⭐ 明日續漲 Top 5")
        for i, t in enumerate(tomorrow, 1):
            ind = t.get("industry") or "未分類"
            tier = t.get("tier", "⭐")
            score = t.get("momentum_score", 0)
            prob = t.get("next_day_prob", "")
            lines.append(f"  #{i} {tier} {t['ticker']} {t['name']}（{ind}）")
            lines.append(f"     {score}/100 {prob}｜${t.get('today',0):.1f} {t.get('change_pct',0):+.1f}%"
                         f" 量{t.get('vol_ratio',0):.1f}x")
            notes = "、".join(t.get("momentum_notes", [])[:3])
            if notes: lines.append(f"     📌 {notes}")
    else:
        lines.append("─" * 22)
        lines.append("⏸ 目前無高機率股（盤中持續觀察）")

    lines.append("─" * 22)
    lines.append("⚠️ yfinance 延遲約 15 分鐘，請以即時報價為準")
    return "\n".join(lines)


def main():
    if not run_market_scan():
        notify_line.push("⚠️ 盤中掃描失敗")
        return
    msg = build_intraday_msg()
    if not msg:
        print("[intraday] no report generated")
        return
    print(msg)
    notify_line.push(msg)


if __name__ == "__main__":
    main()
