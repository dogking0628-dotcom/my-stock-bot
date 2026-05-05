#!/usr/bin/env python3
"""
📊 投資監控 Dashboard — 讀 GitHub Actions 每日掃描結果（與 LINE 完全一致）
"""
import streamlit as st
import json, os
import datetime as dt
import urllib.request

# ════════════════════════════════════════
# 設定
# ════════════════════════════════════════
st.set_page_config(
    page_title="📊 投資監控",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .stApp { background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); }
    .block-container { padding-top: 0.8rem; max-width: 800px; }
    .stTabs [data-baseweb="tab"] { padding: 6px 12px; font-size: 13px; }

    .alert-card {
        background: rgba(255,255,255,0.04);
        border-radius: 10px; padding: 12px 14px; margin: 6px 0;
        border-left: 5px solid;
    }
    .ok      { border-color: #10b981; background: rgba(16,185,129,0.08); }
    .warn    { border-color: #f59e0b; background: rgba(245,158,11,0.08); }
    .danger  { border-color: #ef4444; background: rgba(239,68,68,0.08); }
    .strong  { border-color: #8b5cf6; background: rgba(139,92,246,0.10); }
    .info    { border-color: #3b82f6; background: rgba(59,130,246,0.08); }

    .name { font-size: 15px; font-weight: 700; color: #f1f5f9; }
    .meta { font-size: 12px; color: #94a3b8; margin-top: 4px; }
    .green { color: #10b981; font-weight: 600; }
    .red   { color: #ef4444; font-weight: 600; }
    .purple{ color: #8b5cf6; font-weight: 600; }
    h1 { font-size: 22px !important; }
    h2 { font-size: 18px !important; }
    h3 { font-size: 15px !important; }
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════
# 載入 dashboard_data.json（從 GitHub raw 讀，永遠最新）
# ════════════════════════════════════════
DASHBOARD_URL = "https://raw.githubusercontent.com/dogking0628-dotcom/my-stock-bot/main/dashboard_data.json"

@st.cache_data(ttl=300)  # 5 分快取
def load_dashboard():
    try:
        req = urllib.request.Request(DASHBOARD_URL, headers={"User-Agent":"Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=15).read()
        return json.loads(data)
    except Exception as e:
        st.error(f"❌ 載入 dashboard_data.json 失敗：{e}")
        st.info("💡 daily_scan.py 還沒跑過或 commit 失敗。請先 trigger workflow")
        return None

# ════════════════════════════════════════
# Header
# ════════════════════════════════════════
st.title("📊 投資監控")

if st.button("🔄 重新載入", type="primary", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

data = load_dashboard()
if data is None:
    st.stop()

st.caption(f"📅 資料日期: **{data.get('timestamp','?')}** ｜ 與 LINE 推播完全一致")

# ════════════════════════════════════════
# 大盤體制
# ════════════════════════════════════════
st.markdown("### 📊 大盤體制")
regime = data.get("regime", {})

def regime_card(r, market):
    if not r: return None
    cls_map = {"重大警報":"danger", "警報":"danger", "注意":"warn", "過熱":"warn", "正常":"ok"}
    cls = "ok"
    for k, v in cls_map.items():
        if k in r.get("level", ""): cls = v; break
    return f"""
    <div class="alert-card {cls}">
      <div class="name">{market} ${r['today']:,.2f}</div>
      <div class="meta">
        {r['level']} ｜ 距 MA200 <strong>{r['vs_ma200_pct']:+.1f}%</strong> ｜ 距高點 {r['from_peak_pct']:.1f}%
      </div>
      <div class="meta">💡 {r['action']}</div>
    </div>
    """

c1, c2 = st.columns(2)
with c1:
    h = regime_card(regime.get("spy"), "🇺🇸 美股 SPY")
    if h: st.markdown(h, unsafe_allow_html=True)
with c2:
    h = regime_card(regime.get("tw0050"), "🇹🇼 台股 0050")
    if h: st.markdown(h, unsafe_allow_html=True)

st.markdown("---")

# ════════════════════════════════════════
# Tabs
# ════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 動能 Top5", "🇹🇼 台股篩選", "🇹🇼 0050 策略", "🇺🇸 美股", "⚠️ 警報"
])

# ────────────────────────
# Tab 1: 動能 Top 5 推薦 + Top 20 候選
# ────────────────────────
def render_stock_card(s, rank=None, is_recommend=False):
    cat = s.get("category", "")
    tag = "🚀" if cat == "limit_up" else ("🟢" if cat == "high" else "🟡")
    cls = "strong" if cat == "limit_up" else ("ok" if cat == "high" else "warn")
    chg = s.get("change", 0)
    chg_color = "green" if chg >= 0 else "red"
    score = s.get("score", 0)
    rank_str = f"#{rank} " if rank else ""
    star = "⭐ " if is_recommend else ""
    html = f"""
    <div class="alert-card {cls}">
      <div class="name">{star}{tag} {rank_str}{s['ticker']} {s['name']}
        <span class="purple">{score}/90</span></div>
      <div class="meta">
        ${s.get('close', 0):,.2f}
        <span class="{chg_color}"> {chg:+.2f}%</span>
        ｜ 量 {s.get('vol_ratio', 0):.1f}x ｜ RSI {s.get('rsi', 0):.0f}
        ｜ 多頭 {s.get('bull_strength', 0):+.0f}%
      </div>
      <div class="meta" style="margin-top:4px;">
        進場 5MA(${s.get('ma5', 0):.0f}) ｜ 停損 20MA(${s.get('ma20', 0):.0f})
      </div>
    </div>
    """
    return html

with tab1:
    top5  = data.get("tw_top5", [])
    top20 = data.get("tw_top20_candidates", [])

    # ── 推薦 5 檔 ──
    st.markdown("### ⭐ 推薦 Top 5（LINE 也推這 5 檔）")
    if not top5:
        st.info("⏸ 今日無高品質推薦股")
    else:
        for i, s in enumerate(top5, 1):
            st.markdown(render_stock_card(s, rank=i, is_recommend=True),
                        unsafe_allow_html=True)

    # ── 候選 20 檔 ──
    if len(top20) > 5:
        st.markdown(f"### 📋 候選名單（Top {len(top20)}）")
        st.caption("第 6-20 名為觀察候選，動能評分 ≥ 30 但暫未列入推薦")
        with st.expander(f"展開查看候選 {len(top20)-5} 檔", expanded=False):
            for i, s in enumerate(top20[5:], 6):
                st.markdown(render_stock_card(s, rank=i, is_recommend=False),
                            unsafe_allow_html=True)

# ────────────────────────
# Tab 2: 台股突破篩選
# ────────────────────────
with tab2:
    st.markdown("### 🇹🇼 台股突破篩選（從 1962 檔 + Shioaji 永豐 API）")
    breakout = data.get("tw_breakout", {})
    cat_info = [
        ("limit_up", "🚀 漲停鎖死", "strong", "最強訊號"),
        ("high",     "🟢 高機率",   "ok",     "量爆+漲幅+多頭強"),
        ("medium",   "🟡 普通",     "warn",   "部分條件達成"),
        ("fake",     "🔴 假突破",   "danger", "不建議追"),
        ("low",      "🟠 低機率",   "info",   "條件多數不符"),
    ]
    for cat_key, label, cls, desc in cat_info:
        stocks = breakout.get(cat_key, [])
        if not stocks: continue
        with st.expander(f"{label} ({len(stocks)}) — {desc}", expanded=(cat_key in ("limit_up","high"))):
            for s in stocks[:10]:
                chg = s.get("change", 0)
                chg_c = "green" if chg >= 0 else "red"
                st.markdown(f"""
                <div class="alert-card {cls}">
                  <div class="name">{s['ticker']} {s['name']}</div>
                  <div class="meta">
                    ${s.get('close', 0):,.2f} <span class="{chg_c}">{chg:+.2f}%</span>
                    ｜ 量 {s.get('vol_ratio', 0):.1f}x
                    ｜ RSI {s.get('rsi', 0):.0f}
                    ｜ 多頭 {s.get('bull_strength', 0):+.0f}%
                  </div>
                </div>
                """, unsafe_allow_html=True)

# ────────────────────────
# Tab 3: 0050 體制
# ────────────────────────
with tab3:
    st.markdown("### 🇹🇼 台股 0050 體制策略")
    sig = data.get("tw_0050_signal", {})
    if not sig:
        st.info("無資料")
    else:
        regime_emoji = {"bull":"🐂", "bear":"🐻", "reduced":"🟠", "neutral":"⚪"}
        e = regime_emoji.get(sig.get("regime","").split("_")[0] if "_" in sig.get("regime","") else sig.get("regime",""), "⚪")
        st.markdown(f"""
        <div class="alert-card info">
          <div class="name">{e} 體制：{sig.get('regime', '?')}</div>
          <div class="meta">建議持倉：<strong>{sig.get('allocation', 0)*100:.0f}%</strong></div>
          <div class="meta">當前價：${sig.get('current', 0):.2f} ｜ MA200：${sig.get('ma200', 0):.2f}</div>
          <div class="meta">距 MA200：{sig.get('vs_ma200_pct', 0):+.1f}%</div>
        </div>
        """, unsafe_allow_html=True)

# ────────────────────────
# Tab 4: 美股
# ────────────────────────
with tab4:
    st.markdown("### 🇺🇸 美股 ATH 突破（S&P 500 / 503 檔）")
    buys = data.get("us_buys", [])
    sells = data.get("us_sells", [])
    holds = data.get("us_holds", [])
    us_state = data.get("us_state", {})

    if buys:
        st.markdown(f"#### 🟢 BUY 訊號 ({len(buys)})")
        for b in buys:
            st.markdown(f"""
            <div class="alert-card ok">
              <div class="name">🟢 BUY {b['ticker']}</div>
              <div class="meta">${b['last']:,.2f} ｜ 突破 ATH +{b['breakout_pct']:.1f}%</div>
            </div>
            """, unsafe_allow_html=True)

    if sells:
        st.markdown(f"#### 🔴 SELL 訊號 ({len(sells)})")
        for s in sells:
            chg_c = "green" if s.get("change_pct", 0) >= 0 else "red"
            st.markdown(f"""
            <div class="alert-card danger">
              <div class="name">🔴 SELL {s['ticker']}</div>
              <div class="meta">${s.get('current',0):,.2f} ｜ 損益 <span class="{chg_c}">{s.get('change_pct',0):+.2f}%</span></div>
            </div>
            """, unsafe_allow_html=True)

    if not buys and not sells:
        st.info("⏸ 今日美股無訊號")

    if holds:
        st.markdown(f"#### 📌 持有中 ({len(holds)})")
        for h in holds[:5]:
            chg_c = "green" if h.get("change_pct", 0) >= 0 else "red"
            st.markdown(f"<div class='meta'>{h['ticker']} ${h.get('current',0):,.2f} <span class='{chg_c}'>{h.get('change_pct',0):+.1f}%</span></div>",
                        unsafe_allow_html=True)

    if us_state:
        st.caption(f"💼 持倉 {us_state.get('n_positions',0)}/{us_state.get('max_slots',10)} ｜ 現金 ${us_state.get('cash',0):,.0f}")

# ────────────────────────
# Tab 5: 警報
# ────────────────────────
with tab5:
    st.markdown("### ⚠️ 假突破警報")
    today_warn = data.get("tw_today_warnings", [])
    dropped_warn = data.get("tw_dropped_warnings", [])

    if today_warn:
        st.markdown("#### 🔴 Top 5 內出現警訊")
        for w in today_warn:
            st.markdown(f"""
            <div class="alert-card warn">
              <div class="name">⚠️ {w['ticker']} {w['name']}</div>
              {''.join(f"<div class='meta'>{wn}</div>" for wn in w['warnings'])}
            </div>
            """, unsafe_allow_html=True)

    if dropped_warn:
        st.markdown("#### 🚨 昨日榜單掉出 + 假突破")
        for w in dropped_warn[:5]:
            tag = "🚨" if w.get("severity",0) >= 2 else "⚠️"
            st.markdown(f"""
            <div class="alert-card danger">
              <div class="name">{tag} {w['ticker']} {w['name']} ${w.get('current_close',0):,.2f}</div>
              {''.join(f"<div class='meta'>{wn}</div>" for wn in w['warnings'])}
            </div>
            """, unsafe_allow_html=True)

    if not today_warn and not dropped_warn:
        st.success("✅ 目前無假突破警報")

st.markdown("---")
st.caption(f"📦 資料源：GitHub Actions daily_scan.py + Shioaji 永豐 API ｜ 與 LINE 完全一致 ｜ {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
