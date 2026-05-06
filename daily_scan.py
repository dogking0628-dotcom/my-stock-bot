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
from config import UNIVERSE as DEFAULT_UNIVERSE, MAX_SLOTS, STOP_PCT, INITIAL_CASH
import notify_line
import tw_0050_signal
import tw_breakout_filter
import universe_loader
import market_regime_alert

# 動態抓 S&P 500 作為美股池（如果失敗 fallback 到 config 的 30 檔）
try:
    UNIVERSE = universe_loader.fetch_sp500() or DEFAULT_UNIVERSE
    print(f"[universe] US: {len(UNIVERSE)} 檔（S&P 500 動態載入）")
except Exception as e:
    UNIVERSE = DEFAULT_UNIVERSE
    print(f"[universe] US: fallback to {len(UNIVERSE)} 檔")

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

# ── yfinance 抓收盤（靜音下市股錯誤）──────
def fetch_data(tickers, period="2y"):
    """回傳 {ticker: [(date_str, close)]} — 分批 50 檔下載避免 yfinance batch 失敗"""
    import contextlib, io as _io, logging, time
    print(f"Fetching {len(tickers)} tickers from yfinance (batched)...")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    BATCH = 50
    out = {}
    skipped = 0
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i+BATCH]
        try:
            with contextlib.redirect_stderr(_io.StringIO()):
                data = yf.download(" ".join(batch), period=period, group_by="ticker",
                                   auto_adjust=True, progress=False, threads=True)
        except Exception as e:
            print(f"  batch {i} fail: {type(e).__name__}")
            skipped += len(batch)
            time.sleep(2)
            continue
        for t in batch:
            try:
                if len(batch) == 1:
                    df = data
                else:
                    if t not in data.columns.get_level_values(0):
                        skipped += 1; continue
                    df = data[t]
                df = df.dropna(subset=["Close"])
                if df.empty:
                    skipped += 1; continue
                out[t] = [(d.strftime("%Y-%m-%d"), float(c)) for d, c in df["Close"].items()]
            except Exception:
                skipped += 1
        time.sleep(0.8)  # 避免 rate limit
    print(f"  成功 {len(out)} 檔，跳過 {skipped} 檔")
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

# ── 全市場族群統計（讀 industry_ath_yf.py 產的報表）──
def build_industry_block():
    """讀 ath_industry_report.json，產出全市場創新高族群統計 LINE 區塊"""
    path = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")
    if not os.path.exists(path):
        return ""  # 報表不存在直接跳過
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
    except Exception:
        return ""
    exact = r.get("exact_ath", [])
    industry_stats = r.get("industry_stats", [])
    top_ind = r.get("top_industry")
    if not exact:
        return ""
    lines = ["🌐 全市場創 2y 月線新高",
             f"  共 {len(exact)} 檔創新高"]
    classified = [s for s in industry_stats if s["industry"] != "未分類"]
    classified.sort(key=lambda x: -x["count"])
    if classified:
        top3 = classified[:3]
        lines.append(f"  🏆 強勢族群 Top 3：")
        for s in top3:
            lines.append(f"    • {s['industry']} {s['count']} 檔（多頭 {s['bullish_count']}）")
    # 🆕 明日續漲高機率 Top 5
    tomorrow = r.get("tomorrow_top5", [])
    high_n = r.get("high_prob_count", 0)
    if tomorrow:
        lines.append("")
        lines.append(f"⭐⭐⭐ 明日續漲高機率 Top 5（{high_n} 檔 ≥85%）")
        for i, t in enumerate(tomorrow, 1):
            ind = t.get("industry") or "未分類"
            lines.append(f"  #{i} {t['ticker']} {t['name']} ({ind})"
                         f" {t.get('tier','⭐')} {t.get('momentum_score',0)}/100"
                         f" {t.get('next_day_prob','')}")
            notes = "、".join(t.get("momentum_notes", [])[:3])
            if notes:
                lines.append(f"     {notes}")
    return "\n".join(lines)


# ── 合併訊息 ──────────────────────────────
def build_combined_msg(us_block, tw_result, tw_breakout_block, watchlist_block, regime_block, today, industry_block="", exit_block=""):
    tw_block    = tw_0050_signal.build_line_block(tw_result)
    has_us_act  = ("SELL" in us_block or "BUY" in us_block)
    has_tw_act  = tw_result.get("action", "HOLD") != "HOLD"
    has_tw_high = "🟢 高機率" in tw_breakout_block
    has_watch_alert = "🚨 進場！" in watchlist_block
    has_any_act = has_us_act or has_tw_act or has_tw_high or has_watch_alert

    header = f"📊 投資策略日報 {today}"
    if has_any_act:
        header = f"🚨 投資訊號觸發 {today}"

    parts = [header, "═" * 22, regime_block, "─" * 22,
             us_block, "─" * 22, tw_block, "─" * 22,
             tw_breakout_block, "─" * 22, watchlist_block]
    if exit_block:
        parts += ["─" * 22, exit_block]
    if industry_block:
        parts += ["─" * 22, industry_block]
    parts += ["─" * 22, "👉 建議收盤前執行，moomoo/Firstrade"]
    return "\n".join(parts)

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

    # ── 大盤體制警報（SPY/0050 vs MA200）──
    print("Checking market regime...")
    regime_block = market_regime_alert.build_line_block()

    # ── 動態 Top 5 推薦 + Top 20 候選 + 假突破警報 ──
    print("Tracking dynamic Top 5 + Top 20 candidates...")
    today_top5, dropped_warnings, today_warnings, today_top20, exit_signals = \
        tw_breakout_filter.update_and_track_top5(tw_breakout_results)
    # 計算推薦族群（傳給 LINE 顯示）
    _industry_groups = tw_breakout_filter.group_by_industry(today_top20)
    _recommended_industry = tw_breakout_filter.recommend_industry(_industry_groups)
    watchlist_block = tw_breakout_filter.build_top5_block(
        today_top5, dropped_warnings, today_warnings,
        top20=today_top20, recommended_industry=_recommended_industry)

    # ── 出場訊號（跌破 20MA）──
    exit_block = ""
    if exit_signals:
        lines = ["🚨 跌破 20MA 出場訊號"]
        for e in exit_signals[:5]:
            lines.append(f"  ❌ {e['ticker']} {e['name']} ${e['current_close']:.1f}"
                         f"（20MA ${e['ma20']:.1f}，{e['drop_pct']:+.1f}%）")
        exit_block = "\n".join(lines)

    # ── 全市場族群統計（須先跑完 industry_ath_yf.py）──
    industry_block = build_industry_block()

    # ── 昨日 Top 5 回顧 + 改進建議 ──
    review_block = ""
    try:
        import daily_review
        analysis = daily_review.analyze_review(daily_review.review_yesterday())
        if analysis:
            review_block = daily_review.build_review_block(analysis)
            with open(os.path.join(os.path.dirname(__file__), "daily_review.json"),
                      "w", encoding="utf-8") as f:
                json.dump(analysis, f, ensure_ascii=False, indent=2)
        # 紀錄今日（供明日回顧）
        daily_review.record_today_top5()
    except Exception as e:
        print(f"[review] failed: {e}")

    # ── 合併推播 ──────────────────────────
    msg = build_combined_msg(us_block, tw_result, tw_breakout_block, watchlist_block, regime_block, today, industry_block, exit_block)
    print(msg)
    notify_line.push(msg)

    # ── 寫 dashboard_data.json 給 Streamlit App 讀（保證一致）──
    try:
        # market regime 細節
        spy_r = market_regime_alert.check_regime("SPY", "🇺🇸 美股 SPY")
        tw_r  = market_regime_alert.check_regime("0050.TW", "🇹🇼 台股 0050")

        def slim(stock):
            """挑出 dashboard 需要欄位（避免 JSON 過大）"""
            keys = ("ticker","name","close","change","vol_ratio","rsi","ma5","ma20","ma60","ma120","ma200",
                    "bull_strength","is_ath","is_bullish","category","score","monthly_ath_5y","industry")
            return {k: stock.get(k) for k in keys if k in stock}

        # 族群分組與推薦（重用上面的計算）
        industry_summary = [
            {"industry": k,
             "count": v["count"],
             "avg_score": round(v["avg_score"], 1),
             "strength": round(v["strength"], 1),
             "stocks": [slim(s) for s in v["stocks"]]}
            for k, v in _industry_groups
        ]
        recommended_industry = _recommended_industry

        dashboard = {
            "timestamp": today,
            "regime": {"spy": spy_r, "tw0050": tw_r},
            "tw_top5": [slim(s) for s in today_top5],          # 推薦 5 檔（LINE 也用這個）
            "tw_top20_candidates": [slim(s) for s in today_top20],  # 候選 20 檔
            "tw_industry_groups": industry_summary,             # 族群分組
            "tw_recommended_industry": recommended_industry,    # 推薦族群
            "tw_dropped_warnings": dropped_warnings,
            "tw_today_warnings": today_warnings,
            "tw_exit_signals": exit_signals,                  # 🚨 跌破 20MA 出場

            "tw_breakout": {
                cat: [slim(s) for s in stocks]
                for cat, stocks in tw_breakout_results.items()
            },
            "tw_0050_signal": tw_result,
            "us_buys":  [{"ticker":b["ticker"], "last":b["last"],
                          "breakout_pct":b["breakout_pct"]} for b in buys[:n_buy]],
            "us_sells": [{"ticker":s["ticker"], "shares":s["shares"], "current":s["current"],
                          "change_pct":s["change_pct"]} for s in sells],
            "us_holds": [{"ticker":h["ticker"], "shares":h["shares"], "current":h["current"],
                          "change_pct":h["change_pct"]} for h in holds[:10]],
            "us_state": {"cash": state["cash"], "n_positions": len(state["positions"]),
                         "max_slots": MAX_SLOTS},
        }
        # 全市場族群統計（從 ath_industry_report.json 帶進來）
        try:
            ath_path = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")
            if os.path.exists(ath_path):
                with open(ath_path, encoding="utf-8") as f:
                    dashboard["tw_market_industry"] = json.load(f)
                market = dashboard["tw_market_industry"]
                # ── Fallback 1: Top 5 空時從全市場 154 檔挑（多頭排列 + 距高最近前 5）──
                if not dashboard["tw_top5"]:
                    cands = [r for r in market.get("exact_ath", []) if r.get("bullish")]
                    cands.sort(key=lambda x: -x["ratio"])
                    fallback5 = cands[:5]
                    # 補上 dashboard 需要的欄位（與 slim 對齊）
                    for r in fallback5:
                        r["close"] = r["today"]; r["change"] = 0
                        r["score"] = 0; r["category"] = "market_ath"
                        r["ma5"] = r["ma20"] = r["ma60"] = r["ma120"] = r["ma200"] = r["today"]
                        r["bull_strength"] = r["from_high_pct"]
                        r["is_ath"] = True; r["is_bullish"] = r.get("bullish", False)
                        r["vol_ratio"] = 0; r["rsi"] = 0
                    dashboard["tw_top5"] = fallback5
                    dashboard["tw_top20_candidates"] = cands[:20]
                    dashboard["tw_top5_fallback"] = True  # 標示為 fallback 來源
                # ── Fallback 2: 推薦族群空時，用全市場族群 stats ──
                if not dashboard["tw_recommended_industry"]:
                    stats = market.get("industry_stats", [])
                    classified = [s for s in stats if s["industry"] not in ("未分類", None)]
                    if classified:
                        top = classified[0]
                        dashboard["tw_recommended_industry"] = {
                            "industry": top["industry"],
                            "count": top["count"],
                            "avg_score": 0,
                            "strength": top["count"] * 10,
                            "top_stocks": [r for r in market.get("exact_ath", [])
                                           if r.get("industry") == top["industry"]][:5],
                            "source": "market_scan",
                        }
        except Exception as e:
            print(f"[dashboard] industry merge failed: {e}")
        dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard_data.json")
        with open(dashboard_path, "w", encoding="utf-8") as f:
            json.dump(dashboard, f, ensure_ascii=False, indent=2, default=str)
        print(f"[dashboard] saved {dashboard_path}")
    except Exception as e:
        print(f"[dashboard] save failed: {e}")

    # ── 更新美股狀態 ──────────────────────
    commit_actions(state, sells, buys, holds, today_data)
    save_state(state)
    print(f"\n[STATE] US saved. TW regime={tw_result['regime']} alloc={tw_result['allocation']:.0%}")

if __name__ == "__main__":
    main()
