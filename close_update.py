#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收盤更新 close_update.py — 每日收盤後一鍵：跑完整資料管線 → 印 Markdown 摘要（給對話版面）
═════════════════════════════════════════════════
步驟（全部非致命，失敗就標註略過）：
  1 institutional_tracker  T86 投信 + 主動 ETF
  2 smart_money_radar      外資/投信 5日 vs 20日
  3 sitc_product_flow      投信 × 產品細類金額
  4 market_thermometer     量能天險 + 87MA 雙指數（FinMind）
  5 industry_ath_yf        ATH 全市場掃描（證交所 MI_INDEX 校驗最新 K 棒）
然後輸出：盤勢 / 今早掛單檢討 / 投信單日流向 / 明日 V2+V4.3 預覽 / 提醒
用法：python close_update.py [--skip-scan]（--skip-scan 不重跑 ATH 掃描，只用現有 report）
"""
import sys, io, os, json, subprocess, datetime as dt, time
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or "").lower() != "utf-8":
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    except Exception: pass
ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT); sys.path.insert(0, ROOT)
_OUT = sys.stdout
E8 = 1e8
STEPS = [("institutional_tracker.py", 300), ("smart_money_radar.py", 300),
         ("sitc_product_flow.py", 300), ("market_thermometer.py", 180),
         ("industry_ath_yf.py", 1500)]


def run_steps(skip_scan):
    status = {}
    env = dict(os.environ, PYTHONIOENCODING="utf-8", LINE_PAUSED="1")
    for script, to in STEPS:
        if skip_scan and script == "industry_ath_yf.py":
            status[script] = "skip"; continue
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, script], capture_output=True, timeout=to, env=env)
            status[script] = f"ok {time.time()-t0:.0f}s" if r.returncode == 0 else f"exit{r.returncode}"
        except subprocess.TimeoutExpired:
            status[script] = "timeout"
        except Exception as e:
            status[script] = type(e).__name__
        print(f"  [{script}] {status[script]}", file=sys.stderr)
    return status


def jload(p, default=None):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return default


def prev_trading_day(m, ref):
    d = ref - dt.timedelta(days=1)
    for _ in range(8):
        if d.weekday() < 5:
            day = m.fetch_twse_day(d)
            if day: return d, day
        d -= dt.timedelta(days=1)
    return None, None


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def main():
    skip_scan = "--skip-scan" in sys.argv
    print("▶ 跑資料管線...", file=sys.stderr)
    status = run_steps(skip_scan)

    import industry_ath_yf as m
    import daily_v41_picker as v43
    import daily_v2_picker as v2
    from product_taxonomy import PRODUCT_TAXONOMY

    ref, day = m.latest_trading_day()
    pref, pday = prev_trading_day(m, ref) if ref else (None, None)
    names = {s["code"]: s["name"] for s in (jload("tw_universe.json") or {}).get("stocks", [])}
    report = jload("ath_industry_report.json") or {}
    thermo = jload("market_thermometer.json") or {}
    inst = jload("institutional_signal.json") or {}
    cache = jload("t86_3party_cache.json") or {}
    out = []
    P = out.append

    # ── 標題 / 新鮮度 ──
    P(f"# 📅 {ref.strftime('%m/%d') if ref else '?'}（{'一二三四五六日'[ref.weekday()] if ref else '?'}）收盤更新")
    dd, rt = report.get("data_date"), report.get("ref_trading_date")
    fresh = ("✅ 資料新鮮" if dd and rt and dd == rt else f"⚠️ 掃描資料 {dd} ≠ 最新交易日 {rt}")
    P(f"_{fresh}；ATH 掃描補棒 {report.get('patched_bars', 0)} 檔；管線：" +
      "、".join(f"{k.split('.')[0]}={v}" for k, v in status.items()) + "_")
    P("")

    # ── 盤勢 ──
    P("## 📊 盤勢")
    ma = thermo.get("ma87") or {}
    tw, to = ma.get("twii") or {}, ma.get("twoii") or {}
    t = thermo.get("turnover") or {}
    rows = []
    # 指數漲跌用 FinMind 連兩日
    try:
        import urllib.request
        for did, lab, cur in (("TAIEX", "加權", tw), ("TPEx", "櫃買", to)):
            u = (f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={did}"
                 f"&start_date={(dt.date.today()-dt.timedelta(days=10)).isoformat()}")
            rs = [r for r in json.loads(urllib.request.urlopen(u, timeout=20).read()).get("data", []) if r.get("close")]
            if len(rs) >= 2:
                c, p = rs[-1]["close"], rs[-2]["close"]
                g = cur.get("gap_pct")
                rows.append(f"| {lab} | {c:,.0f} | {pct(c,p):+.2f}% | " +
                            (f"{'✅' if cur.get('above') else '⚠️'} {g:+.1f}% |" if g is not None else "– |"))
    except Exception:
        pass
    if day and pday and "0050" in day and "0050" in pday:
        c, p = day["0050"]["Close"], pday["0050"]["Close"]
        rg = report.get("market_regime") or {}
        rows.append(f"| 0050 | {c:.1f} | {pct(c,p):+.2f}% | 距MA200 {rg.get('ext_pct', 0):+.0f}% |")
    if rows:
        P("| | 收盤 | 漲跌 | vs 87MA |"); P("|---|---|---|---|"); out.extend(rows)
    if t.get("twse_amount"):
        amt = t["twse_amount"] / 1e12
        P(f"\n量能 **{amt:.2f} 兆**（天險 1.5 兆的 {amt/1.5*100:.0f}%）"
          + ("　🌋 **量能天險！**" if t.get("sky_alert") else "")
          + (f"　強弱：**{ma.get('strength')}**" if ma.get("strength") else ""))
    P("")

    # ── 今早掛單檢討 ──
    P("## 🔍 今早掛單檢討")
    any_pick = False
    for label, sig, cap in (("V4.3", jload("daily_v41_signal.json") or {}, 450000),
                            ("V2", jload("daily_v2_signal.json") or {}, 450000)):
        picks = sig.get("picks") or []
        if not picks:
            P(f"- **{label}**：空手"); continue
        n = min(len(picks), 3); per = cap / n
        for p in picks[:n]:
            any_pick = True
            c = p["ticker"]; today_px = p.get("today") or 0
            lo, hi = round(today_px * 1.008, 1), round(today_px * 1.02, 1)
            d = (day or {}).get(c)
            if not d:
                P(f"- **{label} {c} {p.get('name','')}**：今日無成交資料"); continue
            o, h, l, cl = d["Open"], d["High"], d["Low"], d["Close"]
            if o <= lo: fill = o
            elif o <= hi: fill = o
            else: fill = None
            if fill:
                ma20 = p.get("ma20", fill * 0.95); stop = max(ma20, fill * 0.93)
                r = pct(cl, fill)
                tag = "🛑 收盤破停損 → 明開出場" if cl < stop else "✅ 續抱"
                P(f"- **{label} {c} {p.get('name','')}**：限價 {lo}-{hi}，開 {o:.1f} 成交 → 收 {cl:.1f}（**{r:+.1f}%**，低 {l:.1f}）停損 {stop:.1f} {tag}")
            else:
                P(f"- **{label} {c} {p.get('name','')}**：限價 {lo}-{hi}，開 {o:.1f} 高於上限 → 未成交（收 {cl:.1f}，{pct(cl, today_px):+.1f}%）")
    P("")

    # ── 投信單日 × 產品 ──
    key = ref.isoformat() if ref else None
    dayflow = cache.get(key) if key else None
    if dayflow and day:
        P(f"## 🏦 {ref.strftime('%m/%d')} 投信單日流向（金額）")
        ftot = sum(v.get("外資", 0) * day[c]["Close"] for c, v in dayflow.items() if c in day) / E8
        itot = sum(v.get("投信", 0) * day[c]["Close"] for c, v in dayflow.items() if c in day) / E8
        P(f"全市場：外資 **{ftot:+.0f} 億** / 投信 **{itot:+.0f} 億**")
        prow = []
        for prod, cs in PRODUCT_TAXONOMY.items():
            tot = 0; items = []
            for c in dict.fromkeys(cs):
                v = dayflow.get(c, {}).get("投信", 0); px = (day.get(c) or {}).get("Close")
                if v and px:
                    a = v * px / E8; tot += a; items.append((a, names.get(c, c)))
            items.sort(); prow.append((tot, prod, items))
        prow.sort(reverse=True)
        def cell(tot, prod, items, side):
            if side == "b":
                x = items[-1] if items and items[-1][0] > 0 else None
            else:
                x = items[0] if items and items[0][0] < 0 else None
            return f"{prod} **{tot:+.0f}**" + (f"（{x[1]} {x[0]:+.1f}）" if x else "")
        P("- 📈 買：" + "、".join(cell(*r, "b") for r in prow[:6] if r[0] > 0.5))
        P("- 📉 賣：" + "、".join(cell(*r, "s") for r in prow[::-1][:6] if r[0] < -0.5))
        st = []
        for c, v in dayflow.items():
            px = (day.get(c) or {}).get("Close")
            if px and len(c) == 4 and c.isdigit() and not c.startswith("00"):
                st.append((v.get("投信", 0) * px / E8, names.get(c, c), v.get("外資", 0) * px / E8))
        st.sort()
        P("- 個股買超：" + "、".join(f"{n} {a:+.1f}(外{f:+.0f})" for a, n, f in st[-6:][::-1]))
        P("- 個股賣超：" + "、".join(f"{n} {a:+.1f}(外{f:+.0f})" for a, n, f in st[:6]))
        spf = jload("sitc_product_flow.json") or {}
        pr = spf.get("products") or []
        if pr:
            b5 = [r for r in pr if r["amt5"] > 0][:4]; s5 = [r for r in sorted(pr, key=lambda x: x["amt5"]) if r["amt5"] < 0][:4]
            P(f"- 5 日累計：買 " + "、".join(f"{r['product']}{r['amt5']:+.0f}" for r in b5) +
              "；賣 " + "、".join(f"{r['product']}{r['amt5']:+.0f}" for r in s5))
        P("")

    # ── 明日預覽 ──
    td = report.get("trade_date") or "?"
    P(f"## 🎯 明日 {td[5:]} 預覽（本機預跑；正式版 04:00 會加美股連動分數）")
    if status.get("industry_ath_yf.py", "").startswith("ok") or skip_scan:
        try:
            note = v43.fresh_note(report)
            msg43 = v43.build_message(report.get("tomorrow_top5", []), report.get("tomorrow_top5_industry"),
                                      report.get("market_regime"), report.get("v4_blocked", False), td,
                                      inst=v43.load_inst(), data_note=note)
            # 只取到主動ETF/溫度計前（流向資訊上面已給）
            cut = msg43.split("🌡️")[0].rstrip()
            P("```"); P(cut); P("```")
        except Exception as e:
            P(f"V4.3 預覽失敗：{e}")
        try:
            picks2, top2 = v2.pick_v2_from_report(report)
            msg2 = v2.build_message(picks2, top2, td, inst=v2.load_inst(), data_note=v2.fresh_note(report))
            P("```"); P(msg2.split("━━━")[0].rstrip()); P("```")
        except Exception as e:
            P(f"_V2 預覽略過（{type(e).__name__}）_")
    else:
        P("_ATH 掃描未完成，無法預覽；明早 07:09 正式日報為準_")
    P("")

    # ── 提醒 ──
    warns = []
    rg = report.get("market_regime") or {}
    if rg.get("ext_pct", 0) > 25: warns.append(f"0050 距 MA200 **{rg['ext_pct']:+.0f}%** 偏熱")
    if to and not to.get("above"): warns.append(f"櫃買低於 87MA（{to.get('gap_pct', 0):+.1f}%）中小型弱")
    if t.get("sky_alert"): warns.append("🌋 量能天險（6 月實證：群聚後 -15%）")
    hp = [f"{p['ticker']}{p['name']}({p['today']:.0f})" for p in report.get("tomorrow_top5", [])[:3] if (p.get("today") or 0) > 199]
    if hp: warns.append("回測從未買過 >199 元股：" + "、".join(hp) + " 屬樣本外")
    if not (dd and rt and dd == rt): warns.append("⚠️ 掃描資料可能落後，明早以正式日報「📅 依…收盤資料」為準")
    if warns:
        P("## ⚠️ 提醒"); [P(f"- {w}") for w in warns]
    print("\n".join(out))


if __name__ == "__main__":
    main()
