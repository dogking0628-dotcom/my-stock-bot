#!/usr/bin/env python3
"""
📊 投資監控 Dashboard — 讀 GitHub Actions 每日掃描結果（與 LINE 完全一致）
"""
import streamlit as st
import json, os
import datetime as dt
import urllib.request, urllib.error

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

    .stock-row {
        background: rgba(255,255,255,0.03);
        border-radius: 6px; padding: 8px 10px; margin: 4px 0;
    }
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
# GitHub Actions workflow 觸發（手機一鍵更新）
# ════════════════════════════════════════
GITHUB_OWNER  = "dogking0628-dotcom"
GITHUB_REPO   = "my-stock-bot"
GITHUB_BRANCH = "main"

WORKFLOWS = [
    ("daily.yml",             "🔥 Daily ATH Scan",         "完整每日掃描（會更新 dashboard_data.json）"),
    ("intraday.yml",          "⏰ Intraday Scan",          "台股盤中掃描"),
    ("post_close_review.yml", "📝 Post-close Review",      "收盤策略回顧"),
    ("weekly_top30.yml",      "🏆 Weekly Top30",           "Top30 對照"),
    ("industry_scan.yml",     "🏭 Industry ATH (Shioaji)", "產業 ATH（Shioaji 版）"),
]

def _gh_token():
    try:
        t = st.secrets.get("GITHUB_TOKEN", "")
        if t:
            return t
    except Exception:
        pass
    return os.environ.get("GITHUB_TOKEN", "")

def _gh_request(url, method="GET", payload=None, token=""):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "streamlit-app",
            "Content-Type": "application/json",
        },
    )
    return urllib.request.urlopen(req, timeout=15)

def trigger_workflow(workflow_file):
    token = _gh_token()
    if not token:
        return False, "❌ 缺少 GITHUB_TOKEN，請到 Streamlit Cloud → App settings → Secrets 設定"
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/workflows/{workflow_file}/dispatches")
    try:
        _gh_request(url, method="POST", payload={"ref": GITHUB_BRANCH}, token=token)
        return True, f"✅ 已觸發 `{workflow_file}`，1-3 分鐘後再點「重新載入」看新資料"
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200] if hasattr(e, "read") else ""
        return False, f"❌ HTTP {e.code}: {body}"
    except Exception as e:
        return False, f"❌ {e}"

@st.cache_data(ttl=30)
def get_latest_run(workflow_file):
    token = _gh_token()
    if not token:
        return None
    url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
           f"/actions/workflows/{workflow_file}/runs?per_page=1")
    try:
        data = json.loads(_gh_request(url, token=token).read())
        runs = data.get("workflow_runs", [])
        return runs[0] if runs else None
    except Exception:
        return None

def _run_badge(run):
    if not run:
        return "—"
    status = run.get("status", "")
    conclusion = run.get("conclusion") or ""
    when = (run.get("updated_at") or "")[:16].replace("T", " ")
    if status == "completed":
        emoji = {"success": "✅", "failure": "❌", "cancelled": "⚪"}.get(conclusion, "⚫")
        return f"{emoji} {conclusion} · {when}"
    return f"🟡 {status} · {when}"

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

with st.expander("🚀 手動觸發 GitHub Actions Workflow", expanded=False):
    if not _gh_token():
        st.warning(
            "尚未設定 `GITHUB_TOKEN`。到 Streamlit Cloud → **App settings** → **Secrets** "
            "貼入下面這行（PAT 需勾選 `workflow` scope）："
        )
        st.code('GITHUB_TOKEN = "ghp_xxx..."', language="toml")
    else:
        st.caption("點按鈕後到 GitHub Actions 看進度，跑完回來點「重新載入」拿到新資料。")
    for wf_file, label, desc in WORKFLOWS:
        c1, c2 = st.columns([2, 3])
        with c1:
            if st.button(label, key=f"trigger_{wf_file}", use_container_width=True):
                ok, msg = trigger_workflow(wf_file)
                (st.success if ok else st.error)(msg)
                if ok:
                    get_latest_run.clear()
        with c2:
            run = get_latest_run(wf_file)
            st.caption(f"{desc}")
            st.caption(f"最近一次：{_run_badge(run)}")

data = load_dashboard()
if data is None:
    st.stop()

_market = data.get("tw_market_industry", {})
_intraday = _market.get("intraday_updated") if _market else None
if _intraday:
    st.caption(f"📅 收盤資料: **{data.get('timestamp','?')}** ｜ 🟢 **盤中即時更新**: {_intraday}（yfinance 延遲約 15 分）")
else:
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
tab1, tab_ind, tab_market, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 Top5+候選", "🏆 族群推薦", "🌐 全市場族群", "🇹🇼 篩選", "🇹🇼 0050", "🇺🇸 美股", "⚠️ 警報"
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
# Tab Industry: 族群推薦
# ────────────────────────
with tab_ind:
    st.markdown("### 🏆 推薦族群")
    rec = data.get("tw_recommended_industry")
    groups = data.get("tw_industry_groups", [])

    if rec:
        st.markdown(f"""
        <div class="alert-card strong">
          <div class="name">🏆 {rec['industry']} 族群最強</div>
          <div class="meta">
            {rec['count']} 檔入選候選 ｜ 平均動能 <strong>{rec['avg_score']}/90</strong>
            ｜ 強度指標 {rec['strength']}
          </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"#### 🌟 {rec['industry']} 族群代表股")
        for s in rec.get("top_stocks", []):
            chg_c = "green" if s.get("change", 0) >= 0 else "red"
            st.markdown(f"""
            <div class="alert-card ok">
              <div class="name">⭐ {s['ticker']} {s['name']}
                <span class="purple">{s['score']}/90</span></div>
              <div class="meta">
                ${s.get('close', 0):,.2f}
                <span class="{chg_c}"> {s.get('change', 0):+.2f}%</span>
              </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("⏸ 今日無推薦族群")

    # 全部族群分布
    if groups:
        st.markdown("### 📊 候選股族群分布")
        for g in groups:
            ind = g["industry"]
            cnt = g["count"]
            avg = g["avg_score"]
            stocks = g.get("stocks", [])
            with st.expander(f"🏭 {ind} ({cnt} 檔，平均 {avg:.0f}/90，強度 {g['strength']:.0f})",
                             expanded=(ind == rec["industry"] if rec else False)):
                for s in stocks:
                    chg_c = "green" if s.get("change", 0) >= 0 else "red"
                    st.markdown(f"""
                    <div class="stock-row">
                      <div class="name">{s['ticker']} {s['name']} <span class="purple">{s.get('score', 0)}</span></div>
                      <div class="meta">${s.get('close', 0):,.2f} <span class="{chg_c}">{s.get('change', 0):+.2f}%</span></div>
                    </div>
                    """, unsafe_allow_html=True)

# ────────────────────────
# Tab Market: 全市場創 2y 月線新高族群統計（yfinance 全市場 1962 檔）
# ────────────────────────
with tab_market:
    st.markdown("### 🌐 全市場創 2y 月線新高族群統計")
    mkt = data.get("tw_market_industry")
    if not mkt:
        st.info("⏸ 全市場族群資料未生成（等待下次 daily scan）")
    else:
        exact = mkt.get("exact_ath", [])
        near = mkt.get("near_ath_top30", [])
        stats = mkt.get("industry_stats", [])
        top_ind = mkt.get("top_industry")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("🔥 真正創新高", f"{len(exact)} 檔")
        col2.metric("🟡 接近高點 Top30", f"{len(near)} 檔")
        col3.metric("⭐⭐⭐ 明日高機率", f"{mkt.get('high_prob_count', 0)} 檔")
        col4.metric("🏆 族群最多", top_ind or "—")

        # ⭐⭐⭐ 明日續漲 Top 5
        tomorrow = mkt.get("tomorrow_top5", [])
        if tomorrow:
            st.markdown("### ⭐⭐⭐ 明日續漲高機率 Top 5")
            for i, t in enumerate(tomorrow, 1):
                ind = t.get("industry") or "未分類"
                tier = t.get("tier", "⭐")
                score = t.get("momentum_score", 0)
                prob = t.get("next_day_prob", "")
                notes = "、".join(t.get("momentum_notes", []))
                cls = "strong" if score >= 80 else ("ok" if score >= 60 else "warn")
                st.markdown(f"""
                <div class="alert-card {cls}">
                  <div class="name">#{i} {tier} {t['ticker']} {t['name']}
                    <span class="purple">{score}/100</span> ｜ 隔日續漲 {prob}</div>
                  <div class="meta">
                    {ind} ｜ ${t.get('today',0):.1f}
                    <span class="{'green' if t.get('change_pct',0)>=0 else 'red'}"> {t.get('change_pct',0):+.1f}%</span>
                    ｜ 量 {t.get('vol_ratio',0):.1f}x ｜ RSI {t.get('rsi',0):.0f}
                  </div>
                  <div class="meta" style="margin-top:4px;">📌 {notes}</div>
                </div>
                """, unsafe_allow_html=True)
            st.divider()

        # 族群分布表
        if stats:
            st.markdown("#### 📊 族群分布（依檔數排序）")
            classified = [s for s in stats if s["industry"] != "未分類"]
            unclassified = next((s for s in stats if s["industry"] == "未分類"), None)
            for s in classified[:10]:
                pct = s["bullish_count"] / s["count"] * 100 if s["count"] else 0
                st.markdown(f"""
                <div class="stock-row">
                  <div class="name">🏭 {s['industry']}</div>
                  <div class="meta">{s['count']} 檔 ｜ 多頭排列 {s['bullish_count']} 檔（{pct:.0f}%）</div>
                </div>
                """, unsafe_allow_html=True)
            if unclassified:
                st.caption(f"💡 未分類 {unclassified['count']} 檔（多為半導體周邊/設備未在字典內，可待擴充分類字典）")

        # 創新高個股清單（可展開）
        if exact:
            with st.expander(f"📋 全部 {len(exact)} 檔創新高個股", expanded=False):
                for r in exact:
                    bull = "✅ 多頭" if r.get("bullish") else "—"
                    ind = r.get("industry") or "未分類"
                    st.markdown(f"""
                    <div class="stock-row">
                      <div class="name">{r['ticker']} {r['name']} <span class="purple">{ind}</span></div>
                      <div class="meta">${r['today']:.1f} ｜ 距高 {r['from_high_pct']:+.2f}% ｜ {bull}</div>
                    </div>
                    """, unsafe_allow_html=True)

        st.caption(f"基準：{mkt.get('basis', '2y monthly')} ｜ 共分析 {mkt.get('total_analyzed', '?')} 檔")


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
