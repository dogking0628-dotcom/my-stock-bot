#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily V4.3 Picker — 每日 LINE 推播（V4.3 = V4.2 + 硬停損-7%樓地板，與 V2 並行）
═════════════════════════════════════════════════
V4.3 邏輯（在 industry_ath_yf.py 算好，這裡只讀 tomorrow_top5 推播）：
  ① 創 2y 月線 ATH  ② 多頭排列  ③ 科技 7 族群  ④ 市值 ≥ 100 億
  ⑤ 動能評分 ≥ 80   ⑥ 美股族群加分  ⑦ 0050 > MA200 才進場
  ⑧ 最強族群挑 5    ⑨ 7 日內虧損股黑名單
出場：跌破 20MA 或 進場價-7%（先到先出）/ 從峰值 -30%

每日 cron（接在 industry_ath_yf.py 之後，與 daily_v2_picker.py 並行）
"""
import sys, io, os, json, datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(ROOT, "ath_industry_report.json")
SIGNAL_PATH = os.path.join(ROOT, "daily_v41_signal.json")
INST_PATH = os.path.join(ROOT, "institutional_signal.json")

ACTIVE_CAPITAL = 450_000
MAX_PUSH = 3   # LINE 推前 3 檔（與 V2 一致，方便並行比較）


def fresh_note(report):
    """資料日期標示：資料=最後一根K棒；若落後證交所最新交易日 → ⚠️"""
    dd, ref = report.get("data_date"), report.get("ref_trading_date")
    if not dd:
        return None
    if ref and dd < ref:
        return f"⚠️ 資料僅到 {dd[5:]} 收盤（最新交易日 {ref[5:]}，訊號落後）"
    return f"📅 依 {dd[5:]} 收盤資料"


def load_inst():
    try:
        with open(INST_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def inst_tag(ticker, inst):
    parts = []
    sitc = (inst.get("sitc_net") or {}).get(ticker, 0)
    if sitc > 0:
        parts.append(f"投信+{sitc/1000:,.0f}張")
    e = (inst.get("etf_stock") or {}).get(ticker)
    if e:
        ids = "·".join(f"{k}{v:.1f}%" for k, v in sorted(e["etfs"].items()))
        parts.append(f"ETF:{ids}")
    for etf, c in (inst.get("etf_changes") or {}).items():
        if any(x.get("code") == ticker for x in c.get("new", [])):
            parts.append(f"🆕{etf}新增")
        elif any(x.get("code") == ticker for x in c.get("added", [])):
            parts.append(f"➕{etf}加碼")
    return " ".join(parts) if parts else None


def etf_changes_block(inst, max_lines=4):
    """主動 ETF 昨日異動摘要（只列新增/剔除）"""
    lines = []
    for etf, c in (inst.get("etf_changes") or {}).items():
        news = [x["name"] for x in c.get("new", [])][:3]
        rems = [x["name"] for x in c.get("removed", [])][:3]
        if news:
            lines.append(f"  🆕 {etf} 新增: {'、'.join(news)}")
        if rems:
            lines.append(f"  ❌ {etf} 剔除: {'、'.join(rems)}")
    return lines[:max_lines]


def tangle_block(report_extra):
    """🌀 糾結突破雷達（T2 測試軌道）— V4.3 空手日也要顯示（互補訊號源）"""
    tg = (report_extra or {}).get("tangle_breakout") or []
    if not tg:
        return []
    L = ["🌀 糾結突破雷達(T2測試軌道,非掛單):"]
    for r in tg[:5]:
        t = r.get("tangle") or {}
        L.append(f"  {r['ticker']} {r['name']} 收{r['today']:.0f}"
                 f" +{r['change_pct']:.1f}% 量{r['vol_ratio']:.1f}x"
                 f" 距高{t.get('dist_high_pct', 0):+.1f}%")
    L.append("  (糾結首根放量;回測勝率34%靠右尾;同樣-7%停損)")
    L.append("")
    return L


def build_message(picks, strongest, regime, blocked, date, inst=None, data_note=None, report_extra=None):
    inst = inst or {}
    lines = [f"🎯 V4.3 開盤掛單 {date[5:]}"]
    if data_note:
        lines.append(data_note)
    lines.append("")

    # 大盤體制
    if regime:
        ext = regime.get("ext_pct", 0)
        stage = "Stage 2 多頭" if regime.get("in_stage2") else "Stage 4 空手"
        lines.append(f"📊 0050 距MA200 {ext:+.0f}% ({stage})")

    if blocked:
        lines.append("")
        lines.append("⛔ 0050 跌破 MA200 → V4.3 今日空手")
        lines.append("（熊市段，嚴禁追價）")
        return "\n".join(lines)

    if strongest:
        lines.append(f"🏆 最強族群: {strongest}")
    lines.append("")

    if not picks:
        lines.append("📭 今日無 V4.3 訊號（動能<80 或黑名單）→ 空手")
        tb = tangle_block(report_extra)
        if tb:
            lines.append("")
            lines.extend(tb)
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
        floor = round(limit_low * 0.93, 1)          # V4.3 硬停損 -7%
        stop_eff = max(ma20, floor)
        which = "20MA" if ma20 >= floor else "-7%樓地板"
        shares = int(per / limit_low / 1000) * 1000
        odd_note = None
        if shares < 1000:                      # 1 張 > 每檔配置（千金股）→ 改零股，不硬買 1 張
            shares = int(per / limit_low / 10) * 10
            odd_note = f"（1張=${limit_low*1000/10000:.0f}萬 超配置 → 盤中零股 9:10 起每分鐘撮合）"
        cost = shares * limit_low
        notes = "、".join(p.get("momentum_notes", [])[:3])
        lines.append(f"{i}. {p['ticker']} {p['name']} ({p['industry']})")
        lines.append(f"   {p.get('tier','⭐')} {p.get('momentum_score',0)}分"
                     f" {p.get('next_day_prob','')}")
        lines.append(f"   📍 限價 ${limit_low}-${limit_high}")
        lines.append(f"   💰 {shares}股 ≈ ${cost:,.0f}")
        if odd_note:
            lines.append(f"   ⚠️ 零股{odd_note}")
        lines.append(f"   🛑 停損 ${stop_eff:.1f} ({which}; 20MA ${ma20:.1f} / -7% ${floor})")
        lines.append(f"   📊 量{p.get('vol_ratio',0):.1f}x RSI{p.get('rsi',0):.0f}"
                     f" {notes}")
        itag = inst_tag(p["ticker"], inst)
        if itag:
            lines.append(f"   🏦 {itag}")
        lines.append("")

    lines.extend(tangle_block(report_extra))

    chg_lines = etf_changes_block(inst)
    if chg_lines:
        lines.append("🏦 主動ETF昨日異動:")
        lines.extend(chg_lines)
        lines.append("")

    try:
        from market_thermometer import build_block as _thermo
        tb = _thermo()
        if tb:
            lines.append(tb)
            lines.append("")
    except Exception:
        pass
    try:
        from smart_money_radar import build_block as _smr
        sb = _smr()
        if sb:
            lines.append(sb)
            lines.append("")
    except Exception:
        pass
    try:
        from sitc_product_flow import build_block as _spf
        pb = _spf()
        if pb:
            lines.append(pb)
            lines.append("")
    except Exception:
        pass
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 操作:")
    lines.append("  9:00前掛限價低點")
    lines.append("  9:05沒成交→改限價高點")
    lines.append("  9:10仍無→放棄")
    lines.append("  出場: 收盤跌破20MA 或 進場-7% → 隔日開盤賣")
    return "\n".join(lines)


def main():
    if not os.path.exists(REPORT_PATH):
        print(f"❌ 找不到 {REPORT_PATH}")
        sys.exit(1)
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)

    date = report.get("trade_date") or report.get("timestamp", dt.date.today().isoformat())
    data_note = fresh_note(report)
    picks = report.get("tomorrow_top5", [])
    strongest = report.get("tomorrow_top5_industry")
    regime = report.get("market_regime")
    blocked = report.get("v4_blocked", False)

    print(f"[V4.3] 載入 ath_industry_report ({date})")
    print(f"       tomorrow_top5: {len(picks)} 檔 / 最強族群: {strongest}")
    print(f"       0050 體制: {'空手' if blocked else '可進場'}")

    signal = {
        "timestamp": date,
        "strategy": "V4.3 (V4.2 + 硬停損-7%樓地板; 2y回測 +107.6%/CAGR 28.4%/PF 3.94/MDD -14%)",
        "strongest_industry": strongest,
        "v4_blocked": blocked,
        "picks": picks[:MAX_PUSH],
    }
    with open(SIGNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=2)

    msg = build_message(picks, strongest, regime, blocked, date, inst=load_inst(), data_note=data_note, report_extra=report)
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
