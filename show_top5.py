# -*- coding: utf-8 -*-
"""快速顯示當前 Dashboard 上的 Top 5 推薦"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

d = json.loads(io.open('dashboard_data.json', encoding='utf-8').read())
print(f"📊 Dashboard 最新更新: {d.get('timestamp')}")
print(f"V4 推薦來源: {'⚠️ Fallback' if d.get('tw_top5_fallback') else '✅ V4 嚴格篩選'}")
rec_ind = d.get('tw_recommended_industry', {})
print(f"推薦族群: {rec_ind.get('industry', '?')}")
print()
print("=== 🎯 Top 5 推薦 ===")
for i, s in enumerate(d.get('tw_top5', []), 1):
    print(f"  #{i} {s.get('ticker')} {s.get('name')} ({s.get('industry','?')})")
    print(f"     收 ${s.get('close',0):.1f} {s.get('change',0):+.2f}%")
    if s.get('score', 0) > 0:
        print(f"     動能 {s.get('score',0)}/100 ｜ {s.get('category','?')}")
print()
print("=== 大盤體制 ===")
spy = d.get('regime', {}).get('spy', {})
tw  = d.get('regime', {}).get('tw0050', {})
if spy:
    print(f"  SPY ${spy.get('today',0):.2f} {spy.get('level','')}"
          f" 距MA200 {spy.get('vs_ma200_pct',0):+.1f}%")
if tw:
    print(f"  0050 ${tw.get('today',0):.2f} {tw.get('level','')}"
          f" 距MA200 {tw.get('vs_ma200_pct',0):+.1f}%")
print()
print("=== 📡 美台同步族群 ===")
sync = d.get('us_tw_sync', {})
if sync:
    hot = sync.get('hot_us_sectors', {})
    for tk, chg in sorted(hot.items(), key=lambda x: -x[1])[:3]:
        print(f"  🔥 {tk} {chg:+.2f}%")
    top5_sync = sync.get('top5_synced_picks', [])
    if top5_sync:
        print("  對應台股 Top 5：")
        for t in top5_sync[:5]:
            print(f"    {t['ticker']} {t['name']} ({t.get('industry','?')}) {t['change_pct']:+.2f}%")
