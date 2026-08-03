# -*- coding: utf-8 -*-
"""過去一週 V2 + V4.x 所有推播選股 → 抓現價驗證績效（從 git 歷史撈每日 signal）"""
import sys, io, os, json, subprocess
import datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DAYS = 9  # 往回撈 9 天（涵蓋一週交易日）


def git_versions(path):
    """回傳 [(commit, iso_date)]，最舊在前"""
    out = subprocess.run(
        ["git", "log", f"--since={DAYS} days ago", "--format=%H %cI", "--", path],
        capture_output=True, text=True).stdout.strip()
    rows = [l.split() for l in out.splitlines() if l.strip()]
    return list(reversed([(r[0], r[1][:10]) for r in rows]))


def git_show(commit, path):
    p = subprocess.run(["git", "show", f"{commit}:{path}"],
                       capture_output=True)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout.decode("utf-8"))
    except Exception:
        return None


def collect(path, label, pick_key="picks"):
    """撈出該 signal 檔一週內每個交易日的推薦"""
    seen_ts = set()
    days = []
    for commit, cdate in git_versions(path):
        d = git_show(commit, path)
        if not d:
            continue
        ts = d.get("timestamp")
        if not ts or ts in seen_ts:
            continue
        seen_ts.add(ts)
        picks = d.get(pick_key) or []
        days.append({"date": ts, "strategy": label,
                     "picks": [{"ticker": p["ticker"], "name": p["name"],
                                "industry": p.get("industry"),
                                "rec_price": p.get("today") or p.get("rec_close")}
                               for p in picks]})
    return days


def main():
    v2_days = collect("daily_v2_signal.json", "V2")
    v4_days = collect("daily_v41_signal.json", "V4.x")
    all_days = sorted(v2_days + v4_days, key=lambda x: (x["date"], x["strategy"]))
    if not all_days:
        print("一週內 git 歷史找不到 signal 紀錄")
        return

    # 收集要抓價的 ticker
    tickers = sorted({p["ticker"] for d in all_days for p in d["picks"]})
    prices = {}
    if tickers:
        import yfinance as yf
        print(f"抓 {len(tickers)} 檔現價...\n")
        df = yf.download(" ".join(f"{t}.TW" for t in tickers), period="5d",
                         auto_adjust=True, progress=False, threads=True,
                         group_by="ticker")
        for t in tickers:
            try:
                sub = df[f"{t}.TW"] if len(tickers) > 1 else df
                cl = sub["Close"].dropna()
                if len(cl):
                    prices[t] = float(cl.iloc[-1])
            except Exception:
                pass

    print("=" * 62)
    print(f"📆 過去一週推播選股總驗證（{all_days[0]['date']} ~ {all_days[-1]['date']}）")
    print("=" * 62)

    stats = {}
    for d in all_days:
        tag = f"[{d['strategy']}] {d['date']}"
        if not d["picks"]:
            print(f"\n{tag}  📭 空手")
            continue
        print(f"\n{tag}")
        for p in d["picks"]:
            cur = prices.get(p["ticker"])
            rp = p.get("rec_price")
            if cur is None or not rp:
                print(f"  {p['ticker']} {p['name']:<8} 推薦 ${rp} → 無現價資料")
                continue
            ret = (cur / rp - 1) * 100
            em = "✅" if ret > 0 else "❌"
            s = stats.setdefault(d["strategy"], {"n": 0, "win": 0, "sum": 0.0,
                                                 "best": None, "worst": None})
            s["n"] += 1
            s["win"] += ret > 0
            s["sum"] += ret
            item = (p["ticker"], p["name"], ret, d["date"])
            if s["best"] is None or ret > s["best"][2]:
                s["best"] = item
            if s["worst"] is None or ret < s["worst"][2]:
                s["worst"] = item
            print(f"  {em} {p['ticker']} {p['name']:<8}({p.get('industry') or '?'})"
                  f" 推薦${rp:,.1f} → 現${cur:,.1f}  {ret:+.2f}%")

    print("\n" + "=" * 62)
    print("📊 各版本一週統計")
    print("=" * 62)
    for label, s in stats.items():
        if s["n"] == 0:
            continue
        avg = s["sum"] / s["n"]
        print(f"\n[{label}] {s['n']} 筆 / 勝率 {s['win']}/{s['n']}"
              f" ({s['win']/s['n']*100:.0f}%) / 平均 {avg:+.2f}%")
        b, w = s["best"], s["worst"]
        print(f"  🏆 最佳 {b[0]} {b[1]} {b[2]:+.2f}% ({b[3]} 推)")
        print(f"  💀 最差 {w[0]} {w[1]} {w[2]:+.2f}% ({w[3]} 推)")


if __name__ == "__main__":
    main()
