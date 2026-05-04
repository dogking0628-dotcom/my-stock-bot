#!/usr/bin/env python3
"""
📊 強勢股動能掃描器 — Streamlit PWA
台股 + 美股 突破新高 + 完美多頭排列 + 假突破濾網 + 動能評分
"""
import streamlit as st
import yfinance as yf
import numpy as np
import pandas as pd
import datetime as dt
import json

# ═══════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════
st.set_page_config(
    page_title="強勢股掃描器",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",  # 手機體驗
)

# 自訂 CSS — 手機友善
st.markdown("""
<style>
    .stApp { background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); }
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 720px; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 8px 12px; }
    .metric-card {
        background: rgba(255,255,255,0.05);
        border-radius: 8px;
        padding: 12px;
        margin: 6px 0;
        border-left: 4px solid;
    }
    .ok { border-color: #10b981; }
    .warn { border-color: #f59e0b; }
    .bad { border-color: #ef4444; }
    .ticker-name { font-size: 16px; font-weight: bold; color: white; }
    .ticker-detail { font-size: 13px; color: #cbd5e1; margin-top: 4px; }
    .ticker-action { font-size: 12px; color: #94a3b8; margin-top: 4px; }
    .green { color: #10b981; }
    .red { color: #ef4444; }
    .yellow { color: #f59e0b; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════
# 股票池
# ═══════════════════════════════════════════════
TW_UNIVERSE = [
    ("2330","台積電"),("2454","聯發科"),("2303","聯電"),("3711","日月光投控"),
    ("5347","世界"),("8046","南電"),("3034","聯詠"),("6415","矽力-KY"),
    ("3661","世芯-KY"),("3231","緯創"),("2382","廣達"),("2376","技嘉"),
    ("3017","奇鋐"),("2308","台達電"),("6669","緯穎"),("3653","健策"),
    ("2379","瑞昱"),("8069","元太"),("2881","富邦金"),("2882","國泰金"),
    ("2891","中信金"),("2884","玉山金"),("2885","元大金"),("2886","兆豐金"),
    ("2317","鴻海"),("2474","可成"),("2412","中華電"),("3008","大立光"),
    ("2357","華碩"),("4938","和碩"),("3045","台灣大"),("4904","遠傳"),
    ("1101","台泥"),("1216","統一"),("1301","台塑"),("2002","中鋼"),
    ("2603","長榮"),("2609","陽明"),("2615","萬海"),("2618","長榮航"),
    ("3443","創意"),("6488","環球晶"),("3293","鈊象"),("8086","宏捷科"),
]

US_UNIVERSE = ["AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA","AMD","AVGO","QCOM",
               "MU","INTC","TXN","AMAT","JPM","GS","MS","V","MA","UNH","LLY","ABBV",
               "HD","MCD","COST","XOM","CVX","ORCL","CRM","CAT","NFLX","ADBE","PLTR",
               "TSM","ASML","WMT","JNJ","PG","BAC","KO","PEP","MRK","BRK-B"]

# ═══════════════════════════════════════════════
# 工具函式
# ═══════════════════════════════════════════════
def rsi(closes, period=14):
    delta = np.diff(closes)
    if len(delta) < period: return 50.0
    up = np.where(delta > 0, delta, 0)
    dn = np.where(delta < 0, -delta, 0)
    avg_up = np.mean(up[-period:])
    avg_dn = np.mean(dn[-period:])
    if avg_dn == 0: return 100.0
    return 100 - 100 / (1 + avg_up/avg_dn)

@st.cache_data(ttl=600)  # 10 分鐘快取
def fetch_stock(ticker, suffix="", price_mode="adjusted"):
    """
    抓股價資料
    price_mode:
      - 'adjusted' : 還原權值（手動用配息計算，因 yfinance 對台股不還原）
      - 'raw'      : 盤面實價（不還原）
    """
    try:
        full_ticker = f"{ticker}{suffix}"
        # 取 Ticker 物件以拿配息
        t = yf.Ticker(full_ticker)
        df = t.history(period="2y", auto_adjust=False)
        if df.empty: return None
        closes = df["Close"].dropna()
        volumes = df["Volume"].dropna()
        if len(closes) < 200: return None

        if price_mode == "adjusted":
            # 手動「向前還原」：每次配息 ex-date，把該日之前的價格 × (1 - div/close_前一日)
            divs = t.dividends
            if not divs.empty:
                # 對齊時區
                for ex_date, div_amount in divs.items():
                    ex_date_naive = ex_date.tz_localize(None) if ex_date.tzinfo else ex_date
                    # 找到 ex-date 之前最近的交易日
                    before_mask = closes.index.tz_localize(None) < ex_date_naive if closes.index.tz else closes.index < ex_date_naive
                    if before_mask.sum() == 0: continue
                    last_close_before = closes[before_mask].iloc[-1]
                    if last_close_before <= 0: continue
                    factor = 1 - div_amount / last_close_before
                    if factor <= 0 or factor > 1: continue
                    # 把 ex-date 之前的所有價格 × factor
                    closes.loc[before_mask] = closes.loc[before_mask] * factor

        c = closes.values.astype(float)
        v = volumes.values.astype(float)
        if len(c) < 200: return None
        return c, v
    except Exception as e:
        return None

def analyze_stock(ticker, name, suffix="", price_mode="adjusted"):
    """完整分析一檔股票"""
    data = fetch_stock(ticker, suffix, price_mode)
    if not data: return None
    c, v = data
    today, prev = c[-1], c[-2]
    change = (today/prev - 1)*100
    avg_vol_20 = v[-20:].mean()
    vol_ratio = v[-1]/avg_vol_20 if avg_vol_20 > 0 else 0
    rsi_val = rsi(c, 14)
    ma5, ma20, ma60, ma200 = c[-5:].mean(), c[-20:].mean(), c[-60:].mean(), c[-200:].mean()
    bull_strength = (ma5/ma200 - 1)*100
    hist_max = c.max()
    is_ath = today >= hist_max * 0.999
    is_bullish = ma5 > ma20 > ma60 > ma200
    # 4 個濾網
    pass_vol  = vol_ratio >= 1.5
    pass_rsi  = rsi_val < 80
    pass_heat = bull_strength < 100
    return {
        "ticker": ticker, "name": name,
        "close": today, "change": change,
        "vol_ratio": vol_ratio, "rsi": rsi_val,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "ma200": ma200,
        "bull_strength": bull_strength,
        "is_ath": is_ath, "is_bullish": is_bullish,
        "pass_vol": pass_vol, "pass_rsi": pass_rsi, "pass_heat": pass_heat,
        "stop_price": ma5 * 0.98,
        "entry_price": ma5,
    }

def market_regime(market="US"):
    """大盤體制判讀（一律用還原）"""
    ticker = "SPY" if market == "US" else "0050.TW"
    data = fetch_stock(ticker.split(".")[0], ".TW" if "TW" in ticker else "", "adjusted")
    if not data: return None, None, None
    c, _ = data
    today = c[-1]; ma200 = c[-200:].mean()
    return today, ma200, today > ma200

def render_stock_card(a, market_bull):
    """渲染個股卡片"""
    n_pass = sum([a["pass_vol"], a["pass_rsi"], a["pass_heat"], market_bull])
    if n_pass == 4:
        cls, status, emoji = "ok", "🟢 可進場", "🟢"
    elif n_pass >= 2:
        cls, status, emoji = "warn", "🟡 觀察", "🟡"
    else:
        cls, status, emoji = "bad", "🔴 跳過", "🔴"

    chg_color = "green" if a["change"] > 0 else "red"
    rsi_tag = "🔥過熱" if a["rsi"] > 80 else ("✓健康" if a["rsi"] >= 50 else "")
    vol_tag = "✓爆量" if a["vol_ratio"] >= 1.5 else "量少"
    heat_tag = "✓正常" if a["bull_strength"] < 100 else "🔥漲過頭"

    html = f"""
    <div class="metric-card {cls}">
        <div class="ticker-name">{emoji} {a['ticker']} {a['name']} — ${a['close']:,.2f}
            <span class="{chg_color}"> {a['change']:+.2f}%</span></div>
        <div class="ticker-detail">
            量{a['vol_ratio']:.1f}x ({vol_tag}) ｜ RSI {a['rsi']:.0f} ({rsi_tag}) ｜ 多頭強度 {a['bull_strength']:+.0f}% ({heat_tag})
        </div>
    """
    if cls == "ok":
        html += f"""
        <div class="ticker-action">
            ✅ 進場: 等回測 5MA ≈ ${a['entry_price']:,.2f} ｜ 停損: ${a['stop_price']:,.2f}
        </div>
        """
    elif cls == "warn":
        missing = []
        if not a["pass_vol"]:  missing.append(f"量能不足({a['vol_ratio']:.1f}x)")
        if not a["pass_rsi"]:  missing.append(f"RSI過熱({a['rsi']:.0f})")
        if not a["pass_heat"]: missing.append(f"漲過頭({a['bull_strength']:+.0f}%)")
        html += f'<div class="ticker-action">⚠️ 缺: {", ".join(missing)}</div>'
    else:
        html += '<div class="ticker-action">❌ 不建議進場（多項條件不符）</div>'
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# ═══════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════
st.markdown("# 📊 強勢股掃描器")
st.caption(f"創新高 + 完美多頭排列 + 假突破濾網 ｜ 更新於 {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")

colA, colB = st.columns([1, 1])
with colA:
    if st.button("🔄 重新掃描", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with colB:
    price_mode = st.radio(
        "資料基準",
        ["adjusted", "raw"],
        format_func=lambda x: "📊 還原權值（含股利）" if x=="adjusted" else "💰 盤面實價（不還原）",
        horizontal=True,
        label_visibility="collapsed",
    )

st.caption(
    "📊 **還原權值**：含現金/股票股利調整，反映「總報酬」新高"
    if price_mode == "adjusted"
    else "💰 **盤面實價**：真實盤面價格（除權後會下調），反映「股價」新高"
)

# ═══════════════════════════════════════════════
# 大盤體制
# ═══════════════════════════════════════════════
col1, col2 = st.columns(2)
with col1:
    spy, ma_us, bull_us = market_regime("US")
    if spy:
        emoji = "🐂" if bull_us else "🐻"
        delta = ((spy/ma_us - 1)*100)
        st.metric(f"美股 SPY {emoji}", f"${spy:.2f}",
                  f"{delta:+.2f}% vs MA200")
with col2:
    tw, ma_tw, bull_tw = market_regime("TW")
    if tw:
        emoji = "🐂" if bull_tw else "🐻"
        delta = ((tw/ma_tw - 1)*100)
        st.metric(f"台股 0050 {emoji}", f"${tw:.2f}",
                  f"{delta:+.2f}% vs MA200")

if not (bull_us and bull_tw):
    st.warning("⚠️ 大盤其中一邊處於空頭，建議降低進場部位")

# ═══════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════
tab_tw, tab_us, tab_settings = st.tabs(["🇹🇼 台股", "🇺🇸 美股", "⚙️ 設定"])

# ───────────────────────────────────────────────
# 台股
# ───────────────────────────────────────────────
with tab_tw:
    bar = st.progress(0, text="掃描台股中...")
    tw_results = []
    for i, (tk, name) in enumerate(TW_UNIVERSE):
        bar.progress((i+1)/len(TW_UNIVERSE), text=f"分析 {tk} {name}...")
        a = analyze_stock(tk, name, ".TW", price_mode)
        if a and a["is_ath"] and a["is_bullish"]:
            tw_results.append(a)
    bar.empty()

    if not tw_results:
        st.info("🟡 台股今日無創新高 + 完美多頭排列個股")
    else:
        # 分類
        ok_list   = [a for a in tw_results if a["pass_vol"] and a["pass_rsi"] and a["pass_heat"] and bull_tw]
        warn_list = [a for a in tw_results if a not in ok_list and sum([a["pass_vol"], a["pass_rsi"], a["pass_heat"], bull_tw])>=2]
        bad_list  = [a for a in tw_results if a not in ok_list and a not in warn_list]

        if ok_list:
            st.markdown(f"### 🟢 可進場 ({len(ok_list)} 檔)")
            for a in sorted(ok_list, key=lambda x: -x["change"]):
                render_stock_card(a, bull_tw)
        if warn_list:
            st.markdown(f"### 🟡 觀察 ({len(warn_list)} 檔)")
            for a in sorted(warn_list, key=lambda x: -x["change"]):
                render_stock_card(a, bull_tw)
        if bad_list:
            with st.expander(f"🔴 跳過 ({len(bad_list)} 檔) — 條件不足"):
                for a in sorted(bad_list, key=lambda x: -x["change"]):
                    render_stock_card(a, bull_tw)

# ───────────────────────────────────────────────
# 美股
# ───────────────────────────────────────────────
with tab_us:
    bar = st.progress(0, text="掃描美股中...")
    us_results = []
    for i, tk in enumerate(US_UNIVERSE):
        bar.progress((i+1)/len(US_UNIVERSE), text=f"分析 {tk}...")
        a = analyze_stock(tk, tk, "", price_mode)
        if a and a["is_ath"] and a["is_bullish"]:
            us_results.append(a)
    bar.empty()

    if not us_results:
        st.info("🟡 美股今日無創新高 + 完美多頭排列個股")
    else:
        ok_list   = [a for a in us_results if a["pass_vol"] and a["pass_rsi"] and a["pass_heat"] and bull_us]
        warn_list = [a for a in us_results if a not in ok_list and sum([a["pass_vol"], a["pass_rsi"], a["pass_heat"], bull_us])>=2]
        bad_list  = [a for a in us_results if a not in ok_list and a not in warn_list]

        if ok_list:
            st.markdown(f"### 🟢 可進場 ({len(ok_list)} 檔)")
            for a in sorted(ok_list, key=lambda x: -x["change"]):
                render_stock_card(a, bull_us)
        if warn_list:
            st.markdown(f"### 🟡 觀察 ({len(warn_list)} 檔)")
            for a in sorted(warn_list, key=lambda x: -x["change"]):
                render_stock_card(a, bull_us)
        if bad_list:
            with st.expander(f"🔴 跳過 ({len(bad_list)} 檔)"):
                for a in sorted(bad_list, key=lambda x: -x["change"]):
                    render_stock_card(a, bull_us)

# ───────────────────────────────────────────────
# 設定/說明
# ───────────────────────────────────────────────
with tab_settings:
    st.markdown("""
    ### 📋 評分標準
    **基本條件**：
    - ✅ 創歷史新高（今日收盤 ≥ 過去 2 年最高）
    - ✅ 完美多頭排列（5MA > 20MA > 60MA > 200MA）

    **假突破濾網**（4 項都通過 = 🟢 可進場）：
    1. 量 ≥ 20 日均量 1.5 倍
    2. RSI < 80（避免過熱）
    3. 5MA 距 200MA < 100%（避免漲過頭）
    4. 大盤同時 > MA200

    ### 💡 進場 Playbook
    - 不要追當日突破高點
    - 等回測 5MA 附近進 50% 倉
    - 強勢突破再加 50%
    - 跌破 5MA - 2% 立即停損

    ### ⚠️ 風險提醒
    本工具僅為技術派參考，不構成投資建議。
    歷史表現不代表未來，請依個人紀律操作。
    """)
    st.caption(f"📦 v1.0 · 資料源 yfinance · 約 10 分鐘快取一次")
