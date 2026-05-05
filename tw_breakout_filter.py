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
import os, sys

# 嘗試使用 Shioaji（永豐官方 API）— 無設定時 fallback 到 yfinance
USE_SHIOAJI = bool(os.environ.get("SHIOAJI_API_KEY") and os.environ.get("SHIOAJI_SECRET_KEY"))
if USE_SHIOAJI:
    try:
        import shioaji_data
        print("[tw_filter] 🚀 使用 Shioaji 永豐 API（速度 50x）", file=sys.stderr)
    except ImportError:
        USE_SHIOAJI = False
        print("[tw_filter] ⚠️ 找不到 shioaji_data 模組，改用 yfinance", file=sys.stderr)

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
    """抓 TW 還原權值 — Shioaji 為主，無資料直接跳過（不再用 yfinance fallback 避免噪音）"""
    if USE_SHIOAJI:
        try:
            bars = shioaji_data.fetch_kbars(ticker)
            if bars and len(bars) > 100:
                import numpy as np
                closes  = np.array([b["close"]  for b in bars])
                volumes = np.array([b["volume"] for b in bars])
                dates   = [b["date"] for b in bars]
                return closes, volumes, dates
        except Exception:
            pass  # 靜默失敗（主流股 95%+ 正常）
        return None  # Shioaji 沒資料 → 直接跳過，不用 yfinance fallback

    # 沒設 Shioaji 才用 yfinance（本機測試用）
    import contextlib, io as _io
    df = None; t = None
    for suffix in (".TW", ".TWO"):
        try:
            with contextlib.redirect_stderr(_io.StringIO()):  # 抑制 yfinance 噪音
                tk = yf.Ticker(f"{ticker}{suffix}")
                d = tk.history(period="5y", auto_adjust=False)
            if not d.empty and len(d) > 100:
                t = tk; df = d
                break
        except: continue
    if df is None or df.empty: return None
    try:
        closes = df["Close"].copy()
        volumes = df["Volume"]
        divs = t.dividends
        if not divs.empty:
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
        closes = closes.dropna()
        dates = [d.strftime("%Y-%m-%d") for d in closes.index]
        return closes.values.astype(float), volumes.dropna().values.astype(float), dates
    except: return None

def _monthly_max_close(closes, dates):
    """從日線取每月最後一個交易日的收盤，回傳歷史最高（排除當月未收完）"""
    import datetime as _dt
    by_month = {}
    for d, c in zip(dates, closes):
        ym = d[:7]  # YYYY-MM
        by_month[ym] = c  # 後寫覆蓋 → 月底收盤
    today_ym = _dt.datetime.now().strftime("%Y-%m")
    historical = [v for ym, v in by_month.items() if ym < today_ym]
    return max(historical) if historical else None

def analyze(ticker, name):
    """完整分析一檔股票，返回所有特徵與分類"""
    data = fetch_tw(ticker)
    if not data: return None
    c, v, dates = data
    if len(c) < 200: return None

    today = c[-1]; prev = c[-2]
    change = (today/prev - 1) * 100
    avg_vol_20 = v[-20:].mean()
    vol_ratio = v[-1]/avg_vol_20 if avg_vol_20 > 0 else 0
    rsi_val = rsi(c, 14)
    ma5, ma20, ma60, ma200 = c[-5:].mean(), c[-20:].mean(), c[-60:].mean(), c[-200:].mean()
    ma120 = c[-120:].mean() if len(c) >= 120 else ma200
    bull_strength = (today/ma200 - 1) * 100  # 用 200MA 為基準

    # 🆕 2 年日線 ATH（504 日內最高）
    last_504 = c[-504:] if len(c) >= 504 else c[:-1]
    daily_2y_max = float(np.max(last_504)) if len(last_504) > 0 else None
    monthly_max = _monthly_max_close(c, dates)
    is_ath = (daily_2y_max is not None) and (today >= daily_2y_max * 0.999)
    # 🆕 多頭排列：今日收盤 > 20MA > 60MA > 200MA（放寬版，去掉 5MA 嚴格性）
    is_bullish = today > ma20 > ma60 > ma200

    # ── 漲停鎖死偵測（台股獨有 +10% 限制）──
    # 漲幅 ≥ 9.5% 視為觸及漲停板，量縮代表「鎖死」= 賣壓真空 = 最強訊號
    is_limit_up = change >= 9.5
    is_locked_limit_up = is_limit_up and vol_ratio < 1.2  # 漲停 + 量縮 = 鎖死

    # ── 統計實證濾網（4 個條件，漲停板特例）──
    pass_volume = vol_ratio >= 1.5 or is_locked_limit_up  # 漲停鎖死視同 pass
    pass_change = 2 <= change <= 10
    pass_rsi    = rsi_val < 80 or is_limit_up  # 漲停板放寬 RSI 要求
    pass_bull   = 20 <= bull_strength <= 100

    # ── 假突破嫌疑（漲停板免責）──
    is_fake = (not is_limit_up) and (
        vol_ratio < 1.0 or          # 真量縮（非漲停）
        rsi_val > 85 or             # 嚴重過熱
        bull_strength > 100         # 漲過頭
    )

    # ── 分類 ──
    n_pass = sum([pass_volume, pass_change, pass_rsi, pass_bull])
    if is_locked_limit_up and bull_strength <= 100:
        category = "limit_up"  # 🚀 漲停鎖死（最強）
    elif is_fake:
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
        "monthly_ath_5y": monthly_max,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma200": ma200,
        "bull_strength": bull_strength,
        "is_ath": is_ath, "is_bullish": is_bullish,
        "pass_volume": pass_volume, "pass_change": pass_change,
        "pass_rsi": pass_rsi, "pass_bull": pass_bull,
        "n_pass": n_pass, "is_fake": is_fake,
        "category": category,
        "stop_price": ma20 * 0.98,
        "entry_price": ma20,
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
    """0-90 分動能評分（含漲停鎖死特例）"""
    s = 0
    is_limit_up = a["change"] >= 9.5
    is_locked = is_limit_up and a["vol_ratio"] < 1.2
    # 漲停鎖死 = 滿分量能（最強訊號）
    if is_locked:
        s += 30
    elif a["vol_ratio"] >= 3: s += 30
    elif a["vol_ratio"] >= 2: s += 20
    elif a["vol_ratio"] >= 1.5: s += 12
    elif a["vol_ratio"] < 1 and not is_limit_up: s -= 5
    # 漲幅
    if is_limit_up: s += 30  # 漲停滿分
    elif 5 <= a["change"] <= 8: s += 25
    elif 2 <= a["change"] < 5: s += 15
    elif 0 < a["change"] < 2: s += 8
    # RSI
    if 60 <= a["rsi"] <= 75: s += 20
    elif 75 < a["rsi"] <= 80: s += 12
    elif a["rsi"] > 85 and not is_limit_up: s -= 10
    elif a["rsi"] > 85 and is_limit_up: s += 5  # 漲停板過熱合理
    # 多頭強度
    if 20 <= a["bull_strength"] <= 60: s += 15
    elif 60 < a["bull_strength"] <= 100: s += 10
    elif a["bull_strength"] > 100: s -= 8
    return max(0, s)

def scan_watchlist():
    """每日無條件追蹤 5 檔個人觀察股，計算動能（舊版，保留兼容）"""
    out = []
    for tk, name in WATCHLIST:
        a = analyze(tk, name)
        if not a: continue
        a["score"] = momentum_score(a)
        a["is_trigger"] = a["score"] > 50 and a["is_ath"] and a["is_bullish"]
        out.append(a)
    out.sort(key=lambda x: -x["score"])
    return out

# ───── 進階濾網：股本 + 產業族群 ─────

# ⚠️ 黑名單清空 — 已改用「市值 + 流動性」自動過濾
# 信驊、創意等股本小但市值大的「實質大型股」不能用股本判斷
SMALL_CAP_BLACKLIST = set()

# 動態股本快取（每週更新一次）
import os, json
CAPITAL_CACHE_PATH = os.path.join(os.path.dirname(__file__), "capital_cache.json")

def load_capital_cache():
    if not os.path.exists(CAPITAL_CACHE_PATH):
        return {}
    try:
        with open(CAPITAL_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def save_capital_cache(cache):
    with open(CAPITAL_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def fetch_stock_metrics(ticker):
    """
    用 yfinance 抓股本/市值/成交量（一次性）
    回傳：(capital_億, market_cap_億, avg_volume_NT$)
    """
    try:
        import yfinance as yf
        for suffix in (".TW", ".TWO"):
            tk = yf.Ticker(f"{ticker}{suffix}")
            info = tk.info
            shares = info.get("sharesOutstanding") or 0
            mcap   = info.get("marketCap") or 0   # 已是 NT$
            vol    = info.get("averageVolume") or 0
            price  = info.get("regularMarketPrice") or info.get("previousClose") or 0
            if shares > 0:
                capital_nt = shares * 10
                avg_vol_nt = vol * price  # 每日成交額（NT$）
                return (capital_nt / 1e8,    # 股本(億)
                        mcap / 1e8,           # 市值(億)
                        avg_vol_nt / 1e4)     # 成交額(萬)
    except: pass
    return (None, None, None)

# 過濾門檻（市值+流動性 為主，股本不單獨判斷）
# 例：創意 3443 股本 13 億但市值 6,479 億 → 應保留
MIN_MARKET_CAP  = 200     # 市值 ≥ 200 億 NT$（主要濾網）
MIN_AVG_VOLUME  = 5000    # 日均成交額 ≥ NT$5,000 萬

def is_small_cap(ticker):
    """雙層濾網：市值 + 流動性（任一不過就濾掉）"""
    # 1. 黑名單即時排除
    if ticker in SMALL_CAP_BLACKLIST:
        return True
    # 2. 動態查 cache
    cache = load_capital_cache()
    metrics = cache.get(ticker)
    if metrics is None or not isinstance(metrics, dict):
        cap, mcap, avg_vol = fetch_stock_metrics(ticker)
        metrics = {"capital": cap, "mcap": mcap, "avg_vol": avg_vol}
        cache[ticker] = metrics
        save_capital_cache(cache)
    mcap    = metrics.get("mcap")
    avg_vol = metrics.get("avg_vol")
    # 市值 < 200 億 → 剔除
    if mcap is not None and mcap < MIN_MARKET_CAP:
        return True
    if avg_vol is not None and avg_vol < MIN_AVG_VOLUME:
        return True
    return False

# 產業族群分類（用於「整個族群連漲 3 天」確認）
INDUSTRY_GROUPS = {
    "半導體": ["2330","2454","2303","3711","3034","3661","3443","6488",
              "5347","8046","6415","2449","3037","3105","6531","5274"],
    "AI伺服器": ["2317","2382","2376","3231","6669","3017","2356","2354",
                "3653","4938","2357"],
    "面板": ["3481","2409","2474","8069"],
    "金融": ["2881","2882","2891","2884","2885","2886","2880","2887","2890",
            "2892","5880","5876","2812","2823","2867","2888","2889"],
    "電信": ["2412","3045","4904"],
    "塑化": ["1101","1102","1216","1301","1303","1326"],
    "鋼鐵": ["2002","2027","2028"],
    "航運": ["2603","2609","2615","2618","2610"],
    "通路": ["2912","2491","8454","8044"],
    "生技": ["4137","4147","6446","4128"],
    "儲存": ["8299","2316","6121"],
    "成衣": ["9904","9910"],
    "汽車": ["2207","2204","1536"],
}

def get_industry(ticker):
    """回傳該股票所屬產業，無歸類的回 None"""
    for industry, tickers in INDUSTRY_GROUPS.items():
        if ticker in tickers:
            return industry
    return None

def industry_3day_strength(scan_results, ticker):
    """檢查該產業是否連續 3 天強勢（同產業股票過去 3 天平均漲幅 > 1%）"""
    industry = get_industry(ticker)
    if not industry: return False, None
    members = INDUSTRY_GROUPS[industry]
    # 從本日掃描結果中找同族群股票
    all_stocks = []
    for cat in ["limit_up","high","medium","low","fake"]:
        for s in scan_results.get(cat, []):
            if s["ticker"] in members:
                all_stocks.append(s)
    if len(all_stocks) < 3: return False, industry  # 至少 3 檔同族群有資料
    # 連 3 天強勢 = 該族群當日漲幅平均 > 1% 且至少 3 檔今日漲
    avg_chg = sum(s["change"] for s in all_stocks) / len(all_stocks)
    n_up = sum(1 for s in all_stocks if s["change"] > 0)
    is_strong = avg_chg > 1.0 and n_up >= len(all_stocks) * 0.6
    return is_strong, industry

# ───── 動態 Top 5 追蹤系統 ─────
import os, json
TOP5_STATE_PATH = os.path.join(os.path.dirname(__file__), "tw_top5_state.json")

def load_top5_state():
    if not os.path.exists(TOP5_STATE_PATH):
        return {"date": None, "stocks": {}}
    with open(TOP5_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_top5_state(state):
    with open(TOP5_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def pick_dynamic_topn(scan_results, n=20):
    """
    從 scan_all 結果中挑出動能 Top N（候選）
    濾網（升級版）：
      ① 市值 ≥ 200 億 NT$ + 流動性夠
      ② 整個產業族群連漲 3 天（避免單一拉抬）
      ③ 漲停 / 高機率 / 普通 三類
      ④ 動能評分 ≥ 30
    """
    candidates = []
    for cat in ["limit_up", "high", "medium"]:
        for a in scan_results.get(cat, []):
            a = dict(a)
            if is_small_cap(a["ticker"]):
                continue
            is_strong, industry = industry_3day_strength(scan_results, a["ticker"])
            a["industry"] = industry
            a["industry_strong"] = is_strong
            if industry and not is_strong:
                continue
            a["score"] = momentum_score(a)
            a["category"] = cat
            if a["score"] < 30:
                continue
            candidates.append(a)
    candidates.sort(key=lambda x: -x["score"])
    return candidates[:n]

def pick_dynamic_top5(scan_results):
    """向後相容：取 Top 5"""
    return pick_dynamic_topn(scan_results, 5)

def group_by_industry(stocks):
    """
    將股票清單按產業分組
    回傳 list of (industry, {stocks, count, avg_score, strength})
    依強度排序，未分類歸入「其他」
    """
    groups = {}
    for s in stocks:
        ind = s.get("industry") or "其他"
        if ind not in groups:
            groups[ind] = {"stocks": [], "total_score": 0}
        groups[ind]["stocks"].append(s)
        groups[ind]["total_score"] += s.get("score", 0)
    for ind, g in groups.items():
        g["count"] = len(g["stocks"])
        g["avg_score"] = g["total_score"] / g["count"] if g["count"] else 0
        # 強度 = 檔數 × 平均分（鼓勵多檔同步走強）
        g["strength"] = g["count"] * g["avg_score"]
    return sorted(groups.items(), key=lambda x: -x[1]["strength"])

def recommend_industry(grouped):
    """從分組挑出最強族群"""
    # 排除「其他」（未分類），找有名稱的最強族群
    classified = [(k, v) for k, v in grouped if k != "其他" and v["count"] >= 2]
    if not classified:
        # 退而求其次：取最強（含其他）
        if not grouped: return None
        top = grouped[0]
    else:
        top = classified[0]
    name, info = top
    return {
        "industry": name,
        "count": info["count"],
        "avg_score": round(info["avg_score"], 1),
        "strength": round(info["strength"], 1),
        "top_stocks": [{"ticker": s["ticker"], "name": s["name"],
                        "score": s["score"], "change": s.get("change", 0),
                        "close": s.get("close", 0)}
                       for s in info["stocks"][:5]],
    }

def check_fake_breakout(stock):
    """檢查單檔股票是否出現假突破警訊"""
    warnings = []
    severity = 0  # 0=正常, 1=輕微警告, 2=強警報

    # 警訊 1: 跌破 5MA（短期動能消退）
    if stock["close"] < stock["ma5"]:
        warnings.append(f"❌ 跌破 5MA (${stock['ma5']:.2f})")
        severity = max(severity, 1)

    # 警訊 2: 跌破 20MA（趨勢反轉，建議出場）
    if stock["close"] < stock["ma20"]:
        warnings.append(f"🚨 跌破 20MA (${stock['ma20']:.2f}) — 出場訊號")
        severity = max(severity, 2)

    # 警訊 3: 量縮（量能 < 0.7×）
    if stock["vol_ratio"] < 0.7 and stock["change"] < 1:
        warnings.append(f"📉 量縮 {stock['vol_ratio']:.1f}x")
        severity = max(severity, 1)

    # 警訊 4: RSI 過熱
    if stock["rsi"] > 85:
        warnings.append(f"🔥 RSI 過熱 {stock['rsi']:.0f}")
        severity = max(severity, 1)

    # 警訊 5: 漲過頭
    if stock["bull_strength"] > 120:
        warnings.append(f"🌋 漲過頭 +{stock['bull_strength']:.0f}%")
        severity = max(severity, 1)

    return {"warnings": warnings, "severity": severity}

def update_and_track_top5(scan_results):
    """
    動態追蹤 Top 5 + 回傳 Top 20 候選：
      - 每日重新挑選動能 Top 5（推薦）和 Top 20（候選）
      - 對昨日 Top 5 做假突破檢查（即使今日掉出榜）
      - 回傳：今日 Top 5 推薦 + Top 20 候選 + 昨日掉出股的警報
    """
    import datetime as _dt
    state = load_top5_state()
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    yesterday_stocks = state.get("stocks", {})

    # 取 Top 20 候選，前 5 為推薦
    today_top20 = pick_dynamic_topn(scan_results, n=20)
    today_top5 = today_top20[:5]
    today_codes = {s["ticker"] for s in today_top5}

    # 對「昨日榜上但今日掉出」的股票做假突破檢查
    dropped_warnings = []
    for code, prev_data in yesterday_stocks.items():
        if code in today_codes: continue  # 仍在榜上跳過
        # 重新分析這檔股票看現況
        analysis = analyze(code, prev_data.get("name", code))
        if analysis:
            check = check_fake_breakout(analysis)
            if check["severity"] > 0:
                dropped_warnings.append({
                    "ticker": code,
                    "name": prev_data.get("name", ""),
                    "prev_score": prev_data.get("score", 0),
                    "current_close": analysis["close"],
                    "warnings": check["warnings"],
                    "severity": check["severity"],
                })

    # 對今日 Top 5 也檢查警訊（持有中）
    today_warnings = []
    for s in today_top5:
        check = check_fake_breakout(s)
        if check["severity"] > 0:
            today_warnings.append({
                "ticker": s["ticker"],
                "name": s["name"],
                "warnings": check["warnings"],
                "severity": check["severity"],
            })

    # 儲存今日 Top 5 狀態（明天會用）
    new_state = {
        "date": today,
        "stocks": {s["ticker"]: {"name": s["name"], "score": s["score"],
                                  "close": s["close"], "category": s["category"]}
                   for s in today_top5},
    }
    save_top5_state(new_state)

    return today_top5, dropped_warnings, today_warnings, today_top20

def build_industry_recommend_block(recommended):
    """LINE 推薦族群短訊（一行）"""
    if not recommended: return ""
    stocks_str = ", ".join(s["ticker"] for s in recommended["top_stocks"][:3])
    return (f"🏆 推薦族群：{recommended['industry']}（"
            f"{recommended['count']} 檔，平均 {recommended['avg_score']:.0f}/90）\n"
            f"   主要：{stocks_str}")

def build_top5_block(today_top5, dropped_warnings, today_warnings, top20=None, recommended_industry=None):
    """產生 LINE Top 5 區塊"""
    lines = ["🎯 動能 Top 5 動態追蹤"]
    # 推薦族群
    if recommended_industry:
        lines.append(build_industry_recommend_block(recommended_industry))

    if not today_top5:
        lines.append("  ⏸ 今日無高品質動能股")
        lines.append("  （已濾掉小股本 + 族群不強的雜訊）")
    else:
        cat_emoji = {"limit_up":"🚀","high":"🟢","medium":"🟡"}
        for i, s in enumerate(today_top5, 1):
            ce = cat_emoji.get(s["category"], "⚪")
            ath_mark = "✅" if s.get("is_ath") else " "
            ind = s.get("industry") or "-"
            lines.append(f"  {i}. {ce} {s['ticker']} {s['name']} {s['score']}/90"
                         f"  ${s['close']:.0f} {s['change']:+.1f}%")
            lines.append(f"      🏭 {ind} 族群同步走強  ATH{ath_mark}")

    # 假突破警報區塊
    if today_warnings:
        lines.append("")
        lines.append("⚠️ Top5 內出現警訊：")
        for w in today_warnings:
            lines.append(f"  {w['ticker']} {w['name']}")
            for warn in w["warnings"]:
                lines.append(f"    {warn}")

    # 昨日掉出股票的警報
    if dropped_warnings:
        lines.append("")
        lines.append("🔴 昨日榜單掉出 + 假突破：")
        for w in dropped_warnings[:3]:
            tag = "🚨" if w["severity"] >= 2 else "⚠️"
            lines.append(f"  {tag} {w['ticker']} {w['name']} ${w['current_close']:.0f}")
            for warn in w["warnings"][:2]:
                lines.append(f"    {warn}")

    return "\n".join(lines)

def get_full_universe():
    """動態抓全台股市場（上市+上櫃約 1900+ 檔），失敗時 fallback 到內建 145 檔"""
    try:
        import universe_loader
        full = universe_loader.fetch_tw_universe()
        if full and len(full) > 200:
            return full
    except Exception as e:
        print(f"[tw_filter] full universe load failed: {e}")
    return TW_UNIVERSE

import time

def _quick_screen_batch(tickers_batch):
    """
    批次快速篩選（raw close 寬鬆篩）
    用 0.92 閾值（容忍 8% 配息衰減），讓真正的還原 ATH 候選不被漏掉
    """
    candidates = []
    yf_codes = []
    code_map = {}
    for code, name in tickers_batch:
        # 4 位數股號預設先試 .TW；若 fail Stage 2 會自動 fallback .TWO
        yf_codes.append(f"{code}.TW")
        code_map[f"{code}.TW"] = (code, name)
    try:
        df = yf.download(" ".join(yf_codes), period="2y",
                         auto_adjust=False, progress=False, threads=True,
                         group_by="ticker")
    except: return []
    for yfc in yf_codes:
        try:
            if yfc not in df.columns.get_level_values(0): continue
            sub = df[yfc]
            cl = sub["Close"].dropna()
            if len(cl) < 200: continue
            today = float(cl.iloc[-1])
            hist_max = float(cl.max())
            # 寬鬆 0.92：留 8% 緩衝給配息衰減（高股息股 raw 會掉，還原後可能仍 ATH）
            if today >= hist_max * 0.92:
                code, name = code_map[yfc]
                candidates.append((code, name))
        except: continue
    return candidates

def scan_all(use_full_universe=True):
    """
    雙階段掃描：
    - Shioaji 模式：直接全用 Shioaji 分析（速度快，無限流）
    - yfinance 模式：Stage 1 批次快篩 → Stage 2 完整分析
    """
    results = {"limit_up": [], "high": [], "medium": [], "low": [], "fake": []}
    universe = get_full_universe() if use_full_universe else TW_UNIVERSE

    # ═══ Shioaji 路徑（速度快，跳過 Stage 1）═══
    if USE_SHIOAJI:
        print(f"[tw_filter] 🚀 Shioaji 模式：直接全分析 {len(universe)} 檔...", file=sys.stderr)
        analyzed = 0
        for tk, name in universe:
            a = analyze(tk, name)
            analyzed += 1
            if analyzed % 200 == 0:
                hits = sum(len(v) for v in results.values())
                print(f"  [{analyzed}/{len(universe)}] 已找 {hits} 個 ATH+多頭", file=sys.stderr)
            if not a: continue
            if a["is_ath"] and a["is_bullish"]:
                results[a["category"]].append(a)
        for k in results:
            results[k].sort(key=lambda x: -x["change"])
        return results

    # ═══ yfinance fallback 路徑（雙階段）═══
    print(f"[tw_filter] Stage 1: 批次快篩 {len(universe)} 檔...")
    all_candidates = []
    BATCH = 50
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i+BATCH]
        cands = _quick_screen_batch(batch)
        all_candidates.extend(cands)
        if i % 200 == 0:
            print(f"  [{i}/{len(universe)}] 已找 {len(all_candidates)} 個寬鬆 ATH 候選")
        time.sleep(0.8)  # 避免限流
    print(f"[tw_filter] Stage 1 完成：{len(all_candidates)} 個候選（將用還原權值二次驗證）")

    # 第二階段：完整 analyze（含還原權值、多頭排列、漲停判讀）
    print(f"[tw_filter] Stage 2: 完整分析 {len(all_candidates)} 檔...")
    for code, name in all_candidates:
        a = analyze(code, name)
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
    limit_up = results.get("limit_up", [])
    high = results["high"]; medium = results["medium"]
    low = results["low"]; fake = results["fake"]
    total = len(limit_up) + len(high) + len(medium) + len(low) + len(fake)

    lines = ["🇹🇼 台股突破篩選 (統計濾網)"]
    if total == 0:
        lines.append("  ⏸ 今日無創新高+多頭排列個股")
        return "\n".join(lines)

    if limit_up:
        lines.append(f"  🚀 漲停鎖死 ({len(limit_up)})")
        for a in limit_up[:3]:
            lines.append(f"    {a['ticker']} {a['name']} ${a['close']:.0f} {a['change']:+.1f}%")
    if high:
        lines.append(f"  🟢 高機率 ({len(high)})")
        for a in high[:3]:
            lines.append(f"    {a['ticker']} {a['name']} ${a['close']:.0f} {a['change']:+.1f}%")
    if medium:
        lines.append(f"  🟡 普通 ({len(medium)})")
        for a in medium[:3]:
            lines.append(f"    {a['ticker']} {a['name']} ${a['close']:.0f} {a['change']:+.1f}%")
    if fake:
        fake_top3 = ", ".join(a['ticker'] for a in fake[:3])
        lines.append(f"  🔴 假突破({len(fake)}): {fake_top3}")
    if low:
        low_top3 = ", ".join(a['ticker'] for a in low[:3])
        lines.append(f"  🟠 低機率({len(low)}): {low_top3}")

    return "\n".join(lines)

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print("掃描台股觀察池...")
    res = scan_all()
    print(build_line_block(res))
    print("\n--- 詳細 ---")
    for cat in ["limit_up", "high", "medium", "low", "fake"]:
        emoji = {"limit_up":"🚀","high":"🟢","medium":"🟡","low":"🟠","fake":"🔴"}[cat]
        for a in res[cat]:
            print(f"{emoji} {a['ticker']} {a['name']:<8} "
                  f"${a['close']:.0f} {a['change']:+.1f}% "
                  f"量{a['vol_ratio']:.1f}x RSI{a['rsi']:.0f} "
                  f"多頭{a['bull_strength']:+.0f}%")
