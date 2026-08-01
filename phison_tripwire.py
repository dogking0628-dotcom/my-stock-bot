#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phison_tripwire.py — 群聯跑道保險線監測
═════════════════════════════════════════════════
背景(2026/8/1 約定):
  用戶持群聯 2,176 股(成本 2,214),選擇不賣、靠薪水補跑道(選項C)。
  C 成立的自動保險之一:群聯「收盤」跌破 1,490(7月崩盤收盤低點 1,495 下緣)
  → 反彈失敗、下跌段重啟 → 隔天賣 420 股補跑道,無條件、不再討論。
本腳本每日盤後檢查收盤價,跌破即推 LINE 警報。
"""
import sys, io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

TRIP_LINE = 1490
SHARES_TO_SELL = 420

def main():
    import yfinance as yf
    df = yf.download('8299.TWO', period='10d', auto_adjust=True, progress=False)
    if hasattr(df.columns, 'levels'):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna(subset=['Close'])
    if len(df) == 0:
        print('⚠️ 群聯無資料'); return
    close = float(df['Close'].iloc[-1])
    date = df.index[-1].strftime('%m/%d')
    print(f'群聯 {date} 收盤 {close:.0f}(保險線 {TRIP_LINE})')

    if close < TRIP_LINE:
        msg = (f'🚨 群聯保險線觸發 🚨\n\n'
               f'{date} 收盤 {close:.0f} < {TRIP_LINE}\n'
               f'(跌破 7 月崩盤低點=反彈失敗)\n\n'
               f'📋 約定動作(2026/8/1 已同意):\n'
               f'  明天賣出 {SHARES_TO_SELL} 股群聯\n'
               f'  ≈ {close*SHARES_TO_SELL/10000:.0f} 萬 → 補跑道\n\n'
               f'這不是討論,是執行。\n'
               f'保留 {2176-SHARES_TO_SELL} 股繼續持有。')
        try:
            import notify_line
            ok = notify_line.push(msg)
            print(f'LINE 警報: {"✅" if ok else "❌"}')
        except Exception as e:
            print(f'LINE err: {e}')
    else:
        print(f'✅ 未觸發(距線 {(close/TRIP_LINE-1)*100:+.1f}%)')

if __name__ == '__main__':
    main()
