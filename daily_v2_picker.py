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


def build_message(picks, top_inds, date):
    lines = [f"📡 {date[5:]} V2 推薦", ""]

    # 推薦族群
    if top_inds:
        ind_summary = " / ".join(
            f"{s['industry']}({s['bullish_count']}多頭)"
            for s in top_inds
        )
        lines.append(f"🔥 強族群: {ind_summary}")
    else:
        lines.append("😐 無強族群（多頭排列檔數不足）")
    lines.append("")

    # 個股
    if picks:
        lines.append(f"🎯 {len(picks)} 檔個股（V2 嚴選）：")
        for i, p in enumerate(picks, 1):
            price = p["today"]
            stop = round(price * 0.93, 1)
            tag_parts = []
            if p.get("long_red"): tag_parts.append("長紅")
            if p.get("gap_up"): tag_parts.append("跳空")
            tag = "/".join(tag_parts) if tag_parts else ""
            lines.append(f"  {i}. {p['ticker']} {p['name']} ({p['industry']})")
            lines.append(f"     進${price} 停${stop} 量{p['vol_ratio']:.1f}x RSI{p['rsi']:.0f} {tag}")
    else:
        lines.append("📭 今日無符合 V2 條件的個股")
        lines.append("（嚴格過濾：ATH+多頭+快多頭+量1.5x+長紅/跳空+RSI55-75+收高+市值100億）")

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
