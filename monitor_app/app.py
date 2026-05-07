#!/usr/bin/env python3
"""
📡 投資監控 APP（V4 策略監控骨架）

讀取 my-stock-bot 每日產出的 dashboard_data.json，
顯示大盤體制、Top 5、出場警報。

部署：Streamlit Cloud → main file 填 `monitor_app/app.py`
"""
import json
import urllib.request

import streamlit as st

DASHBOARD_URL = (
    "https://raw.githubusercontent.com/"
    "dogking0628-dotcom/my-stock-bot/main/dashboard_data.json"
)
CACHE_TTL_SEC = 300

st.set_page_config(
    page_title="📡 投資監控",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data(ttl=CACHE_TTL_SEC)
def load_dashboard():
    req = urllib.request.Request(
        DASHBOARD_URL, headers={"User-Agent": "Mozilla/5.0"}
    )
    raw = urllib.request.urlopen(req, timeout=15).read()
    return json.loads(raw)


def render_regime(regime):
    st.subheader("📊 大盤體制")
    if not regime:
        st.info("尚無體制資料")
        return
    cols = st.columns(len(regime))
    for col, (_, info) in zip(cols, regime.items()):
        with col:
            st.markdown(f"**{info.get('market', '')}**")
            st.metric(
                label=info.get("level", ""),
                value=f"{info.get('today', 0):.2f}",
                delta=f"vs MA200 {info.get('vs_ma200_pct', 0):+.1f}%",
            )
            st.caption(info.get("action", ""))


def render_top5(top5):
    st.subheader("🎯 台股 Top 5（V4 動能）")
    if not top5:
        st.info("今日無 Top 5（可能空手或資料未更新）")
        return
    for s in top5:
        with st.container(border=True):
            st.markdown(
                f"**{s.get('ticker','')} {s.get('name','')}** "
                f"｜{s.get('industry','')}｜{s.get('tier','')}"
            )
            st.caption(
                f"動能 {s.get('momentum_score',0)} 分"
                f"｜收 {s.get('close',0):.2f}"
                f"｜{', '.join(s.get('momentum_notes', []) or [])}"
            )


def render_exits(exits):
    st.subheader("🚨 出場警報（跌破 20MA）")
    if not exits:
        st.success("✅ 無出場訊號")
        return
    for s in exits:
        st.error(
            f"{s.get('ticker','')} {s.get('name','')}"
            f"｜收 {s.get('close',0):.2f}"
            f"｜MA20 {s.get('ma20',0):.2f}"
        )


st.title("📡 投資監控")

if st.button("🔄 重新載入", type="primary", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

try:
    data = load_dashboard()
except Exception as exc:
    st.error(f"❌ 載入失敗：{exc}")
    st.stop()

st.caption(f"資料時間：{data.get('timestamp', '未知')}")

render_regime(data.get("regime") or {})
render_top5(data.get("tw_top5") or [])
render_exits(data.get("tw_exit_signals") or [])
