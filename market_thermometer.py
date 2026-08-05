#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market Thermometer — 台股市場溫度計
═════════════════════════════════════════════════
① 成交量天險警報：上市成交金額(TWSE官方) + 估算合計(上櫃≈+18%)
   合計 ≥ 1.5 兆 → 🌋 天險警報（歷史上量能天險常見短線高點）
② 87MA 雙指數強弱：加權(^TWII) / 櫃買(^TWOII) 是否 > 87 日均線
   雙上=🟢雙多頭 / 大上小下=🟡大強小弱 / 大下小上=🟡小強大弱 / 雙下=🔴雙空頭

輸出 market_thermometer.json 給 V2/V4.3 picker 附加顯示
"""
import sys, io, os, json, urllib.request
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(ROOT, "market_thermometer.json")

SKY_THRESHOLD = 1.5e12      # 合計 1.5 兆天險
OTC_RATIO = 1.18            # 合計 ≈ 上市 × 1.18（上櫃歷史占比估算）
MA_LEN = 87
UA = {"User-Agent": "Mozilla/5.0"}


def fetch_twse_turnover():
    """TWSE FMTQIK 當月（月初補抓上月）→ (date_str, 上市成交金額)"""
    rows = []
    today = dt.date.today()
    months = [today]
    if today.day <= 3:
        months.append((today.replace(day=1) - dt.timedelta(days=1)))
    for m in reversed(months):
        u = (f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
             f"?date={m.strftime('%Y%m%d')}&response=json")
        try:
            d = json.loads(urllib.request.urlopen(
                urllib.request.Request(u, headers=UA), timeout=20).read())
            if d.get("stat") == "OK":
                rows += d.get("data", [])
        except Exception as e:
            print(f"  [FMTQIK] {type(e).__name__}", file=sys.stderr)
    if not rows:
        return None, None
    last = rows[-1]
    return last[0], float(last[2].replace(",", ""))   # (ROC日期, 金額)


def fetch_index_vs_ma(ticker):
    """yfinance 指數 → (close, ma87, above)"""
    import yfinance as yf
    df = yf.download(ticker, period="7mo", auto_adjust=True,
                     progress=False, threads=False, group_by="column")
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    cl = df["Close"].dropna()
    if len(cl) < MA_LEN:
        return None
    close = float(cl.iloc[-1])
    ma = float(cl.iloc[-MA_LEN:].mean())
    return {"close": close, "ma87": ma, "above": close > ma,
            "gap_pct": (close / ma - 1) * 100}


def main():
    print("[1/2] 上市成交金額（TWSE 官方）...")
    tdate, twse_amt = fetch_twse_turnover()
    combined = twse_amt * OTC_RATIO if twse_amt else None
    sky = bool(combined and combined >= SKY_THRESHOLD)
    if twse_amt:
        print(f"  {tdate} 上市 {twse_amt/1e12:.3f} 兆"
              f" → 估合計 {combined/1e12:.3f} 兆"
              f" {'🌋 天險!' if sky else '(未達 1.5 兆)'}")

    print("[2/2] 87MA 雙指數強弱...")
    twii = twoii = None
    try:
        twii = fetch_index_vs_ma("^TWII")
        if twii:
            print(f"  加權 {twii['close']:,.0f} vs 87MA {twii['ma87']:,.0f}"
                  f" → {'✅上' if twii['above'] else '⚠️下'} ({twii['gap_pct']:+.1f}%)")
    except Exception as e:
        print(f"  ^TWII fail: {type(e).__name__}")
    try:
        twoii = fetch_index_vs_ma("^TWOII")
        if twoii:
            print(f"  櫃買 {twoii['close']:,.2f} vs 87MA {twoii['ma87']:,.2f}"
                  f" → {'✅上' if twoii['above'] else '⚠️下'} ({twoii['gap_pct']:+.1f}%)")
    except Exception as e:
        print(f"  ^TWOII fail: {type(e).__name__}")

    # 強弱矩陣
    strength = None
    if twii and twoii:
        a, b = twii["above"], twoii["above"]
        strength = ("🟢 雙多頭" if a and b else
                    "🟡 大強小弱" if a and not b else
                    "🟡 小強大弱(投機盤)" if b and not a else
                    "🔴 雙空頭")
        print(f"  強弱判定: {strength}")

    out = {
        "date": dt.date.today().isoformat(),
        "turnover": {
            "twse_date_roc": tdate,
            "twse_amount": twse_amt,
            "combined_est": combined,
            "sky_alert": sky,
            "threshold": SKY_THRESHOLD,
            "note": "合計=上市×1.18 估算(TPEX API 擋外部連線)",
        },
        "ma87": {"twii": twii, "twoii": twoii, "strength": strength},
    }
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"💾 已輸出 {OUT_PATH}")


def build_block(data=None):
    """給 picker 用的 LINE 區塊（2-4 行）"""
    if data is None:
        try:
            with io.open(OUT_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
    lines = ["🌡️ 市場溫度計"]
    t = data.get("turnover") or {}
    if t.get("twse_amount"):
        amt = t["twse_amount"] / 1e12
        comb = (t.get("combined_est") or 0) / 1e12
        if t.get("sky_alert"):
            lines.append(f"  🌋 量能天險! 合計約{comb:.2f}兆(≥1.5兆)")
            lines.append("  ⚠️ 歷史高檔訊號→不追高/分批鎖利")
        else:
            lines.append(f"  量能: 上市{amt:.2f}兆 合計約{comb:.2f}兆 (天險1.5兆)")
    m = data.get("ma87") or {}
    twii, twoii = m.get("twii"), m.get("twoii")
    if twii and twoii:
        s1 = "✅" if twii["above"] else "⚠️"
        s2 = "✅" if twoii["above"] else "⚠️"
        lines.append(f"  加權{s1}87MA({twii['gap_pct']:+.0f}%)"
                     f" 櫃買{s2}87MA({twoii['gap_pct']:+.0f}%)"
                     f" → {m.get('strength','')}")
    return "\n".join(lines) if len(lines) > 1 else None


if __name__ == "__main__":
    main()
