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
def find_real_breakouts_from_rising(today_report, momentum, max_picks=8):
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
# ⭐ 韌性過濾（用戶指標：3 日修正 < 10% = 強勢）
# ═════════════════════════════════════════════════
def add_resilience(picks):
    """
    對 picks 加入「3/5/10 日 close-to-close max drawdown」+ 量增 + 連漲天數
    用戶定義「最熱族群修正都很小」→ 拉回 < 10% 強勢、< 5% 超強勢
    """
    if not picks:
        return picks
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️ yfinance 未安裝，跳過韌性分析", file=sys.stderr)
        return picks

    tickers = [f"{p['ticker']}.TW" for p in picks]
    print(f"      抓 {len(tickers)} 檔最近 15 天 yfinance...")
    try:
        import contextlib, io as _io, logging
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        with contextlib.redirect_stderr(_io.StringIO()):
            data = yf.download(" ".join(tickers), period="20d", group_by="ticker",
                               auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print(f"⚠️ yfinance 抓取失敗：{e}", file=sys.stderr)
        return picks

    def max_dd(prices, n):
        window = prices[-n:]
        peak = window[0]
        m = 0.0
        for c in window:
            if c > peak: peak = c
            dd = (c / peak - 1) * 100
            if dd < m: m = dd
        return m

    enriched = []
    for p in picks:
        t = f"{p['ticker']}.TW"
        try:
            df = data[t] if len(tickers) > 1 else data
            df = df.dropna(subset=["Close"])
            if len(df) < 5:
                enriched.append(p); continue
            closes = df["Close"].values
            vols = df["Volume"].values
            today = float(closes[-1])
            p3 = max_dd(closes, 3)
            p5 = max_dd(closes, 5)
            p10 = max_dd(closes, min(10, len(closes)))
            rising = 0
            for i in range(len(closes)-1, 0, -1):
                if closes[i] > closes[i-1]: rising += 1
                else: break
            vg = 0
            if len(vols) >= 10:
                rv = vols[-5:].mean()
                pv = vols[-10:-5].mean()
                vg = (rv/pv - 1) * 100 if pv > 0 else 0
            # 韌性等級
            if p3 >= -5 and p5 >= -5:
                strength = "⭐⭐⭐ 超強勢"
            elif p3 >= -10 and p5 >= -10:
                strength = "⭐⭐ 強勢"
            elif p3 >= -15:
                strength = "⭐ 一般"
            else:
                strength = "⚠️ 弱勢"
            enriched.append({**p,
                "pullback_3d": round(p3, 2),
                "pullback_5d": round(p5, 2),
                "pullback_10d": round(p10, 2),
                "consecutive_rising": rising,
                "vol_growth_pct": round(vg, 1),
                "strength": strength,
            })
        except Exception as e:
            enriched.append(p)

    # 重排：韌性 + 連漲 + 量能
    def resilience_score(p):
        if "pullback_3d" not in p: return -999
        # 拉回越小越好（-5% 滿分 100）
        p3 = p["pullback_3d"]; p5 = p["pullback_5d"]
        s = max(0, 100 + p3*10) * 0.5 + max(0, 100 + p5*5) * 0.3
        s += min(p.get("consecutive_rising", 0), 5) * 5
        s += min(p.get("vol_growth_pct", 0), 100) * 0.1
        return s

    enriched.sort(key=lambda x: -resilience_score(x))
    return enriched


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
    """極簡版：今日推薦族群 + 5 檔個股（進場/停損）"""
    rotation_picks = rotation_picks or []
    date = today_report.get("timestamp", dt.date.today().isoformat())

    # 候選池：升溫族群裡的真突破 + V4 真突破，去重
    from collections import Counter
    seen = set()
    pool = []
    for p in (rotation_picks or []) + (real or []):
        tk = p.get("ticker")
        if not tk or tk in seen:
            continue
        seen.add(tk)
        pool.append(p)

    # 推薦族群 = pool 中個股最多且 status=rising 的族群
    # （個股池大 + 動能強 = 真接棒，避免「電腦及週邊動能高但只 1-2 檔可選」）
    ind_count = Counter(p.get("industry", "?") for p in pool)
    top_industry = None
    best_score = -1
    for ind, cnt in ind_count.items():
        m = momentum.get(ind, {})
        if m.get("status") != "rising":
            continue
        mom_pct = m.get("momentum_pct", 0) or 0
        # 加權：個股數 ×2 + 動能 %
        score = cnt * 2 + mom_pct
        if score > best_score:
            best_score = score
            top_industry = (ind, m)

    # 推薦族群內優先排到前面
    if top_industry:
        in_top = [p for p in pool if p.get("industry") == top_industry[0]]
        out_top = [p for p in pool if p.get("industry") != top_industry[0]]
        picks = in_top + out_top
    else:
        picks = pool

    lines = [f"📡 {date[5:]} 推薦", ""]

    if top_industry:
        ind, m = top_industry
        trend = f" 連{m['trend_days']}天" if m.get('trend_days', 0) >= 2 else ""
        lines.append(f"🔥 族群：{ind} +{m['momentum_pct']:.0f}%{trend}")
    elif any(m.get("status") == "new" for m in momentum.values()):
        lines.append("📅 系統歷史累積中")
    else:
        lines.append("😐 無明顯熱錢族群（空手觀望）")
    lines.append("")

    if picks:
        lines.append("🎯 5 檔個股：")
        for i, p in enumerate(picks[:5], 1):
            price = p.get("today") or p.get("close", 0)
            stop = round(price * 0.93, 1)
            name = p.get("name", "")
            tk = p.get("ticker", "")
            ind = p.get("industry", "")
            lines.append(f"  {i}. {tk} {name} ({ind})")
            lines.append(f"     進${price} 停${stop}")

    # 假突破警示（V4 推但族群退潮的）— 只顯示族群名，不羅列個股
    if fake:
        fake_inds = sorted(set(p.get("industry") for p in fake))
        lines.append("")
        lines.append(f"⚠️ 避開：{','.join(fake_inds)}（族群退溫）")

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
