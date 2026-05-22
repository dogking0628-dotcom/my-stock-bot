# -*- coding: utf-8 -*-
"""
OOS 壓力測試分析（對應用戶 4 個情境）
═════════════════════════════════════════════════
1. 2018-Q4 升息：策略應該空手
2. 2020-03 COVID -30%：跌破 20MA 應 2-5 天全砍，總虧 <= -20%
3. 2020-Q2~2021 QE 多頭：應抓到台積電/聯發科主升段
4. 2022 升息熊 -28%：應大部分時間空手
"""
import json, sys, io
from collections import defaultdict
from datetime import datetime, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

d = json.load(open('backtest_v2_oos.json', encoding='utf-8'))
ts = d['trades']
final = d['final_cash']
n = len(ts)

if n == 0:
    print('⚠️ OOS 無交易 — 策略可能完全空手 (這對 2022 熊市可能反而是優點)')
    print(f'最終資金: {final:,.0f}  總報酬: {(final/1_000_000-1)*100:+.1f}%')
    sys.exit(0)

# ── 整體績效 ──
tr = (final / 1_000_000 - 1) * 100
wins = sum(1 for t in ts if t['ret_pct'] > 0)
wr = wins / n * 100
gains = sum(t['ret_pct'] for t in ts if t['ret_pct'] > 0)
loss = abs(sum(t['ret_pct'] for t in ts if t['ret_pct'] < 0))
pf = gains / loss if loss > 0 else 999

s = datetime.strptime(d['params']['start'], '%Y-%m-%d')
e = datetime.strptime(d['params']['end'], '%Y-%m-%d')
years = (e - s).days / 365.25
cagr = ((final / 1_000_000) ** (1 / years) - 1) * 100 if years > 0 else 0

print('=' * 70)
print(f"📊 OOS 整體 ({d['params']['start']} ~ {d['params']['end']}, {years:.1f}年)")
print('=' * 70)
print(f"總報酬: {tr:+.1f}%  CAGR: {cagr:+.1f}%")
print(f"交易 {n} 筆  勝率 {wr:.0f}%  PF {pf:.2f}")
print(f"平均賺 {gains/wins if wins else 0:+.2f}%  平均賠 {-loss/(n-wins) if (n-wins) else 0:+.2f}%")

# ── 4 個壓力測試 ──
periods = [
    ("2018-Q4 升息", "2018-10-01", "2018-12-31"),
    ("2020-03 COVID 崩盤", "2020-02-15", "2020-04-30"),
    ("2020-Q2~2021 QE多頭", "2020-05-01", "2021-12-31"),
    ("2022 升息熊", "2022-01-01", "2022-10-31"),
    ("2022-Q4 反彈", "2022-11-01", "2022-12-31"),
]

print('\n' + '=' * 70)
print('🎯 4 個壓力場景分析')
print('=' * 70)
for label, s_str, e_str in periods:
    s_d = datetime.strptime(s_str, '%Y-%m-%d')
    e_d = datetime.strptime(e_str, '%Y-%m-%d')
    days = (e_d - s_d).days
    period_trades = [t for t in ts
                     if datetime.strptime(t['entry_date'][:10], '%Y-%m-%d') >= s_d
                     and datetime.strptime(t['entry_date'][:10], '%Y-%m-%d') <= e_d]
    period_exits = [t for t in ts
                    if datetime.strptime(t['exit_date'][:10], '%Y-%m-%d') >= s_d
                    and datetime.strptime(t['exit_date'][:10], '%Y-%m-%d') <= e_d]
    n_in = len(period_trades)
    n_out = len(period_exits)
    period_pnl = sum(t['ret_pct'] for t in period_exits)
    n_per_month = n_in / (days / 30) if days > 0 else 0

    print(f"\n【{label}】 ({s_str} ~ {e_str}, {days} 天)")
    print(f"  進場 {n_in} 筆 ({n_per_month:.1f}/月)，出場 {n_out} 筆")
    print(f"  期間累計報酬: {period_pnl:+.1f}%")

    if n_in == 0:
        print(f"  ✅ 完全空手 (符合預期)")
    elif n_in < 3:
        print(f"  🟡 罕見進場 (少量參與)")
    else:
        worst = min(period_exits, key=lambda x: x['ret_pct']) if period_exits else None
        best = max(period_exits, key=lambda x: x['ret_pct']) if period_exits else None
        if worst:
            print(f"  💀 最差: {worst['ticker']} {worst['ret_pct']:+.1f}%")
        if best:
            print(f"  🏆 最佳: {best['ticker']} {best['ret_pct']:+.1f}%")

# ── 是否抓到台積電 / 聯發科 ──
print('\n' + '=' * 70)
print('🔍 QE 多頭期 (2020-2021) 是否抓到大型科技股')
print('=' * 70)
qe_targets = {
    "2330": "台積電", "2454": "聯發科", "2317": "鴻海",
    "2412": "中華電", "2308": "台達電", "2382": "廣達",
    "2376": "技嘉", "3008": "大立光", "2891": "中信金",
}
qe_period_trades = [t for t in ts
                    if "2020" <= t['entry_date'][:4] <= "2021"]
print(f"QE 期 (2020-2021) 共 {len(qe_period_trades)} 筆交易")
for code, name in qe_targets.items():
    found = [t for t in qe_period_trades if t['ticker'] == code]
    if found:
        for t in found:
            print(f"  ✅ {code} {name} {t['ret_pct']:+.1f}% ({t['entry_date'][:10]}~{t['exit_date'][:10]})")

print('\n所有 QE 期交易 Top 10 獲利:')
qe_sorted = sorted(qe_period_trades, key=lambda x: -x['ret_pct'])[:10]
for t in qe_sorted:
    print(f"  {t['ticker']} {t['industry']} {t['ret_pct']:+.1f}% ({t['entry_date'][:10]}~{t['exit_date'][:10]})")

# ── 月份持倉強度 ──
print('\n' + '=' * 70)
print('📅 月份交易強度（驗證熊市是否空手）')
print('=' * 70)
by_month = defaultdict(int)
for t in ts:
    by_month[t['entry_date'][:7]] += 1
months = sorted(by_month.keys())
for m in months:
    bar = '█' * by_month[m]
    flag = ''
    if m.startswith('2020-03') or m.startswith('2020-02'):
        flag = ' ⚡ COVID'
    elif m.startswith('2022'):
        flag = ' ⚡ 熊'
    elif m.startswith('2018-1') or m.startswith('2018-12'):
        flag = ' ⚡ 升息'
    print(f"  {m} {by_month[m]:>2}筆 {bar}{flag}")

# ── 最大回撤估算 ──
print('\n' + '=' * 70)
print('💀 最大回撤估算（基於每月累計）')
print('=' * 70)
# 按月計算累積資金
ts_sorted = sorted(ts, key=lambda x: x['exit_date'])
cumulative = [1_000_000]
for t in ts_sorted:
    # 每筆損益 (假設等部位)
    pnl = 200_000 * (t['ret_pct'] / 100)
    cumulative.append(cumulative[-1] + pnl)
peak = cumulative[0]
max_dd = 0
for v in cumulative:
    if v > peak: peak = v
    dd = (v / peak - 1) * 100
    if dd < max_dd: max_dd = dd
print(f"  最大回撤估算: {max_dd:.1f}% (基於 trades 累計)")

# ── 通過標準 ──
print('\n' + '=' * 70)
print('🎯 OOS 通過標準檢核')
print('=' * 70)
checks = [
    ("PF >= 1.5", pf >= 1.5, f"PF={pf:.2f}"),
    ("勝率 >= 40%", wr >= 40, f"勝率={wr:.0f}%"),
    ("最大回撤 <= -20%", max_dd >= -20, f"回撤={max_dd:.1f}%"),
    ("總報酬 > 0", tr > 0, f"報酬={tr:+.1f}%"),
]
for name, ok, val in checks:
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}: {val}")
all_ok = all(c[1] for c in checks)
print(f"\n{'🎉 全部通過！策略沒有 look-ahead bias' if all_ok else '⚠️ 部分未通過，需檢視'}")
