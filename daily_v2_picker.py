#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily V2 Picker — 每日 LINE 推播（V2 原版策略）
═════════════════════════════════════════════════
策略：
  1. ATH 池（從 ath_industry_report.json 讀 exact_ath）
  2. 族群選「多頭比例 + ATH 檔數」Top 2 強族群
  3. 該族群內挑「最強線型」：
     - 多頭排列 + 快速多頭
     - 量增 ≥ 1.5x
     - 收長紅 K 或跳空缺口
     - RSI 55-75
     - 收盤靠近當日高
     - 市值 ≥ 100 億

每日 5:30 cron（接在 industry_ath_yf.py 之後）
"""
import sys, io, os, json, datetime as dt
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(ROOT, "ath_industry_report.json")
SIGNAL_PATH = os.path.join(ROOT, "daily_v2_signal.json")

# ── 策略參數（與 backtest_hot_money_v2.py 一致）──
TOP_INDUSTRIES = 2
MIN_INDUSTRY_BULLISH = 5    # 族群多頭排列 ≥ 5 檔才視為強族群
VOL_RATIO_MIN = 1.5
RSI_MIN = 55
RSI_MAX = 75
MIN_MCAP = 100              # 億
MAX_PICKS = 5


def pick_v2_from_report(report):
    """從 ath_industry_report.json 用 V2 邏輯挑 5 檔"""
    exact = report.get("exact_ath", [])
    stats = report.get("industry_stats", [])

    # Step 1+2: 選 Top 2 強族群（按多頭排列檔數）
    qualified = [s for s in stats
                 if s.get("bullish_count", 0) >= MIN_INDUSTRY_BULLISH]
    qualified.sort(key=lambda x: -x["bullish_count"])
    top_inds = qualified[:TOP_INDUSTRIES]
    top_inds_set = {s["industry"] for s in top_inds}

    # Step 3: 從 exact_ath 過濾
    picks = []
    for r in exact:
        if r.get("industry") not in top_inds_set:
            continue
        if not r.get("bullish"):
            continue
        if not r.get("bullish_fast"):
            continue
        if r.get("vol_ratio", 0) < VOL_RATIO_MIN:
            continue
        if not (r.get("long_red") or r.get("gap_up")):
            continue
        rsi = r.get("rsi", 0)
        if rsi < RSI_MIN or rsi > RSI_MAX:
            continue
        if not r.get("close_near_high"):
            continue
        mc = r.get("market_cap_billions", 0)
        if mc < MIN_MCAP:
            continue
        # 綜合分
        score = (r.get("vol_ratio", 0) * 10 + rsi +
                 r.get("change_pct", 0) * 2)
        r["_score"] = round(score, 2)
        picks.append(r)

    picks.sort(key=lambda x: -x["_score"])
    return picks[:MAX_PICKS], top_inds


def build_message(picks, top_inds, date, active_capital=450_000):
    """訊息格式: 開盤掛單可執行版"""
    lines = [f"📡 {date[5:]} 開盤掛單", ""]

    if top_inds:
        ind_summary = " / ".join(
            f"{s['industry']}({s['bullish_count']}多頭)"
            for s in top_inds
        )
        lines.append(f"🔥 強族群: {ind_summary}")
    else:
        lines.append("😐 今日無強族群")
    lines.append("")

    if not picks:
        lines.append("📭 今日無 V2 訊號 → 空手")
        lines.append("（不要硬找，等明天）")
        return "\n".join(lines)

    # 部位分配：總主動 / 檔數 (1-3 檔)
    n_picks = min(len(picks), 3)
    per_stock = active_capital / max(n_picks, 1)

    lines.append(f"🎯 {n_picks} 檔開盤掛單（每檔 {per_stock/10000:.0f} 萬）：")
    lines.append("")
    for i, p in enumerate(picks[:n_picks], 1):
        price = p["today"]
        # 限價單建議：今日收盤 +0.8%（給跳空空間）
        limit_low = round(price * 1.008, 1)
        limit_high = round(price * 1.02, 1)
        stop = round(price * 0.93, 1)
        # 部位 → 股數 (千股單位)
        shares = int(per_stock / limit_low / 1000) * 1000
        if shares < 1000:
            shares = 1000
        actual_cost = shares * limit_low

        tag_parts = []
        if p.get("long_red"): tag_parts.append("長紅")
        if p.get("gap_up"): tag_parts.append("跳空")
        tag = "/".join(tag_parts) or ""

        lines.append(f"{i}. {p['ticker']} {p['name']} ({p['industry']})")
        lines.append(f"   📍 限價 ${limit_low}-${limit_high}")
        lines.append(f"   💰 {shares}股 ≈ ${actual_cost:,.0f}")
        lines.append(f"   🛑 停損 ${stop} (-7%)")
        lines.append(f"   📊 量{p['vol_ratio']:.1f}x RSI{p['rsi']:.0f} {tag}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 操作:")
    lines.append("  9:00前掛限價低點")
    lines.append("  9:05沒成交→改限價高點")
    lines.append("  9:10仍無→放棄")
    lines.append("  跳空+3%以上→不追")

    return "\n".join(lines)


def main():
    if not os.path.exists(REPORT_PATH):
        print(f"❌ 找不到 {REPORT_PATH}")
        sys.exit(1)

    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)

    date = report.get("timestamp", dt.date.today().isoformat())
    print(f"[1/3] 載入 ath_industry_report ({date})")
    print(f"      exact_ath: {len(report.get('exact_ath', []))} 檔")
    print(f"      industry_stats: {len(report.get('industry_stats', []))} 族群")

    picks, top_inds = pick_v2_from_report(report)
    print(f"[2/3] V2 過濾：")
    print(f"      🔥 強族群 Top {len(top_inds)}: {', '.join(s['industry'] for s in top_inds)}")
    print(f"      🎯 過濾後個股: {len(picks)} 檔")

    # 輸出 signal JSON
    signal = {
        "timestamp": date,
        "strategy": "V2 原版 (MIN_MCAP=100, OOS PF 2.29 / MDD -9.7%)",
        "top_industries": top_inds,
        "picks": picks,
        "params": {
            "top_industries": TOP_INDUSTRIES,
            "vol_ratio_min": VOL_RATIO_MIN,
            "rsi_min": RSI_MIN, "rsi_max": RSI_MAX,
            "min_mcap": MIN_MCAP,
        }
    }
    with open(SIGNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=2)
    print(f"[3/3] 已輸出 {SIGNAL_PATH}")

    msg = build_message(picks, top_inds, date)
    print("\n" + "=" * 60)
    print("LINE 訊息預覽：")
    print("=" * 60)
    print(msg)
    print("=" * 60)

    # 推 LINE
    try:
        import notify_line
        ok = notify_line.push(msg)
        print(f"\nLINE: {'✅' if ok else '❌'}")
    except Exception as e:
        print(f"\n⚠️ LINE 推播錯誤: {e}")


if __name__ == "__main__":
    main()
