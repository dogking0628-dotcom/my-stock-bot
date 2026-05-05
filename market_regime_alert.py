#!/usr/bin/env python3
"""
大盤體制警報 — SPY/0050 跌破 MA200 觸發加碼提醒
"""
import yfinance as yf
import numpy as np

def check_regime(ticker, market_name, current_price=None):
    """檢查大盤是否跌破 MA200，並回傳警報等級"""
    try:
        df = yf.download(ticker, period="2y", auto_adjust=False, progress=False)
        if df.empty: return None
        closes = df["Close"]
        if hasattr(closes, "columns"): closes = closes[closes.columns[0]]
        closes = closes.dropna().values.astype(float)
        if len(closes) < 200: return None

        today = float(closes[-1]) if current_price is None else current_price
        ma200 = float(np.mean(closes[-200:]))
        ma50  = float(np.mean(closes[-50:]))
        peak  = float(np.max(closes[-252:]))  # 過去 1 年最高
        from_peak = (today / peak - 1) * 100
        vs_ma200 = (today / ma200 - 1) * 100

        # 警報等級
        if vs_ma200 < -10:
            level = "🚨🚨 重大警報"
            action = "動用 50% 現金加碼"
        elif vs_ma200 < -3:
            level = "🚨 警報"
            action = "動用 30% 現金加碼"
        elif from_peak < -20:
            level = "⚠️ 注意"
            action = "減倉 → 增加現金"
        elif vs_ma200 > 15:
            level = "🔥 過熱"
            action = "鎖利 → 增加現金"
        else:
            level = "🟢 正常"
            action = "維持配置"

        return {
            "market": market_name,
            "ticker": ticker,
            "today": today,
            "ma200": ma200,
            "ma50": ma50,
            "peak_1y": peak,
            "vs_ma200_pct": vs_ma200,
            "from_peak_pct": from_peak,
            "level": level,
            "action": action,
        }
    except Exception as e:
        return None

def build_line_block():
    """產生 LINE 訊息區塊"""
    spy = check_regime("SPY", "🇺🇸 美股")
    tw  = check_regime("0050.TW", "🇹🇼 台股")

    lines = ["📊 大盤體制警報"]
    for r in [spy, tw]:
        if not r: continue
        lines.append(f"  {r['market']} {r['ticker']} ${r['today']:.2f}")
        lines.append(f"    {r['level']} | 距 MA200 {r['vs_ma200_pct']:+.1f}% | 距高點 {r['from_peak_pct']:.1f}%")
        lines.append(f"    💡 {r['action']}")
    return "\n".join(lines)

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print(build_line_block())
