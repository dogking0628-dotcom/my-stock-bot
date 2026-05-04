#!/usr/bin/env python3
"""
台股突破篩選器 — 含 3,292 筆歷史統計濾網
─────────────────────────────────────────
統計實證的進場條件：
  🟢 高機率：量 ≥ 1.5x  + 漲 2-10%  + RSI 60-80  + 多頭 20-100%
  🟡 普通  ：少於 4 個條件達成（單純 ATH + MA 多頭排列）
  🔴 假突破：量 < 1x  OR  RSI > 85  OR  漲過頭 (>10%)

歷史統計（3,292 筆突破事件）：
  3-5x 爆量 → 強漲機率 32%
  5-8% 漲幅 → 強漲機率 40%
  多頭60-100% → 強漲機率 44%
"""
import yfinance as yf
import numpy as np

# 台股觀察池 — 上市 + 上櫃 200+ 檔（fetch_tw 自動嘗試 .TW / .TWO）
TW_UNIVERSE = [
    # ═══ 上市權值股／半導體 ═══
    ("2330","台積電"),("2454","聯發科"),("2303","聯電"),("3711","日月光投控"),
    ("3034","聯詠"),("3661","世芯-KY"),("3231","緯創"),("2382","廣達"),
    ("2376","技嘉"),("3017","奇鋐"),("2308","台達電"),("6669","緯穎"),
    ("3653","健策"),("2379","瑞昱"),("8046","南電"),("2474","可成"),
    ("3443","創意"),("3008","大立光"),("2317","鴻海"),("2357","華碩"),
    ("4938","和碩"),("2327","國巨"),("2449","京元電子"),("2354","鴻準"),
    ("2356","英業達"),("2377","微星"),("2383","台光電"),("3037","欣興"),
    ("3035","智原"),("3105","穩懋"),("3406","玉晶光"),("4904","遠傳"),
    ("4958","臻鼎-KY"),("6176","瑞儀"),("6285","啟碁"),("6526","達發"),
    ("6531","愛普*"),("6605","帝寶"),("8210","勤誠"),("3045","台灣大"),
    ("3702","大聯大"),("2347","聯強"),("2353","宏碁"),("2360","致茂"),
    ("2455","全新"),("2812","台中銀"),("3014","聯陽"),("3023","信邦"),
    ("3036","文曄"),("3044","健鼎"),("3189","景碩"),("4919","新唐"),
    ("5388","中磊"),("6239","力成"),("6488","環球晶"),
    # ═══ 金融保險 ═══
    ("2881","富邦金"),("2882","國泰金"),("2891","中信金"),("2884","玉山金"),
    ("2885","元大金"),("2886","兆豐金"),("2880","華南金"),("2887","台新金"),
    ("2890","永豐金"),("2892","第一金"),("5880","合庫金"),("5876","上海商銀"),
    ("2801","彰銀"),("2812","台中銀"),("2823","中壽"),("2845","遠東銀"),
    ("2849","安泰銀"),("2867","三商壽"),("2888","新光金"),("2889","國票金"),
    ("5820","日盛金"),("2832","台產"),
    # ═══ 電信／民生／傳產 ═══
    ("2412","中華電"),("1101","台泥"),("1102","亞泥"),
    ("1216","統一"),("1301","台塑"),("1303","南亞"),("1326","台化"),
    ("2002","中鋼"),("2105","正新"),("2207","和泰車"),("9904","寶成"),
    ("9910","豐泰"),("2912","統一超"),("2603","長榮"),("2609","陽明"),
    ("2615","萬海"),("2618","長榮航"),("2610","華航"),("2633","台灣高鐵"),
    ("9921","巨大"),("9914","美利達"),("1722","台肥"),("1723","中碳"),
    ("1227","佳格"),("1234","黑松"),("9907","統一實"),("2393","億光"),
    # ═══ 生技醫療 ═══
    ("4137","麗豐-KY"),("4147","中裕"),("6446","藥華藥"),
    ("4128","中天"),("4729","熒茂"),("6505","台塑化"),
    # ═══ 上櫃 .TWO 主力 ═══
    ("8299","群聯"),("5347","世界"),("6533","晶心科"),("5274","信驊"),
    ("6770","力積電"),("4763","材料-KY"),("5269","祥碩"),("6789","采鈺"),
    ("6121","新普"),("8086","宏捷科"),("3293","鈊象"),("6415","矽力-KY"),
    ("8069","元太"),("3661","世芯-KY"),("4966","譜瑞-KY"),("6147","頎邦"),
    ("6477","安集"),("6491","晶碩"),("6531","愛普"),("6573","虹堡"),
    ("6679","鈺太"),("6691","洋基工程"),("6741","91APP"),("6762","達發"),
    ("8054","安國"),("8064","東捷"),("8341","日友"),("3105","穩懋"),
    ("3402","漢科"),("4977","眾達-KY"),("5483","中美晶"),("5904","寶雅"),
    ("8044","網家"),("8358","金居"),("8454","富邦媒"),
    ("3443","創意"),("3680","家登"),("4906","正文"),
    ("5347","世界"),("6231","系微"),
]
# 去重
TW_UNIVERSE = list(dict.fromkeys(TW_UNIVERSE))

def rsi(closes, period=14):
    delta = np.diff(closes)
    if len(delta) < period: return 50.0
    up = np.where(delta > 0, delta, 0)
    dn = np.where(delta < 0, -delta, 0)
    avg_up = up[-period:].mean()
    avg_dn = dn[-period:].mean()
    if avg_dn == 0: return 100.0
    return 100 - 100 / (1 + avg_up/avg_dn)

def fetch_tw(ticker):
    """抓 TW 還原權值（手動處理配息）— 自動嘗試 .TW 與 .TWO"""
    df = None; t = None
    # 上市優先，再試上櫃
    for suffix in (".TW", ".TWO"):
        try:
            tk = yf.Ticker(f"{ticker}{suffix}")
            d = tk.history(period="2y", auto_adjust=False)
            if not d.empty and len(d) > 100:
                t = tk; df = d
                break
        except: continue
    if df is None or df.empty: return None
    try:
        pass
        closes = df["Close"].copy()
        volumes = df["Volume"]
        # 手動還原（向下調整過去價格）
        divs = t.dividends
        if not divs.empty:
            # 統一去時區
            cl_idx_naive = (closes.index.tz_convert(None)
                           if closes.index.tz else closes.index)
            divs_naive = divs.copy()
            if divs_naive.index.tz:
                divs_naive.index = divs_naive.index.tz_convert(None)
            for ex_date, div_amount in divs_naive.items():
                mask = cl_idx_naive < ex_date
                if mask.sum() == 0: continue
                last_before = closes.iloc[mask].iloc[-1]
                if last_before <= 0: continue
                factor = 1 - div_amount / last_before
                if 0 < factor <= 1:
                    closes.iloc[mask] = closes.iloc[mask] * factor
        return closes.dropna().values.astype(float), volumes.dropna().values.astype(float)
    except: return None

def analyze(ticker, name):
    """完整分析一檔股票，返回所有特徵與分類"""
    data = fetch_tw(ticker)
    if not data: return None
    c, v = data
    if len(c) < 200: return None

    today = c[-1]; prev = c[-2]
    change = (today/prev - 1) * 100
    avg_vol_20 = v[-20:].mean()
    vol_ratio = v[-1]/avg_vol_20 if avg_vol_20 > 0 else 0
    rsi_val = rsi(c, 14)
    ma5, ma20, ma60, ma200 = c[-5:].mean(), c[-20:].mean(), c[-60:].mean(), c[-200:].mean()
    bull_strength = (ma5/ma200 - 1) * 100
    hist_max = c.max()
    is_ath = today >= hist_max * 0.999
    is_bullish = ma5 > ma20 > ma60 > ma200

    # ── 統計實證濾網（4 個條件）──
    pass_volume = vol_ratio >= 1.5
    pass_change = 2 <= change <= 10  # 中段漲幅最強
    pass_rsi    = rsi_val < 80
    pass_bull   = 20 <= bull_strength <= 100  # 不太弱也不過熱

    # ── 假突破嫌疑 ──
    is_fake = (
        vol_ratio < 1.0 or          # 量縮突破
        rsi_val > 85 or             # 嚴重過熱
        change > 10 or              # 噴出末端
        bull_strength > 100         # 漲過頭
    )

    # ── 分類 ──
    n_pass = sum([pass_volume, pass_change, pass_rsi, pass_bull])
    if is_fake:
        category = "fake"   # 🔴 假突破嫌疑
    elif n_pass == 4:
        category = "high"   # 🟢 高機率
    elif n_pass >= 2:
        category = "medium" # 🟡 普通
    else:
        category = "low"    # 🟠 低機率

    return {
        "ticker": ticker, "name": name,
        "close": today, "change": change,
        "vol_ratio": vol_ratio, "rsi": rsi_val,
        "ma5": ma5, "ma200": ma200, "bull_strength": bull_strength,
        "is_ath": is_ath, "is_bullish": is_bullish,
        "pass_volume": pass_volume, "pass_change": pass_change,
        "pass_rsi": pass_rsi, "pass_bull": pass_bull,
        "n_pass": n_pass, "is_fake": is_fake,
        "category": category,
        "stop_price": ma5 * 0.98,
        "entry_price": ma5,
    }

# ── 個人觀察清單（無條件每日追蹤）──
WATCHLIST = [
    ("2449","京元電子"),
    ("8299","群聯"),
    ("2327","國巨"),
    ("6488","環球晶"),
    ("2454","聯發科"),
]

def momentum_score(a):
    """0-90 分動能評分（基於 3,292 筆統計）"""
    s = 0
    if a["vol_ratio"] >= 3: s += 30
    elif a["vol_ratio"] >= 2: s += 20
    elif a["vol_ratio"] >= 1.5: s += 12
    elif a["vol_ratio"] < 1: s -= 5
    if 5 <= a["change"] <= 8: s += 25
    elif 2 <= a["change"] < 5: s += 15
    elif a["change"] > 10: s += 5
    elif 0 < a["change"] < 2: s += 8
    if 60 <= a["rsi"] <= 75: s += 20
    elif 75 < a["rsi"] <= 80: s += 12
    elif a["rsi"] > 85: s -= 10
    if 20 <= a["bull_strength"] <= 60: s += 15
    elif 60 < a["bull_strength"] <= 100: s += 10
    elif a["bull_strength"] > 100: s -= 8
    return max(0, s)

def scan_watchlist():
    """每日無條件追蹤 5 檔個人觀察股，計算動能"""
    out = []
    for tk, name in WATCHLIST:
        a = analyze(tk, name)
        if not a: continue
        a["score"] = momentum_score(a)
        # 觸發條件：分數>50 + ATH + 多頭
        a["is_trigger"] = a["score"] > 50 and a["is_ath"] and a["is_bullish"]
        out.append(a)
    out.sort(key=lambda x: -x["score"])
    return out

def scan_all():
    """掃描全部觀察池，返回符合 ATH+多頭排列的清單（按分類）"""
    results = {"high": [], "medium": [], "low": [], "fake": []}
    for tk, name in TW_UNIVERSE:
        a = analyze(tk, name)
        if not a: continue
        if a["is_ath"] and a["is_bullish"]:
            results[a["category"]].append(a)
    # 各組按漲幅排序
    for k in results:
        results[k].sort(key=lambda x: -x["change"])
    return results

def build_watchlist_block(watchlist):
    """個人觀察清單區塊（每日固定 5 檔追蹤）"""
    if not watchlist:
        return "📌 個人觀察清單：資料載入失敗"
    lines = ["📌 個人觀察清單（5 檔追蹤）"]
    has_trigger = any(a["is_trigger"] for a in watchlist)
    if has_trigger:
        lines.append("  🚨 有股票觸發進場條件！")
    for a in watchlist:
        if a["is_trigger"]:
            tag = "🚨 進場！"
        elif a["score"] >= 50:
            tag = "🟢 高動能"
        elif a["score"] >= 30:
            tag = "🟡 普通"
        else:
            tag = "🔴 弱"
        ath_mark = "✅" if a["is_ath"] else "  "
        bull_mark = "✅" if a["is_bullish"] else "  "
        lines.append(f"  {tag} {a['ticker']} {a['name']:<6} {a['score']:>2}/90"
                     f"  ${a['close']:.0f} {a['change']:+.1f}%"
                     f"  ATH{ath_mark} 多頭{bull_mark}")
    return "\n".join(lines)

def build_line_block(results):
    """為 LINE 訊息產生台股突破區塊（精簡版）"""
    high = results["high"]; medium = results["medium"]
    low = results["low"]; fake = results["fake"]
    total = len(high) + len(medium) + len(low) + len(fake)

    lines = ["🇹🇼 台股突破篩選 (統計濾網)"]
    if total == 0:
        lines.append("  ⏸ 今日無創新高+多頭排列個股")
        return "\n".join(lines)

    if high:
        lines.append(f"  🟢 高機率 ({len(high)}) — 量爆+漲幅佳+多頭強")
        for a in high[:5]:
            lines.append(f"    {a['ticker']} {a['name']}  ${a['close']:.0f}"
                         f" {a['change']:+.1f}% 量{a['vol_ratio']:.1f}x")
            lines.append(f"      💡 等回測 5MA(${a['entry_price']:.0f}) 進場")
    if medium:
        lines.append(f"  🟡 普通 ({len(medium)}) — 部分條件達成")
        for a in medium[:5]:
            lines.append(f"    {a['ticker']} {a['name']}  ${a['close']:.0f}"
                         f" {a['change']:+.1f}% RSI{a['rsi']:.0f}")
    if fake:
        lines.append(f"  🔴 假突破嫌疑 ({len(fake)}) — 不建議追")
        fake_str = ", ".join(f"{a['ticker']}" for a in fake[:8])
        lines.append(f"    {fake_str}")
    if low:
        lines.append(f"  🟠 低機率 ({len(low)})")
        low_str = ", ".join(f"{a['ticker']}" for a in low[:8])
        lines.append(f"    {low_str}")

    return "\n".join(lines)

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print("掃描台股觀察池...")
    res = scan_all()
    print(build_line_block(res))
    print("\n--- 詳細 ---")
    for cat in ["high", "medium", "low", "fake"]:
        emoji = {"high":"🟢","medium":"🟡","low":"🟠","fake":"🔴"}[cat]
        for a in res[cat]:
            print(f"{emoji} {a['ticker']} {a['name']:<8} "
                  f"${a['close']:.0f} {a['change']:+.1f}% "
                  f"量{a['vol_ratio']:.1f}x RSI{a['rsi']:.0f} "
                  f"多頭{a['bull_strength']:+.0f}%")
