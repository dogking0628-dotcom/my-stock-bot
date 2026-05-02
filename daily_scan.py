#!/usr/bin/env python3
"""
雲端版每日掃描 — 用 yfinance 取代 moomoo
─────────────────────────────────────────
邏輯：
  進場：當日收盤 > 過去所有歷史最高
  止損：從持倉峰值 -20%
  最多 10 檔分散

執行頻率：每日美股收盤後（GitHub Actions cron 21:30 UTC = 04:30 VN）
狀態：state.json 在 repo 內，每次執行後 commit 回去
"""
import os, sys, json, datetime as dt
import yfinance as yf
from config import UNIVERSE, MAX_SLOTS, STOP_PCT, INITIAL_CASH
import notify_line

STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")

# ── 狀態載入/儲存 ────────────────────────
def load_state():
    if not os.path.exists(STATE_PATH):
        return {"last_run": None, "cash": INITIAL_CASH, "positions": [], "running_ath": {}}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

# ── yfinance 抓收盤 ──────────────────────
def fetch_data(tickers, period="2y"):
    """回傳 {ticker: [(date_str, close)]}"""
    print(f"Fetching {len(tickers)} tickers from yfinance...")
    data = yf.download(" ".join(tickers), period=period, group_by="ticker",
                       auto_adjust=True, progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            df = data[t] if len(tickers) > 1 else data
            df = df.dropna(subset=["Close"])
            out[t] = [(d.strftime("%Y-%m-%d"), float(c)) for d, c in df["Close"].items()]
        except Exception as e:
            print(f"  {t}: ERROR {e}")
    return out

# ── 掃描邏輯 ─────────────────────────────
def scan(state, hist):
    """
    hist: {ticker: [(date, close)]}, 已排序
    回傳 (sells, buys, holds)
    """
    today = max((s[-1][0] for s in hist.values() if s))
    held = {p["ticker"]: p for p in state["positions"]}
    sells, buys, holds = [], [], []

    # ① 持倉檢查
    for tk, p in held.items():
        if tk not in hist or not hist[tk]:
            continue
        last_close = hist[tk][-1][1]
        new_peak = max(p["peak_price"], last_close)
        change_pct = (last_close / p["entry_price"] - 1) * 100
        peak_dd = (last_close / new_peak - 1) * 100
        rec = {**p, "current": last_close, "new_peak": new_peak,
               "change_pct": change_pct, "peak_dd": peak_dd}
        if last_close <= new_peak * (1 - STOP_PCT):
            sells.append(rec)
        else:
            holds.append(rec)

    # ② 候選 ATH（未持倉、今日收盤 > 截至昨日的最高）
    for tk in UNIVERSE:
        if tk in held: continue
        series = hist.get(tk, [])
        if len(series) < 2: continue
        prev_max = max(c for _, c in series[:-1])
        last_close = series[-1][1]
        if last_close > prev_max:
            buys.append({
                "ticker": tk,
                "last": last_close,
                "prev_ath": prev_max,
                "breakout_pct": (last_close/prev_max - 1) * 100,
            })
    buys.sort(key=lambda x: -x["breakout_pct"])
    return sells, buys, holds, today

# ── 自動更新狀態 ─────────────────────────
def commit_actions(state, sells, buys, holds, today):
    # 賣出
    for r in sells:
        proceeds = r["shares"] * r["current"]
        state["cash"] += proceeds
        state["positions"] = [p for p in state["positions"] if p["ticker"] != r["ticker"]]
    # 更新留存持倉峰值
    held_map = {p["ticker"]: p for p in state["positions"]}
    for h in holds:
        if h["ticker"] in held_map:
            held_map[h["ticker"]]["peak_price"] = h["new_peak"]
    # 買入
    n_to_buy = min(len(buys), MAX_SLOTS - len(state["positions"]))
    if n_to_buy > 0 and state["cash"] > 100:
        # 每筆 = 組合總值/MAX_SLOTS（10%目標）
        total_eq = state["cash"] + sum(p["shares"] * h["current"]
                                       for p in state["positions"]
                                       for h in holds if h["ticker"] == p["ticker"])
        target = total_eq / MAX_SLOTS
        rem = n_to_buy
        for r in buys[:n_to_buy]:
            cash_for = min(state["cash"]/rem if rem else 0, target)
            if cash_for < 100:
                rem -= 1; continue
            shares = cash_for / r["last"]
            state["positions"].append({
                "ticker": r["ticker"],
                "entry_date": today,
                "entry_price": round(r["last"], 4),
                "peak_price":  round(r["last"], 4),
                "shares":      round(shares, 6),
            })
            state["cash"] -= shares * r["last"]
            rem -= 1
    state["last_run"] = today

# ── LINE 訊息 ────────────────────────────
def build_msg(state, sells, buys, holds, today, n_buy):
    cash_after = state["cash"] + sum(r["shares"] * r["current"] for r in sells)
    cash_per = cash_after / n_buy if n_buy else 0
    holdings_val = sum(h["shares"] * h["current"] for h in holds)
    total_val = state["cash"] + holdings_val + sum(r["shares"]*r["current"] for r in sells)

    lines = [f"📊 策略A 雲端掃描 {today}", "═" * 18]

    if not sells and n_buy == 0:
        lines.append("✅ 今日無動作（無止損觸發、無新高訊號）")
    else:
        lines.append("【今日下單清單】")
        if sells:
            lines.append("")
            lines.append(f"🔴 賣出 {len(sells)} 檔：")
            for r in sells:
                lines.append(f"  • SELL {r['ticker']} {r['shares']:.2f}股 @${r['current']:.2f}")
                lines.append(f"    損益 {r['change_pct']:+.1f}% (從峰值 -{abs(r['peak_dd']):.1f}%)")
        if n_buy > 0:
            lines.append("")
            lines.append(f"🟢 買入 {n_buy} 檔（每檔 ≈${cash_per:,.0f}）：")
            for r in buys[:n_buy]:
                shares = cash_per / r["last"]
                lines.append(f"  • BUY  {r['ticker']} {shares:.2f}股 @${r['last']:.2f}")
                lines.append(f"    突破前ATH +{r['breakout_pct']:.1f}%")

    lines.append("")
    lines.append("─" * 18)
    lines.append(f"💼 組合總值 ${total_val:,.0f}")
    lines.append(f"持倉 {len(state['positions'])}/{MAX_SLOTS}  現金 ${state['cash']:,.0f}")

    if holds:
        holds_sorted = sorted(holds, key=lambda h: -h["shares"]*h["current"])
        lines.append("")
        lines.append(f"📌 持有 {len(holds)} 檔：")
        for r in holds_sorted:
            mkt = r["shares"] * r["current"]
            wt = mkt / total_val * 100 if total_val else 0
            sign = "+" if r["change_pct"] >= 0 else ""
            lines.append(f"  {r['ticker']:<5} {wt:>4.1f}%  ${mkt:>6,.0f}  {sign}{r['change_pct']:.1f}%  止損${r['new_peak']*(1-STOP_PCT):.2f}")

    lines.append("")
    lines.append("👉 收盤前下 MOC（moomoo）或限價單（Firstrade）")
    return "\n".join(lines)

# ── Main ─────────────────────────────────
def main():
    state = load_state()
    hist = fetch_data(UNIVERSE)
    if not hist:
        notify_line.push("❌ 雲端掃描：無法取得 yfinance 資料")
        return

    sells, buys, holds, today = scan(state, hist)
    n_buy = min(len(buys), MAX_SLOTS - len(state["positions"]) + len(sells))
    msg = build_msg(state, sells, buys, holds, today, n_buy)
    print(msg)
    notify_line.push(msg)

    # 自動 commit
    commit_actions(state, sells, buys, holds, today)
    save_state(state)
    print(f"\n[STATE] saved {STATE_PATH}")

if __name__ == "__main__":
    main()
