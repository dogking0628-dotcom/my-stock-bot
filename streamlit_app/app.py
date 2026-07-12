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
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
DASHBOARD_URL = f"{RAW_BASE}/dashboard_data.json"

@st.cache_data(ttl=300)
def load_raw_json(filename):
    """從 GitHub raw 讀任一 JSON（daily_v2_signal / daily_v41_signal / hot_money_signal / weekly_v2_review）"""
    try:
        req = urllib.request.Request(f"{RAW_BASE}/{filename}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception:
        return None

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
tab_signal, tab1, tab_ind, tab_market, tab_flow, tab_weekly, tab_zheng, tab_sync, tab_perf, tab2, tab3, tab4, tab5 = st.tabs([
    "📡 開盤掛單", "🎯 Top5+候選", "🏆 族群推薦", "🌐 全市場族群", "🔄 資金輪動", "📝 週檢討",
    "🧭 鄭大策略", "📡 美台同步", "📈 績效追蹤", "🇹🇼 篩選", "🇹🇼 0050", "🇺🇸 美股", "⚠️ 警報"
])

# ────────────────────────
# Tab Signal: V2 / V4.1 開盤掛單（電腦版選股系統手機版）
# ────────────────────────
def order_plan(price, per_stock):
    """與 daily_v2_picker.py / daily_v41_picker.py 完全相同的掛單計算"""
    limit_low = round(price * 1.008, 1)
    limit_high = round(price * 1.02, 1)
    shares = int(per_stock / limit_low / 1000) * 1000
    if shares < 1000:
        shares = 1000
    return limit_low, limit_high, shares, shares * limit_low

with tab_signal:
    st.markdown("### 📡 開盤掛單（V2 / V4.1 選股系統）")
    st.caption("與每日 5:30 LINE 推播完全同源，資料來自 daily_v2_signal.json / daily_v41_signal.json")

    capital = st.number_input(
        "💰 主動資金（元）", min_value=100_000, max_value=20_000_000,
        value=450_000, step=50_000,
        help="用來計算每檔掛單股數：總主動資金 ÷ 檔數（最多 3 檔）",
    )

    v2 = load_raw_json("daily_v2_signal.json")
    v41 = load_raw_json("daily_v41_signal.json")

    # ── V2 原版（唯一上線主策略）──
    st.markdown("#### 📡 V2 原版（主策略）")
    if not v2:
        st.info("⏸ 尚無 V2 訊號資料（等 daily scan 跑完）")
    else:
        v2_date = v2.get("timestamp", "?")
        top_inds = v2.get("top_industries", [])
        picks = v2.get("picks", [])
        st.caption(f"📅 訊號日期：**{v2_date}** ｜ {v2.get('strategy','')}")

        if top_inds:
            ind_summary = " / ".join(
                f"{s['industry']}({s.get('bullish_count', 0)}多頭)" for s in top_inds)
            st.markdown(f"""
            <div class="alert-card info">
              <div class="name">🔥 強族群</div>
              <div class="meta">{ind_summary}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="alert-card warn">
              <div class="name">😐 今日無強族群</div>
            </div>
            """, unsafe_allow_html=True)

        if not picks:
            st.markdown("""
            <div class="alert-card info">
              <div class="name">📭 今日無 V2 訊號 → 空手</div>
              <div class="meta">不要硬找，等明天（V2 抗崩盤強、常空手是特性）</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            n_picks = min(len(picks), 3)
            per_stock = capital / max(n_picks, 1)
            st.markdown(f"**🎯 {n_picks} 檔開盤掛單（每檔 {per_stock/10000:.0f} 萬）**")
            for i, p in enumerate(picks[:n_picks], 1):
                price = p.get("today", 0)
                limit_low, limit_high, shares, cost = order_plan(price, per_stock)
                stop = round(price * 0.93, 1)
                tag_parts = []
                if p.get("long_red"): tag_parts.append("長紅")
                if p.get("gap_up"): tag_parts.append("跳空")
                tag = "/".join(tag_parts)
                st.markdown(f"""
                <div class="alert-card strong">
                  <div class="name">#{i} {p['ticker']} {p['name']}
                    <span class="purple">{p.get('industry','?')}</span></div>
                  <div class="meta">📍 限價 <strong>${limit_low} - ${limit_high}</strong>
                    ｜ 💰 {shares:,} 股 ≈ ${cost:,.0f}</div>
                  <div class="meta">🛑 停損 <span class="red">${stop}</span> (-7%)
                    ｜ 📊 量 {p.get('vol_ratio',0):.1f}x ｜ RSI {p.get('rsi',0):.0f} {tag}</div>
                </div>
                """, unsafe_allow_html=True)

    # ── V4.1（並行比較）──
    st.markdown("#### 🎯 V4.1（並行比較）")
    if not v41:
        st.info("⏸ 尚無 V4.1 訊號資料")
    else:
        v41_date = v41.get("timestamp", "?")
        st.caption(f"📅 訊號日期：**{v41_date}** ｜ {v41.get('strategy','')}")

        if v41.get("v4_blocked"):
            st.markdown("""
            <div class="alert-card danger">
              <div class="name">⛔ 0050 跌破 MA200 → V4.1 今日空手</div>
              <div class="meta">熊市段，嚴禁追價</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            strongest = v41.get("strongest_industry")
            if strongest:
                st.markdown(f"""
                <div class="alert-card info">
                  <div class="name">🏆 最強族群：{strongest}</div>
                </div>
                """, unsafe_allow_html=True)
            v41_picks = v41.get("picks", [])
            if not v41_picks:
                st.markdown("""
                <div class="alert-card info">
                  <div class="name">📭 今日無 V4.1 訊號（動能&lt;80 或黑名單）→ 空手</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                n = min(len(v41_picks), 3)
                per = capital / max(n, 1)
                st.markdown(f"**🎯 {n} 檔開盤掛單（每檔 {per/10000:.0f} 萬）**")
                for i, p in enumerate(v41_picks[:n], 1):
                    price = p.get("today", 0)
                    limit_low, limit_high, shares, cost = order_plan(price, per)
                    ma20 = p.get("ma20", price * 0.95)
                    notes = "、".join(p.get("momentum_notes", [])[:3])
                    st.markdown(f"""
                    <div class="alert-card ok">
                      <div class="name">#{i} {p['ticker']} {p['name']}
                        <span class="purple">{p.get('industry','?')}</span>
                        ｜ {p.get('tier','⭐')} {p.get('momentum_score',0)}分 {p.get('next_day_prob','')}</div>
                      <div class="meta">📍 限價 <strong>${limit_low} - ${limit_high}</strong>
                        ｜ 💰 {shares:,} 股 ≈ ${cost:,.0f}</div>
                      <div class="meta">🛑 停損 跌破20MA <span class="red">${ma20:.1f}</span>
                        ｜ 📊 量 {p.get('vol_ratio',0):.1f}x ｜ RSI {p.get('rsi',0):.0f}</div>
                      <div class="meta">📌 {notes}</div>
                    </div>
                    """, unsafe_allow_html=True)

    # ── 操作 SOP ──
    st.markdown("""
    <div class="alert-card info">
      <div class="name">💡 操作 SOP</div>
      <div class="meta">1️⃣ 9:00 前掛限價低點</div>
      <div class="meta">2️⃣ 9:05 沒成交 → 改限價高點</div>
      <div class="meta">3️⃣ 9:10 仍無 → 放棄</div>
      <div class="meta">4️⃣ 跳空 +3% 以上 → 不追</div>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("⛔ 7 大鐵律（點開複習）", expanded=False):
        for rule in [
            "1. 不追今天漲停股（隔日 30% 機率假突破）",
            "2. 不聽喊單",
            "3. 不 panic sell（-10% 不砍，按系統 4 重防線出場）",
            "4. 不 all-in（主動部位永遠 ≤ 5%）",
            "5. 不加碼漲停（拉高均成本）",
            "6. 不破壞分批（0050 分批必須執行）",
            "7. 不看財經 24/7 直播（情緒污染）",
        ]:
            st.markdown(f"<div class='meta'>{rule}</div>", unsafe_allow_html=True)

# ────────────────────────
# Tab Zheng: 鄭大策略（讀 private repo invest-bot，需 GITHUB_TOKEN）
# ────────────────────────
INVEST_REPO = "dogking0628-dotcom/invest-bot"

@st.cache_data(ttl=300)
def load_invest_json(path):
    """從 private repo invest-bot 用 GitHub API 讀 JSON（需 token 有 invest-bot 的 Contents:Read）"""
    token = _gh_token()
    if not token:
        return None, "no_token"
    url = f"https://api.github.com/repos/{INVEST_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.raw+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "streamlit-app",
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)

with tab_zheng:
    st.markdown("### 🧭 鄭大策略（invest-bot）")
    st.caption("位階 × 族群 × CB 三層分析，資料來自 private repo invest-bot（每天 21:30 更新）")

    mp, err = load_invest_json("zheng-tracker/market_position.json")
    if mp is None:
        if err == "no_token":
            st.warning("需要 `GITHUB_TOKEN` 才能讀取 private repo invest-bot。"
                       "到 Streamlit Cloud → App settings → Secrets 設定。")
        elif err in ("HTTP 404", "HTTP 403", "HTTP 401"):
            st.warning(f"讀取 invest-bot 失敗（{err}）。"
                       "你的 GITHUB_TOKEN 可能只授權了 my-stock-bot——"
                       "請到 GitHub → Settings → Developer settings → Personal access tokens，"
                       "把 invest-bot 也加入授權（Contents: Read）。")
        else:
            st.error(f"讀取失敗：{err}")
    else:
        # ── 1️⃣ 大盤位階 ──
        stage = mp.get("stage", "?")
        cls = "danger" if stage.startswith("BEAR") else ("warn" if stage in ("BULL_LATE", "CONSOLIDATION") else "ok")
        ind = mp.get("indicators", {})
        pb = mp.get("playbook", {})
        notes = " ｜ ".join(mp.get("notes", []))
        st.markdown(f"""
        <div class="alert-card {cls}">
          <div class="name">{mp.get('label','?')}（{stage}）</div>
          <div class="meta">{mp.get('desc','')} ｜ {notes}</div>
          <div class="meta">{ind.get('zheng_da_signal','')}</div>
          <div class="meta">87MA 乖離 {ind.get('bias_87_pct',0):+.1f}% ｜ 284MA 乖離 {ind.get('bias_284_pct',0):+.1f}%
            ｜ 週K {ind.get('weekly_K',0):.0f} ｜ 月K {ind.get('monthly_K',0):.0f}</div>
          <div class="meta">💡 {pb.get('action','')}</div>
          <div class="meta">⚖️ {pb.get('risk_advice','')}</div>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"📅 更新：{mp.get('date','?')}")

        # ── 2️⃣ 族群輪動 ──
        rot, _ = load_invest_json("zheng-tracker/sector_rotation.json")
        if rot:
            results = rot.get("sector_results", {})
            leaders = sorted(results.items(), key=lambda kv: -kv[1].get("avg_5d", 0))
            st.markdown("#### 🔥 族群 RS 排名（5 日強弱）")
            for name, s in leaders[:8]:
                a5 = s.get("avg_5d", 0)
                color = "green" if a5 >= 0 else "red"
                st.markdown(f"""
                <div class="stock-row">
                  <div class="name">🏭 {name}
                    <span class="{color}">{a5:+.1f}%</span></div>
                  <div class="meta">20日 {s.get('avg_20d',0):+.1f}% ｜ RS動能 {s.get('rs_momentum',0):+.0f}
                    ｜ 最強成員 {s.get('top_member','?')}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── 3️⃣ 最新 picks ──
        ph, _ = load_invest_json("zheng-tracker/picks_history.json")
        if ph and ph.get("open_picks"):
            picks = ph["open_picks"]
            latest_date = max(p.get("pick_date", "") for p in picks)
            latest = [p for p in picks if p.get("pick_date") == latest_date]
            st.markdown(f"#### 🎯 最新 picks（{latest_date}，{len(latest)} 檔）")
            for p in latest[:10]:
                ret = p.get("current_return", 0)
                color = "green" if ret >= 0 else "red"
                st.markdown(f"""
                <div class="alert-card {'ok' if ret >= 0 else 'warn'}">
                  <div class="name">{p['ticker'].split('.')[0]} {p.get('name','')}
                    <span class="purple">{p.get('sector','?')}</span></div>
                  <div class="meta">進場 ${p.get('pick_price',0):,.1f} → 現價 ${p.get('current_price',0):,.1f}
                    ｜ <span class="{color}">{ret:+.1f}%</span> ｜ 持有 {p.get('days_held',0)} 天</div>
                  <div class="meta">📌 {p.get('reason','')}</div>
                </div>
                """, unsafe_allow_html=True)

            # 歷史最佳
            top_perf = sorted(picks, key=lambda p: -p.get("current_return", 0))[:5]
            with st.expander("🏆 未平倉最佳 5 檔", expanded=False):
                for p in top_perf:
                    st.markdown(f"""
                    <div class="stock-row">
                      <div class="name">{p['ticker'].split('.')[0]} {p.get('name','')}
                        <span class="green">{p.get('current_return',0):+.1f}%</span></div>
                      <div class="meta">{p.get('pick_date','?')} 進場 ｜ {p.get('sector','?')}
                        ｜ 峰值 +{p.get('max_return',0):.1f}%</div>
                    </div>
                    """, unsafe_allow_html=True)

        # ── 累積勝率 ──
        stats, _ = load_invest_json("zheng-tracker/stats.json")
        if stats:
            st.markdown("#### 📊 累積勝率")
            c1, c2, c3 = st.columns(3)
            c1.metric("總 picks", f"{stats.get('total',0)}")
            c2.metric("勝率", f"{stats.get('win_rate',0):.0f}%")
            c3.metric("平均報酬", f"{stats.get('avg_return',0):+.1f}%")
            by_stage = stats.get("by_stage", {})
            if by_stage:
                for sname, s in by_stage.items():
                    st.markdown(f"<div class='meta'>• <strong>{sname}</strong>："
                                f"{s.get('n',0)} 檔 ｜ 勝率 {s.get('win_rate',0):.0f}% "
                                f"｜ 平均 {s.get('avg_ret',0):+.1f}%</div>",
                                unsafe_allow_html=True)

# ────────────────────────
# Tab Flow: 資金輪動雷達（hot_money_signal.json）
# ────────────────────────
with tab_flow:
    st.markdown("### 🔄 資金輪動雷達")
    st.caption("「資金是一套」— 同一套錢從 A 族群跑到 B 族群。資料來自 hot_money_signal.json")
    hm = load_raw_json("hot_money_signal.json")
    if not hm:
        st.info("⏸ 尚無資金輪動資料（等 daily scan 跑完）")
    else:
        st.caption(f"📅 資料日期：**{hm.get('timestamp','?')}**")

        rising = hm.get("rising_industries", [])
        cooling = hm.get("cooling_industries", [])

        c1, c2 = st.columns(2)
        c1.metric("🔥 升溫族群", f"{len(rising)} 個")
        c2.metric("🧊 降溫族群", f"{len(cooling)} 個")

        if rising:
            st.markdown("#### 🔥 資金流入（升溫）")
            for r in rising:
                br = r.get("bullish_ratio", 0)
                st.markdown(f"""
                <div class="alert-card ok">
                  <div class="name">🏭 {r.get('industry','?')}
                    <span class="green">{r.get('momentum_pct',0):+.0f}%</span></div>
                  <div class="meta">{r.get('status','')} ｜ 連續 {r.get('trend_days',0)} 天
                    ｜ 今日 {r.get('today',0)} 檔 vs 均值 {r.get('avg_n',0):.1f}
                    ｜ 多頭比 {br*100 if br <= 1 else br:.0f}%</div>
                </div>
                """, unsafe_allow_html=True)

        if cooling:
            st.markdown("#### 🧊 資金流出（降溫）— 持股在這些族群要警覺")
            for r in cooling:
                st.markdown(f"""
                <div class="alert-card danger">
                  <div class="name">🏭 {r.get('industry','?')}
                    <span class="red">{r.get('momentum_pct',0):+.0f}%</span></div>
                  <div class="meta">{r.get('status','')} ｜ 連續 {r.get('trend_days',0)} 天
                    ｜ 今日 {r.get('today',0)} 檔 vs 均值 {r.get('avg_n',0):.1f}</div>
                </div>
                """, unsafe_allow_html=True)

        rotation = hm.get("rotation_picks", [])
        if rotation:
            st.markdown("#### 🎯 輪動候選（升溫族群內最強個股）")
            for p in rotation:
                st.markdown(f"""
                <div class="alert-card strong">
                  <div class="name">{p['ticker']} {p['name']}
                    <span class="purple">{p.get('industry','?')}</span>
                    ｜ {p.get('tier','⭐')} {p.get('momentum_score',0)}分</div>
                  <div class="meta">${p.get('today',0):,.1f}
                    ｜ 族群動能 {p.get('industry_momentum_pct',0):+.0f}%
                    ｜ 輪動分 {p.get('rotation_score',0):.0f}</div>
                </div>
                """, unsafe_allow_html=True)

        fake_risk = hm.get("fake_breakout_risk", [])
        if fake_risk:
            st.markdown("#### ⚠️ 假突破風險（個股強但族群降溫 = Sell the news）")
            for p in fake_risk:
                st.markdown(f"""
                <div class="alert-card warn">
                  <div class="name">⚠️ {p['ticker']} {p['name']}
                    <span class="purple">{p.get('industry','?')}</span></div>
                  <div class="meta">個股 {p.get('momentum_score',0)} 分但族群
                    <span class="red">{p.get('industry_momentum_pct',0):+.0f}%</span>
                    （{p.get('industry_status','降溫')}）｜ 不建議追</div>
                </div>
                """, unsafe_allow_html=True)

        if not rising and not cooling and not rotation and not fake_risk:
            st.info("⏸ 今日族群熱度平穩，無明顯輪動訊號")

# ────────────────────────
# Tab Weekly: V2 週檢討（weekly_v2_review.json）
# ────────────────────────
with tab_weekly:
    st.markdown("### 📝 V2 週檢討")
    st.caption("每週六自動產出，追蹤本週 V2 訊號的實際表現。資料來自 weekly_v2_review.json")
    wk = load_raw_json("weekly_v2_review.json")
    if not wk or not wk.get("picks"):
        st.info("⏸ 尚無週檢討資料（每週六 10:00 產出）")
    else:
        st.caption(f"📅 期間：**{wk.get('period','?')}**")
        total = wk.get("total_picks", 0)
        wins = wk.get("wins", 0)
        avg_ret = wk.get("avg_return", 0)
        c1, c2, c3 = st.columns(3)
        c1.metric("本週訊號", f"{total} 檔")
        c2.metric("勝率", f"{wins/total*100:.0f}%" if total else "—", f"{wins} 勝 {total-wins} 敗")
        c3.metric("平均報酬", f"{avg_ret:+.1f}%")

        for p in wk["picks"]:
            ret = p.get("ret_pct", 0)
            status = p.get("status", "?")
            cls = "danger" if "停損" in status or ret < 0 else "ok"
            color = "green" if ret >= 0 else "red"
            st.markdown(f"""
            <div class="alert-card {cls}">
              <div class="name">{p['ticker']} {p.get('name','')}
                <span class="purple">{p.get('industry','?')}</span></div>
              <div class="meta">{status} ｜ 訊號日 {p.get('signal_date','?')}
                ｜ 持有 {p.get('days_held',0)} 天</div>
              <div class="meta">進場 ${p.get('actual_entry',0):,.1f} → 現價 ${p.get('current',0):,.1f}
                ｜ <span class="{color}">{ret:+.2f}%</span>
                ｜ 峰值 +{p.get('peak_pct',0):.1f}% / 低點 {p.get('low_pct',0):.1f}%</div>
            </div>
            """, unsafe_allow_html=True)

        by_ind = wk.get("by_industry", {})
        if by_ind:
            st.markdown("#### 🏭 本週訊號族群分布")
            for ind, tickers in by_ind.items():
                st.markdown(f"<div class='meta'>• <strong>{ind}</strong>：{', '.join(tickers)}</div>",
                            unsafe_allow_html=True)

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
                <span class="purple">{s.get('score', 0)}/90</span></div>
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
# Tab Sync: 美台同步族群（V5c 資訊參考，不影響進場）
# ────────────────────────
with tab_sync:
    st.markdown("### 📡 美台同步族群（資訊參考）")
    st.caption("昨日美股大漲族群 → 對應台股族群同步表現（不取代 V4 主邏輯）")
    sync = data.get("us_tw_sync")
    if not sync:
        st.info("⏸ 資料尚未生成（等待下次 daily scan）")
    else:
        us_sectors = sync.get("us_sectors_yesterday", {})
        hot = sync.get("hot_us_sectors", {})
        synced = sync.get("tw_industries_synced", [])
        top5 = sync.get("top5_synced_picks", [])

        # 美股 ETF 漲跌排名
        st.markdown("#### 🇺🇸 昨日美股 ETF 漲跌")
        sorted_us = sorted(us_sectors.items(), key=lambda x: -x[1])
        for tk, chg in sorted_us:
            emoji = "🔥" if chg >= 1 else ("✅" if chg >= 0 else "❌")
            color = "green" if chg >= 0 else "red"
            st.markdown(f"<div class='stock-row'>"
                       f"<div class='name'>{emoji} {tk}</div>"
                       f"<div class='meta'><span class='{color}'>{chg:+.2f}%</span></div>"
                       f"</div>", unsafe_allow_html=True)

        # 對應台股族群
        if synced:
            st.markdown(f"#### 🇹🇼 對應台股族群")
            for ind in synced:
                st.markdown(f"  • **{ind}**")

        # Top 5 同步推薦
        if top5:
            st.markdown("#### 🎯 美台同步 Top 5（可考慮加碼參考）")
            for i, s in enumerate(top5, 1):
                st.markdown(f"""
                <div class="alert-card ok">
                  <div class="name">#{i} {s['ticker']} {s['name']}（{s.get('industry','?')}）</div>
                  <div class="meta">
                    昨日 <span class="green">{s['change_pct']:+.2f}%</span>
                    ｜ 收 ${s['today']:.1f}
                    ｜ 美股族群連動 +{s.get('us_score',0):.1f}%
                    ｜ 綜合分 {s.get('combined',0):.1f}
                  </div>
                </div>
                """, unsafe_allow_html=True)
        elif hot:
            st.warning("⚠️ 美股有大漲但台股對應族群昨日未跟進")
        else:
            st.info("⏸ 昨日美股無大漲族群（≥1%）")


# ────────────────────────
# Tab Perf: 績效追蹤（每日 Top 5 後續表現）
# ────────────────────────
with tab_perf:
    st.markdown("### 📈 績效追蹤儀表板")
    st.caption("追蹤每日 Top 5 推薦的實際後續表現")

    # 從 GitHub 拉 top5_history.json
    HISTORY_URL = "https://raw.githubusercontent.com/dogking0628-dotcom/my-stock-bot/main/top5_history.json"

    @st.cache_data(ttl=600)
    def load_history():
        try:
            req = urllib.request.Request(HISTORY_URL, headers={"User-Agent":"Mozilla/5.0"})
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception as e:
            return None

    @st.cache_data(ttl=600)
    def fetch_latest_prices(tickers):
        """批次抓最新收盤"""
        try:
            import yfinance as yf
            yf_codes = " ".join(f"{t}.TW" for t in tickers)
            df = yf.download(yf_codes, period="3d", auto_adjust=True,
                            progress=False, threads=True, group_by="ticker")
            out = {}
            for t in tickers:
                try:
                    yfc = f"{t}.TW"
                    if len(tickers) == 1:
                        sub = df
                    else:
                        if yfc not in df.columns.get_level_values(0): continue
                        sub = df[yfc]
                    cl = sub["Close"].dropna()
                    if len(cl) > 0:
                        out[t] = float(cl.iloc[-1])
                except Exception:
                    continue
            return out
        except Exception:
            return {}

    hist = load_history()
    if not hist or not hist.get("records"):
        st.info("⏸ 尚無選股歷史紀錄（等待 daily scan 累積）")
    else:
        records = hist["records"]
        # 收集所有推薦過的 ticker
        all_tickers = list(set(p["ticker"] for r in records for p in r.get("picks", [])))

        with st.spinner(f"抓取 {len(all_tickers)} 檔最新報價..."):
            latest_prices = fetch_latest_prices(all_tickers)

        # 計算每筆推薦的後續表現
        all_picks = []
        for r in records:
            rec_date = r["date"]
            for p in r.get("picks", []):
                latest = latest_prices.get(p["ticker"])
                if latest is None or p.get("rec_close", 0) <= 0:
                    continue
                ret = (latest / p["rec_close"] - 1) * 100
                all_picks.append({
                    "date": rec_date,
                    "ticker": p["ticker"],
                    "name": p["name"],
                    "industry": p.get("industry", "?"),
                    "rec_close": p["rec_close"],
                    "current": latest,
                    "ret_pct": ret,
                    "hit": ret > 0,
                    "score": p.get("momentum_score", 0),
                    "tier": p.get("tier", "⭐"),
                })

        if not all_picks:
            st.warning("無法取得後續價格資料")
        else:
            # ── 整體統計 ──
            n = len(all_picks)
            wins = sum(1 for p in all_picks if p["hit"])
            avg_ret = sum(p["ret_pct"] for p in all_picks) / n
            win_picks = [p for p in all_picks if p["hit"]]
            lose_picks = [p for p in all_picks if not p["hit"]]
            avg_win = sum(p["ret_pct"] for p in win_picks)/len(win_picks) if win_picks else 0
            avg_loss = sum(p["ret_pct"] for p in lose_picks)/len(lose_picks) if lose_picks else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("總推薦數", f"{n} 筆")
            c2.metric("勝率", f"{wins/n*100:.0f}%", f"{wins} 勝 {n-wins} 敗")
            c3.metric("平均報酬", f"{avg_ret:+.2f}%")
            c4.metric("盈虧比", f"{abs(avg_win/avg_loss):.2f}" if avg_loss else "∞")

            # ── 最佳 / 最差 ──
            best = max(all_picks, key=lambda x: x["ret_pct"])
            worst = min(all_picks, key=lambda x: x["ret_pct"])
            cb, cw = st.columns(2)
            with cb:
                st.markdown(f"""
                <div class="alert-card ok">
                  <div class="name">🏆 最佳</div>
                  <div class="meta">{best['ticker']} {best['name']} ({best['industry']})</div>
                  <div class="meta"><span class="green">{best['ret_pct']:+.2f}%</span> ｜ {best['date']}</div>
                </div>
                """, unsafe_allow_html=True)
            with cw:
                st.markdown(f"""
                <div class="alert-card danger">
                  <div class="name">💀 最差</div>
                  <div class="meta">{worst['ticker']} {worst['name']} ({worst['industry']})</div>
                  <div class="meta"><span class="red">{worst['ret_pct']:+.2f}%</span> ｜ {worst['date']}</div>
                </div>
                """, unsafe_allow_html=True)

            # ── 各日 Top 5 紀錄表 ──
            st.markdown("### 📋 每日推薦回顧")
            for r in reversed(records):
                rec_date = r["date"]
                day_picks = [p for p in all_picks if p["date"] == rec_date]
                if not day_picks: continue
                d_wins = sum(1 for p in day_picks if p["hit"])
                d_avg = sum(p["ret_pct"] for p in day_picks) / len(day_picks)
                tag = "✅" if d_avg > 0 else "❌"
                with st.expander(
                    f"{tag} {rec_date} {r.get('industry','?')} "
                    f"{d_wins}/{len(day_picks)} 勝 ({d_wins/len(day_picks)*100:.0f}%) "
                    f"平均 {d_avg:+.2f}%",
                    expanded=(rec_date == records[-1]["date"])
                ):
                    for p in day_picks:
                        emoji = "✅" if p["hit"] else "❌"
                        color = "green" if p["hit"] else "red"
                        st.markdown(f"""
                        <div class="stock-row">
                          <div class="name">{emoji} {p['ticker']} {p['name']}
                            <span class="purple">{p['industry']}</span></div>
                          <div class="meta">
                            推薦 ${p['rec_close']:.1f} → 現價 ${p['current']:.1f}
                            ｜ <span class="{color}">{p['ret_pct']:+.2f}%</span>
                            ｜ {p['tier']} 動能 {p['score']}/100
                          </div>
                        </div>
                        """, unsafe_allow_html=True)

            # ── 族群表現 ──
            st.markdown("### 🏭 各族群表現")
            ind_stats = {}
            for p in all_picks:
                ind = p["industry"]
                if ind not in ind_stats:
                    ind_stats[ind] = {"n": 0, "wins": 0, "ret_sum": 0}
                ind_stats[ind]["n"] += 1
                ind_stats[ind]["ret_sum"] += p["ret_pct"]
                if p["hit"]: ind_stats[ind]["wins"] += 1
            sorted_ind = sorted(ind_stats.items(),
                              key=lambda x: -x[1]["ret_sum"]/x[1]["n"])
            for ind, s in sorted_ind:
                avg = s["ret_sum"]/s["n"]
                wr = s["wins"]/s["n"]*100
                color = "green" if avg > 0 else "red"
                st.markdown(f"""
                <div class="stock-row">
                  <div class="name">🏭 {ind}</div>
                  <div class="meta">
                    {s['n']} 檔 ｜ 勝率 {wr:.0f}%
                    ｜ 平均 <span class="{color}">{avg:+.2f}%</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

            # ── 訊號表現（Tier） ──
            st.markdown("### ⭐ 訊號表現（按 Tier）")
            tier_stats = {}
            for p in all_picks:
                t = p["tier"]
                if t not in tier_stats:
                    tier_stats[t] = {"n": 0, "wins": 0, "ret_sum": 0}
                tier_stats[t]["n"] += 1
                tier_stats[t]["ret_sum"] += p["ret_pct"]
                if p["hit"]: tier_stats[t]["wins"] += 1
            for t in ["⭐⭐⭐", "⭐⭐", "⭐"]:
                if t not in tier_stats: continue
                s = tier_stats[t]
                avg = s["ret_sum"]/s["n"]
                wr = s["wins"]/s["n"]*100
                color = "green" if avg > 0 else "red"
                st.markdown(f"""
                <div class="stock-row">
                  <div class="name">{t}</div>
                  <div class="meta">
                    {s['n']} 檔 ｜ 勝率 {wr:.0f}%
                    ｜ 平均 <span class="{color}">{avg:+.2f}%</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

    st.caption("💡 資料來自 top5_history.json + 即時 yfinance 報價，每 10 分鐘更新一次")


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
