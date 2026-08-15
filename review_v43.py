# -*- coding: utf-8 -*-
"""
V4.3 週/月檢討 — 從 git 歷史撈每日 daily_v41_signal.json 推薦，
以 V4.3 實際出場規則模擬（收盤跌破20MA 或 進場-7% → 隔日開盤賣），
未觸發者以最新收盤 mark-to-market。
"""
import sys, os, io, json, subprocess
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

import pandas as pd
import yfinance as yf

LOOKBACK_DAYS = 35
HARD_FLOOR = 0.93


def git_signal_history(path="daily_v41_signal.json"):
    out = subprocess.run(
        ["git", "log", f"--since={LOOKBACK_DAYS} days ago", "--format=%H", "--", path],
        capture_output=True, text=True).stdout.split()
    seen, days = set(), []
    for commit in reversed(out):
        p = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True)
        if p.returncode != 0:
            continue
        try:
            d = json.loads(p.stdout.decode("utf-8"))
        except Exception:
            continue
        ts = d.get("timestamp")
        if not ts or ts in seen:
            continue
        seen.add(ts)
        days.append({"date": ts, "picks": d.get("picks") or [],
                     "blocked": d.get("v4_blocked", False)})
    return sorted(days, key=lambda x: x["date"])


def simulate(pick, rec_date, ohlc):
    """V4.3 出場模擬。回傳 dict(entry, exit/mtm, ret, reason, days)"""
    df = ohlc
    cl, op = df["Close"], df["Open"]
    ma20 = cl.rolling(20).mean()
    after = df.index[df.index > pd.Timestamp(rec_date)]
    if len(after) == 0:
        return None
    e_day = after[0]
    entry = float(op.loc[e_day])
    if entry <= 0:
        return None
    floor = entry * HARD_FLOOR
    days_held = 0
    for i, d in enumerate(df.index[df.index >= e_day]):
        c = float(cl.loc[d]); m = float(ma20.loc[d]) if not pd.isna(ma20.loc[d]) else 0
        days_held = i
        hit = (c < m) or (c < floor)
        if hit and i > 0:  # 進場當天收盤即破 → 也算隔日出
            nxt = df.index[df.index > d]
            if len(nxt) == 0:  # 還沒有隔日 → 視為續抱
                break
            xp = float(op.loc[nxt[0]])
            reason = "跌破20MA" if c < m else "-7%樓地板"
            if c < m and c < floor:
                reason = "-7%樓地板+20MA"
            return {"entry": entry, "exit": xp, "ret": (xp/entry-1)*100,
                    "reason": reason, "days": i, "open": False}
        if hit and i == 0:
            nxt = df.index[df.index > d]
            if len(nxt):
                xp = float(op.loc[nxt[0]])
                return {"entry": entry, "exit": xp, "ret": (xp/entry-1)*100,
                        "reason": "進場日即破", "days": 1, "open": False}
    last = float(cl.iloc[-1])
    return {"entry": entry, "exit": last, "ret": (last/entry-1)*100,
            "reason": "續抱中", "days": days_held, "open": True}


def main():
    days = git_signal_history()
    picks = []
    for d in days:
        for p in d["picks"]:
            picks.append({"date": d["date"], "ticker": p["ticker"],
                          "name": p["name"], "industry": p.get("industry"),
                          "score": p.get("momentum_score"),
                          "notes": p.get("momentum_notes") or []})
    print(f"git 歷史 {LOOKBACK_DAYS} 天：{len(days)} 個訊號日，共 {len(picks)} 筆推薦")
    empty_days = sum(1 for d in days if not d["picks"])
    print(f"（其中空手 {empty_days} 日）\n")
    if not picks:
        return

    tickers = sorted({p["ticker"] for p in picks})
    print(f"抓 {len(tickers)} 檔 4 個月 OHLC...")
    data = yf.download(" ".join(f"{t}.TW" for t in tickers), period="4mo",
                       auto_adjust=True, progress=False, threads=True,
                       group_by="ticker")
    ohlc = {}
    for t in tickers:
        try:
            sub = data[f"{t}.TW"] if len(tickers) > 1 else data
            sub = sub.dropna(subset=["Close"])
            if len(sub) > 25:
                ohlc[t] = sub
        except Exception:
            continue

    results = []
    for p in picks:
        o = ohlc.get(p["ticker"])
        if o is None:
            continue
        r = simulate(p, p["date"], o)
        if r:
            results.append({**p, **r})

    today = dt.date.today()
    def in_window(r, days_n):
        return (today - dt.date.fromisoformat(r["date"])).days <= days_n

    for label, win in [("📅 週檢討（近 7 日推薦）", 7),
                       ("🗓️ 月檢討（近 30 日推薦）", 30)]:
        sub = [r for r in results if in_window(r, win)]
        print("\n" + "=" * 62)
        print(f"{label}  共 {len(sub)} 筆")
        print("=" * 62)
        if not sub:
            print("（無推薦）")
            continue
        for r in sorted(sub, key=lambda x: x["date"]):
            em = "✅" if r["ret"] > 0 else "❌"
            st = "🔓續抱" if r["open"] else f"出:{r['reason']}"
            print(f"  {em} {r['date'][5:]} {r['ticker']} {r['name']:<7}"
                  f"({(r['industry'] or '?')[:5]:<5}) 進${r['entry']:,.1f}"
                  f"→${r['exit']:,.1f} {r['ret']:+6.2f}% {st} {r['days']}d")
        n = len(sub)
        wins = [r for r in sub if r["ret"] > 0]
        losses = [r for r in sub if r["ret"] <= 0]
        avg = sum(r["ret"] for r in sub) / n
        print(f"\n  勝率 {len(wins)}/{n} ({len(wins)/n*100:.0f}%) | 平均 {avg:+.2f}%")
        if wins:
            print(f"  平均賺 {sum(r['ret'] for r in wins)/len(wins):+.2f}%", end="")
        if losses:
            print(f" | 平均賠 {sum(r['ret'] for r in losses)/len(losses):+.2f}%", end="")
        if wins and losses:
            pf = abs(sum(r['ret'] for r in wins) / sum(r['ret'] for r in losses))
            print(f" | PF {pf:.2f}", end="")
        print()
        floor_exits = [r for r in sub if "樓地板" in r["reason"]]
        if floor_exits:
            print(f"  🛡️ -7%樓地板觸發 {len(floor_exits)} 次"
                  f"（平均 {sum(r['ret'] for r in floor_exits)/len(floor_exits):+.2f}%"
                  f" — 若無樓地板將依 20MA 更深)")
        still = [r for r in sub if r["open"]]
        if still:
            print(f"  🔓 續抱中 {len(still)} 筆: "
                  + ", ".join(f"{r['ticker']}{r['ret']:+.1f}%" for r in still))
        best = max(sub, key=lambda x: x["ret"]); worst = min(sub, key=lambda x: x["ret"])
        print(f"  🏆 {best['ticker']} {best['name']} {best['ret']:+.2f}%"
              f" | 💀 {worst['ticker']} {worst['name']} {worst['ret']:+.2f}%")

    with io.open("review_v43_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    print("\n💾 已輸出 review_v43_result.json")


if __name__ == "__main__":
    main()
