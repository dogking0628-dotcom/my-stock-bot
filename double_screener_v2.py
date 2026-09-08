# -*- coding: utf-8 -*-
"""
3年翻倍股初篩器 v2 — Quality × Structural Growth × EPS Revision × Valuation × Re-rating
（規格：Downloads/3Y_DoubleBagger_APP_v2-2.md，2026-09-06 ChatGPT 版；本檔為其可自動化子集實作）
═══════════════════════════════════════════════════════════════════════
漏斗（FinMind 免費額度 600/hr → checkpoint 可續跑）：
  S0 市值 ≥100億（marketcap_cache.json，零 API）
  S1 月營收動能：YoY3m ≥10% 且加速 ≥-5pp，或 v1 規則（≥15% / ≥5%且加速≥10pp）（沿用 v1 快取）
  S2 Quality：損益表 5y + 資產負債表 + 現金流量表（3 call/檔，精確標籤解析，_per 排除）
  S3 估值：PE 5y 分布（p25/p50/p75/百分位）（1 call/檔）
  S4 技術位：yfinance 批次（零額度）
  ── 以下零 API ──
  Cycle Pool 分流：cyclical_sector（產業/名單）＋ cycle_peak_risk 自動規則 → Normalized EPS / PE
  Structural Growth 20 分：質化輸入（double_inputs_v2.json）優先，缺則用 capex/毛利/營收 proxy
  Forward EPS：質化輸入（法人/ChatGPT 估）優先，缺則用 fade-growth proxy → EPS_CAGR_3Y
  Double-PE Test：required_pe_for_2x = 2×現價 / EPS_FY3，對照歷史 PE p50/p75
  Bear/Base/Bull：EPS_FY3 × 歷史 PE p25/p50/p75
  100 分制：Quality25 + Growth20 + Structural20 + Revision15 + Valuation10 + Rerating10
  分級：S ≥85（且 Forward EPS 為已驗證輸入）/ A 75-84 / B 65-74 / WATCH <65 / Cycle Pool 獨立（不與結構成長股同榜）
輸出：double_candidates_v2.md / .csv / double_screener_v2_result.json
用法：python double_screener_v2.py [--only 2330,2345] [--max-calls 550] [--no-tech] [--strict-eps] [--research-pack 30]
v3 研究層（2026-09-06 對齊 ChatGPT tw_doublebagger_screener_v3.zip）：讀 data/qualitative_research.csv（分數需附 source 才採計）、
  data/scenario_inputs.csv（人工 Bear/Base/Bull EPS×PE，優先於自動情境）、data/forward_consensus.csv、data/structural_inputs.csv；
  S 級硬規則＝已驗證 FY EPS＋有來源質化＋人工 Bear case＋Base≥60%＋R/R≥1.5
v4 決策層（對齊 tw_doublebagger_screener_v4.zip）：data/evidence_ledger.csv 證據帳本（180 天內＋URL 才有效；新鮮證據亦視為有來源）、
  S 級另需覆蓋率≥50%（否則降 A）、Refresh Queue（double_refresh_queue.csv）、Portfolio Engine（double_model_portfolio.csv，單檔≤20%/產業≤35%）、
  儀表板 double_dashboard.md（Top10＋模型組合＋重查佇列＋動作訊號）
v5 追蹤層（對齊 tw_doublebagger_screener_v5.zip）：double_holdings.json 實際持股強制納入評分；data/thesis_updates.csv（thesis_status/
  governance_red_flag）、data/revision_updates.csv、data/monthly_updates.csv（覆寫用）；Signal Engine 輸出 ADD/HOLD/WATCH/REDUCE/EXIT
  （ADD 需新鮮證據帳本，較 ChatGPT 版嚴）→ double_action_queue.csv
v6 Horizon Guard（對齊 v6.zip）：eps_<YYYY> 共識映射 T+1~T+3（2026 執行 → 2029E 才算完整）；只有 T+2 → eps_confidence=verified-T+2、
  判定加註(T+2代理)、不得進 S、入重查佇列；data/consensus_pool.csv = ChatGPT 第一版真實候選池（2026-09-06，法人共識為 ChatGPT 檢索、未驗證）
      額度用盡會印「額度用盡」並正常退出（exit 0），再跑即續（_screener_v2_loop.sh）
"""
import sys, io, os, json, time, math, argparse, urllib.request, urllib.error
import datetime as dt
from statistics import median

if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT); sys.path.insert(0, ROOT)
from industry_map_loader import get_industry

VERSION = "v2.4"
MIN_MCAP = 100
API = "https://api.finmindtrade.com/api/v4/data"
SLEEP = 0.35
CKPT_DIR = os.path.join("data", "checkpoints")
V1_PROG = "double_screener_progress.json"          # v1 進度檔：s1 直接沿用
INPUTS = "double_inputs_v2.json"                    # 質化輸入（用戶/ChatGPT 填）
# 每個 stage 的 parser schema 版本；parser 改了就 +1，舊 checkpoint 自動失效
SCHEMA = {"s1": 1, "s2": 3, "s3": 2}

# 週期性產業（證交所產業別）＋ 已知週期股名單（記憶體/面板/航運/鋼鐵）；inputs 可覆寫 cyclical_override
CYCLICAL_INDUSTRIES = {"航運", "鋼鐵", "塑膠", "水泥", "造紙", "橡膠", "玻璃陶瓷", "油電燃氣"}
CYCLICAL_TICKERS = {
    "2344", "2408", "3006", "4967", "2451", "8271", "2337", "3260", "8299", "3474", "5289",  # 記憶體
    "2409", "3481", "6116",                                                                   # 面板
    "2603", "2609", "2615", "2606", "2637", "2605",                                           # 航運
    "2002", "2014", "2027", "2006",                                                           # 鋼鐵
    "1301", "1303", "1326", "1305", "1605",                                                   # 塑化/線纜
}

quota_dead = False
calls_used = 0
MAX_CALLS = 550


# ───────────────────────────── FinMind ─────────────────────────────
def _token():
    t = os.environ.get("FINMIND_TOKEN")
    if t: return t.strip()
    try: return io.open(os.path.join(ROOT, "finmind_token.txt"), encoding="utf-8").read().strip()
    except Exception: return ""


TOKEN = _token()


def fm(dataset, sid, start):
    """FinMind 抓取；402/429 → 休息一次再試，仍失敗視為額度用盡；超過 MAX_CALLS 也視為用盡（保護額度）"""
    global quota_dead, calls_used
    if quota_dead: return None
    if calls_used >= MAX_CALLS:
        quota_dead = True; return None
    u = f"{API}?dataset={dataset}&data_id={sid}&start_date={start}"
    if TOKEN: u += f"&token={TOKEN}"
    for attempt in range(2):
        try:
            if not reserve_call():
                print(f"  ⏳ 最近一小時已用 {HOURLY_CAP} 次（跨執行統計），視為額度用盡", file=sys.stderr)
                quota_dead = True; return None
            calls_used += 1
            j = json.loads(urllib.request.urlopen(u, timeout=25).read())
            time.sleep(SLEEP)
            return j.get("data") or []
        except urllib.error.HTTPError as e:
            if e.code in (402, 429):
                if attempt == 0:
                    print("  ⏳ 額度受限，休息 65s 再試...", file=sys.stderr); time.sleep(65); continue
                quota_dead = True; return None
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return []


# ───────────────────────────── Checkpoint ─────────────────────────────
def ckpt_path(stage): return os.path.join(CKPT_DIR, f"{stage}.json")


def load_ckpt(stage):
    os.makedirs(CKPT_DIR, exist_ok=True)
    p = ckpt_path(stage)
    if os.path.exists(p):
        d = json.load(io.open(p, encoding="utf-8"))
        if d.get("schema_version") == SCHEMA[stage]:
            return d
        print(f"  ♻️ {stage} checkpoint schema {d.get('schema_version')} → {SCHEMA[stage]}，舊快取失效")
    d = {"schema_version": SCHEMA[stage], "items": {}}
    if stage == "s1" and os.path.exists(V1_PROG):       # v1 的 s1 解析未變，直接遷移
        v1 = json.load(io.open(V1_PROG, encoding="utf-8")).get("s1") or {}
        d["items"].update(v1)
        print(f"  ♻️ 自 v1 進度檔遷移 s1 {len(v1)} 檔")
    save_ckpt(stage, d)
    return d


def save_ckpt(stage, d):
    """原子寫入：先寫 .tmp 再 replace，避免中途被殺留下半截 JSON（學自 ChatGPT 版 checkpoint.py）"""
    p = ckpt_path(stage); tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, p)


USAGE_PATH = os.path.join(CKPT_DIR, "api_usage.json")
HOURLY_CAP = 590            # FinMind 免費 600/hr，留 10 次餘裕；跨執行滾動一小時計算（學自 ChatGPT 版 finmind_client.py）


def _usage_load():
    try: return json.load(io.open(USAGE_PATH, encoding="utf-8"))
    except Exception: return []


def reserve_call():
    """記錄一次呼叫；若最近一小時已達 HOURLY_CAP 則回 False（呼叫方視為額度用盡）"""
    os.makedirs(CKPT_DIR, exist_ok=True)
    now = time.time(); xs = [t for t in _usage_load() if now - t < 3600]
    if len(xs) >= HOURLY_CAP:
        json.dump(xs, io.open(USAGE_PATH, "w", encoding="utf-8")); return False
    xs.append(now); json.dump(xs, io.open(USAGE_PATH, "w", encoding="utf-8")); return True


# ───────────────────────────── 精確標籤解析（規格 §14）─────────────────────────────
def pick_exact(rows, labels):
    """long-format 財報 → {date: value}。只比對 type 精確相等，依 labels 優先序；_per / 百分比欄一律排除。"""
    for lab in labels:
        out = {}
        for r in rows:
            ty = r.get("type") or ""
            if ty.endswith("_per") or "percent" in ty.lower() or "佔比" in ty or "百分比" in ty:
                continue
            if ty == lab:
                out[r["date"]] = r["value"]
        if out: return out
    return {}


def _sum_last(d, dates, n, offset=0):
    """dates 已排序；取倒數 offset..offset+n 個季度加總（缺值視 0）"""
    seg = dates[len(dates) - offset - n: len(dates) - offset] if len(dates) >= offset + n else []
    return sum(d.get(x, 0) or 0 for x in seg) if seg else None


# ───────────────────────────── S1 月營收動能（沿用 v1）─────────────────────────────
def stage1(sid, ck):
    it = ck["items"]
    if sid in it: return it[sid]
    rows = fm("TaiwanStockMonthRevenue", sid, (dt.date.today() - dt.timedelta(days=800)).isoformat())
    if rows is None: return None
    rev = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in rows}
    keys = sorted(rev); out = {"ok": False}
    if len(keys) >= 16:
        def yoy(k):
            prev = (k[0] - 1, k[1])
            return (rev[k] / rev[prev] - 1) * 100 if rev.get(prev) else None
        yoys = [(k, yoy(k)) for k in keys[-6:]]
        yoys = [(k, v) for k, v in yoys if v is not None]
        if len(yoys) >= 6:
            y3 = sum(v for _, v in yoys[-3:]) / 3; yp3 = sum(v for _, v in yoys[:3]) / 3
            r12 = sum(rev[k] for k in keys[-12:]); r12p = sum(rev[k] for k in keys[-24:-12]) if len(keys) >= 24 else None
            out = {"ok": True, "yoy3": round(y3, 1), "yoy_prev3": round(yp3, 1), "accel": round(y3 - yp3, 1),
                   "rev12m_g": round((r12 / r12p - 1) * 100, 1) if r12p else None}
    it[sid] = out; save_ckpt("s1", ck)
    return out


def s1_pass(r):
    if not r or not r.get("ok"): return False
    y, a = r["yoy3"], r["accel"]
    return (y >= 10 and a >= -5) or y >= 15 or (y >= 5 and a >= 10)


# ───────────────────────────── S2 Quality + 5y EPS 歷史 + 現金流 ─────────────────────────────
def stage2(sid, ck):
    it = ck["items"]
    if sid in it: return it[sid]
    inc = fm("TaiwanStockFinancialStatements", sid, (dt.date.today() - dt.timedelta(days=365 * 6 + 60)).isoformat())  # 6y → 5 個完整年度
    if inc is None: return None
    bal = fm("TaiwanStockBalanceSheet", sid, (dt.date.today() - dt.timedelta(days=900)).isoformat())
    if bal is None: return None
    cf = fm("TaiwanStockCashFlowsStatement", sid, (dt.date.today() - dt.timedelta(days=900)).isoformat())
    if cf is None: return None

    eps = pick_exact(inc, ["EPS"])
    ni = pick_exact(inc, ["EquityAttributableToOwnersOfParent", "IncomeAfterTaxes"])
    revq = pick_exact(inc, ["Revenue"])
    gp = pick_exact(inc, ["GrossProfit"])
    opi = pick_exact(inc, ["OperatingIncome"])
    eq = pick_exact(bal, ["EquityAttributableToOwnersOfParent", "Equity"])
    cap = pick_exact(bal, ["OrdinaryShare", "CapitalStock"])
    cash = pick_exact(bal, ["CashAndCashEquivalents"])
    ltd = pick_exact(bal, ["LongtermBorrowings"]); std = pick_exact(bal, ["ShorttermBorrowings"])
    bonds = pick_exact(bal, ["BondsPayable"])
    ocf = pick_exact(cf, ["NetCashInflowFromOperatingActivities", "CashFlowsFromOperatingActivities"])
    capex = pick_exact(cf, ["PropertyAndPlantAndEquipment"])
    dep = pick_exact(cf, ["Depreciation"]); amo = pick_exact(cf, ["AmortizationExpense"])

    ds = sorted(eps); out = {"ok": False}
    if len(ds) >= 8:
        e4 = _sum_last(eps, ds, 4); e4p = _sum_last(eps, ds, 4, 4)
        ni4 = _sum_last(ni, ds, 4)
        r4 = _sum_last(revq, ds, 4); r4p = _sum_last(revq, ds, 4, 4)
        eqd = sorted(eq)[-4:]
        eqs = [eq[d] for d in eqd if eq.get(d)]
        roe = ni4 / (sum(eqs) / len(eqs)) * 100 if eqs and sum(eqs) > 0 and ni4 is not None else None
        # ROIC proxy = 營業利益×(1-20%) / (權益 + 有息負債 − 現金)
        opi4 = _sum_last(opi, ds, 4)
        bd = sorted(eq)[-1] if eq else None
        debt = (ltd.get(bd, 0) or 0) + (std.get(bd, 0) or 0) + (bonds.get(bd, 0) or 0) if bd else 0
        cash_now = cash.get(bd, 0) or 0 if bd else 0
        ic = (eq.get(bd, 0) or 0) + debt - cash_now if bd else 0
        roic = opi4 * 0.8 / ic * 100 if opi4 is not None and ic and ic > 0 else None

        def margin(num, dd):
            r, g = revq.get(dd), num.get(dd)
            return g / r * 100 if r and g is not None and r > 0 else None
        gms = [m for m in (margin(gp, d) for d in ds[-8:]) if m is not None]
        opms = [m for m in (margin(opi, d) for d in ds[-8:]) if m is not None]
        gm_now = sum(gms[-4:]) / len(gms[-4:]) if len(gms) >= 4 else None
        gm_prev = sum(gms[:-4]) / len(gms[:-4]) if len(gms) >= 8 else None
        opm_now = sum(opms[-4:]) / len(opms[-4:]) if len(opms) >= 4 else None
        opm_prev = sum(opms[:-4]) / len(opms[:-4]) if len(opms) >= 8 else None
        gm_5y = [m for m in (margin(gp, d) for d in ds) if m is not None]
        gm_5y_med = median(gm_5y) if len(gm_5y) >= 8 else None

        caps = sorted(cap)
        cap_chg = (cap[caps[-1]] / cap[caps[0]] - 1) * 100 if len(caps) >= 2 and cap[caps[0]] else 0

        # 年度 EPS（完整年份，用於 5y median = Normalized EPS proxy）
        by_year = {}
        for d in ds: by_year.setdefault(d[:4], []).append(eps[d])
        annual = {y: sum(v) for y, v in by_year.items() if len(v) == 4}
        ann_vals = [annual[y] for y in sorted(annual)]
        eps_5y_med = median(ann_vals) if len(ann_vals) >= 3 else None
        # 最新一季 EPS YoY（Revision 1M proxy 用）
        q_yoy = None
        if len(ds) >= 5 and eps.get(ds[-5]) and eps[ds[-5]] > 0:
            q_yoy = (eps[ds[-1]] / eps[ds[-5]] - 1) * 100

        # 現金流（近 4 季 vs 前 4 季）
        cds = sorted(ocf)
        ocf4 = _sum_last(ocf, cds, 4); capex4 = _sum_last(capex, sorted(capex), 4); capex4p = _sum_last(capex, sorted(capex), 4, 4)
        ocf8 = _sum_last(ocf, cds, 8) if len(cds) >= 8 else ocf4
        ni8 = _sum_last(ni, ds, 8) if len(cds) >= 8 else ni4
        capex4 = -capex4 if capex4 is not None and capex4 < 0 else capex4      # FinMind 取得不動產為負值
        capex4p = -capex4p if capex4p is not None and capex4p < 0 else capex4p
        fcf4 = ocf4 - capex4 if ocf4 is not None and capex4 is not None else None
        da4 = (_sum_last(dep, sorted(dep), 4) or 0) + (_sum_last(amo, sorted(amo), 4) or 0)
        ebitda4 = (opi4 or 0) + da4
        net_debt = debt - cash_now
        nd_ebitda = net_debt / ebitda4 if ebitda4 and ebitda4 > 0 else None

        out = {"ok": True, "last_q": ds[-1], "eps4": round(e4, 2), "eps4_prev": round(e4p, 2),
               "eps_g": round((e4 / e4p - 1) * 100, 1) if e4p and e4p > 0 else (999 if e4 and e4 > 0 else None),
               "q_eps_yoy": round(q_yoy, 1) if q_yoy is not None else None,
               "roe": round(roe, 1) if roe is not None else None,
               "roic": round(roic, 1) if roic is not None else None,
               "rev4": r4, "rev4_g": round((r4 / r4p - 1) * 100, 1) if r4 and r4p else None,
               "gm_now": round(gm_now, 1) if gm_now is not None else None,
               "gm_chg": round(gm_now - gm_prev, 1) if gm_now is not None and gm_prev is not None else None,
               "gm_5y_med": round(gm_5y_med, 1) if gm_5y_med is not None else None,
               "opm_now": round(opm_now, 1) if opm_now is not None else None,
               "opm_chg": round(opm_now - opm_prev, 1) if opm_now is not None and opm_prev is not None else None,
               "cap_chg": round(cap_chg, 1),
               "eps_annual": {y: round(v, 2) for y, v in annual.items()},
               "eps_5y_med": round(eps_5y_med, 2) if eps_5y_med is not None else None,
               "ocf4": ocf4, "capex4": capex4, "fcf4": fcf4,
               "fcf_margin": round(fcf4 / r4 * 100, 1) if fcf4 is not None and r4 else None,
               "ocf_ni": round(ocf8 / ni8, 2) if ocf8 is not None and ni8 and ni8 > 0 else None,   # 8 季（規格「長期」）
               "capex_g": round((capex4 / capex4p - 1) * 100, 1) if capex4 and capex4p else None,
               "capex_rev": round(capex4 / r4 * 100, 1) if capex4 is not None and r4 else None,
               "net_debt": net_debt, "nd_ebitda": round(nd_ebitda, 2) if nd_ebitda is not None else None}
    it[sid] = out; save_ckpt("s2", ck)
    return out


def s2_pass(r):
    return bool(r and r.get("ok") and (r.get("roe") or 0) >= 12 and (r.get("eps_g") or -1) > 0)


# ───────────────────────────── S3 PE 5y 分布 ─────────────────────────────
def stage3(sid, ck):
    it = ck["items"]
    if sid in it: return it[sid]
    rows = fm("TaiwanStockPER", sid, (dt.date.today() - dt.timedelta(days=365 * 5)).isoformat())
    if rows is None: return None
    pes = [r["PER"] for r in rows if r.get("PER") and 0 < r["PER"] < 300]
    out = {"ok": False}
    if len(pes) >= 200:
        cur = pes[-1]; s = sorted(pes); n = len(s)
        q = lambda p: s[min(n - 1, int(n * p))]
        out = {"ok": True, "pe": round(cur, 1), "pe_pct5y": round(sum(1 for x in pes if x <= cur) / n * 100, 0),
               "pe_p25": round(q(0.25), 1), "pe_p50": round(q(0.5), 1), "pe_p75": round(q(0.75), 1),
               "pe_p90": round(q(0.9), 1), "pbr": (rows[-1].get("PBR") if rows else None)}
    it[sid] = out; save_ckpt("s3", ck)
    return out


# ───────────────────────────── S4 技術位（yfinance）─────────────────────────────
def stage4(tickers):
    tech = {}
    if not tickers: return tech
    try:
        import yfinance as yf
        for chunk in (tickers[i:i + 150] for i in range(0, len(tickers), 150)):
            df = yf.download(" ".join(f"{c}.TW" for c in chunk), period="2y", auto_adjust=True,
                             progress=False, threads=True, group_by="ticker")
            for c in chunk:
                try:
                    cl = df[f"{c}.TW"]["Close"].dropna()
                    if len(cl) < 200: continue
                    px = float(cl.iloc[-1])
                    tech[c] = {"px": round(px, 1), "vs200": round((px / float(cl.rolling(200).mean().iloc[-1]) - 1) * 100, 1),
                               "vs_hi2y": round((px / float(cl.max()) - 1) * 100, 1)}
                except Exception: pass
    except Exception as e:
        print("S4 tech fail", e)
    return tech


# ───────────────────────────── 質化輸入（JSON + ChatGPT v3 CSV）─────────────────────────────
RESEARCH_CSVS = {   # ChatGPT tw_doublebagger_screener v3~v5 的資料檔（data/ 下），欄位原樣相容；CSV 優先於 JSON，後者覆蓋前者
    "structural_inputs.csv": None, "forward_consensus.csv": None,
    "consensus_pool.csv": None,      # ChatGPT「第一版真實候選池」格式：eps_2026~eps_2029 / base_pe_t3 / bull_eps_t3 / bull_pe_t3 / action_first_cut / thesis / risk
    "qualitative_research.csv": None, "scenario_inputs.csv": None,
    "monthly_updates.csv": None, "revision_updates.csv": None, "thesis_updates.csv": None,   # v5（monthly 多為本系統自算，僅作覆寫）
}
HOLDINGS = "double_holdings.json"     # 實際持股（v5 追蹤用；持股一律強制納入評分，即使不在市值名單）
_num_keys = {"next_platform_score", "new_capacity_score", "new_product_mix_score", "order_visibility_score", "tam_score",
             "governance_score", "rerating_score", "customer_concentration_pct", "eps_fy1", "eps_fy2", "eps_fy3",
             "eps_rev_1m", "eps_rev_3m", "eps_rev_6m", "revision_breadth", "normal_pe",
             "bear_eps_3y", "bear_pe_3y", "base_eps_3y", "base_pe_3y", "bull_eps_3y", "bull_pe_3y",
             "revenue_yoy_pct", "revenue_accel_pp", "pe_percentile_5y", "base_return_pct", "reward_risk",
             "eps_2025", "eps_2026", "eps_2027", "eps_2028", "eps_2029", "eps_2030", "base_pe_t3", "bull_eps_t3", "bull_pe_t3"}
T3_YEAR = dt.date.today().year + 3      # v6 Horizon Guard：2026 執行 → 真正三年期 = 2029E


def apply_horizon(inp):
    """v6：把 eps_<YYYY> 共識映射成 FY1~FY3（T+1~T+3）。只有 T+3 存在才算 horizon_complete；只有 T+2 → 代理、不得標三年翻倍。"""
    e = {y: inp.get(f"eps_{y}") for y in range(T3_YEAR - 3, T3_YEAR + 1)}
    t3, t2, t1 = e.get(T3_YEAR), e.get(T3_YEAR - 1), e.get(T3_YEAR - 2)
    note = str(inp.get("scenario_notes") or "")
    if ("T+2" in note or "2028E" in note) and not (t3 and t3 > 0):   # 情境檔自述為 T+2 代理 → 三年期不完整
        inp["horizon_complete"] = False
    if inp.get("eps_fy1") and inp.get("eps_fy3"):          # 已直接給 FY 欄位者不動
        inp.setdefault("horizon_complete", True); return inp
    if t3 and t3 > 0:
        inp.update(eps_fy1=t1 or t2, eps_fy2=t2 or t3, eps_fy3=t3, horizon_complete=True, eps_source=f"consensus T+3({T3_YEAR}E)")
    elif t2 and t2 > 0:
        inp.update(eps_fy1=t1 or t2, eps_fy2=t2, eps_fy3=t2, horizon_complete=False, eps_source=f"consensus T+2({T3_YEAR-1}E)代理")
    elif t1 and t1 > 0:
        inp.update(eps_fy1=t1, horizon_complete=False, eps_source=f"consensus T+1({T3_YEAR-2}E)僅一年")
    # ChatGPT 池的 T+3 情境：base_pe_t3 / bull_eps_t3 / bull_pe_t3（無 Bear → 只能算 partial）
    if inp.get("base_pe_t3") and inp.get("eps_fy3") and not inp.get("base_eps_3y"):
        inp.update(base_eps_3y=inp["eps_fy3"], base_pe_3y=inp["base_pe_t3"])
    if inp.get("bull_eps_t3") and inp.get("bull_pe_t3") and not inp.get("bull_eps_3y"):
        inp.update(bull_eps_3y=inp["bull_eps_t3"], bull_pe_3y=inp["bull_pe_t3"])
    if inp.get("thesis") and not inp.get("why"): inp["why"] = inp["thesis"]
    return inp
_bool_keys = {"governance_red_flag", "capacity_prebooked", "long_term_contract", "hard_reject", "cyclical_override"}


def load_holdings():
    if not os.path.exists(HOLDINGS): return {}
    d = json.load(io.open(HOLDINGS, encoding="utf-8"))
    return {str(h["ticker"]): h for h in d.get("tw", []) if h.get("ticker")}


def _read_csv_rows(path):
    import csv as _csv
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for row in _csv.DictReader(f):
            t = (row.get("ticker") or row.get("stock_id") or "").strip()
            if not t: continue
            clean = {}
            for k, v in row.items():
                if k is None: continue
                k = k.strip(); v = (v or "").strip()
                if v == "": continue
                if k in _num_keys:
                    try: v = float(v)
                    except ValueError: continue
                elif k in _bool_keys:
                    v = v.strip().lower() in ("true", "1", "yes", "y", "是")
                clean[k] = v
            yield t, clean


def load_inputs():
    """合併順序：double_inputs_v2.json → data/*.csv（後者覆蓋）。qualitative_research.csv 無 source 者其分數作廢（v3 Hard Rule 1）。"""
    d = {}
    if os.path.exists(INPUTS):
        raw = json.load(io.open(INPUTS, encoding="utf-8"))
        d = {k: dict(v) for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
    for fn in [f for base in RESEARCH_CSVS for f in (base, base.replace(".csv", ".pending.csv"))]:   # .pending = 檔案被鎖時的暫存
        p = os.path.join("data", fn)
        if not os.path.exists(p): continue
        n = 0
        for t, row in _read_csv_rows(p):
            n += 1
            if fn == "qualitative_research.csv":
                srcs = [row.get(k) for k in ("source_1", "source_2", "source_3") if row.get(k)]
                row["evidence_ok"] = bool(srcs); row["sources"] = srcs
                if not srcs:      # 無來源：只保留 thesis/risk 文字，分數一律不採計
                    for k in ("next_platform_score", "new_capacity_score", "new_product_mix_score", "order_visibility_score",
                              "tam_score", "governance_score", "rerating_score"):
                        row.pop(k, None)
                if row.get("thesis"): row["why"] = row["thesis"]
                if row.get("customer_concentration_pct") is not None: row["customer_concentration"] = row["customer_concentration_pct"]
            d.setdefault(t, {}).update(row)
        print(f"  📥 data/{fn}: {n} 檔")
    for t in d: apply_horizon(d[t])
    return d


def clip(x, lo, hi): return max(lo, min(hi, x))


# ───────────────────────────── Cycle Pool 分流（規格 §4 / §11）─────────────────────────────
def cycle_check(sid, industry, m, inp):
    ov = inp.get("cyclical_override")
    cyc_sector = bool(ov) if ov is not None else (industry in CYCLICAL_INDUSTRIES or sid in CYCLICAL_TICKERS)
    pe, eg, y3 = m.get("pe"), m.get("eps_g"), m.get("yoy3")
    gm_jump = (m.get("gm_now") - m.get("gm_5y_med")) if m.get("gm_now") is not None and m.get("gm_5y_med") is not None else None
    # 自動觸發：PE<10 且 EPS YoY>100% 且營收 YoY>30% 且毛利率大幅跳升（>5pp vs 5y 中位）
    auto = (pe is not None and pe < 10 and (eg or 0) > 100 and (y3 or 0) > 30 and (gm_jump is not None and gm_jump > 5))
    # 週期產業且當前 EPS 高於 5y 中位 1.5 倍以上 → 同樣視為高峰
    peak = auto or (cyc_sector and m.get("eps_5y_med") and m.get("eps4") and m["eps4"] > 1.5 * max(m["eps_5y_med"], 0.01))
    norm_eps = m.get("eps_5y_med")
    if norm_eps is not None and norm_eps <= 0:
        norm_eps = round((m.get("eps4") or 0) * 0.5, 2)   # 5y 中位為負/零 → 保守取當前一半
    px = m.get("px")
    return {"cyclical_sector": cyc_sector, "cycle_peak_risk": bool(peak),
            "normalized_eps_required": bool(auto or cyc_sector),
            "gm_jump": round(gm_jump, 1) if gm_jump is not None else None,
            "normalized_eps": norm_eps,
            "normalized_pe": round(px / norm_eps, 1) if px and norm_eps and norm_eps > 0 else None}


# ───────────────────────────── Forward EPS / Revision（規格 §5）─────────────────────────────
STRICT_EPS = False   # --strict-eps：無法人共識就不猜（ChatGPT 版原則），FY 留空、Double-PE 回「資料不足」


def forward_eps(m, cyc, inp):
    eps4 = m.get("eps4") or 0
    conf = "proxy"
    if inp.get("eps_fy1") and inp.get("eps_fy3"):
        fy1, fy3 = float(inp["eps_fy1"]), float(inp["eps_fy3"])
        fy2 = float(inp.get("eps_fy2") or math.sqrt(max(fy1 * fy3, 0)))
        src = "manual:" + str(inp.get("eps_source", "consensus"))
        conf = "verified" if inp.get("horizon_complete", True) else "verified-T+2"   # v6：只有 T+3 共識才算完整
    elif inp.get("eps_fy1") and eps4 > 0:                    # 只有 T+1 共識：以它為起點，後兩年 proxy 衰減
        fy1 = float(inp["eps_fy1"]); g = clip(fy1 / eps4 - 1, -0.2, 0.45)
        fy2 = fy1 * (1 + g * 0.5); fy3 = fy2 * (1 + g * 0.3)
        src = "partial:" + str(inp.get("eps_source", "T+1")) + "+proxy"; conf = "partial"
    elif inp.get("base_eps_3y"):                      # v3 scenario_inputs 的 Base EPS 3Y 亦視為已驗證 FY3
        fy3 = float(inp["base_eps_3y"]); fy1 = eps4 * (fy3 / eps4) ** (1 / 3) if eps4 > 0 else fy3; fy2 = fy1 * (fy3 / eps4) ** (1 / 3) if eps4 > 0 else fy3
        src = "manual:scenario base_eps_3y"; conf = "verified"
    elif STRICT_EPS:
        fy1 = fy2 = fy3 = None; src = "none(strict)"; conf = "none"
    elif cyc["cycle_peak_risk"] and cyc.get("normalized_eps"):
        n = cyc["normalized_eps"]; fy1, fy2, fy3 = eps4 * 0.85, (eps4 * 0.85 + n) / 2, n
        src = "proxy:cycle→normalized"
    else:
        y3, eg = m.get("yoy3") or 0, m.get("eps_g")
        eg = 100 if eg is None or eg >= 999 else eg
        raw = 0.5 * y3 + 0.5 * min(eg, 120)                            # 營收/EPS 動能各半
        g1 = 45 * math.tanh(max(raw, -40) / 50) / 100                  # tanh 平滑：raw 30→21%、50→34%、85→42%、上限 45%
        fy1 = eps4 * (1 + g1); fy2 = fy1 * (1 + g1 * 0.5); fy3 = fy2 * (1 + g1 * 0.3)   # 成長逐年衰減
        src = f"proxy:raw{raw:.0f}%→g1={g1*100:.0f}%衰減"
    cagr = ((fy3 / eps4) ** (1 / 3) - 1) * 100 if eps4 > 0 and fy3 and fy3 > 0 else None
    # Revision：質化輸入優先，否則 proxy（3M=月營收加速；1M=最新季 EPS YoY 相對 4 季成長的變化）
    if inp.get("eps_rev_3m") is not None:
        rev3, rev1, rsrc = float(inp["eps_rev_3m"]), float(inp.get("eps_rev_1m") or inp["eps_rev_3m"]), "manual"
    else:
        rev3 = m.get("accel")
        qy, eg4 = m.get("q_eps_yoy"), m.get("eps_g")
        rev1 = (min(qy, 300) - min(eg4, 300)) if qy is not None and eg4 is not None else None
        rsrc = "proxy"
    up, dn = inp.get("analyst_up_count"), inp.get("analyst_down_count")
    if up is None and inp.get("revision_breadth") is not None:      # ChatGPT 版欄位別名：淨上修家數（正=上修多）
        rb = float(inp["revision_breadth"]); up, dn = (max(rb, 0), 0) if rb >= 0 else (0, -rb)
    r2 = lambda v: round(v, 2) if v is not None else None
    return {"eps_fy1": r2(fy1), "eps_fy2": r2(fy2), "eps_fy3": r2(fy3), "eps_source": src, "eps_confidence": conf,
            "eps_cagr_3y": round(cagr, 1) if cagr is not None else None,
            "eps_rev_1m": round(rev1, 1) if rev1 is not None else None,
            "eps_rev_3m": round(rev3, 1) if rev3 is not None else None, "rev_source": rsrc,
            "analyst_up": up, "analyst_down": dn}


# ───────────────────────────── Structural Growth 20 分（規格 §3）─────────────────────────────
def structural(m, inp):
    src = []
    direct = lambda k, cap: clip(float(inp[k]), 0, cap) if inp.get(k) is not None else None   # v3 CSV 已是滿分制分數
    # Next Platform 5：v3 直接分數 0-5；或 JSON 等級 0-3
    np_score = direct("next_platform_score", 5)
    if np_score is None:
        npf = inp.get("next_platform_12m")
        if npf is None: npf = inp.get("next_platform_24m")
        np_score = clip(int(npf), 0, 3) / 3 * 5 if npf is not None else 0
        src.append("platform:" + ("manual" if npf is not None else "none"))
    else: src.append("platform:manual")
    # New Capacity 4：v3 直接分數；或 JSON 等級 0-3；否則 capex 成長 proxy
    nc = inp.get("new_capacity_24m", inp.get("new_capacity_12m"))
    if direct("new_capacity_score", 4) is not None:
        nc_score = direct("new_capacity_score", 4); src.append("capacity:manual")
    elif nc is not None:
        nc_score = clip(int(nc), 0, 3) / 3 * 4; src.append("capacity:manual")
    else:
        cg, cr = m.get("capex_g"), m.get("capex_rev")
        nc_score = 0
        if cg is not None:
            nc_score = 4 if cg >= 50 else 3 if cg >= 20 else 1.5 if cg > 0 else 0
            if cr is not None and cr < 2: nc_score = min(nc_score, 1.5)   # 資本支出佔營收<2% → 擴產不顯著
        src.append("capacity:proxy")
    if inp.get("capacity_prebooked"): nc_score = min(4, nc_score + 1)
    # New Product Mix 4：質化 new_product_revenue_pct / ai_revenue_pct；否則 毛利率↑且營收↑ proxy
    npct = inp.get("new_product_revenue_pct", inp.get("ai_revenue_pct"))
    if direct("new_product_mix_score", 4) is not None:
        mix = direct("new_product_mix_score", 4); src.append("mix:manual")
    elif npct is not None:
        npct = float(npct); mix = 4 if npct >= 40 else 3 if npct >= 25 else 2 if npct >= 10 else 1 if npct > 0 else 0
        src.append("mix:manual")
    else:
        gc, y3 = m.get("gm_chg") or 0, m.get("yoy3") or 0
        mix = 2 if (gc > 2 and y3 > 20) else 1 if (gc > 0.5 and y3 > 10) else 0
        src.append("mix:proxy")
    # Order Visibility 3：質化 months；客戶集中 >50% 扣 1
    ov = inp.get("order_visibility_months")
    if direct("order_visibility_score", 3) is not None:
        vis = direct("order_visibility_score", 3); src.append("visibility:manual")
    else:
        vis = (3 if ov >= 12 else 2 if ov >= 6 else 1 if ov >= 3 else 0) if ov is not None else 0
        if inp.get("long_term_contract"): vis = min(3, vis + 1)
        src.append("visibility:" + ("manual" if ov is not None else "none"))
    cc = inp.get("customer_concentration")
    if cc is not None and float(cc) > 50: vis = max(0, vis - 1)
    # TAM 4：v3 直接分數 tam_score；或 JSON tam_growth_score 0-4
    if direct("tam_score", 4) is not None:
        tam = direct("tam_score", 4); src.append("tam:manual")
    else:
        tam = clip(int(inp["tam_growth_score"]), 0, 4) if inp.get("tam_growth_score") is not None else 0
        if inp.get("new_market_entry") and tam < 4: tam = min(4, tam + 1)
        src.append("tam:" + ("manual" if inp.get("tam_growth_score") is not None else "none"))
    total = np_score + nc_score + mix + vis + tam
    return {"next_platform_score": round(np_score, 1), "new_capacity_score": round(nc_score, 1),
            "new_product_mix": round(mix, 1), "order_visibility": vis, "tam_score": tam,
            "structural_score": round(total, 1), "structural_source": ",".join(src),
            "quali_filled": sum(1 for s in src if s.endswith("manual")),
            "evidence_ok": bool(inp.get("evidence_ok")), "sources": "；".join(inp.get("sources") or [])}


# ───────────────────────────── Re-rating 10 分（規格 §7）─────────────────────────────
def rerating(m, inp):
    if inp.get("rerating_score") is not None:      # v3 CSV 直接給 0-10（已通過來源檢查）
        s = clip(float(inp["rerating_score"]), 0, 10)
        return {"rerating_bm": None, "rerating_nm": None, "rerating_room": None, "rerating_score": s}
    bm = clip(int(inp.get("business_model_change") or 0), 0, 4)
    nm = clip(int(inp.get("new_market_entry") or 0), 0, 3)
    if inp.get("pe_expansion_room") is not None:
        room = clip(int(inp["pe_expansion_room"]), 0, 3)
    else:
        pp = m.get("pe_pct5y")
        room = 0 if pp is None else 3 if pp <= 30 else 2 if pp <= 50 else 1 if pp <= 70 else 0
    return {"rerating_bm": bm, "rerating_nm": nm, "rerating_room": room, "rerating_score": bm + nm + room}


# ───────────────────────────── Double-PE Test + 三情境（規格 §8 §9）─────────────────────────────
def double_pe(m, fe, cyc, rr, inp=None):
    inp = inp or {}
    px, fy3 = m.get("px"), fe.get("eps_fy3")
    p25, p50, p75, p90 = m.get("pe_p25"), m.get("pe_p50"), m.get("pe_p75"), m.get("pe_p90")
    out = {"required_pe_for_2x": None, "double_verdict": "資料不足(無FY3 EPS)" if not fy3 else "資料不足",
           "bear_target": None, "base_target": None, "bull_target": None,
           "bear_upside": None, "base_upside": None, "bull_upside": None, "down_up_ratio": None}
    if not px or not fy3 or fy3 <= 0 or not p50: return out
    req = 2 * px / fy3
    out["required_pe_for_2x"] = round(req, 1)
    normal = inp.get("normal_pe")                                  # ChatGPT 版欄位：公司「合理 PE」，有填就以它為準
    if normal:
        normal = float(normal)
        v = "合理(≤合理PE)" if req <= normal else "需Re-rating" if req <= 1.3 * normal else "淘汰(PE不合理)"
        p50 = normal; p75 = max(p75 or normal, normal)
    elif req <= p50: v = "合理(≤PE中位)"
    elif req <= p75: v = "合理(≤PE p75)"
    elif req <= p90 or req <= 1.3 * p75: v = "需Re-rating"
    else: v = "淘汰(PE不合理)"
    if fe.get("eps_confidence") == "proxy": v += "(proxy EPS)"
    if cyc["cycle_peak_risk"]: v += "｜週期:用Normalized"
    out["double_verdict"] = v
    manual = all(inp.get(k) for k in ("bear_eps_3y", "bear_pe_3y", "base_eps_3y", "base_pe_3y", "bull_eps_3y", "bull_pe_3y"))
    bear_pe = min(p25, m.get("pe") or p25); bull_pe = p75 if rr["rerating_score"] < 6 else max(p75, p90 or p75)
    bear_a, base_a, bull_a = fy3 * 0.75 * bear_pe, fy3 * p50, fy3 * 1.15 * bull_pe
    if manual:                                                     # v3 Scenario Engine：人工三情境 EPS×PE
        bear = float(inp["bear_eps_3y"]) * float(inp["bear_pe_3y"]); base = float(inp["base_eps_3y"]) * float(inp["base_pe_3y"])
        bull = float(inp["bull_eps_3y"]) * float(inp["bull_pe_3y"]); out["scenario_source"] = "manual"
    elif inp.get("base_eps_3y") and inp.get("base_pe_3y"):       # 只有 Base（/Bull）人工、無 Bear → partial（v3 Hard Rule 4：不得進 S）
        base = float(inp["base_eps_3y"]) * float(inp["base_pe_3y"])
        bull = float(inp["bull_eps_3y"]) * float(inp["bull_pe_3y"]) if inp.get("bull_eps_3y") and inp.get("bull_pe_3y") else bull_a
        bear = base * 0.75 * 0.75                                   # Bear 由人工 Base 推：EPS -25% × PE -25%（不混用歷史 PE）
        out["scenario_source"] = "partial(無Bear)"
    else:
        bear, base, bull = bear_a, base_a, bull_a; out["scenario_source"] = "auto"
    bear = min(bear, base * 0.8); bull = max(bull, base)           # 情境單調：Bear ≤ 0.8×Base ≤ Bull
    if not inp.get("horizon_complete", True): out["double_verdict"] += "(T+2代理)"
    bear_r, base_r, bull_r = (bear / px - 1) * 100, (base / px - 1) * 100, (bull / px - 1) * 100
    out.update({"bear_target": round(bear, 0), "base_target": round(base, 0), "bull_target": round(bull, 0),
                "bear_upside": round(bear_r, 0), "base_upside": round(base_r, 0), "bull_upside": round(bull_r, 0),
                "reward_risk": round(min(max(base_r, 0) / max(-bear_r, 5), 9.9), 2),   # v3：downside 至少以 5% 計；顯示上限 9.9
                "doublebagger_base": base_r >= 100, "doublebagger_bull": bull_r >= 100})
    if base > px and bear < px:
        out["down_up_ratio"] = round((px - bear) / (base - px), 2)
    return out


# ───────────────────────────── 100 分評分（規格 §10）─────────────────────────────
def score100(m, st, fe, rr, inp):
    d = {}
    # A. Quality 25
    roe, roic = m.get("roe") or 0, m.get("roic")
    a = (5 if roe >= 20 else 4 if roe >= 15 else 2 if roe >= 12 else 0)
    a += (3 if roic is not None and roic >= 15 else 2 if roic is not None and roic >= 12 else 1 if roic is not None and roic >= 8 else 0)
    fm_, ocfn = m.get("fcf_margin"), m.get("ocf_ni")
    cfs = 0
    if (m.get("ocf4") or 0) > 0: cfs += 2
    if fm_ is not None: cfs += 4 if fm_ >= 10 else 3 if fm_ >= 5 else 1 if fm_ > 0 else 0
    if ocfn is not None and ocfn < 0.5: cfs = min(cfs, 1)          # 現金流與獲利背離
    cfs = min(cfs, 6)
    nd, nde = m.get("net_debt"), m.get("nd_ebitda")
    bs = 4 if nd is not None and nd <= 0 else 3 if nde is not None and nde < 1 else 2 if nde is not None and nde < 2 else 1 if nde is not None and nde < 3 else 0
    cc = abs(m.get("cap_chg") or 0)
    dil = 3 if cc < 3 else 2 if cc < 10 else 1 if cc < 20 else 0
    gov = clip(int(inp["governance_score"]), 0, 4) if inp.get("governance_score") is not None else 2   # 未審核預設 2
    d["quality"] = a + cfs + bs + dil + gov
    # B. Growth 20
    y3, eg = m.get("yoy3") or 0, m.get("eps_g")
    eg = 100 if eg is None or eg >= 999 else eg
    g = (6 if y3 >= 30 else 5 if y3 >= 20 else 3 if y3 >= 10 else 1 if y3 >= 5 else 0)
    g += (6 if eg >= 40 else 4 if eg >= 20 else 2 if eg > 0 else 0)
    gc, oc = m.get("gm_chg"), m.get("opm_chg")
    g += (2 if gc is not None and gc > 2 else 1 if gc is not None and gc > 0.5 else 0)
    g += (2 if oc is not None and oc > 2 else 1 if oc is not None and oc > 0.5 else 0)
    g += (4 if eg > y3 and (oc or 0) > 0 else 2 if eg > y3 else 0)     # 營運槓桿
    d["growth"] = min(g, 20)
    # C. Structural 20
    d["structural"] = st["structural_score"]
    # D. Revision 15
    r1, r3 = fe.get("eps_rev_1m"), fe.get("eps_rev_3m")
    rv = (5 if r3 is not None and r3 >= 10 else 4 if r3 is not None and r3 >= 5 else 3 if r3 is not None and r3 >= 0 else 1 if r3 is not None and r3 >= -5 else 0)
    rv += (5 if r1 is not None and r1 >= 10 else 3 if r1 is not None and r1 >= 0 else 1 if r1 is not None and r1 >= -10 else 0)
    up, dn = fe.get("analyst_up"), fe.get("analyst_down")
    if up is not None and dn is not None:
        rv += 5 if up >= 3 * max(dn, 1) and up >= 3 else 3 if up > dn else 1 if up == dn else 0
    d["revision"] = rv
    # E. Valuation 10（週期股不用 PEG，改看 Normalized PE）
    fpe, cagr, pp = m.get("forward_pe"), fe.get("eps_cagr_3y"), m.get("pe_pct5y")
    v = (4 if fpe is not None and fpe <= 15 else 3 if fpe is not None and fpe <= 25 else 2 if fpe is not None and fpe <= 35 else 1 if fpe is not None and fpe <= 50 else 0)
    peg = m.get("peg")
    if m.get("cycle_peak_risk"):
        npe = m.get("normalized_pe")
        v += 3 if npe is not None and npe <= 12 else 2 if npe is not None and npe <= 18 else 0
    else:
        v += (3 if peg is not None and peg < 0.8 else 2 if peg is not None and peg < 1.2 else 1 if peg is not None and peg < 1.5 else 0)
    v += (3 if pp is not None and pp <= 30 else 2 if pp is not None and pp <= 60 else 1 if pp is not None and pp <= 80 else 0)
    d["valuation"] = v
    # F. Re-rating 10
    d["rerating"] = rr["rerating_score"]
    d["score100"] = round(sum(d.values()), 1)
    return d


def tier_of(sc, fe, dp, cyc, inp, st):
    if inp.get("hard_reject"): return "REJECT"
    if cyc["cycle_peak_risk"] or cyc["cyclical_sector"]: return "CYCLE"
    s = sc["score100"]; cagr = fe.get("eps_cagr_3y") or 0; ok_pe = dp["double_verdict"].startswith("合理")
    # S 級硬規則（v3）：已驗證 Forward EPS、有來源的質化研究、人工 Bear case、Base 報酬≥60% 且 reward/risk≥1.5
    if (s >= 85 and cagr >= 25 and ok_pe and (fe.get("eps_rev_3m") or 0) >= 0 and fe.get("eps_confidence") == "verified"
            and st.get("evidence_ok") and dp.get("scenario_source") == "manual"
            and (dp.get("base_upside") or 0) >= 60 and (dp.get("reward_risk") or 0) >= 1.5):
        ev = inp.get("_evidence") or {}
        # v4 Hard Rule 3：S 級但證據覆蓋率 <50% 或無新鮮證據 → 降 A 並進重查佇列
        if (ev.get("fresh_evidence_count") or 0) > 0 and (ev.get("evidence_coverage") or 0) >= 0.5: return "S"
        return "A"
    if s >= 75: return "A"
    if s >= 65: return "B"
    return "WATCH"


# ───────────────────────────── v4 Evidence Ledger / Refresh Queue / Portfolio ─────────────────────────────
EVIDENCE_TYPES = {"next_platform", "new_capacity", "new_product_mix", "order_visibility", "tam", "governance",
                  "rerating", "eps_revision", "customer", "risk"}
EVIDENCE_CORE = {"next_platform", "new_capacity", "new_product_mix", "order_visibility", "tam", "rerating"}
EVIDENCE_MAX_AGE = 180
EVIDENCE_COLS = ["ticker", "claim_type", "claim", "source_url", "source_name", "published_at", "research_date",
                 "confidence", "direction", "notes"]


def load_evidence():
    """data/evidence_ledger.csv → {ticker: 摘要}。有效證據 = 合法 claim_type + 有 URL + 有 claim + published_at 在 180 天內。"""
    p = os.path.join("data", "evidence_ledger.csv")
    if not os.path.exists(p): return {}
    import csv as _csv
    today = dt.date.today(); by = {}
    with io.open(p, encoding="utf-8-sig", newline="") as f:
        for r in _csv.DictReader(f):
            t = (r.get("ticker") or "").strip()
            if not t: continue
            ct = (r.get("claim_type") or "").strip()
            try: age = (today - dt.date.fromisoformat((r.get("published_at") or "").strip()[:10])).days
            except ValueError: age = None
            fresh = age is not None and 0 <= age <= EVIDENCE_MAX_AGE
            valid = ct in EVIDENCE_TYPES and bool((r.get("source_url") or "").strip()) and bool((r.get("claim") or "").strip()) and fresh
            try: conf = clip(float(r.get("confidence") or 0), 0, 1)
            except ValueError: conf = 0
            e = by.setdefault(t, {"evidence_count": 0, "fresh_evidence_count": 0, "stale": 0, "conf": [], "types": set(),
                                  "sources": [], "risk_claims": []})
            e["evidence_count"] += 1
            if not fresh: e["stale"] += 1
            if valid:
                e["fresh_evidence_count"] += 1; e["conf"].append(conf); e["types"].add(ct)
                sn = (r.get("source_name") or "").strip() or (r.get("source_url") or "").strip()
                if sn and sn not in e["sources"]: e["sources"].append(sn)
                if ct == "risk": e["risk_claims"].append((r.get("claim") or "").strip())
    out = {}
    for t, e in by.items():
        out[t] = {"evidence_count": e["evidence_count"], "fresh_evidence_count": e["fresh_evidence_count"],
                  "avg_confidence": round(sum(e["conf"]) / len(e["conf"]), 2) if e["conf"] else None,
                  "stale_ratio": round(e["stale"] / e["evidence_count"], 2) if e["evidence_count"] else None,
                  "evidence_coverage": round(len(e["types"] & EVIDENCE_CORE) / len(EVIDENCE_CORE), 2),
                  "sources": e["sources"], "risk_claims": e["risk_claims"]}
    print(f"  📥 data/evidence_ledger.csv: {len(out)} 檔、{sum(v['evidence_count'] for v in out.values())} 條")
    return out


def refresh_info(tier, ev, horizon_missing=False):
    """v4 Refresh Queue：S/A 級但沒新鮮證據、覆蓋率<50%、過期比例>50% → 高優先重查；v6：缺 T+3 共識亦入列"""
    ev = ev or {}
    fresh = ev.get("fresh_evidence_count") or 0
    cov = ev.get("evidence_coverage") or 0
    stale = ev.get("stale_ratio"); stale = 1.0 if stale is None else stale
    pri = {"S": 4, "A": 3, "B": 2}.get(tier, 1) + (3 if fresh == 0 else 0) + (2 if cov < 0.5 else 0) + (2 if stale > 0.5 else 0)
    if not ev: reasons = ["無證據帳本"]
    else: reasons = ([] if fresh else ["無新鮮證據"]) + (["論點覆蓋不足"] if cov < 0.5 else []) + (["來源過期"] if stale > 0.5 else [])
    if horizon_missing: reasons.append(f"缺{T3_YEAR}E共識"); pri += 2
    return {"refresh_priority": pri, "refresh_reason": "、".join(reasons)}


def action_signal(x, inp, held):
    """v5 Signal Engine：ADD / HOLD / WATCH / REDUCE / EXIT。
    與 ChatGPT 版差異：ADD 必須有證據帳本的新鮮證據（無帳本不得 ADD）；HOLD 容許無帳本。持股另附交易憲法防守線提示。"""
    ev = inp.get("_evidence") or {}
    score, base, rr = x["score100"], x.get("base_upside"), x.get("reward_risk")
    r1, r3 = x.get("eps_rev_1m"), x.get("eps_rev_3m")
    pp = inp.get("pe_percentile_5y") if inp.get("pe_percentile_5y") is not None else x.get("pe_pct5y")
    if inp.get("base_return_pct") is not None: base = inp["base_return_pct"]
    if inp.get("reward_risk") is not None: rr = inp["reward_risk"]
    fresh, cov = ev.get("fresh_evidence_count"), ev.get("evidence_coverage")
    has_ledger = bool(ev)
    ev_fresh = bool(has_ledger and (fresh or 0) >= 1 and (cov or 0) >= 0.5)
    status = str(inp.get("thesis_status") or "").strip().upper()
    broken = status in ("BROKEN", "FAIL", "EXIT") or bool(inp.get("governance_red_flag")) or bool(inp.get("hard_reject"))
    rev_bad = r1 is not None and r3 is not None and r1 < -5 and r3 < -5
    rev_good = (r1 is not None and r1 >= 0) or (r3 is not None and r3 >= 0)
    cyc = bool(x.get("cycle_peak_risk"))
    reasons = []
    if broken: sig = "EXIT"; reasons.append("Thesis 破壞/治理紅旗")
    elif rev_bad or (base is not None and base < 20 and (pp or 0) >= 95) or (score < 60 and held):
        # 「分數<60 → REDUCE」只對實際持股有意義；未持有的低分股歸 WATCH（否則全榜大半都是 REDUCE）
        sig = "REDUCE"
        if rev_bad: reasons.append("EPS 1M/3M 同步下修")
        if base is not None and base < 20 and (pp or 0) >= 95: reasons.append("Base<20% 且 PE 位階≥95%")
        if score < 60: reasons.append("分數<60")
    elif (score >= 75 and (base or 0) >= 50 and (rr or 0) >= 1.5 and rev_good and (pp is None or pp <= 80) and ev_fresh and not cyc
          and x.get("eps_confidence") != "proxy"):                 # ADD 不得建立在 proxy EPS 上（Base 報酬也是 proxy 推的）
        sig = "ADD"; reasons.append("分數/報酬/R/R/Revision/估值/證據全過")
    elif score >= 65 and (base or 0) >= 20 and (ev_fresh or not has_ledger):
        sig = "HOLD"
        if not has_ledger: reasons.append("無證據帳本")
        elif not ev_fresh: reasons.append("證據不足")
    else:
        sig = "WATCH"
        if score < 65: reasons.append("分數<65")
        if (base or 0) < 20: reasons.append("Base<20%")
        if has_ledger and not ev_fresh: reasons.append("證據不足/過期")
        if cyc: reasons.append("週期高峰")
    if score >= 75 and (base or 0) >= 50 and (rr or 0) >= 1.5 and not ev_fresh and sig not in ("EXIT", "REDUCE") and not cyc:
        reasons.append("量化達 ADD 門檻但缺新鮮證據→先補帳本")
    elif sig == "HOLD" and ev_fresh and x.get("eps_confidence") == "proxy" and score >= 75 and (base or 0) >= 50:
        reasons.append("量化達 ADD 門檻但 EPS 為 proxy→需法人/研究 FY EPS")
    note = ""
    if held:
        cost = held.get("avg_cost"); px = x.get("px")
        if cost and px:
            pnl = (px / cost - 1) * 100
            note = f"持{held.get('shares')}股@{cost:g}，{pnl:+.1f}%"
            line = held.get("stop_line") or round(cost * 0.93, 0)
            note += f"，防守線{line:g}" + ("⚠️已破" if px < line else "")
    return {"action_signal": sig, "signal_reason": "、".join(reasons), "held": bool(held), "holding_note": note,
            "thesis_status": status or None, "thesis_note": inp.get("thesis_note")}


def construct_portfolio(core, n=10, max_name=0.20, max_industry=0.35):
    """v4 Portfolio Engine：只用 S/A/B（CYCLE 不進），rank = 分數×0.6 + max(Base,0)×0.25 + R/R×1.5；單檔≤20%、單產業≤35%。
    這是「模型組合」輸出，不是下單指令（排行榜≠投資組合；買進仍受交易憲法約束）。"""
    rows = []
    for x in core:
        if x["tier"] not in ("S", "A", "B"): continue
        up = clip(x.get("base_upside") or 0, -100, 300); rr = clip(x.get("reward_risk") or 0, 0, 5)
        rows.append((x["score100"] * 0.6 + max(up, 0) * 0.25 + rr * 1.5, x))
    rows.sort(key=lambda r: -r[0])
    picks, ind_used, base_w = [], {}, 1.0 / max(n, 1)
    for rank_score, x in rows[:max(n * 3, n)]:
        if len(picks) >= n: break
        ind = x.get("industry") or "?"
        w = min(max_name, base_w)
        if ind_used.get(ind, 0) + w > max_industry: w = max(0.0, max_industry - ind_used.get(ind, 0))
        if w <= 0.001: continue
        picks.append({"ticker": x["ticker"], "name": x["name"], "industry": ind, "tier": x["tier"], "score100": x["score100"],
                      "base_upside": x.get("base_upside"), "reward_risk": x.get("reward_risk"),
                      "scenario_source": x.get("scenario_source"), "rank_score": round(rank_score, 1), "w": w})
        ind_used[ind] = ind_used.get(ind, 0) + w
    # 候選不足 n 檔時不放大權重（守住單檔 20% 上限），餘額視為現金
    for p in picks: p["weight"] = round(p.pop("w") * 100, 1)
    return picks


# ───────────────────────────── v3 研究模板（ChatGPT build_research_pack 相容）─────────────────────────────
QUAL_COLS = ["ticker", "research_date", "next_platform_score", "new_capacity_score", "new_product_mix_score",
             "order_visibility_score", "tam_score", "governance_score", "rerating_score", "customer_concentration_pct",
             "source_1", "source_2", "source_3", "thesis", "risk", "notes"]
SCEN_COLS = ["ticker", "bear_eps_3y", "bear_pe_3y", "base_eps_3y", "base_pe_3y", "bull_eps_3y", "bull_pe_3y", "scenario_notes"]
REF_COLS = ["ref_name", "ref_px", "ref_eps_ttm", "ref_eps_fy3_proxy", "ref_pe_now", "ref_pe_p25", "ref_pe_p50", "ref_pe_p75", "ref_score100", "ref_tier"]


def write_research_pack(cands, n):
    """為前 n 檔非週期候選產生 data/qualitative_research.csv + data/scenario_inputs.csv；既有列保留、只補新 ticker。
    尾端 ref_* 欄是給研究者（ChatGPT）的參考值，ChatGPT 版腳本會忽略多餘欄位。"""
    import csv as _csv
    os.makedirs("data", exist_ok=True)
    pick = [x for x in cands if x["tier"] in ("S", "A", "B", "WATCH")][:n]

    def upsert(path, cols):
        rows, seen = [], set()
        if os.path.exists(path):
            with io.open(path, encoding="utf-8-sig", newline="") as fh:
                for r in _csv.DictReader(fh):
                    rows.append(r); seen.add((r.get("ticker") or "").strip())
        added = 0
        for x in pick:
            if x["ticker"] in seen: continue
            r = {c: "" for c in cols + REF_COLS}; r["ticker"] = x["ticker"]
            r.update({"ref_name": x["name"], "ref_px": x["px"], "ref_eps_ttm": x["eps4"], "ref_eps_fy3_proxy": x["eps_fy3"],
                      "ref_pe_now": x["pe"], "ref_pe_p25": x["pe_p25"], "ref_pe_p50": x["pe_p50"], "ref_pe_p75": x["pe_p75"],
                      "ref_score100": x["score100"], "ref_tier": x["tier"]})
            rows.append(r); added += 1
        allcols = list(dict.fromkeys(cols + REF_COLS + [k for r in rows for k in r.keys()]))
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=allcols); w.writeheader()
            for r in rows: w.writerow({k: r.get(k, "") for k in allcols})
        print(f"  📝 {path}: 既有 {len(seen)} + 新增 {added} 檔")

    upsert(os.path.join("data", "qualitative_research.csv"), QUAL_COLS)
    upsert(os.path.join("data", "scenario_inputs.csv"), SCEN_COLS)
    ep = os.path.join("data", "evidence_ledger.csv")
    if not os.path.exists(ep):          # v4 證據帳本：只建表頭，一列一條論點（claim_type 見 EVIDENCE_TYPES）
        with io.open(ep, "w", encoding="utf-8-sig", newline="") as fh:
            _csv.DictWriter(fh, fieldnames=EVIDENCE_COLS).writeheader()
        print(f"  📝 {ep}: 建立空表頭")
    # v5：thesis / revision 更新檔（monthly_updates 由本系統自算，不建模板）
    tp = os.path.join("data", "thesis_updates.csv"); rp = os.path.join("data", "revision_updates.csv")
    rows_t, seen_t = [], set()
    if os.path.exists(tp):
        with io.open(tp, encoding="utf-8-sig", newline="") as fh:
            for r in _csv.DictReader(fh): rows_t.append(r); seen_t.add((r.get("ticker") or "").strip())
    for x in pick:
        if x["ticker"] not in seen_t: rows_t.append({"ticker": x["ticker"], "thesis_status": "OK", "governance_red_flag": "False", "thesis_note": "", "ref_name": x["name"]})
    with io.open(tp, "w", encoding="utf-8-sig", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=["ticker", "thesis_status", "governance_red_flag", "thesis_note", "ref_name"]); w.writeheader()
        for r in rows_t: w.writerow({k: r.get(k, "") for k in w.fieldnames})
    upsert(rp, ["ticker", "eps_rev_1m", "eps_rev_3m", "eps_rev_6m"])


# ───────────────────────────── 主流程 ─────────────────────────────
def main():
    global MAX_CALLS
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑指定代號（逗號分隔），測試用")
    ap.add_argument("--max-calls", type=int, default=550)
    ap.add_argument("--no-tech", action="store_true")
    ap.add_argument("--strict-eps", action="store_true", help="無法人 Forward EPS 就不猜（ChatGPT 版原則）")
    ap.add_argument("--portfolio-n", type=int, default=10, help="v4 模型組合檔數（預設 10）")
    ap.add_argument("--research-pack", type=int, default=0, metavar="N",
                    help="為前 N 檔非週期候選產生/補充 data/qualitative_research.csv 與 data/scenario_inputs.csv 研究模板（既有列不動）")
    args = ap.parse_args(); MAX_CALLS = args.max_calls
    global STRICT_EPS; STRICT_EPS = args.strict_eps

    mc = json.load(io.open("marketcap_cache.json", encoding="utf-8"))
    names = {s["code"]: s["name"] for s in json.load(io.open("tw_universe.json", encoding="utf-8"))["stocks"]}
    inputs = load_inputs()
    evidence = load_evidence()
    for t, ev in evidence.items():                    # 帳本的新鮮有效證據也算「有來源」
        inp = inputs.setdefault(t, {})
        inp["_evidence"] = ev
        if ev["fresh_evidence_count"] > 0:
            inp["evidence_ok"] = True
            inp["sources"] = list(dict.fromkeys((inp.get("sources") or []) + ev["sources"]))
    uni = sorted([c for c, v in mc.items() if isinstance(v, (int, float)) and v >= MIN_MCAP], key=lambda c: -mc[c])
    if args.only:
        uni = [c for c in args.only.split(",") if c]
    holdings = load_holdings()
    forced = [t for t in holdings if t not in uni]
    uni = uni + forced                                     # 實際持股強制納入（v5 追蹤）
    print(f"[{VERSION}] S0 市值≥{MIN_MCAP}億: {len(uni)} 檔（質化輸入 {len(inputs)} 檔；持股 {len(holdings)} 檔，其中 {len(forced)} 檔為強制納入）")

    ck1, ck2, ck3 = load_ckpt("s1"), load_ckpt("s2"), load_ckpt("s3")

    # S1
    for i, sid in enumerate(uni):
        if quota_dead: break
        stage1(sid, ck1)
        if i % 100 == 0 and i: print(f"  S1 {i}/{len(uni)}...")
    p1 = [c for c in uni if s1_pass(ck1["items"].get(c))]
    print(f"S1 月營收動能: 已掃 {sum(1 for c in uni if c in ck1['items'])}/{len(uni)} → 通過 {len(p1)} 檔")

    # S2（先跑 v1 曾通過 Quality 的、再跑其餘，讓核心名單先出來）
    v1s2 = set()
    if os.path.exists(V1_PROG):
        v1p = json.load(io.open(V1_PROG, encoding="utf-8"))
        v1s2 = {c for c, r in (v1p.get("s2") or {}).items() if r.get("ok") and (r.get("roe") or 0) >= 12 and (r.get("eps_g") or -1) > 0}
    order = [c for c in p1 if c in v1s2] + [c for c in p1 if c not in v1s2]
    for i, sid in enumerate(order):
        if quota_dead: break
        stage2(sid, ck2)
        if i % 25 == 0: print(f"  S2 {i}/{len(order)}... (calls {calls_used})")
    p2 = [c for c in p1 if s2_pass(ck2["items"].get(c))]
    # 持股：不論 S1/S2 是否通過都要進評分（追蹤用）；需補抓 S2
    for t in holdings:
        if t in p2: continue
        if t not in ck2["items"] and not quota_dead: stage2(t, ck2)
        if t in ck2["items"] and ck2["items"][t].get("ok"): p2.append(t)
    print(f"S2 Quality: 已掃 {sum(1 for c in p1 if c in ck2['items'])}/{len(p1)} → 通過 {len(p2)} 檔（含持股強制 {sum(1 for t in holdings if t in p2)} 檔）")

    # S3
    for sid in p2:
        if quota_dead: break
        stage3(sid, ck3)
    print(f"S3 估值: 完成 {sum(1 for c in p2 if c in ck3['items'])}/{len(p2)}（本輪 API {calls_used} 次）")

    # S4
    tech = {} if args.no_tech else stage4(p2)

    # ── 零 API 分析 ──
    cands = []
    for c in p2:
        m = {}
        for src in (ck1["items"].get(c), ck2["items"].get(c), ck3["items"].get(c), tech.get(c)):
            m.update(src or {})
        if not m.get("px") and m.get("pe") and m.get("eps4"):
            m["px"] = round(m["pe"] * m["eps4"], 1)                 # yfinance 缺價時用 PE×EPS 回推
        inp = inputs.get(c, {})
        ind = get_industry(c) or "?"
        cyc = cycle_check(c, ind, m, inp); m.update(cyc)
        fe = forward_eps(m, cyc, inp)
        m["forward_pe"] = round(m["px"] / fe["eps_fy1"], 1) if m.get("px") and fe["eps_fy1"] and fe["eps_fy1"] > 0 else None
        m["peg"] = round(m["forward_pe"] / fe["eps_cagr_3y"], 2) if m.get("forward_pe") and fe.get("eps_cagr_3y") and fe["eps_cagr_3y"] > 0 else None
        st = structural(m, inp); rr = rerating(m, inp); dp = double_pe(m, fe, cyc, rr, inp)
        sc = score100(m, st, fe, rr, inp); tier = tier_of(sc, fe, dp, cyc, inp, st)
        why, risk = [], []
        if (m.get("yoy3") or 0) >= 30: why.append(f"營收+{m['yoy3']:.0f}%")
        if (m.get("accel") or 0) >= 10: why.append(f"加速+{m['accel']:.0f}pp")
        if (m.get("roe") or 0) >= 20: why.append(f"ROE{m['roe']:.0f}%")
        if (m.get("fcf_margin") or 0) >= 10: why.append(f"FCF{m['fcf_margin']:.0f}%")
        if st["structural_score"] >= 12: why.append(f"結構{st['structural_score']:.0f}")
        if dp["double_verdict"].startswith("合理"): why.append("翻倍PE合理")
        if cyc["cycle_peak_risk"]: risk.append("週期高峰")
        if (m.get("pe_pct5y") or 0) >= 85: risk.append(f"PE位階{m['pe_pct5y']:.0f}%")
        if m.get("ocf_ni") is not None and m["ocf_ni"] < 0.5: risk.append("現金流背離")
        if abs(m.get("cap_chg") or 0) >= 10: risk.append(f"股本+{m['cap_chg']:.0f}%")
        if dp["double_verdict"].startswith("淘汰"): risk.append("翻倍需PE過高")
        if st["quali_filled"] == 0: risk.append("待質化")
        elif not st["evidence_ok"]: risk.append("質化無來源")
        if inp.get("risk"): risk.append(str(inp["risk"]))
        ev = inp.get("_evidence") or {}
        if ev.get("risk_claims"): risk.append(ev["risk_claims"][0][:30])
        if inp.get("why"): why.insert(0, str(inp["why"]))
        rf = refresh_info(tier, ev, horizon_missing=(fe["eps_confidence"] in ("verified-T+2", "partial")))
        rf["horizon_complete"] = fe["eps_confidence"] == "verified"; rf["gpt_action"] = inp.get("action_first_cut")
        if not (ck1["items"].get(c) or {}).get("ok") or not s1_pass(ck1["items"].get(c)): risk.append("S1未過(持股強制)") if c in holdings else None
        row = {"ticker": c, "name": names.get(c, "?"), "industry": ind, "mcap": round(mc.get(c) or 0, 0),
               "tier": tier, **sc,
               **{k: m.get(k) for k in ("px", "vs200", "vs_hi2y", "yoy3", "accel", "rev12m_g", "eps4", "eps_g", "q_eps_yoy",
                                        "roe", "roic", "gm_now", "gm_chg", "gm_5y_med", "opm_chg", "fcf_margin", "ocf_ni",
                                        "capex_g", "capex_rev", "nd_ebitda", "cap_chg", "pe", "pe_pct5y", "pe_p25", "pe_p50",
                                        "pe_p75", "forward_pe", "peg", "eps_annual", "eps_5y_med", "cyclical_sector", "cycle_peak_risk",
                                        "normalized_eps", "normalized_pe", "gm_jump")},
               **fe, **st, **rr, **dp, **rf,
               **{k: ev.get(k) for k in ("evidence_count", "fresh_evidence_count", "avg_confidence", "stale_ratio", "evidence_coverage")},
               "why": why, "risk": risk}
        row.update(action_signal(row, inp, holdings.get(c)))
        cands.append(row)

    rank = {"S": 0, "A": 1, "B": 2, "WATCH": 3, "CYCLE": 4, "REJECT": 5}
    cands.sort(key=lambda x: (rank[x["tier"]], -x["score100"]))
    # Top 10（v3）：S/A 優先，人工三情境者優先，再依 Base 報酬、分數
    top10 = sorted([x for x in cands if x["tier"] in ("S", "A", "B")],
                   key=lambda x: (rank[x["tier"]] if x["tier"] != "B" else 2, 0 if x.get("scenario_source") == "manual" else 1,
                                  -(x.get("base_upside") or -999), -x["score100"]))[:10]
    if args.research_pack:
        write_research_pack(cands, args.research_pack)
    portfolio = construct_portfolio(cands, n=args.portfolio_n)
    refresh_q = sorted([x for x in cands if x["tier"] in ("S", "A", "B")], key=lambda x: -x["refresh_priority"])
    sig_rank = {"EXIT": 0, "REDUCE": 1, "ADD": 2, "HOLD": 3, "WATCH": 4}
    action_q = sorted(cands, key=lambda x: (0 if x["held"] else 1, sig_rank[x["action_signal"]], -x["score100"]))
    core = [x for x in cands if x["tier"] in ("S", "A", "B", "WATCH")]
    cyc_pool = sorted([x for x in cands if x["tier"] == "CYCLE"], key=lambda x: (x["normalized_pe"] or 999))
    rej = [x for x in cands if x["tier"] == "REJECT"]
    today = dt.date.today().isoformat()
    json.dump({"version": VERSION, "date": today, "universe": len(uni), "s1_pass": len(p1), "s2_pass": len(p2),
               "s2_scanned": sum(1 for c in p1 if c in ck2["items"]), "quota_dead": quota_dead,
               "candidates": cands},
              io.open("double_screener_v2_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ── Markdown ──
    def f(v, suf="", nd=0):
        if v is None: return "–"
        return (f"{v:.{nd}f}" if isinstance(v, (int, float)) else str(v)) + suf
    L = [f"# 3年翻倍股初篩 {VERSION}（100 分制）｜{today}",
         f"漏斗：市值≥{MIN_MCAP}億 {len(uni)} → 月營收動能 {len(p1)} → Quality {len(p2)}"
         f"（S2 已掃 {sum(1 for c in p1 if c in ck2['items'])}/{len(p1)}{'，額度用盡、部分資料' if quota_dead else ''}）",
         "評分 = Quality25 + Growth20 + Structural20 + Revision15 + Valuation10 + Rerating10；"
         "Structural/Re-rating/法人 EPS 由 `double_inputs_v2.json` 提供，缺者以 proxy 估算並標「待質化」。",
         "分級：S ≥85（且 EPS CAGR≥25%、Double-PE 合理、Revision≥0、**Forward EPS 須為已驗證輸入**）/ A 75-84 / B 65-74 / WATCH <65；週期股獨立 Cycle Pool。"
         + ("　⚠️ 本次 --strict-eps：無法人 EPS 者 FY/CAGR/Double-PE 留空" if STRICT_EPS else "　（未填法人 EPS 者以 proxy 估算，判定標 proxy EPS）"), ""]
    hdr = ("| # | 代號 | 名稱 | 產業 | 級 | 分 | Q | G | St | Rv | V | Rr | 現價 | 營收YoY3m | 加速 | ROE | ROIC | FCF% | EPS_TTM | FY1 | FY3 | EPS來源 | CAGR | FwdPE | PEG | PE位階 | 翻倍需PE | 判定 | Bear | Base | Bull | 亮點 | 風險 |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    def line(i, x):
        return (f"| {i} | {x['ticker']} | {x['name']} | {(x['industry'] or '?')[:4]} | **{x['tier']}** | **{x['score100']:.0f}** | "
                f"{x['quality']:.0f} | {x['growth']:.0f} | {x['structural']:.0f} | {x['revision']:.0f} | {x['valuation']:.0f} | {x['rerating']:.0f} | "
                f"{f(x['px'])} | {f(x['yoy3'], '%')} | {f(x['accel'], 'pp')} | {f(x['roe'], '%')} | {f(x['roic'], '%')} | {f(x['fcf_margin'], '%')} | "
                f"{f(x['eps4'], '', 1)} | {f(x['eps_fy1'], '', 1)} | {f(x['eps_fy3'], '', 1)} | {x['eps_confidence']} | {f(x['eps_cagr_3y'], '%')} | "
                f"{f(x['forward_pe'], '', 1)} | {f(x['peg'], '', 2)} | {f(x['pe_pct5y'], '%')} | {f(x['required_pe_for_2x'], 'x')} | {x['double_verdict']} | "
                f"{f(x['bear_upside'], '%')} | {f(x['base_upside'], '%')} | {f(x['bull_upside'], '%')} | "
                f"{'、'.join(x['why'][:3])} | {'、'.join(x['risk'][:3])} |")

    top_start = len(L)
    L += ["## 🏆 Top 10（S/A 優先；有人工三情境者優先；再依 Base 報酬）", "",
          "| # | 代號 | 名稱 | 級 | 分 | 現價 | EPS來源 | FY3 | 翻倍需PE | 判定 | Bear | Base | Bull | R/R | 情境來源 | 質化來源 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(top10, 1):
        L.append(f"| {i} | {x['ticker']} | {x['name']} | **{x['tier']}** | {x['score100']:.0f} | {f(x['px'])} | {x['eps_confidence']} | {f(x['eps_fy3'], '', 1)} | "
                 f"{f(x['required_pe_for_2x'], 'x')} | {x['double_verdict']} | {f(x['bear_upside'], '%')} | {f(x['base_upside'], '%')} | {f(x['bull_upside'], '%')} | "
                 f"{f(x.get('reward_risk'), '', 2)} | {x.get('scenario_source') or '–'} | {'✅' if x['evidence_ok'] else '無'} |")
    L += ["", f"## 💼 模型組合（v4 Portfolio Engine，{len(portfolio)} 檔；單檔≤20%、單產業≤35%、CYCLE 不進）——排行榜≠投資組合，買進仍受交易憲法約束", "",
          "| # | 代號 | 名稱 | 產業 | 級 | 權重 | 分 | Base | R/R | 情境來源 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(portfolio, 1):
        L.append(f"| {i} | {p['ticker']} | {p['name']} | {p['industry'][:4]} | {p['tier']} | {p['weight']}% | {p['score100']:.0f} | "
                 f"{f(p['base_upside'], '%')} | {f(p['reward_risk'], '', 2)} | {p['scenario_source'] or '–'} |")
    cash = round(100 - sum(p["weight"] for p in portfolio), 1)
    if cash > 0: L.append(f"| – | – | 現金/未配置 | – | – | {cash}% | – | – | – | 候選不足或受上限約束 |")
    L += ["", "## 🔁 重查佇列（v4 Refresh Queue，前 15）——S/A 但無新鮮證據 / 覆蓋率<50% / 來源過期者優先", "",
          "| # | 代號 | 名稱 | 級 | 優先度 | 新鮮證據 | 覆蓋率 | 過期比 | 原因 |", "|---|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(refresh_q[:15], 1):
        L.append(f"| {i} | {x['ticker']} | {x['name']} | {x['tier']} | {x['refresh_priority']} | {x.get('fresh_evidence_count') or 0} | "
                 f"{f((x.get('evidence_coverage') or 0) * 100, '%')} | {f(x.get('stale_ratio'), '', 2)} | {x['refresh_reason'] or '–'} |")
    held_rows = [x for x in action_q if x["held"]]
    act_rows = [x for x in action_q if not x["held"] and x["action_signal"] in ("ADD", "REDUCE", "EXIT")][:15]
    L += ["", f"## 🧭 動作訊號（v5 Signal Engine）——持股 {len(held_rows)} 檔 ＋ 候選 ADD/REDUCE/EXIT 前 {len(act_rows)}；ADD 必須有新鮮證據帳本", "",
          "| 代號 | 名稱 | 持股 | 級 | 分 | 訊號 | GPT建議 | T+3共識 | Base | R/R | Rev1M | Rev3M | PE位階 | Thesis | 原因 | 持股備註 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    gpt_rows = [x for x in action_q if not x["held"] and x.get("gpt_action") and x not in act_rows]
    for x in held_rows + act_rows + gpt_rows:
        L.append(f"| {x['ticker']} | {x['name']} | {'✅' if x['held'] else ''} | {x['tier']} | {x['score100']:.0f} | **{x['action_signal']}** | {x.get('gpt_action') or '–'} | "
                 f"{'✅' if x.get('horizon_complete') else '缺'} | {f(x['base_upside'], '%')} | "
                 f"{f(x.get('reward_risk'), '', 2)} | {f(x['eps_rev_1m'], '%')} | {f(x['eps_rev_3m'], '%')} | {f(x['pe_pct5y'], '%')} | {x.get('thesis_status') or '–'} | "
                 f"{x['signal_reason'] or '–'} | {x['holding_note'] or ''} |")
    cnt = {}
    for x in cands: cnt[x["action_signal"]] = cnt.get(x["action_signal"], 0) + 1
    L += ["", "訊號分布：" + "、".join(f"{k} {cnt.get(k, 0)}" for k in ("ADD", "HOLD", "WATCH", "REDUCE", "EXIT")), ""]
    io.open("double_dashboard.md", "w", encoding="utf-8", newline="\n").write("\n".join([L[0]] + L[top_start:]))
    import csv as _csv3
    with io.open("double_action_queue.csv", "w", encoding="utf-8-sig", newline="") as fa:
        w = _csv3.writer(fa); w.writerow(["ticker", "name", "held", "tier", "score100", "action_signal", "signal_reason", "base_upside", "reward_risk",
                                          "eps_rev_1m", "eps_rev_3m", "pe_pct5y", "thesis_status", "holding_note"])
        for x in action_q: w.writerow([x["ticker"], x["name"], x["held"], x["tier"], x["score100"], x["action_signal"], x["signal_reason"], x["base_upside"],
                                       x.get("reward_risk"), x["eps_rev_1m"], x["eps_rev_3m"], x["pe_pct5y"], x.get("thesis_status"), x["holding_note"]])
    import csv as _csv2
    with io.open("double_refresh_queue.csv", "w", encoding="utf-8-sig", newline="") as fq:
        w = _csv2.writer(fq); w.writerow(["ticker", "name", "tier", "refresh_priority", "refresh_reason", "fresh_evidence_count", "evidence_coverage", "stale_ratio"])
        for x in refresh_q: w.writerow([x["ticker"], x["name"], x["tier"], x["refresh_priority"], x["refresh_reason"], x.get("fresh_evidence_count"), x.get("evidence_coverage"), x.get("stale_ratio")])
    with io.open("double_model_portfolio.csv", "w", encoding="utf-8-sig", newline="") as fp:
        w = _csv2.DictWriter(fp, fieldnames=list(portfolio[0].keys()) if portfolio else ["ticker"]); w.writeheader()
        for p in portfolio: w.writerow(p)
    for t, title in (("S", "🟢 S Tier（三年翻倍核心候選）"), ("A", "🟡 A Tier（高品質成長股）"), ("B", "⚪ B Tier（觀察池）")):
        grp = [x for x in core if x["tier"] == t]
        L += [f"## {title}：{len(grp)} 檔", ""]
        if grp: L += [hdr] + [line(i, x) for i, x in enumerate(grp, 1)] + [""]
    rest = [x for x in core if x["tier"] == "WATCH"][:25]
    L += [f"## ⏸️ WATCH（<65 分，暫不投入）前 {len(rest)} 檔——多數因質化欄位未填，補 inputs 後可能升級", ""]
    if rest: L += [hdr] + [line(i, x) for i, x in enumerate(rest, 1)] + [""]
    L += [f"## 🔄 Cycle Pool（週期股獨立管理，{len(cyc_pool)} 檔）——禁用 current PE，改看 Normalized PE", "",
          "| # | 代號 | 名稱 | 產業 | 分 | 現價 | 營收YoY3m | EPS成長 | EPS_TTM | 5y中位EPS | Normalized EPS | 現PE | Normalized PE | 毛利率跳升 | 高峰旗標 | 翻倍需PE | 判定 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(cyc_pool, 1):
        L.append(f"| {i} | {x['ticker']} | {x['name']} | {(x['industry'] or '?')[:4]} | {x['score100']:.0f} | {f(x['px'])} | {f(x['yoy3'], '%')} | "
                 f"{f(x['eps_g'], '%')} | {f(x['eps4'], '', 1)} | {f(x['eps_5y_med'], '', 1)} | {f(x['normalized_eps'], '', 1)} | {f(x['pe'], '', 1)} | "
                 f"**{f(x['normalized_pe'], 'x', 1)}** | {f(x['gm_jump'], 'pp')} | {'⚠️高峰' if x['cycle_peak_risk'] else '週期產業'} | "
                 f"{f(x['required_pe_for_2x'], 'x')} | {x['double_verdict']} |")
    if rej: L += ["", "## ⛔ Hard Reject：" + "、".join(f"{x['ticker']}{x['name']}" for x in rej)]
    need = [x for x in core if x["quali_filled"] == 0][:20]
    L += ["", f"## 📝 待質化名單（依分數，前 {len(need)} 檔）——請把下列代號帶去 ChatGPT 填 `double_inputs_v2.json`",
          "欄位：next_platform_12m(0-3) / new_capacity_24m(0-3) / new_product_revenue_pct / order_visibility_months / tam_growth_score(0-4) / "
          "business_model_change(0-4) / new_market_entry(0-3) / eps_fy1~fy3 + eps_source / eps_rev_1m,3m / analyst_up_count,down_count / governance_score(0-4)",
          "、".join(f"{x['ticker']}{x['name']}({x['score100']:.0f})" for x in need)]
    io.open("double_candidates_v2.md", "w", encoding="utf-8", newline="\n").write("\n".join(L))

    import csv as _csv
    with io.open("double_candidates_v2.csv", "w", encoding="utf-8-sig", newline="") as fcsv:
        w = _csv.DictWriter(fcsv, fieldnames=list(cands[0].keys()) if cands else ["ticker"])
        w.writeheader()
        for x in cands: w.writerow({k: ("、".join(v) if isinstance(v, list) else json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v) for k, v in x.items()})

    print(f"\n💾 double_candidates_v2.md / .csv / double_screener_v2_result.json / double_dashboard.md / double_refresh_queue.csv / double_model_portfolio.csv / double_action_queue.csv")
    print("S:", "、".join(f"{x['ticker']}{x['name']}({x['score100']:.0f})" for x in core if x['tier'] == 'S') or "無")
    print("A:", "、".join(f"{x['ticker']}{x['name']}({x['score100']:.0f})" for x in core if x['tier'] == 'A') or "無")
    print("B:", "、".join(f"{x['ticker']}{x['name']}({x['score100']:.0f})" for x in core if x['tier'] == 'B')[:300] or "無")
    print("Cycle Pool:", "、".join(f"{x['ticker']}{x['name']}(nPE{x['normalized_pe']})" for x in cyc_pool)[:300] or "無")
    if quota_dead:
        print("⚠️ 本輪額度用盡，結果為部分資料——再跑一次 python double_screener_v2.py 會自動續抓")


if __name__ == "__main__":
    main()
