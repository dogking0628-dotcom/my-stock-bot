#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
熱錢輪動雷達（Hot Money Radar）
═════════════════════════════════════════════════
建構在 V4 系統之上，解決兩個痛點：

1. 「不要族群漲幅，要熱錢流向」
   → 追蹤族群 ATH 檔數的「時序動能」（升溫 / 退潮）
   → 識別「接棒候選族群」（連續 N 天動能上升）

2. 「冷族群的個股突破常是假突破」
   → 過濾 tomorrow_top5
   → 在退潮族群內的個股 → 標記假突破風險
   → 在升溫族群內的個股 → 強推（真突破）

每日 5:30 cron 跑（接在 industry_ath_yf.py 之後）
輸出：hot_money_signal.json + LINE 早盤推播

依賴：
- ath_industry_report.json （今日族群統計）
- industry_heat_history.json （自己維護的歷史，從今天起累積）
"""

import sys, io, os, json, datetime as dt
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
TODAY_REPORT = os.path.join(ROOT, "ath_industry_report.json")
HISTORY_PATH = os.path.join(ROOT, "industry_heat_history.json")
SIGNAL_PATH = os.path.join(ROOT, "hot_money_signal.json")

# ── 參數 ────────────────────────────────────────
LOOKBACK_DAYS = 5          # 計算 N 日熱度動能
RISING_THRESHOLD = 10      # 動能 > +10% 視為升溫（接棒候選）
COOLING_THRESHOLD = -5     # 動能 < -5% 視為退潮（資金撤離）
MIN_BASE = 3               # 基期 ATH 檔數 < 3 視為雜訊（不計算動能）
MAX_HISTORY_DAYS = 60      # 歷史最多保留 60 天
TOP_INDUSTRY_FACTOR = 1.0  # 「最熱族群」加權（領頭羊退溫 = 強警示）


# ═════════════════════════════════════════════════
# 歷史管理
# ═════════════════════════════════════════════════
def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"history": []}
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 歷史檔損毀，重建: {e}", file=sys.stderr)
        return {"history": []}


def save_history(h):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)


def append_today(history, today_report):
    """把今天的 industry_stats 追加到歷史（同一天會覆蓋）"""
    today_date = today_report.get("timestamp", dt.date.today().isoformat())
    stats = {s["industry"]: s["count"] for s in today_report.get("industry_stats", [])}
    bullish = {s["industry"]: s["bullish_count"] for s in today_report.get("industry_stats", [])}
    entry = {"date": today_date, "stats": stats, "bullish": bullish}

    # 移除同日舊紀錄
    history["history"] = [h for h in history["history"] if h["date"] != today_date]
    history["history"].append(entry)
    history["history"].sort(key=lambda x: x["date"])
    history["history"] = history["history"][-MAX_HISTORY_DAYS:]
    return history


# ═════════════════════════════════════════════════
# 動能計算
# ═════════════════════════════════════════════════
def compute_momentum(history):
    """
    計算每個族群的 N 日熱度動能。

    回傳：
    {
      industry: {
        "today": int,          # 今日 ATH 檔數
        "avg_n": float,         # 過去 N 日（不含今日）平均
        "momentum_pct": float,  # (today / avg_n - 1) * 100
        "trend_days": int,      # 連續上升/下降天數
        "status": "rising"|"steady"|"cooling"|"new"|"noise",
        "bullish_ratio": float, # 今日多頭比例
      }
    }
    """
    h = history["history"]
    if not h:
        return {}
    today = h[-1]
    today_stats = today["stats"]
    today_bullish = today["bullish"]

    # 取過去 N 日（不含今日）
    past = h[-(LOOKBACK_DAYS + 1):-1] if len(h) > 1 else []
    have_enough = len(past) >= LOOKBACK_DAYS  # 至少 N 日歷史才能算動能

    result = {}
    for ind, today_count in today_stats.items():
        bullish_count = today_bullish.get(ind, 0)
        bullish_ratio = bullish_count / today_count if today_count > 0 else 0

        if not have_enough:
            # 歷史不足，標記為「新族群」（系統剛上線）
            result[ind] = {
                "today": today_count,
                "avg_n": None,
                "momentum_pct": None,
                "trend_days": 0,
                "status": "new",
                "bullish_ratio": bullish_ratio,
            }
            continue

        past_counts = [p["stats"].get(ind, 0) for p in past]
        avg_n = sum(past_counts) / len(past_counts) if past_counts else 0

        if avg_n < MIN_BASE and today_count < MIN_BASE:
            status = "noise"
            mom = None
        else:
            mom = (today_count / max(avg_n, 1) - 1) * 100
            if mom >= RISING_THRESHOLD:
                status = "rising"
            elif mom <= COOLING_THRESHOLD:
                status = "cooling"
            else:
                status = "steady"

        # 連續上升/下降天數（看最近 5 天差分）
        diffs = [past_counts[i+1] - past_counts[i] for i in range(len(past_counts)-1)]
        diffs.append(today_count - past_counts[-1] if past_counts else 0)
        trend_days = 0
        if status == "rising":
            for d in reversed(diffs):
                if d > 0:
                    trend_days += 1
                else:
                    break
        elif status == "cooling":
            for d in reversed(diffs):
                if d < 0:
                    trend_days += 1
                else:
                    break

        result[ind] = {
            "today": today_count,
            "avg_n": round(avg_n, 1),
            "momentum_pct": round(mom, 1) if mom is not None else None,
            "trend_days": trend_days,
            "status": status,
            "bullish_ratio": round(bullish_ratio, 2),
        }
    return result


# ═════════════════════════════════════════════════
# 過濾 tomorrow_top5（真/假突破標記）
# ═════════════════════════════════════════════════
def classify_picks(today_report, momentum):
    """
    把今天的 tomorrow_top5 分類為：
    - real_breakout: 升溫族群內，真突破候選
    - normal: 持平族群內，一般推薦
    - fake_risk: 退潮族群內，假突破風險
    - new_system: 系統歷史不足，無法判斷
    """
    picks = today_report.get("tomorrow_top5", [])
    real, normal, fake, newsys = [], [], [], []
    for p in picks:
        ind = p.get("industry", "未分類")
        m = momentum.get(ind, {})
        status = m.get("status", "noise")
        enriched = {
            **{k: p.get(k) for k in [
                "ticker", "name", "industry", "today", "momentum_score",
                "tier", "next_day_prob", "from_high_pct", "rsi",
                "market_cap_billions", "momentum_notes"
            ]},
            "industry_status": status,
            "industry_momentum_pct": m.get("momentum_pct"),
            "industry_trend_days": m.get("trend_days", 0),
        }
        if status == "rising":
            real.append(enriched)
        elif status == "cooling":
            fake.append(enriched)
        elif status == "new":
            newsys.append(enriched)
        else:
            normal.append(enriched)
    return real, normal, fake, newsys


# ═════════════════════════════════════════════════
# ⭐ 主動從升溫族群挖真突破候選（解 V4 盲點）
# ═════════════════════════════════════════════════
def find_real_breakouts_from_rising(today_report, momentum, max_picks=6):
    """
    V4 只從「ATH 檔數最多」的族群挑 → 常推到「最熱但動能退溫」的族群
    Hot money radar 從「動能升溫」的族群挑 → 真接棒

    從 exact_ath 中挑：
    - 族群必須是 rising
    - 必須 bullish 多頭排列
    - 按「ratio × 族群動能」排序
    """
    rising_set = {ind for ind, m in momentum.items() if m["status"] == "rising"}
    if not rising_set:
        return []

    candidates = []
    for r in today_report.get("exact_ath", []):
        if r.get("industry") not in rising_set:
            continue
        if not r.get("bullish"):
            continue
        ratio = r.get("ratio", 1.0)
        if ratio < 1.0:
            continue
        ind_mom = momentum[r["industry"]]["momentum_pct"]
        # 綜合分：個股強勢度 × 族群動能加成
        score = (ratio - 1) * 100 + ind_mom * 0.5
        candidates.append({**r, "rotation_score": round(score, 2),
                          "industry_momentum_pct": ind_mom})

    candidates.sort(key=lambda x: -x["rotation_score"])
    return candidates[:max_picks]


# ═════════════════════════════════════════════════
# 接棒族群偵測
# ═════════════════════════════════════════════════
def detect_rotation(momentum):
    """
    回傳：
    - rising_top: 動能最高的升溫族群（候選下波主升）
    - cooling_top: 動能最低的退潮族群（資金撤離）
    """
    rising = sorted(
        [(ind, m) for ind, m in momentum.items() if m["status"] == "rising"],
        key=lambda x: -x[1]["momentum_pct"]
    )
    cooling = sorted(
        [(ind, m) for ind, m in momentum.items() if m["status"] == "cooling"],
        key=lambda x: x[1]["momentum_pct"]
    )
    return rising[:5], cooling[:5]


# ═════════════════════════════════════════════════
# LINE 訊息組裝
# ═════════════════════════════════════════════════
def build_line_message(today_report, momentum, rising, cooling, real, normal, fake, newsys, rotation_picks=None):
    rotation_picks = rotation_picks or []
    date = today_report.get("timestamp", dt.date.today().isoformat())
    regime = today_report.get("market_regime", {})
    v4_blocked = today_report.get("v4_blocked", False)

    lines = []
    lines.append(f"📡 {date[5:]} 熱錢輪動雷達")
    lines.append("")

    # ── 大盤狀態 ──
    in_stage2 = not v4_blocked
    lines.append(f"📊 大盤體制：{'Stage 2 ✅' if in_stage2 else 'Stage 4 ⛔ 嚴禁追價'}")
    lines.append("")

    # ── 接棒族群（升溫）──
    if rising:
        lines.append("🔥 升溫族群（資金進駐）")
        for ind, m in rising[:5]:
            trend = f" 連{m['trend_days']}天" if m['trend_days'] >= 2 else ""
            lines.append(f"  {ind} +{m['momentum_pct']:.0f}%{trend}  {int(m['avg_n'])}→{m['today']}檔")
    else:
        if any(m["status"] == "new" for m in momentum.values()):
            lines.append("📅 系統歷史累積中（需 5 天）")
        else:
            lines.append("😐 無明顯升溫族群（市場觀望）")
    lines.append("")

    # ── 退潮族群 ──
    if cooling:
        lines.append("📉 退潮族群（資金撤離）")
        for ind, m in cooling[:3]:
            lines.append(f"  {ind} {m['momentum_pct']:+.0f}%  {int(m['avg_n'])}→{m['today']}檔")
        lines.append("")

    # ── ⭐ 接棒族群真突破候選（核心輸出） ──
    if rotation_picks:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("⭐ 接棒族群真突破 TOP")
        lines.append("（V4 系統盲點，這裡才是熱錢）")
        for p in rotation_picks[:6]:
            stop = round(p['today'] * 0.93, 1)
            from_high = (p['ratio'] - 1) * 100
            lines.append(f"  {p['ticker']} {p['name']} ({p['industry']})")
            lines.append(f"    超高 +{from_high:.0f}% 族群+{p['industry_momentum_pct']:.0f}%")
            lines.append(f"    進${p['today']} 停${stop}")
        lines.append("")

    # ── V4 picks 中真突破（族群升溫） ──
    if real:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("✅ V4 推薦中的真突破")
        for p in real[:5]:
            stop = round(p['today'] * 0.93, 1)
            lines.append(f"  {p['ticker']} {p['name']} mom{p['momentum_score']}")
            lines.append(f"    進${p['today']} 停${stop} {p['tier']}")
        lines.append("")

    # ── 假突破警示 ──
    if fake:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ 假突破風險（不建議追）")
        for p in fake[:5]:
            lines.append(f"  {p['ticker']} {p['name']} mom{p['momentum_score']}")
            lines.append(f"    ❌ {p['industry']} 族群退潮 {p['industry_momentum_pct']:+.0f}%")
        lines.append("")

    # ── 系統剛上線（前 5 天）──
    if newsys and not real and not fake:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📋 今日 V4 推薦（族群動能尚未累積）")
        for p in newsys[:5]:
            lines.append(f"  {p['ticker']} {p['name']} ({p['industry']}) mom{p['momentum_score']}")
        lines.append("")

    # ── 一般推薦（持平族群）──
    if normal and not (real and fake):
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🟡 一般推薦（族群持平）")
        for p in normal[:3]:
            lines.append(f"  {p['ticker']} {p['name']} ({p['industry']}) mom{p['momentum_score']}")
        lines.append("")

    # ── 行動指引 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if real:
        lines.append("🎯 優先做真突破，避開假突破")
    elif newsys:
        lines.append(f"🎯 系統需 {LOOKBACK_DAYS - len([h for h in load_history()['history'] if h])} 天累積")
    else:
        lines.append("🎯 空手觀望，等明確訊號")
    lines.append("")
    lines.append("💬 細節問 LINE Bot：")
    lines.append("  「2327 國巨能進嗎」")
    lines.append("  「半導體還能追嗎」")

    return "\n".join(lines)


# ═════════════════════════════════════════════════
# 主流程
# ═════════════════════════════════════════════════
def main():
    if not os.path.exists(TODAY_REPORT):
        print(f"❌ 找不到 {TODAY_REPORT}，請先跑 industry_ath_yf.py")
        sys.exit(1)

    with open(TODAY_REPORT, "r", encoding="utf-8") as f:
        today_report = json.load(f)

    print(f"[1/5] 載入今日報告：{today_report.get('timestamp')}")
    print(f"      族群數：{len(today_report.get('industry_stats', []))}")

    history = load_history()
    print(f"[2/5] 載入歷史：{len(history['history'])} 天")

    history = append_today(history, today_report)
    save_history(history)
    print(f"[3/5] 已追加今日，總共：{len(history['history'])} 天")

    momentum = compute_momentum(history)
    rising, cooling = detect_rotation(momentum)
    real, normal, fake, newsys = classify_picks(today_report, momentum)
    rotation_picks = find_real_breakouts_from_rising(today_report, momentum)

    print(f"[4/5] 動能分析：")
    print(f"      🔥 升溫族群：{len(rising)} 個")
    print(f"      📉 退潮族群：{len(cooling)} 個")
    print(f"      ✅ V4 picks 中真突破：{len(real)} 檔")
    print(f"      ⚠️ V4 picks 中假突破風險：{len(fake)} 檔")
    print(f"      🟡 V4 picks 中持平：{len(normal)} 檔")
    print(f"      ⭐ 接棒族群內真突破候選：{len(rotation_picks)} 檔（新挖出）")

    # 輸出訊號 JSON（給 Streamlit / LINE Bot 用）
    signal = {
        "timestamp": today_report.get("timestamp"),
        "lookback_days": LOOKBACK_DAYS,
        "history_days": len(history["history"]),
        "momentum": momentum,
        "rising_industries": [{"industry": i, **m} for i, m in rising],
        "cooling_industries": [{"industry": i, **m} for i, m in cooling],
        "real_breakout": real,
        "fake_breakout_risk": fake,
        "normal": normal,
        "new_system": newsys,
        "rotation_picks": rotation_picks,
        "v4_blocked": today_report.get("v4_blocked", False),
    }
    with open(SIGNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=2)
    print(f"[5/5] 已輸出 {SIGNAL_PATH}")

    # 印出 LINE 訊息（也會被 daily.yml 推送）
    msg = build_line_message(today_report, momentum, rising, cooling, real, normal, fake, newsys, rotation_picks)
    print("\n" + "=" * 60)
    print("LINE 訊息預覽：")
    print("=" * 60)
    print(msg)
    print("=" * 60)

    # 自動推 LINE（如果有設 LINE_TOKEN）
    try:
        import notify_line
        ok = notify_line.push(msg)
        print(f"\nLINE 推播：{'✅' if ok else '❌'}")
    except ImportError:
        print("\n⚠️ notify_line 未找到，跳過推播")
    except Exception as e:
        print(f"\n❌ LINE 推播錯誤：{e}")


if __name__ == "__main__":
    main()
