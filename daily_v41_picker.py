#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily V4.1 Picker — 每日 LINE 推播（V4.1 策略，與 V2 並行）
═════════════════════════════════════════════════
V4.1 邏輯（在 industry_ath_yf.py 算好，這裡只讀 tomorrow_top5 推播）：
  ① 創 2y 月線 ATH  ② 多頭排列  ③ 科技 7 族群  ④ 市值 ≥ 100 億
  ⑤ 動能評分 ≥ 80   ⑥ 美股族群加分  ⑦ 0050 > MA200 才進場
  ⑧ 最強族群挑 5    ⑨ 7 日內虧損股黑名單
出場：跌破 20MA / 從峰值 -30%

每日 cron（接在 industry_ath_yf.py 之後，與 daily_v2_picker.py 並行）
"""
import sys, io, os, json, datetime as dt
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(ROOT, "ath_industry_report.json")
SIGNAL_PATH = os.path.join(ROOT, "daily_v41_signal.json")

ACTIVE_CAPITAL = 450_000
MAX_PUSH = 3   # LINE 推前 3 檔（與 V2 一致，方便並行比較）


def build_message(picks, strongest, regime, blocked, date):
    lines = [f"🎯 V4.1 開盤掛單 {date[5:]}", ""]

    # 大盤體制
    if regime:
        ext = regime.get("ext_pct", 0)
        stage = "Stage 2 多頭" if regime.get("in_stage2") else "Stage 4 空手"
        lines.append(f"📊 0050 距MA200 {ext:+.0f}% ({stage})")

    if blocked:
        lines.append("")
        lines.append("⛔ 0050 跌破 MA200 → V4.1 today 空手")
        lines.append("（熊市段，嚴禁追價）")
        return "\n".join(lines)

    if strongest:
        lines.append(f"🏆 最強族群: {strongest}")
    lines.append("")

    if not picks:
        lines.append("📭 今日無 V4.1 訊號（動能<80 或黑名單）→ 空手")
        return "\n".join(lines)

    n = min(len(picks), MAX_PUSH)
    per = ACTIVE_CAPITAL / max(n, 1)
    lines.append(f"🎯 {n} 檔開盤掛單（每檔 {per/10000:.0f} 萬）：")
    lines.append("")
    for i, p in enumerate(picks[:n], 1):
        price = p["today"]
        limit_low = round(price * 1.008, 1)
        limit_high = round(price * 1.02, 1)
        ma20 = p.get("ma20", price * 0.95)
        shares = int(per / limit_low / 1000) * 1000
        if shares < 1000:
            shares = 1000
        cost = shares * limit_low
        notes = "、".join(p.get("momentum_notes", [])[:3])
        lines.append(f"{i}. {p['ticker']} {p['name']} ({p['industry']})")
        lines.append(f"   {p.get('tier','⭐')} {p.get('momentum_score',0)}分"
                     f" {p.get('next_day_prob','')}")
        lines.append(f"   📍 限價 ${limit_low}-${limit_high}")
        lines.append(f"   💰 {shares}股 ≈ ${cost:,.0f}")
        lines.append(f"   🛑 停損 跌破20MA ${ma20:.1f}")
        lines.append(f"   📊 量{p.get('vol_ratio',0):.1f}x RSI{p.get('rsi',0):.0f}"
                     f" {notes}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 操作:")
    lines.append("  9:00前掛限價低點")
    lines.append("  9:05沒成交→改限價高點")
    lines.append("  9:10仍無→放棄")
    lines.append("  出場: 跌破20MA 隔日開盤賣")
    return "\n".join(lines)


def main():
    if not os.path.exists(REPORT_PATH):
        print(f"❌ 找不到 {REPORT_PATH}")
        sys.exit(1)
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)

    date = report.get("timestamp", dt.date.today().isoformat())
    picks = report.get("tomorrow_top5", [])
    strongest = report.get("tomorrow_top5_industry")
    regime = report.get("market_regime")
    blocked = report.get("v4_blocked", False)

    print(f"[V4.1] 載入 ath_industry_report ({date})")
    print(f"       tomorrow_top5: {len(picks)} 檔 / 最強族群: {strongest}")
    print(f"       0050 體制: {'空手' if blocked else '可進場'}")

    signal = {
        "timestamp": date,
        "strategy": "V4.1 (科技7族群+市值100億+美股加分+0050體制+7日黑名單)",
        "strongest_industry": strongest,
        "v4_blocked": blocked,
        "picks": picks[:MAX_PUSH],
    }
    with open(SIGNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=2)

    msg = build_message(picks, strongest, regime, blocked, date)
    print("\n" + "=" * 60)
    print("LINE 訊息預覽：")
    print("=" * 60)
    print(msg)
    print("=" * 60)

    try:
        import notify_line
        ok = notify_line.push(msg)
        print(f"\nLINE: {'✅' if ok else '❌'}")
    except Exception as e:
        print(f"\n⚠️ LINE 推播錯誤: {e}")


if __name__ == "__main__":
    main()
