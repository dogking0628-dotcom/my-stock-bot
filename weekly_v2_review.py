#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_v2_review.py — V2 系統每週檢討
═════════════════════════════════════════════════
每週六 10:00 自動跑（GitHub Actions cron: 0 2 * * 6 UTC）

邏輯:
1. 從 git log 撈過去 7 天的 daily_v2_signal.json
2. 對每個被推的個股:
   - 用 yfinance 抓「次日開盤」進場價
   - 計算到今天的浮盈/虧
   - 判定是否觸發 4 重防線
3. 統計:
   - 本週推幾檔
   - 命中率（浮盈 > 0 比例）
   - 平均報酬
   - 族群表現
   - 各過濾條件有效性
4. 推 LINE 完整週報
"""
import sys, io, os, json, subprocess, datetime as dt
from collections import defaultdict

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNAL_FILE = "daily_v2_signal.json"
REVIEW_PATH = os.path.join(ROOT, "weekly_v2_review.json")
LOOKBACK_DAYS = 7  # 檢討過去 7 天


# ═════════════════════════════════════════════════
# 從 git log 撈歷史訊號
# ═════════════════════════════════════════════════
def git_history_signals(n_days=7):
    """取過去 N 天 daily_v2_signal.json 的所有版本"""
    try:
        # 取所有 commit hash + 日期
        out = subprocess.check_output(
            ["git", "log", "--max-count=50",
             "--pretty=format:%H|%ai", "--", SIGNAL_FILE],
            cwd=ROOT, text=True
        )
    except subprocess.CalledProcessError:
        print("⚠️ 無 git history", file=sys.stderr)
        return []

    rows = []
    seen_dates = set()
    cutoff = dt.date.today() - dt.timedelta(days=n_days)

    for line in out.strip().split("\n"):
        if "|" not in line:
            continue
        h, ai = line.split("|", 1)
        commit_date = ai[:10]
        if commit_date in seen_dates:
            continue
        seen_dates.add(commit_date)
        try:
            d = dt.datetime.strptime(commit_date, "%Y-%m-%d").date()
            if d < cutoff:
                break
        except ValueError:
            continue
        try:
            raw = subprocess.check_output(
                ["git", "show", f"{h}:{SIGNAL_FILE}"],
                cwd=ROOT, stderr=subprocess.DEVNULL
            )
            data = json.loads(raw.decode('utf-8'))
            rows.append({
                'commit_date': commit_date,
                'signal_date': data.get('timestamp', commit_date),
                'data': data,
            })
        except Exception:
            continue

    # 按日期排序
    rows.sort(key=lambda x: x['signal_date'])
    return rows


# ═════════════════════════════════════════════════
# 抓個股後續走勢 + 4 重防線判定
# ═════════════════════════════════════════════════
def evaluate_pick(ticker, entry_date_str, entry_price):
    """
    對單一 pick 評估：
    - 抓「進場日的次日 ~ 今天」走勢
    - 算最高、最低、現價
    - 判定觸發哪個防線
    """
    try:
        # 進場日次日為實際進場（系統假設）
        entry_d = dt.datetime.strptime(entry_date_str[:10], "%Y-%m-%d")
        actual_entry = (entry_d + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        end_d = (dt.date.today() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        df = yf.download(f"{ticker}.TW", start=actual_entry, end=end_d,
                         auto_adjust=True, progress=False, threads=False)
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(subset=['Close'])
        if len(df) < 1:
            return None

        # 實際進場 = 次日開盤
        actual_open = float(df['Open'].iloc[0])
        current = float(df['Close'].iloc[-1])
        peak = float(df['High'].max())
        low = float(df['Low'].min())

        # 報酬
        ret_pct = (current / actual_open - 1) * 100
        peak_pct = (peak / actual_open - 1) * 100
        low_pct = (low / actual_open - 1) * 100

        # 4 重防線判定
        status = "持有中"
        triggered = None
        for i, (idx, row) in enumerate(df.iterrows()):
            close = float(row['Close'])
            high = float(row['High'])
            day_ret = (close / actual_open - 1) * 100

            # 停損
            if day_ret <= -7:
                status = f"❌ 停損出場 ({day_ret:+.1f}%)"
                triggered = ("停損", idx.strftime("%m/%d"), day_ret)
                break
            # Stage 3 (+20%)
            if day_ret >= 20:
                status = f"🥇 Stage 3 鎖利 ({day_ret:+.1f}%)"
                triggered = ("Stage3", idx.strftime("%m/%d"), day_ret)
                break

        return {
            'ticker': ticker,
            'actual_entry': actual_open,
            'current': current,
            'ret_pct': round(ret_pct, 2),
            'peak_pct': round(peak_pct, 2),
            'low_pct': round(low_pct, 2),
            'status': status,
            'triggered': triggered,
            'days_held': len(df),
        }
    except Exception as e:
        return {'ticker': ticker, 'error': str(e)}


# ═════════════════════════════════════════════════
# 統計分析
# ═════════════════════════════════════════════════
def analyze_picks(history):
    all_picks = []
    by_industry = defaultdict(list)
    by_date = defaultdict(list)

    for entry in history:
        sig_date = entry['signal_date']
        picks = entry['data'].get('picks', [])
        for p in picks:
            ticker = p.get('ticker')
            if not ticker:
                continue
            entry_price = p.get('today', 0)
            industry = p.get('industry', '?')
            print(f"  評估 {ticker} {p.get('name','')} ({industry}) 推送日 {sig_date}...")
            result = evaluate_pick(ticker, sig_date, entry_price)
            if result and 'error' not in result:
                result['signal_date'] = sig_date
                result['name'] = p.get('name', '')
                result['industry'] = industry
                result['system_entry'] = entry_price
                all_picks.append(result)
                by_industry[industry].append(result)
                by_date[sig_date].append(result)

    return all_picks, by_industry, by_date


# ═════════════════════════════════════════════════
# LINE 訊息
# ═════════════════════════════════════════════════
def build_message(all_picks, by_industry, by_date, start_date, end_date):
    n = len(all_picks)
    if n == 0:
        return f"📊 V2 週檢討 ({start_date}~{end_date})\n\n📭 本週無 V2 訊號推送"

    # 統計
    wins = sum(1 for p in all_picks if p['ret_pct'] > 0)
    avg_ret = sum(p['ret_pct'] for p in all_picks) / n
    stop_loss_count = sum(1 for p in all_picks if p.get('triggered') and p['triggered'][0] == '停損')
    lock_count = sum(1 for p in all_picks if p.get('triggered') and 'Stage' in p['triggered'][0])
    holding_count = n - stop_loss_count - lock_count

    lines = [f"📊 V2 週檢討 ({start_date}~{end_date})", ""]
    lines.append(f"本週推 {n} 檔 ({len(by_date)} 個訊號日)")
    lines.append(f"  ✅ 浮盈 {wins} 檔 ({wins/n*100:.0f}%)")
    lines.append(f"  ❌ 浮虧 {n-wins} 檔")
    lines.append(f"  📊 平均報酬 {avg_ret:+.2f}%")
    lines.append(f"  🛑 觸發停損: {stop_loss_count} 檔")
    lines.append(f"  🥇 觸發鎖利: {lock_count} 檔")
    lines.append(f"  📦 持有中: {holding_count} 檔")
    lines.append("")

    # 個股明細
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📋 個股明細:")
    for p in sorted(all_picks, key=lambda x: -x['ret_pct']):
        emoji = "✅" if p['ret_pct'] > 0 else "❌"
        lines.append(f"  {emoji} {p['ticker']} {p['name']} ({p['industry'][:4]})")
        lines.append(f"     系統${p['system_entry']:.0f} → 實際${p['actual_entry']:.0f} → 現${p['current']:.0f} ({p['ret_pct']:+.1f}%)")
        if p.get('triggered'):
            lines.append(f"     {p['status']}")
    lines.append("")

    # 族群表現
    if by_industry:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📊 族群表現:")
        ind_stats = []
        for ind, picks in by_industry.items():
            avg = sum(p['ret_pct'] for p in picks) / len(picks)
            w = sum(1 for p in picks if p['ret_pct'] > 0)
            ind_stats.append((ind, len(picks), w, avg))
        for ind, total, w, avg in sorted(ind_stats, key=lambda x: -x[3]):
            lines.append(f"  {ind}: {w}/{total} 勝 ({w/total*100:.0f}%)  平均{avg:+.1f}%")
        lines.append("")

    # 系統反思
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 系統反思:")
    if wins / n >= 0.5:
        lines.append("  ✅ 命中率 ≥ 50%, V2 設定合理")
    elif wins / n >= 0.3:
        lines.append("  🟡 命中率 30-50%, 符合 V2 預期")
    else:
        lines.append("  ⚠️ 命中率 < 30%, 需檢視過濾條件")
    if stop_loss_count >= n / 3:
        lines.append("  ⚠️ 1/3 觸發停損, 進場時機可能過熱")
    if avg_ret >= 5:
        lines.append("  ⭐ 平均報酬 ≥ 5%, 表現優秀")
    elif avg_ret <= -3:
        lines.append("  💀 平均報酬 ≤ -3%, 大盤可能轉空")

    return "\n".join(lines)


# ═════════════════════════════════════════════════
# 主流程
# ═════════════════════════════════════════════════
def main():
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=LOOKBACK_DAYS)
    print(f"[1/4] 撈過去 {LOOKBACK_DAYS} 天 V2 訊號歷史...")
    history = git_history_signals(LOOKBACK_DAYS)
    print(f"      找到 {len(history)} 個推送日")
    if not history:
        print("📭 無歷史訊號可檢討（系統剛上線）")
        sys.exit(0)

    print(f"[2/4] 評估每個 pick 後續走勢...")
    all_picks, by_industry, by_date = analyze_picks(history)
    print(f"      有效 picks: {len(all_picks)} 檔")

    print(f"[3/4] 組裝週報...")
    msg = build_message(all_picks, by_industry, by_date,
                       start_date.isoformat(), end_date.isoformat())

    # 輸出 JSON
    report = {
        'period': f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        'total_picks': len(all_picks),
        'wins': sum(1 for p in all_picks if p['ret_pct'] > 0),
        'avg_return': sum(p['ret_pct'] for p in all_picks) / len(all_picks) if all_picks else 0,
        'picks': all_picks,
        'by_industry': {ind: [p['ticker'] for p in ps] for ind, ps in by_industry.items()},
    }
    with open(REVIEW_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"[4/4] 已輸出 {REVIEW_PATH}")

    print("\n" + "=" * 60)
    print("LINE 週報預覽:")
    print("=" * 60)
    print(msg)
    print("=" * 60)

    # 推 LINE
    try:
        import notify_line
        ok = notify_line.push(msg)
        print(f"\nLINE: {'✅' if ok else '❌'}")
    except Exception as e:
        print(f"\n⚠️ LINE error: {e}")


if __name__ == "__main__":
    main()
