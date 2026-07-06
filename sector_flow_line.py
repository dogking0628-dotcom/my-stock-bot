#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sector_flow_line.py — 資金流向日報（情報版，不含選股）
═════════════════════════════════════════════════
讀 hot_money_signal.json（hot_money_radar.py 產出）
→ 推 LINE：升溫族群 / 退潮族群 / 持股族群現況

設計原則：
- 只回答「錢從哪走、往哪去」（用戶哲學：資金是一套）
- 刻意不含任何個股與進場價 —— 行動訊號唯一來源是 V2
- 核心部位不輪動，此情報僅供理解盤勢 + 衛星倉參考
"""
import sys, io, os, json

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNAL_PATH = os.path.join(ROOT, "hot_money_signal.json")

# 持股 → 交易所族群分類（2026-07 部位；異動時手動更新這張表）
HOLDING_SECTORS = [
    ("半導體",   "台積電/群聯"),
    ("電子零組件", "華新科"),
    ("光電",     "群創"),
    ("電子通路",  "日電貿"),
]

STATUS_EMOJI = {"rising": "🔥", "steady": "➖", "cooling": "❄️", "new": "🆕", "noise": "·"}


def build_message(signal):
    ts = signal.get("timestamp", "?")
    mom = signal.get("momentum", {})
    rising = signal.get("rising_industries", [])
    cooling = signal.get("cooling_industries", [])

    lines = [f"💧 資金流向 {ts[5:]}（依前一交易日收盤）", ""]

    if rising:
        lines.append("🔥 錢正流入：")
        for r in rising[:4]:
            trend = f" 連{r['trend_days']}天" if r.get("trend_days", 0) >= 2 else ""
            lines.append(f"  {r['industry']} +{r['momentum_pct']:.0f}%{trend}（今{r['today']}檔新高）")
    else:
        lines.append("🔥 錢正流入：無明顯族群")

    lines.append("")
    if cooling:
        lines.append("❄️ 錢正流出：")
        for c in cooling[:4]:
            lines.append(f"  {c['industry']} {c['momentum_pct']:.0f}%（剩{c['today']}檔新高）")
    else:
        lines.append("❄️ 錢正流出：無明顯族群")

    holding_lines = []
    for ind, names in HOLDING_SECTORS:
        m = mom.get(ind)
        if not m:
            continue
        mp = m.get("momentum_pct")
        mtxt = f"{mp:+.0f}%" if mp is not None else "—"
        holding_lines.append(f"  {STATUS_EMOJI.get(m['status'], '·')} {ind} {mtxt}（{names}）")
    if holding_lines:
        lines.append("")
        lines.append("📍 你的持股族群：")
        lines.extend(holding_lines)

    lines.append("")
    lines.append("📌 情報僅供理解盤勢：核心不輪動，行動看 V2")
    return "\n".join(lines)


def main():
    if not os.path.exists(SIGNAL_PATH):
        print("❌ 找不到 hot_money_signal.json（hot_money_radar.py 未跑）")
        sys.exit(0)  # 情報缺席不視為致命
    with open(SIGNAL_PATH, encoding="utf-8") as f:
        signal = json.load(f)

    msg = build_message(signal)
    print("=" * 50)
    print(msg)
    print("=" * 50)

    try:
        import notify_line
        ok = notify_line.push(msg)
        print(f"LINE: {'✅' if ok else '❌'}")
    except Exception as e:
        print(f"⚠️ LINE error: {e}")


if __name__ == "__main__":
    main()
