#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paper Track — V4.4 紙上跟單器（自動化 Phase 0，2026-09-07 起）
═════════════════════════════════════════════════
目的：驗證「訊號→實際可成交→出場」全流程與滑價，為接下單 API 收集 4 週實證。
每個交易日收盤後跑（post_close_review.yml 15:2x 台北）：
  1) 昨日標記 exit_pending 的持倉 → 以今日開盤價出場，記入 ledger
  2) 今晨 daily_v41_signal（若 timestamp=今天）→ 模擬限價單：開盤價 ≤ 限價上限(訊號價×1.02)成交，
     每檔虛擬 15 萬（零股允許），成交價=開盤價
  3) 持倉出場檢查（V4.4 規則）：收盤 < 20MA 或 < 進場×0.93 → 標記 exit_pending（明開出場）
輸出：paper_positions.json / paper_ledger.json + 統計列印（close_update 讀取顯示）
"""
import sys, io, os, json, datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT); sys.path.insert(0, ROOT)

POS_P = "paper_positions.json"
LED_P = "paper_ledger.json"
PER_POS = 150_000
FEE = 0.001425 * 0.6   # 六折手續費
TAX = 0.003


def jload(p, d):
    try: return json.load(io.open(p, encoding="utf-8"))
    except Exception: return d


def main():
    import industry_ath_yf as m
    ref, day = m.latest_trading_day()
    if not ref or not day:
        print("無交易日資料，跳過"); return
    ref_s = str(ref)
    pos = jload(POS_P, {"positions": [], "last_run": None})
    led = jload(LED_P, {"trades": [], "started": ref_s})
    if pos.get("last_run") == ref_s:
        print(f"今日 {ref_s} 已跑過，跳過"); return

    # 需要 ma20 → 持倉股抓 60d 收盤
    import yfinance as yf
    hold_codes = [p["ticker"] for p in pos["positions"]]
    ma20 = {}
    if hold_codes:
        df = yf.download(" ".join(f"{c}.TW" for c in hold_codes), period="60d",
                         auto_adjust=True, progress=False, group_by="ticker", threads=True)
        for c in hold_codes:
            try:
                cl = df[f"{c}.TW"]["Close"].dropna()
                # 補今日棒（yfinance 可能落後）
                rec = day.get(c)
                if rec and (not len(cl) or cl.index[-1].date() < ref):
                    import pandas as pd
                    cl = pd.concat([cl, pd.Series([rec["Close"]], index=[pd.Timestamp(ref)])])
                ma20[c] = float(cl.tail(20).mean())
            except Exception: pass

    # ① exit_pending → 今開出場
    still = []
    for p in pos["positions"]:
        d = day.get(p["ticker"])
        if p.get("exit_pending") and d:
            sp = d["Open"]
            ret = (sp / p["entry"] - 1) * 100
            led["trades"].append({**{k: p[k] for k in ("ticker", "name", "entry", "entry_date", "shares")},
                                  "exit": sp, "exit_date": ref_s, "ret_pct": round(ret - (FEE*2+TAX)*100, 2),
                                  "reason": p.get("exit_reason", "exit")})
            print(f"  出場 {p['ticker']} {p['name']} {p['entry']}→{sp} {ret:+.1f}%")
        else:
            still.append(p)
    pos["positions"] = still

    # ② 今晨訊號進場（signal timestamp == 今天）
    sig = jload("daily_v41_signal.json", {})
    if sig.get("timestamp") == ref_s:
        for pk in (sig.get("picks") or [])[:3]:
            c = pk["ticker"]
            if any(x["ticker"] == c for x in pos["positions"]): continue
            d = day.get(c)
            if not d: continue
            limit_hi = round((pk.get("today") or 0) * 1.02, 1)
            if d["Open"] <= limit_hi and limit_hi > 0:
                sh = int(PER_POS / d["Open"] / 10) * 10
                pos["positions"].append({"ticker": c, "name": pk.get("name", ""), "entry": d["Open"],
                                         "entry_date": ref_s, "shares": sh, "peak": d["Open"]})
                print(f"  進場 {c} {pk.get('name')} @開盤 {d['Open']}（限價上限 {limit_hi}）")
            else:
                led["trades"].append({"ticker": c, "name": pk.get("name", ""), "entry": None,
                                      "entry_date": ref_s, "shares": 0, "exit": None, "exit_date": ref_s,
                                      "ret_pct": None, "reason": f"未成交(開{d['Open']}>限{limit_hi})"})
                print(f"  未成交 {c}（開 {d['Open']} > 限 {limit_hi}）")

    # ③ 出場條件檢查
    for p in pos["positions"]:
        d = day.get(p["ticker"])
        if not d: continue
        cl = d["Close"]; p["peak"] = max(p.get("peak", p["entry"]), cl)
        m20 = ma20.get(p["ticker"])
        floor = p["entry"] * 0.93
        if cl < floor or (m20 and cl < m20) or cl < p["peak"] * 0.7:
            p["exit_pending"] = True
            p["exit_reason"] = "-7%樓地板" if cl < floor else ("跌破20MA" if m20 and cl < m20 else "峰值-30%")
            print(f"  🛑 {p['ticker']} {p['name']} 收 {cl} 觸 {p['exit_reason']} → 明開出場")
        p["last_close"] = cl
        p["unreal_pct"] = round((cl / p["entry"] - 1) * 100, 2)

    pos["last_run"] = ref_s
    json.dump(pos, io.open(POS_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(led, io.open(LED_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    done = [t for t in led["trades"] if t.get("ret_pct") is not None]
    w = [t for t in done if t["ret_pct"] > 0]
    print(f"\n📜 紙上帳本（{led['started']} 起）：平倉 {len(done)} 筆"
          + (f" 勝率 {len(w)/len(done)*100:.0f}% 合計 {sum(t['ret_pct'] for t in done):+.1f}%" if done else "")
          + f"｜持倉 {len(pos['positions'])} 筆 "
          + "、".join(f"{p['ticker']}{p['unreal_pct']:+.1f}%" for p in pos["positions"]))


if __name__ == "__main__":
    main()
