#!/usr/bin/env python3
"""
📡 投資監控 APP（V4 策略監控骨架）

讀取 my-stock-bot 每日產出的 dashboard_data.json，
顯示大盤體制、Top 5、出場警報。

部署：Streamlit Cloud → main file 填 `monitor_app/app.py`
"""
import json
import urllib.error
import urllib.request

import streamlit as st

REPO = "dogking0628-dotcom/my-stock-bot"
DASHBOARD_URL = (
    f"https://raw.githubusercontent.com/{REPO}/main/dashboard_data.json"
)
CACHE_TTL_SEC = 300

WORKFLOWS = {
    "daily.yml": "🌅 Daily 完整掃描（產 dashboard_data.json）",
    "intraday.yml": "⏱️ Intraday 盤中掃描",
    "post_close_review.yml": "📋 盤後策略檢討",
    "industry_scan.yml": "🏭 Industry 掃描（Shioaji）",
    "weekly_top30.yml": "📅 Weekly Top30",
}

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


def dispatch_workflow(workflow_file: str, ref: str = "main") -> tuple[bool, str]:
    """呼叫 GitHub API 觸發 workflow_dispatch。"""
    token = st.secrets.get("GITHUB_TOKEN") if hasattr(st, "secrets") else None
    if not token:
        return False, "缺少 GITHUB_TOKEN（在 Streamlit Cloud Secrets 設定）"

    url = (
        f"https://api.github.com/repos/{REPO}/actions/workflows/"
        f"{workflow_file}/dispatches"
    )
    body = json.dumps({"ref": ref}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 204:
                return True, "已送出觸發請求（請到 Actions 頁查看執行）"
            return False, f"非預期狀態碼 {resp.status}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        return False, f"HTTP {exc.code}：{detail}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


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


def render_workflow_trigger():
    with st.expander("▶️ 觸發 GitHub Actions", expanded=False):
        st.caption(
            "點下後會送出 workflow_dispatch 請求，到 GitHub Actions 排隊執行。"
            "需在 Streamlit Cloud Secrets 設定 `GITHUB_TOKEN`（PAT）。"
        )
        choice = st.selectbox(
            "選擇 workflow",
            options=list(WORKFLOWS.keys()),
            format_func=lambda k: WORKFLOWS[k],
        )
        if st.button("🚀 立即執行", type="secondary", use_container_width=True):
            with st.spinner("送出中..."):
                ok, msg = dispatch_workflow(choice)
            if ok:
                st.success(f"✅ {msg}")
                st.markdown(
                    f"[👉 到 Actions 頁查看](https://github.com/{REPO}/actions)"
                )
            else:
                st.error(f"❌ {msg}")


st.title("📡 投資監控")

c1, c2 = st.columns(2)
with c1:
    if st.button("🔄 重新載入資料", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with c2:
    st.markdown(
        f"[📂 開 Actions 頁](https://github.com/{REPO}/actions)",
        help="到 GitHub 看 workflow 執行狀態",
    )

render_workflow_trigger()

try:
    data = load_dashboard()
except Exception as exc:
    st.error(f"❌ 載入失敗：{exc}")
    st.stop()

st.caption(f"資料時間：{data.get('timestamp', '未知')}")

render_regime(data.get("regime") or {})
render_top5(data.get("tw_top5") or [])
render_exits(data.get("tw_exit_signals") or [])
