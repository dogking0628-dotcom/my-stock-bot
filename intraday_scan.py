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
STATE_PATH = os.path.join(os.path.dirname(__file__), "intraday_state.json")


def load_state():
    """讀取當日已推送過的 ticker"""
    if not os.path.exists(STATE_PATH):
        return {"date": None, "pushed_tickers": []}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("date") != dt.date.today().isoformat():
            return {"date": dt.date.today().isoformat(), "pushed_tickers": []}
        return s
    except Exception:
        return {"date": dt.date.today().isoformat(), "pushed_tickers": []}


def save_state(state):
    state["date"] = dt.date.today().isoformat()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_market_scan():
    """跑一次 industry_ath_yf.py（會更新 ath_industry_report.json）"""
    print("[intraday] 啟動盤中掃描...")
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "industry_ath_yf.py")]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"[intraday] scan failed: {proc.stderr[:500]}")
        return False
    return True


def build_intraday_msg(state):
    """產出盤中 LINE 訊息：只推「新出現的」高機率股（去重）"""
    if not os.path.exists(REPORT_PATH):
        return None, []
    with open(REPORT_PATH, encoding="utf-8") as f:
        r = json.load(f)
    tomorrow = r.get("tomorrow_top5", [])
    high_n = r.get("high_prob_count", 0)
    exact = r.get("exact_ath", [])

    # 把盤中時間戳寫進 report 給 App 用
    now_iso = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    r["intraday_updated"] = now_iso
    r["is_intraday"] = True
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)

    pushed = set(state.get("pushed_tickers", []))
    new_top = [t for t in tomorrow if t["ticker"] not in pushed]
    new_codes = [t["ticker"] for t in new_top]

    today = dt.date.today().strftime("%Y-%m-%d")
    now_hm = dt.datetime.now().strftime("%H:%M")

    # 沒新訊號就不發（避免每 30 分都打擾）
    if not new_top:
        print(f"[intraday] {now_hm}: 無新訊號（已推 {len(pushed)} 檔，今日累計）")
        return None, []

    lines = [f"⏱ 盤中新訊號 {today} {now_hm}",
             "═" * 22,
             f"🌐 全市場創新高 {len(exact)} 檔｜⭐⭐⭐ 高機率 {high_n} 檔",
             f"🆕 本時段新出現高機率股：{len(new_top)} 檔",
             "─" * 22]
    for i, t in enumerate(new_top, 1):
        ind = t.get("industry") or "未分類"
        tier = t.get("tier", "⭐")
        score = t.get("momentum_score", 0)
        prob = t.get("next_day_prob", "")
        lines.append(f"  #{i} {tier} {t['ticker']} {t['name']}（{ind}）")
        lines.append(f"     {score}/100 {prob}｜${t.get('today',0):.1f} {t.get('change_pct',0):+.1f}%"
                     f" 量{t.get('vol_ratio',0):.1f}x")
        notes = "、".join(t.get("momentum_notes", [])[:3])
        if notes: lines.append(f"     📌 {notes}")
    lines.append("─" * 22)
    lines.append(f"📊 今日累計：{len(pushed) + len(new_top)} 檔高機率股")
    lines.append("⚠️ yfinance 延遲約 15 分鐘")
    return "\n".join(lines), new_codes


def main():
    if not run_market_scan():
        notify_line.push("⚠️ 盤中掃描失敗")
        return
    state = load_state()
    msg, new_codes = build_intraday_msg(state)
    if not msg:
        return
    print(msg)
    notify_line.push(msg)
    # 更新 state（把這次推的 ticker 加入）
    state["pushed_tickers"] = list(set(state.get("pushed_tickers", []) + new_codes))
    save_state(state)


if __name__ == "__main__":
    main()
