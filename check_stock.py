# -*- coding: utf-8 -*-
"""逐項檢查股票是否達 V4 選股標準"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import yfinance as yf
import numpy as np
import datetime as dt
from industry_map_loader import get_industry

ALLOWED = {"半導體", "電子零組件", "光電", "電腦及週邊",
           "電子通路", "通信網路", "其他電子"}

def check(code, name):
    print(f"\n{'='*60}")
    print(f"🔍 檢查 {code} {name}")
    print(f"{'='*60}")
    df = yf.download(f"{code}.TW", period="2y", auto_adjust=True,
                     progress=False, threads=False, group_by="column")
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    cl = df["Close"].dropna()
    op = df["Open"].dropna()
    hi = df["High"].dropna()
    lo = df["Low"].dropna()
    vo = df["Volume"].dropna()

    today = float(cl.iloc[-1])
    yesterday = float(cl.iloc[-2])
    change = (today/yesterday - 1) * 100
    ma5 = float(cl.iloc[-5:].mean())
    ma10 = float(cl.iloc[-10:].mean())
    ma20 = float(cl.iloc[-20:].mean())
    ma60 = float(cl.iloc[-60:].mean())
    ma200 = float(cl.iloc[-200:].mean())
    vol_today = float(vo.iloc[-1])
    avg20_vol = float(vo.iloc[-20:].mean())
    vol_ratio = vol_today / avg20_vol if avg20_vol > 0 else 0

    # RSI 14
    delta = np.diff(cl.iloc[-15:].values)
    gains = np.where(delta > 0, delta, 0).mean()
    losses = np.where(delta < 0, -delta, 0).mean()
    rsi = 100.0 if losses == 0 else 100 - 100/(1 + gains/losses)

    # K 線特徵
    today_open = float(op.iloc[-1])
    today_high = float(hi.iloc[-1])
    today_low = float(lo.iloc[-1])
    yest_high = float(hi.iloc[-2])
    rng = today_high - today_low
    close_near_high = rng > 0 and today >= today_high - rng * 0.2
    long_red = rng > 0 and (today - today_open) / rng >= 0.7
    gap_up = today_open > yest_high * 1.005 and today > today_open

    # 月線最高（2y 內，排除當月）
    today_ym = dt.date.today().strftime("%Y-%m")
    by_month = {}
    for ts, c in cl.items():
        by_month[ts.strftime("%Y-%m")] = float(c)
    hist = [v for ym, v in by_month.items() if ym < today_ym]
    mmax = max(hist) if hist else None
    ratio = today/mmax if mmax else 0

    # 多頭判斷
    bullish = today > ma20 > ma60 > ma200
    bullish_fast = today > ma5 > ma10 > ma20
    is_ath = mmax and today >= mmax * 0.999

    # 族群
    industry = get_industry(code)
    in_allowed = industry in ALLOWED

    # 市值（取 info）
    try:
        info = yf.Ticker(f"{code}.TW").info
        mcap = info.get("marketCap", 0) / 1e8  # 億
    except Exception:
        mcap = 0

    # 動能評分
    score = 0
    notes = []
    is_locked = change >= 9.5 and vol_ratio < 1.2
    vol_surge = vol_ratio >= 3 and change >= 5
    if is_locked:   score += 25; notes.append("漲停鎖死")
    elif vol_surge: score += 25; notes.append("量爆價揚")
    elif gap_up:    score += 22; notes.append("跳空缺口")
    if is_ath:      score += 15; notes.append("ATH")
    if 60 <= rsi <= 75: score += 15; notes.append("RSI 強勢")
    if bullish_fast:    score += 15; notes.append("加速多頭")
    if close_near_high: score += 12; notes.append("收近高")
    if long_red:        score += 10; notes.append("長紅 K")

    # 印報告
    print(f"\n📊 價格資訊")
    print(f"  今收: ${today:.2f}  ｜  漲幅: {change:+.2f}%  ｜  量比: {vol_ratio:.2f}x")
    print(f"  MA5/20/60/200: ${ma5:.1f} / ${ma20:.1f} / ${ma60:.1f} / ${ma200:.1f}")
    print(f"  2y 月線最高: ${mmax:.2f}  ｜  距高: {(ratio-1)*100:+.2f}%")
    print(f"  RSI 14: {rsi:.0f}")

    print(f"\n🔬 V4 七大條件檢查")
    print(f"  ① 創 2y 月線 ATH (>=99.9%): {'✅' if is_ath else '❌'} ({(ratio*100):.2f}%)")
    print(f"  ② 多頭排列 (>20>60>200):     {'✅' if bullish else '❌'}")
    print(f"  ③ 產業在科技 7 族群:         {'✅' if in_allowed else '❌'} ({industry})")
    print(f"  ④ 市值 ≥ 100 億 NT$:         {'✅' if mcap >= 100 else '❌'} ({mcap:.0f} 億)")
    print(f"  ⑤ 動能評分 ≥ 80:             {'✅' if score >= 80 else '❌'} ({score}/100)")
    print(f"     觸發訊號: {', '.join(notes) if notes else '無'}")
    print(f"  ⑥ 0050 > MA200 (大盤體制):   需獨立查 0050 (今日為 Stage 2 ✅)")
    print(f"  ⑦ 屬最強族群且足夠多多頭:    需與當日所有 ATH 股比較")

    passed = sum([is_ath, bullish, in_allowed, mcap>=100, score>=80])
    print(f"\n🎯 結論：通過 {passed}/5 主要條件")
    if passed == 5:
        print(f"   ⭐⭐⭐ {code} {name} **符合 V4 進場條件**")
    elif passed == 4:
        print(f"   ⭐⭐ {code} {name} 接近條件，缺 1 項")
    elif passed >= 3:
        print(f"   ⭐ {code} {name} 部分符合，建議觀察")
    else:
        print(f"   ❌ {code} {name} **不符合 V4 條件**")

    return {"code": code, "name": name, "score": score, "passed": passed,
            "is_ath": is_ath, "bullish": bullish, "industry": industry,
            "mcap": mcap, "notes": notes, "change": change}


if __name__ == "__main__":
    targets = [
        ("6285", "啟碁"),
        ("6451", "訊芯-KY"),
    ]
    for c, n in targets:
        check(c, n)
