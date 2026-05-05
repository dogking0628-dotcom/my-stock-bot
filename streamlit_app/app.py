#!/usr/bin/env python3
"""
📊 投資監控 Dashboard
整合：大盤體制 / 動能 Top 5 / 假突破警報 / 台美股突破篩選
"""
import streamlit as st
import yfinance as yf
import numpy as np
import pandas as pd
import datetime as dt
import json, os

# ════════════════════════════════════════
# 設定
# ════════════════════════════════════════
st.set_page_config(
    page_title="📊 投資監控",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Mobile-friendly CSS
st.markdown("""
<style>
    .stApp { background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); }
    .block-container { padding-top: 0.8rem; padding-bottom: 1rem; max-width: 800px; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 6px 10px; font-size: 13px; }

    .alert-card {
        background: rgba(255,255,255,0.04);
        border-radius: 10px;
        padding: 14px;
        margin: 8px 0;
        border-left: 5px solid;
    }
    .ok      { border-color: #10b981; background: rgba(16,185,129,0.08); }
    .warn    { border-color: #f59e0b; background: rgba(245,158,11,0.08); }
    .danger  { border-color: #ef4444; background: rgba(239,68,68,0.08); }
    .strong  { border-color: #8b5cf6; background: rgba(139,92,246,0.10); }

    .stock-row {
        background: rgba(255,255,255,0.03);
        border-radius: 8px;
        padding: 10px 12px;
        margin: 6px 0;
    }
    .name { font-size: 16px; font-weight: 700; color: #f1f5f9; }
    .meta { font-size: 12px; color: #94a3b8; margin-top: 4px; }
    .green   { color: #10b981; font-weight: 600; }
    .red     { color: #ef4444; font-weight: 600; }
    .yellow  { color: #f59e0b; font-weight: 600; }
    .purple  { color: #8b5cf6; font-weight: 600; }

    h1 { font-size: 22px !important; }
    h2 { font-size: 18px !important; }
    h3 { font-size: 16px !important; }
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════
# 工具函式
# ════════════════════════════════════════
@st.cache_data(ttl=600)
def fetch_history(ticker, period="2y"):
    try:
        df = yf.download(ticker, period=period, auto_adjust=False, progress=False)
        if df.empty: return None
        col = df["Close"]
        if hasattr(col, "columns"): col = col[col.columns[0]]
        return col.dropna()
    except: return None

@st.cache_data(ttl=600)
def fetch_history_with_volume(ticker, period="5y"):
    try:
        df = yf.download(ticker, period=period, auto_adjust=False, progress=False)
        if df.empty: return None, None, None
        c = df["Close"]; v = df["Volume"]
        if hasattr(c, "columns"): c = c[c.columns[0]]
        if hasattr(v, "columns"): v = v[v.columns[0]]
        c = c.dropna(); v = v.dropna()
        dates = [d.strftime("%Y-%m-%d") for d in c.index]
        return c.values.astype(float), v.values.astype(float), dates
    except: return None, None, None

@st.cache_data(ttl=86400)
def fetch_market_cap(ticker_yf):
    try:
        info = yf.Ticker(ticker_yf).info
        return info.get("marketCap", 0) / 1e8  # 億
    except: return 0

def rsi(closes, period=14):
    if len(closes) < period+1: return 50
    deltas = np.diff(closes)
    up = np.where(deltas > 0, deltas, 0)[-period:]
    dn = np.where(deltas < 0, -deltas, 0)[-period:]
    if dn.mean() == 0: return 100
    return 100 - 100/(1 + up.mean()/dn.mean())

def monthly_max(closes, dates):
    """5 年內歷史月底收盤最高（排除當月）"""
    by_month = {}
    today_ym = dt.datetime.now().strftime("%Y-%m")
    for d, c in zip(dates, closes):
        ym = d[:7]
        if ym >= today_ym: continue
        by_month[ym] = c
    return max(by_month.values()) if by_month else None

# ════════════════════════════════════════
# Header — 大盤體制
# ════════════════════════════════════════
st.title("📊 投資監控")
st.caption(f"即時更新 ｜ {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")

if st.button("🔄 重新整理", type="primary", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.markdown("### 📊 大盤體制")
col1, col2 = st.columns(2)

def regime_card(ticker, name):
    cl = fetch_history(ticker, "2y")
    if cl is None or len(cl) < 200: return
    today = float(cl.iloc[-1])
    ma200 = float(cl.iloc[-200:].mean())
    peak = float(cl.iloc[-252:].max())
    vs_ma200 = (today/ma200 - 1) * 100
    from_peak = (today/peak - 1) * 100

    if vs_ma200 < -10:
        cls, lvl, action = "danger", "🚨 重大警報", "動用 50% 現金加碼"
    elif vs_ma200 < -3:
        cls, lvl, action = "danger", "🚨 警報", "動用 30% 現金加碼"
    elif from_peak < -20:
        cls, lvl, action = "warn", "⚠️ 注意", "減倉 → 增加現金"
    elif vs_ma200 > 15:
        cls, lvl, action = "warn", "🔥 過熱", "鎖利 → 增加現金"
    else:
        cls, lvl, action = "ok", "🟢 正常", "維持配置"

    return f"""
    <div class="alert-card {cls}">
        <div class="name">{name} ${today:.2f}</div>
        <div class="meta">
            {lvl} ｜ 距 MA200 <strong>{vs_ma200:+.1f}%</strong> ｜ 距高點 {from_peak:.1f}%
        </div>
        <div class="meta" style="color:#cbd5e1; margin-top:6px;">💡 {action}</div>
    </div>
    """

with col1:
    html = regime_card("SPY", "🇺🇸 美股 SPY")
    if html: st.markdown(html, unsafe_allow_html=True)
with col2:
    html = regime_card("0050.TW", "🇹🇼 台股 0050")
    if html: st.markdown(html, unsafe_allow_html=True)

st.markdown("---")

# ════════════════════════════════════════
# Tabs
# ════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs(["🎯 台股動能 Top 5", "🇹🇼 台股突破", "🇺🇸 美股突破", "⚙️ 我的持倉"])

# ════════════════════════════════════════
# 台股股票池（精選 80 檔）
# ════════════════════════════════════════
TW_POOL = [
    ("2330","台積電"),("2454","聯發科"),("2317","鴻海"),("2308","台達電"),
    ("2382","廣達"),("2376","技嘉"),("2303","聯電"),("3711","日月光投控"),
    ("2379","瑞昱"),("2357","華碩"),("3034","聯詠"),("3231","緯創"),
    ("3017","奇鋐"),("6669","緯穎"),("3653","健策"),("8046","南電"),
    ("3443","創意"),("2474","可成"),("3008","大立光"),("4938","和碩"),
    ("2327","國巨"),("2449","京元電子"),("2354","鴻準"),("2356","英業達"),
    ("2377","微星"),("2383","台光電"),("3037","欣興"),("3661","世芯-KY"),
    ("2881","富邦金"),("2882","國泰金"),("2891","中信金"),("2884","玉山金"),
    ("2885","元大金"),("2886","兆豐金"),("2412","中華電"),("3045","台灣大"),
    ("1101","台泥"),("1216","統一"),("1301","台塑"),("2002","中鋼"),
    ("2603","長榮"),("2912","統一超"),("2207","和泰車"),
    ("8299","群聯"),("6488","環球晶"),("5347","世界"),("5274","信驊"),
    ("4763","材料-KY"),("6446","藥華藥"),("3293","鈊象"),("8069","元太"),
    ("2360","致茂"),("4958","臻鼎-KY"),("3008","大立光"),("3045","台灣大"),
]

# ════════════════════════════════════════
# 分析單檔股票
# ════════════════════════════════════════
def analyze_tw(code, name):
    closes_arr, vols_arr, dates = fetch_history_with_volume(f"{code}.TW", "5y")
    if closes_arr is None:
        # try .TWO
        closes_arr, vols_arr, dates = fetch_history_with_volume(f"{code}.TWO", "5y")
    if closes_arr is None or len(closes_arr) < 200:
        return None
    today = float(closes_arr[-1])
    prev = float(closes_arr[-2])
    change = (today/prev - 1) * 100
    avg_vol_20 = vols_arr[-20:].mean()
    vol_ratio = vols_arr[-1] / avg_vol_20 if avg_vol_20 > 0 else 0
    ma5 = closes_arr[-5:].mean()
    ma20 = closes_arr[-20:].mean()
    ma60 = closes_arr[-60:].mean()
    ma200 = closes_arr[-200:].mean()
    rsi_v = rsi(closes_arr, 14)
    bull = (ma5/ma200 - 1) * 100
    mmax = monthly_max(closes_arr, dates)
    is_ath = mmax and today >= mmax * 0.999
    is_bull = ma5 > ma20 > ma60 > ma200
    is_limit_up = change >= 9.5
    is_locked = is_limit_up and vol_ratio < 1.2

    # 動能評分
    score = 0
    if is_locked: score += 30
    elif vol_ratio >= 3: score += 30
    elif vol_ratio >= 2: score += 20
    elif vol_ratio >= 1.5: score += 12
    if is_limit_up: score += 30
    elif 5 <= change <= 8: score += 25
    elif 2 <= change < 5: score += 15
    if 60 <= rsi_v <= 75: score += 20
    elif 75 < rsi_v <= 80: score += 12
    if 20 <= bull <= 60: score += 15
    elif 60 < bull <= 100: score += 10

    # 假突破警訊
    warnings = []
    if today < ma5: warnings.append("跌破 5MA")
    if today < ma20: warnings.append("🚨 跌破 20MA")
    if vol_ratio < 0.7 and change < 1: warnings.append("量縮")
    if rsi_v > 85: warnings.append("RSI 過熱")
    if bull > 120: warnings.append("漲過頭")

    return {
        "code": code, "name": name,
        "close": today, "change": change,
        "vol_ratio": vol_ratio, "rsi": rsi_v,
        "ma5": ma5, "ma20": ma20, "ma200": ma200,
        "bull": bull, "is_ath": is_ath, "is_bull": is_bull,
        "is_locked": is_locked, "is_limit_up": is_limit_up,
        "score": min(score, 90), "warnings": warnings,
    }

# ════════════════════════════════════════
# Tab 1: 動能 Top 5
# ════════════════════════════════════════
with tab1:
    st.markdown("### 🎯 動能 Top 5")
    st.caption("篩選：5年月線ATH + 多頭排列 + 量爆/漲停 + 市值≥200億 + 動能評分≥30")

    bar = st.progress(0, text="掃描台股 80 檔...")
    results = []
    for i, (code, name) in enumerate(TW_POOL):
        bar.progress((i+1)/len(TW_POOL), text=f"分析 {code} {name}...")
        a = analyze_tw(code, name)
        if not a: continue
        if not (a["is_ath"] and a["is_bull"]): continue
        # 市值濾網
        mcap = fetch_market_cap(f"{code}.TW") or fetch_market_cap(f"{code}.TWO")
        if mcap < 200: continue
        a["mcap"] = mcap
        if a["score"] < 30: continue
        results.append(a)
    bar.empty()

    results.sort(key=lambda x: -x["score"])
    top5 = results[:5]

    if not top5:
        st.info("⏸ 今日無高品質動能股")
    else:
        for i, a in enumerate(top5, 1):
            tag = "🚀" if a["is_locked"] else ("🟢" if a["score"] >= 60 else "🟡")
            cls = "strong" if a["is_locked"] else ("ok" if a["score"] >= 60 else "warn")
            chg_color = "green" if a["change"] >= 0 else "red"

            html = f"""
            <div class="alert-card {cls}">
                <div class="name">{tag} #{i} {a['code']} {a['name']} <span class="purple">{a['score']}/90</span></div>
                <div class="meta">
                    ${a['close']:,.2f}
                    <span class="{chg_color}"> {a['change']:+.2f}%</span>
                     ｜ 量 {a['vol_ratio']:.1f}x ｜ RSI {a['rsi']:.0f} ｜ 多頭 {a['bull']:+.0f}%
                </div>
                <div class="meta" style="margin-top:6px;">
                    💼 市值 ${a['mcap']:.0f}億 NT$
                </div>
            """
            if a["warnings"]:
                html += f'<div class="meta" style="margin-top:6px; color:#fbbf24;">⚠️ {" ｜ ".join(a["warnings"])}</div>'
            else:
                html += f'<div class="meta" style="margin-top:6px; color:#10b981;">✅ 持有條件 OK，停損點 ${a["ma20"]:.0f}（20MA）</div>'
            html += "</div>"
            st.markdown(html, unsafe_allow_html=True)

# ════════════════════════════════════════
# Tab 2: 台股突破篩選
# ════════════════════════════════════════
with tab2:
    st.markdown("### 🇹🇼 台股突破篩選")
    if 'results' in dir() and results:
        # 分類顯示
        limit_up = [a for a in results if a["is_locked"]]
        high = [a for a in results if not a["is_locked"] and a["score"] >= 60]
        medium = [a for a in results if not a["is_locked"] and 30 <= a["score"] < 60]

        if limit_up:
            st.markdown(f"#### 🚀 漲停鎖死 ({len(limit_up)})")
            for a in limit_up:
                st.markdown(f"- **{a['code']} {a['name']}** ${a['close']:.0f} {a['change']:+.1f}% — 動能 {a['score']}/90")
        if high:
            st.markdown(f"#### 🟢 高機率 ({len(high)})")
            for a in high[:5]:
                st.markdown(f"- **{a['code']} {a['name']}** ${a['close']:.0f} {a['change']:+.1f}% — 動能 {a['score']}/90")
        if medium:
            st.markdown(f"#### 🟡 普通 ({len(medium)})")
            for a in medium[:5]:
                st.markdown(f"- **{a['code']} {a['name']}** ${a['close']:.0f} {a['change']:+.1f}%")
    else:
        st.info("先在「動能 Top 5」分頁執行掃描")

# ════════════════════════════════════════
# Tab 3: 美股
# ════════════════════════════════════════
with tab3:
    st.markdown("### 🇺🇸 美股 ATH 突破")
    US_POOL = ["AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA","AMD","AVGO",
               "QCOM","TSM","PLTR","COIN","CRM","ORCL","NFLX","NOW","ARM"]
    bar2 = st.progress(0, text="掃描美股...")
    us_hits = []
    for i, t in enumerate(US_POOL):
        bar2.progress((i+1)/len(US_POOL), text=f"{t}...")
        cl = fetch_history(t, "2y")
        if cl is None or len(cl) < 200: continue
        today = float(cl.iloc[-1])
        prev = float(cl.iloc[-2])
        chg = (today/prev - 1) * 100
        ma5 = cl.iloc[-5:].mean()
        ma20 = cl.iloc[-20:].mean()
        ma60 = cl.iloc[-60:].mean()
        ma200 = cl.iloc[-200:].mean()
        max_2y = cl.max()
        is_ath = today >= max_2y * 0.999
        is_bull = ma5 > ma20 > ma60 > ma200
        if is_ath and is_bull:
            us_hits.append({"t": t, "close": today, "chg": chg})
    bar2.empty()

    if not us_hits:
        st.info("今日無美股突破")
    else:
        st.markdown(f"#### 找到 {len(us_hits)} 檔")
        for s in us_hits:
            chg_c = "green" if s["chg"] >= 0 else "red"
            st.markdown(f"""
            <div class="stock-row">
              <div class="name">🟢 {s['t']}</div>
              <div class="meta">${s['close']:,.2f} <span class="{chg_c}">{s['chg']:+.2f}%</span></div>
            </div>
            """, unsafe_allow_html=True)

# ════════════════════════════════════════
# Tab 4: 持倉檢視
# ════════════════════════════════════════
with tab4:
    st.markdown("### 💼 我的持倉")
    st.caption("貼上持倉代號（每行一檔），即時看 ATH/警訊")
    pos_input = st.text_area("持倉清單", "2330\n2454\n6658\n3491\n2449",
                             height=180, label_visibility="collapsed")
    if st.button("分析"):
        codes = [c.strip() for c in pos_input.split("\n") if c.strip()]
        for code in codes:
            a = analyze_tw(code, code)
            if not a:
                st.markdown(f"❌ **{code}** 找不到資料"); continue
            chg_c = "green" if a["change"] >= 0 else "red"
            tag = "🚀" if a["is_locked"] else ("🟢" if a["is_ath"] else ("🟡" if a["is_bull"] else "🔴"))
            cls = "strong" if a["is_locked"] else ("ok" if a["is_ath"] else "warn")
            html = f"""
            <div class="alert-card {cls}">
                <div class="name">{tag} {a['code']} ${a['close']:.2f} <span class="{chg_c}">{a['change']:+.1f}%</span></div>
                <div class="meta">動能 {a['score']}/90 ｜ 量 {a['vol_ratio']:.1f}x ｜ RSI {a['rsi']:.0f}</div>
            """
            if a["warnings"]:
                html += f'<div class="meta" style="color:#ef4444; margin-top:6px;">⚠️ {" ｜ ".join(a["warnings"])}</div>'
            else:
                html += f'<div class="meta" style="color:#10b981;">✅ 持有 OK 停損${a["ma20"]:.0f}</div>'
            html += "</div>"
            st.markdown(html, unsafe_allow_html=True)

st.markdown("---")
st.caption("資料源 yfinance（15 分延遲） ｜ 不構成投資建議 ｜ v2.0")
