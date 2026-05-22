#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 git 歷史 commits 種子化 industry_heat_history.json
讓 hot_money_radar.py 第一次跑就能算動能（不必等 5 天）

用法：python seed_heat_history.py
"""
import sys, io, os, json, subprocess
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(ROOT, "industry_heat_history.json")
TARGET_FILE = "ath_industry_report.json"


def git_log_hashes(n=20):
    """取 ath_industry_report.json 的過去 N 個 commit hash + 日期"""
    out = subprocess.check_output(
        ["git", "log", f"--max-count={n}",
         "--pretty=format:%H|%ai", "--", TARGET_FILE],
        cwd=ROOT, text=True
    )
    rows = []
    for line in out.strip().split("\n"):
        if "|" in line:
            h, ai = line.split("|", 1)
            rows.append((h, ai[:10]))  # commit_date
    return rows


def git_show(commit_hash):
    """取某個 commit 的 ath_industry_report.json 內容"""
    try:
        out = subprocess.check_output(
            ["git", "show", f"{commit_hash}:{TARGET_FILE}"],
            cwd=ROOT, stderr=subprocess.DEVNULL
        )
        return json.loads(out.decode("utf-8"))
    except Exception:
        return None


def main():
    print("[1/4] 取 git history...")
    rows = git_log_hashes(n=30)
    print(f"      找到 {len(rows)} 個 commit")

    print("[2/4] 抓每個 commit 的 ath_industry_report.json...")
    seen_dates = set()
    entries = []
    for h, commit_date in rows:
        d = git_show(h)
        if not d:
            continue
        # 用 report 內的 timestamp（資料日），不是 commit 日
        rep_date = d.get("timestamp", commit_date)
        if rep_date in seen_dates:
            continue
        seen_dates.add(rep_date)

        stats = {s["industry"]: s["count"]
                 for s in d.get("industry_stats", [])}
        bullish = {s["industry"]: s["bullish_count"]
                   for s in d.get("industry_stats", [])}
        if not stats:
            continue
        entries.append({"date": rep_date, "stats": stats, "bullish": bullish})

    entries.sort(key=lambda x: x["date"])
    print(f"      去重後 {len(entries)} 天有效資料")
    print(f"      日期範圍：{entries[0]['date']} ~ {entries[-1]['date']}" if entries else "      空")

    print("[3/4] 寫入 industry_heat_history.json...")
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"history": entries}, f, ensure_ascii=False, indent=2)

    print("[4/4] 完成！前 3 天 + 後 3 天 sample：")
    for e in entries[:3] + (entries[-3:] if len(entries) > 3 else []):
        top3 = sorted(e["stats"].items(), key=lambda x: -x[1])[:3]
        s = ", ".join(f"{ind}:{cnt}" for ind, cnt in top3)
        print(f"      {e['date']}  {s}")


if __name__ == "__main__":
    main()
