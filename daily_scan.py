#!/usr/bin/env python3
"""
雲端版每日掃描 — 美股ATH策略 + 台股0050策略
─────────────────────────────────────────────
美股：ATH突破進場 + 20%移動止損（最多10檔）
台股：MA200(5日確認) + 熊市三段加碼 + 超漲40%減倉

執行頻率：每日美股收盤後（GitHub Actions cron 21:30 UTC）
狀態：state.json / tw_state.json，每次執行後 commit 回去
"""
import os, sys, json, datetime as dt
import yfinance as yf
from config import UNIVERSE, MAX_SLOTS, STOP_PCT, INITIAL_CASH
import notify_line
import tw_0050_signal
import tw_breakout_filter

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

# ── 美股 LINE 訊息區塊 ─────────────────────
def build_us_block(state, sells, buys, holds, today, n_buy):
    cash_after   = state["cash"] + sum(r["shares"] * r["current"] for r in sells)
    cash_per     = cash_after / n_buy if n_buy else 0
    holdings_val = sum(h["shares"] * h["current"] for h in holds)
    total_val    = state["cash"] + holdings_val + sum(r["shares"]*r["current"] for r in sells)

    lines = ["🇺🇸 美股策略A（ATH突破）"]

    if not sells and n_buy == 0:
        lines.append("  ⏸ 今日不動（無止損、無新ATH突破）")
    else:
        if sells:
            lines.append(f"  🔴 賣出 {len(sells)} 檔：")
            for r in sells:
                lines.append(f"    SELL {r['ticker']} {r['shares']:.2f}股 @${r['current']:.2f}"
                              f"  損益{r['change_pct']:+.1f}%")
        if n_buy > 0:
            lines.append(f"  🟢 買入 {n_buy} 檔（每檔 ≈${cash_per:,.0f}）：")
            for r in buys[:n_buy]:
                shares = cash_per / r["last"]
                lines.append(f"    BUY {r['ticker']} {shares:.2f}股 @${r['last']:.2f}"
                              f"  +{r['breakout_pct']:.1f}%ATH")

    lines.append(f"  💼 總值 ${total_val:,.0f}  持倉{len(state['positions'])}/{MAX_SLOTS}"
                 f"  現金${state['cash']:,.0f}")

    if holds:
        holds_sorted = sorted(holds, key=lambda h: -h["shares"]*h["current"])[:5]
        lines.append("  持有（前5）：" + "  ".join(
            f"{h['ticker']}{h['change_pct']:+.0f}%" for h in holds_sorted
        ) + ("  ..." if len(holds) > 5 else ""))

    return "\n".join(lines)

# ── 合併訊息 ──────────────────────────────
def build_combined_msg(us_block, tw_result, tw_breakout_block, watchlist_block, today):
    tw_block    = tw_0050_signal.build_line_block(tw_result)
    has_us_act  = ("SELL" in us_block or "BUY" in us_block)
    has_tw_act  = tw_result.get("action", "HOLD") != "HOLD"
    has_tw_high = "🟢 高機率" in tw_breakout_block
    has_watch_alert = "🚨 進場！" in watchlist_block
    has_any_act = has_us_act or has_tw_act or has_tw_high or has_watch_alert

    header = f"📊 投資策略日報 {today}"
    if has_any_act:
        header = f"🚨 投資訊號觸發 {today}"

    return "\n".join([
        header,
        "═" * 22,
        us_block,
        "─" * 22,
        tw_block,
        "─" * 22,
        tw_breakout_block,
        "─" * 22,
        watchlist_block,
        "─" * 22,
        "👉 建議收盤前執行，moomoo/Firstrade",
    ])

# ── Main ─────────────────────────────────
def main():
    today = dt.date.today().strftime("%Y-%m-%d")

    # ── 美股掃描 ──────────────────────────
    state = load_state()
    hist  = fetch_data(UNIVERSE)
    if not hist:
        notify_line.push("❌ 雲端掃描：yfinance 資料取得失敗")
        return
    sells, buys, holds, today_data = scan(state, hist)
    n_buy   = min(len(buys), MAX_SLOTS - len(state["positions"]) + len(sells))
    us_block= build_us_block(state, sells, buys, holds, today_data, n_buy)

    # ── 台股訊號 ──────────────────────────
    print("Checking TW 0050 signal...")
    tw_result = tw_0050_signal.check()

    # ── 台股突破篩選（含統計濾網）──────────
    print("Scanning TW breakout candidates...")
    tw_breakout_results = tw_breakout_filter.scan_all()
    tw_breakout_block = tw_breakout_filter.build_line_block(tw_breakout_results)

    # ── 個人觀察清單（5 檔追蹤）──────────
    print("Tracking personal watchlist...")
    watchlist = tw_breakout_filter.scan_watchlist()
    watchlist_block = tw_breakout_filter.build_watchlist_block(watchlist)

    # ── 合併推播 ──────────────────────────
    msg = build_combined_msg(us_block, tw_result, tw_breakout_block, watchlist_block, today)
    print(msg)
    notify_line.push(msg)

    # ── 更新美股狀態 ──────────────────────
    commit_actions(state, sells, buys, holds, today_data)
    save_state(state)
    print(f"\n[STATE] US saved. TW regime={tw_result['regime']} alloc={tw_result['allocation']:.0%}")

if __name__ == "__main__":
    main()
