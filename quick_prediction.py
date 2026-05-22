# -*- coding: utf-8 -*-
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

d = json.load(open('hot_money_signal.json', encoding='utf-8'))

print('=' * 60)
print(f"📡 系統 {d['timestamp']} 收盤後分析（歷史累積 {d['history_days']} 天）")
print('=' * 60)

print()
print('🔥 升溫族群（資金流入動能）')
for s in d.get('rising_industries', [])[:6]:
    trend = f" 連{s['trend_days']}天" if s['trend_days'] >= 2 else ''
    avg = int(s['avg_n']) if s.get('avg_n') else '-'
    print(f"  {s['industry']:<10} +{s['momentum_pct']:>4.0f}%{trend:<6}  {avg}→{s['today']} 檔  多頭比{s['bullish_ratio']:.0%}")

print()
print('📉 退潮族群（資金撤離）')
cool = d.get('cooling_industries', [])
if cool:
    for s in cool[:3]:
        avg = int(s['avg_n']) if s.get('avg_n') else '-'
        print(f"  {s['industry']:<10} {s['momentum_pct']:>+5.0f}%  {avg}→{s['today']} 檔")
else:
    print('  無')

print()
print('⭐ 接棒族群真突破 TOP（按綜合分排序）')
for p in d.get('rotation_picks', [])[:8]:
    fh = (p.get('ratio', 1) - 1) * 100
    mc = p.get('market_cap_billions', 0)
    bull = p.get('bullish', False)
    print(f"  {p['ticker']} {p['name']:<10} {p['industry']:<8} "
          f"今${p['today']:>7.1f}  距高+{fh:>5.0f}%  族群+{p['industry_momentum_pct']:.0f}%  市值{mc:.0f}億")
