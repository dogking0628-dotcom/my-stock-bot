# -*- coding: utf-8 -*-
"""
yfinance 全市場掃 2 年還原月線 ATH，按族群統計
+ 對 ATH 股獨立算動能確認分數，標記隔日續漲 ≥85% 的 Top 5
"""
import sys, io, os, json, datetime as dt, time
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception:
        pass

import numpy as np
import yfinance as yf
from collections import defaultdict
from industry_map_loader import get_industry

NEAR_THRESHOLD = 0.95
EXACT_THRESHOLD = 0.999
BATCH = 50

# 科技限定族群（V3 回測：CAGR +23.6%，PF 3.94）
ALLOWED_INDUSTRIES = {
    "半導體", "電子零組件", "光電", "電腦及週邊",
    "電子通路", "通信網路", "其他電子",
}
MIN_MCAP_BILLIONS = 100  # 市值 ≥ 100 億 NT$ — 砍小型股雜訊

# 🆕 V4.1: 重複虧損股 blacklist 視窗（天）
# 回測證實：V5 多樣化反而放大回撤，過熱門檻 5 年內 0 觸發
# 只保留有效的 7 日黑名單（避免 3443 連續虧損案例）
RECENT_LOSER_WINDOW = 7
SCORE_THRESHOLD = 80  # 動能門檻（V4 原始邏輯，不再依過熱調整）

# 美股族群連動加分（昨日漲，TW 對應族群隔日加分）
US_SECTOR_BOOST = {
    "QQQ": {"industries": list(ALLOWED_INDUSTRIES), "threshold": 0.0, "bonus": 5},
    "SMH": {"industries": ["半導體", "其他電子"], "threshold": 0.5, "bonus": 10},
    "IGV": {"industries": ["通信網路", "電腦及週邊"], "threshold": 0.5, "bonus": 10},
}

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "ath_industry_report.json")
TXT_PATH = os.path.join(os.path.dirname(__file__), "scan_output.txt")
MCAP_CACHE = os.path.join(os.path.dirname(__file__), "marketcap_cache.json")

# ── 資料新鮮度守門（2026-08-18 起）─────────────────────────────
# 稽核 8/2~8/17 共 12 次凌晨 cron：週二~五的 run（UTC 21:xx）yfinance 台股日 K
# 有 7/9 次缺最新一根（落後 1 交易日）→ 開盤掛單其實用前天收盤算。
# 修法：以證交所 MI_INDEX（官方、收盤後 ~15:00 即有）確認最新交易日，
#      yfinance 缺那根就把官方 OHLCV 補上去；證交所抓不到則維持原樣並標示落後。
TWSE_UA = {"User-Agent": "Mozilla/5.0"}
_TWSE_DAY_CACHE = {}


def tw_today():
    """台灣日期（Actions 跑在 UTC，凌晨 05:00 台北 = 前一日 UTC）"""
    return (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()


def trade_date():
    """這份掛單要打的台股交易日：14:00 前 = 今天；收盤後跑 = 下一個平日"""
    now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    d = now.date()
    if now.hour >= 14:
        d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def fetch_twse_day(d):
    """證交所 MI_INDEX 全市場當日 OHLCV → {code: dict(Open,High,Low,Close,Volume)}；非交易日/未出 → None"""
    import urllib.request
    ds = d.strftime("%Y%m%d")
    if ds in _TWSE_DAY_CACHE:
        return _TWSE_DAY_CACHE[ds]
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
           f"?date={ds}&type=ALLBUT0999&response=json")
    out = None
    try:
        j = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=TWSE_UA), timeout=30).read())
        if j.get("stat") == "OK":
            tables = j.get("tables") or [j]
            for t in tables:
                f = t.get("fields") or []
                if "證券代號" in f and "收盤價" in f:
                    ix = {k: f.index(k) for k in ("證券代號", "成交股數", "開盤價", "最高價", "最低價", "收盤價")}
                    out = {}
                    for row in t.get("data", []):
                        try:
                            c = row[ix["證券代號"]].strip()
                            vals = [float(row[ix[k]].replace(",", "")) for k in ("開盤價", "最高價", "最低價", "收盤價")]
                            vol = float(row[ix["成交股數"]].replace(",", ""))
                        except Exception:
                            continue   # "--" 無成交
                        out[c] = {"Open": vals[0], "High": vals[1], "Low": vals[2], "Close": vals[3], "Volume": vol}
                    break
    except Exception as e:
        print(f"  [TWSE] MI_INDEX {ds} 抓取失敗: {type(e).__name__}", file=sys.stderr)
    _TWSE_DAY_CACHE[ds] = out
    return out


def latest_trading_day():
    """從台灣今日往回找最近一個證交所有資料的交易日（最多 7 天）；找不到回 (None, None)"""
    d = tw_today()
    for _ in range(7):
        if d.weekday() < 5:
            day = fetch_twse_day(d)
            if day:
                return d, day
        d -= dt.timedelta(days=1)
    return None, None


def patch_last_bar(df_t, code, ref_date, ref_day):
    """yfinance 單檔 df 最後一根若早於 ref_date，補上證交所那根；回傳 (df, patched:bool)"""
    if ref_date is None or df_t is None or len(df_t) == 0:
        return df_t, False
    try:
        last = df_t["Close"].dropna().index[-1]
        last_d = last.date() if hasattr(last, "date") else last
    except Exception:
        return df_t, False
    if last_d >= ref_date:
        return df_t, False
    rec = ref_day.get(code)
    if not rec:
        return df_t, False
    # 除權息防呆（2026-09-04）：TWSE 是「未還原」價，yfinance auto_adjust 是還原價。
    # 除權息日兩者會斷層（例：緯穎 25/8 配股 7000→2000）。台股漲跌停 ±10%，
    # 補棒與前一根落差 >15% 只可能是除權息或資料異常 → 寧可不補、標記落後。
    try:
        prev_close = float(df_t["Close"].dropna().iloc[-1])
        if prev_close > 0 and abs(rec["Close"] / prev_close - 1) > 0.15:
            return df_t, False
    except Exception:
        pass
    import pandas as pd
    row = {c: np.nan for c in df_t.columns}
    for k, v in rec.items():
        if k in row: row[k] = v
    new = pd.DataFrame([row], index=[pd.Timestamp(ref_date)])
    new.index.name = df_t.index.name
    return pd.concat([df_t, new]), True



def load_mcap():
    """讀取市值快取（億 NT$）"""
    if not os.path.exists(MCAP_CACHE):
        return {}
    try:
        with io.open(MCAP_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_recent_losers():
    """V5: 從 top5_history + 即時報價找出 7 日內進場且虧損的股票"""
    history_path = os.path.join(os.path.dirname(__file__), "top5_history.json")
    if not os.path.exists(history_path):
        return set()
    try:
        with open(history_path, encoding="utf-8") as f:
            h = json.load(f)
    except Exception:
        return set()
    cutoff = dt.date.today() - dt.timedelta(days=RECENT_LOSER_WINDOW)
    recent_picks = []
    for r in h.get("records", []):
        try:
            d = dt.date.fromisoformat(r["date"])
            if d < cutoff: continue
            for p in r.get("picks", []):
                recent_picks.append((p["ticker"], p.get("rec_close", 0)))
        except Exception: continue
    if not recent_picks: return set()
    # 抓現價判斷虧損
    tickers = list(set(t for t, _ in recent_picks))
    losers = set()
    try:
        codes_str = " ".join(f"{t}.TW" for t in tickers)
        df = yf.download(codes_str, period="3d", auto_adjust=True,
                         progress=False, threads=True, group_by="ticker")
        latest = {}
        for t in tickers:
            try:
                sub = df[f"{t}.TW"] if len(tickers) > 1 else df
                cl = sub["Close"].dropna()
                if len(cl) > 0: latest[t] = float(cl.iloc[-1])
            except Exception: continue
        for t, rec_close in recent_picks:
            cur = latest.get(t)
            if cur is None or rec_close <= 0: continue
            ret = (cur / rec_close - 1) * 100
            if ret < 0: losers.add(t)
    except Exception as e:
        print(f"  [losers] fetch fail: {e}", file=sys.stderr)
    if losers:
        print(f"  🚫 V5 排除 {len(losers)} 檔 7 日內虧損股: {sorted(losers)[:10]}",
              file=sys.stderr)
    return losers


def get_market_regime():
    """V4：檢查 0050 是否在 MA200 之上（Stage 2 牛市）"""
    try:
        df = yf.download("0050.TW", period="1y", auto_adjust=True,
                         progress=False, threads=False, group_by="column")
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        if "Close" not in df.columns: return True, None
        try:
            _rd, _rday = latest_trading_day()
            df, _p = patch_last_bar(df, "0050", _rd, _rday)
            if _p: print(f"  [0050] yfinance 落後，補證交所 {_rd} 收盤", file=sys.stderr)
        except Exception:
            pass
        cl = df["Close"].dropna().values
        if len(cl) < 200: return True, None
        today = float(cl[-1])
        ma200 = float(cl[-200:].mean())
        ma20 = float(cl[-20:].mean())
        in_stage2 = today > ma200
        below_ma20 = today < ma20      # V4.4 減速器：大盤短線轉弱 → 暫停新倉
        ext_pct = (today / ma200 - 1) * 100
        print(f"  [0050] 今價 ${today:.1f} / MA200 ${ma200:.1f} / MA20 ${ma20:.1f} "
              f"→ {'🟢 Stage 2' if in_stage2 else '🔴 Stage 4（禁止進場）'}"
              f"{'｜⏸️ 破20MA 暫停新倉(V4.4)' if in_stage2 and below_ma20 else ''}"
              f" 偏離 {ext_pct:+.1f}%", file=sys.stderr)
        return in_stage2, {"today": today, "ma200": ma200, "ma20": ma20,
                           "ext_pct": ext_pct, "in_stage2": in_stage2,
                           "below_ma20": below_ma20}
    except Exception as e:
        print(f"  [0050] regime fail: {e}", file=sys.stderr)
        return True, None  # 抓不到資料時預設可進場


def get_us_sector_change():
    """抓 QQQ/SMH/IGV 昨日漲跌（用於美股連動加分）"""
    out = {}
    for tk in US_SECTOR_BOOST.keys():
        try:
            df = yf.download(tk, period="5d", auto_adjust=True,
                             progress=False, threads=False, group_by="column")
            if df.empty: continue
            # yfinance 1.3+ 返回 MultiIndex，先壓平
            if hasattr(df.columns, "levels"):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            if "Close" not in df.columns: continue
            cl = df["Close"].dropna()
            if hasattr(cl, "iloc"): cl = cl.values  # 轉純 array 避免 Series 型別
            if len(cl) < 2: continue
            chg = float((cl[-1] / cl[-2] - 1) * 100)
            out[tk] = chg
            print(f"  [{tk}] 昨日 {chg:+.2f}%", file=sys.stderr)
        except Exception as e:
            print(f"  [{tk}] fail: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def us_bonus_for(industry, us_chg):
    """計算某產業的美股加分"""
    if not us_chg: return 0, []
    bonus = 0; notes = []
    for etf, cfg in US_SECTOR_BOOST.items():
        chg = us_chg.get(etf)
        if chg is None: continue
        if industry in cfg["industries"] and chg >= cfg["threshold"]:
            bonus += cfg["bonus"]
            notes.append(f"{etf}+{chg:.1f}%")
    return bonus, notes


def load_universe():
    with io.open("tw_universe.json", encoding="utf-8") as f:
        u = json.load(f)
    return [(s["code"], s["name"]) for s in u["stocks"]]


def rsi_14(closes):
    if len(closes) < 15: return 50.0
    delta = np.diff(closes[-15:])
    gains = np.where(delta > 0, delta, 0).mean()
    losses = np.where(delta < 0, -delta, 0).mean()
    if losses == 0: return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def monthly_max_close(closes_series):
    if len(closes_series) < 30:
        return None, None
    today_close = float(closes_series.iloc[-1])
    today_ym = dt.date.today().strftime("%Y-%m")
    by_month = {}
    for ts, c in closes_series.items():
        ym = ts.strftime("%Y-%m")
        by_month[ym] = float(c)
    historical = [v for ym, v in by_month.items() if ym < today_ym]
    if not historical:
        return today_close, None
    return today_close, max(historical)


def momentum_confirm_score(rec):
    """簡化版：只看 Tier 1（漲停/量爆/跳空）+ 創 ATH + 族群同步"""
    s = 0
    notes = []

    # Tier 1（核心，3 選 1，+50 分）
    is_locked = rec.get("change_pct", 0) >= 9.5 and rec.get("vol_ratio", 0) < 1.2
    vol_surge = rec.get("vol_ratio", 0) >= 3 and rec.get("change_pct", 0) >= 5
    gap_up = rec.get("gap_up", False)
    if is_locked:
        s += 50; notes.append("漲停鎖死")
    elif vol_surge:
        s += 50; notes.append("量爆價揚")
    elif gap_up:
        s += 45; notes.append("跳空缺口")

    # 創 ATH（+30）
    if rec.get("ratio", 0) >= 0.999:
        s += 30; notes.append("ATH")

    # 族群同步（+20）
    if rec.get("industry_strong"):
        s += 20; notes.append("族群同步")

    return min(s, 100), notes


def analyze_stock(yfc, df_t):
    """從單檔 OHLCV df 算所有動能指標"""
    cl = df_t["Close"].dropna()
    op = df_t["Open"].dropna()
    hi = df_t["High"].dropna()
    lo = df_t["Low"].dropna()
    vo = df_t["Volume"].dropna()
    if len(cl) < 100:
        return None

    today_close, mmax = monthly_max_close(cl)
    if today_close is None or mmax is None or mmax <= 0:
        return None
    ratio = today_close / mmax

    # 完整指標（只算到 2y ATH 候選的更多細節）
    closes_arr = cl.values.astype(float)
    today_open = float(op.iloc[-1]) if len(op) else today_close
    today_high = float(hi.iloc[-1]) if len(hi) else today_close
    today_low = float(lo.iloc[-1]) if len(lo) else today_close
    yesterday_close = float(cl.iloc[-2]) if len(cl) >= 2 else today_close
    yesterday_high = float(hi.iloc[-2]) if len(hi) >= 2 else today_high
    today_vol = float(vo.iloc[-1]) if len(vo) else 0
    avg20_vol = float(vo.iloc[-20:].mean()) if len(vo) >= 20 else max(today_vol, 1)
    change_pct = (today_close / yesterday_close - 1) * 100 if yesterday_close > 0 else 0
    vol_ratio = today_vol / avg20_vol if avg20_vol > 0 else 0
    rsi_val = rsi_14(closes_arr)
    ma5 = float(cl.iloc[-5:].mean()) if len(cl) >= 5 else today_close
    ma10 = float(cl.iloc[-10:].mean()) if len(cl) >= 10 else today_close
    ma20 = float(cl.iloc[-20:].mean()) if len(cl) >= 20 else today_close
    ma60 = float(cl.iloc[-60:].mean()) if len(cl) >= 60 else today_close
    ma200 = float(cl.iloc[-200:].mean()) if len(cl) >= 200 else today_close

    bullish = today_close > ma20 > ma60 > ma200
    bullish_fast = today_close > ma5 > ma10 > ma20
    gap_up = today_open > yesterday_high * 1.005 and today_close > today_open
    candle_range = today_high - today_low
    close_near_high = candle_range > 0 and today_close >= (today_high - candle_range * 0.2)
    long_red = candle_range > 0 and (today_close - today_open) / candle_range >= 0.7

    # 🌀 T2 糾結突破（2026-09-04 評估採納為資訊層測試軌道，非 V4.3 掛單）
    # 昨日 MA5/10/20 帶寬<3% + 昨漲<3%(首根) + 今漲>=3% 量比>=2 + 收盤破糾結帶 + 距2y高<=15%
    tangle = None
    if len(cl) >= 23:
        m5y = float(cl.iloc[-6:-1].mean()); m10y = float(cl.iloc[-11:-1].mean()); m20y = float(cl.iloc[-21:-1].mean())
        prev_c = float(cl.iloc[-2]); prev2_c = float(cl.iloc[-3])
        hi2y_prev = float(cl.iloc[:-1].max())
        if prev_c > 0 and prev2_c > 0 and hi2y_prev > 0:
            band = (max(m5y, m10y, m20y) - min(m5y, m10y, m20y)) / prev_c
            prev_chg = (prev_c / prev2_c - 1) * 100
            if (band < 0.03 and prev_chg < 3.0 and change_pct >= 3.0 and vol_ratio >= 2.0
                    and today_close > max(m5y, m10y, m20y) and today_close >= hi2y_prev * 0.85):
                tangle = {"band_pct": round(band * 100, 2),
                          "dist_high_pct": round((today_close / hi2y_prev - 1) * 100, 1)}

    return {
        "today": today_close, "monthly_max_2y": mmax,
        "ratio": ratio, "from_high_pct": (ratio - 1) * 100,
        "bullish": bool(bullish), "bullish_fast": bool(bullish_fast),
        "change_pct": change_pct, "vol_ratio": vol_ratio,
        "rsi": rsi_val, "ma5": ma5, "ma20": ma20, "ma60": ma60, "ma200": ma200,
        "gap_up": gap_up, "close_near_high": close_near_high, "long_red": long_red,
        "tangle": tangle,
    }


def main():
    universe = load_universe()
    print(f"[1/3] universe: {len(universe)} 檔")
    ref_date, ref_day = latest_trading_day()
    print(f"  [新鮮度] 證交所最新交易日 {ref_date}（{len(ref_day) if ref_day else 0} 檔）", file=sys.stderr)
    n_patched = 0
    data_dates = defaultdict(int)

    results = []
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i+BATCH]
        codes = [f"{c}.TW" for c, _ in batch]
        try:
            df = yf.download(" ".join(codes), period="2y",
                             auto_adjust=True, progress=False, threads=True,
                             group_by="ticker")
        except Exception as e:
            print(f"  batch {i} download fail: {e}")
            time.sleep(2)
            continue

        for code, name in batch:
            yfc = f"{code}.TW"
            try:
                if yfc not in df.columns.get_level_values(0):
                    continue
                df_t, patched = patch_last_bar(df[yfc], code, ref_date, ref_day)
                if patched: n_patched += 1
                try:
                    data_dates[str(df_t["Close"].dropna().index[-1].date())] += 1
                except Exception:
                    pass
                metrics = analyze_stock(yfc, df_t)
                if not metrics:
                    continue
                metrics["ticker"] = code; metrics["name"] = name
                metrics["industry"] = get_industry(code)
                results.append(metrics)
            except Exception:
                continue

        if (i // BATCH) % 5 == 0:
            print(f"  [{i+BATCH}/{len(universe)}] 已分析 {len(results)} 檔")
        time.sleep(1)

    print(f"\n[2/3] 完成，共 {len(results)} 檔有效")
    data_date = max(data_dates) if data_dates else None
    if n_patched:
        print(f"  ⚠️ yfinance 落後 → 以證交所 {ref_date} 補 {n_patched} 檔最新 K 棒", file=sys.stderr)
    if ref_date and data_date and str(ref_date) != data_date:
        print(f"  ⚠️ 資料日 {data_date} ≠ 最新交易日 {ref_date}（證交所補不到，訊號可能落後）", file=sys.stderr)

    # ATH 候選
    exact = sorted([r for r in results if r["ratio"] >= EXACT_THRESHOLD],
                   key=lambda x: -x["ratio"])
    near = sorted([r for r in results if r["ratio"] >= NEAR_THRESHOLD],
                  key=lambda x: -x["ratio"])

    # 族群統計（先算讓 industry_strong 可判定）
    by_ind = defaultdict(list)
    for r in results:
        by_ind[r.get("industry") or "未分類"].append(r)
    industry_up_ratio = {}
    for ind, lst in by_ind.items():
        if not lst: continue
        n_up = sum(1 for x in lst if x.get("change_pct", 0) > 0)
        industry_up_ratio[ind] = n_up / len(lst)

    # V4.1: V4 + 7 日內虧損股黑名單（族群多樣化、過熱門檻已棄用）
    mcap = load_mcap()
    print(f"\n[3/3] 載入市值 {len(mcap)} 檔，檢查 0050 體制 + 美股連動 + V4.1 黑名單...",
          file=sys.stderr)
    in_stage2, regime_info = get_market_regime()
    us_chg = get_us_sector_change()
    recent_losers = get_recent_losers()  # 🆕 V4.1: 7 日內虧損股黑名單
    score_threshold = SCORE_THRESHOLD  # 固定門檻 80（過熱動態提升棄用，5y 從未觸發）

    # 🆕 V4.2: 投信買超加分（讀 institutional_tracker.py 產的 signal）
    sitc_net = {}
    try:
        inst_path = os.path.join(os.path.dirname(__file__), "institutional_signal.json")
        if os.path.exists(inst_path):
            with open(inst_path, encoding="utf-8") as f:
                sitc_net = json.load(f).get("sitc_net", {})
            print(f"  [V4.2] 投信買賣超 {len(sitc_net)} 檔載入", file=sys.stderr)
    except Exception as e:
        print(f"  [V4.2] institutional_signal 載入失敗: {e}", file=sys.stderr)

    # 對 ATH 股算動能確認分數（含 V3 美股加分 + 市值標記）
    for r in exact:
        ind = r.get("industry") or "未分類"
        r["industry_strong"] = industry_up_ratio.get(ind, 0) >= 0.6
        # 標記市值
        mc = mcap.get(r["ticker"])
        r["market_cap_billions"] = mc
        r["mcap_pass"] = (mc is not None and mc >= MIN_MCAP_BILLIONS)
        # 動能基礎分數
        score, notes = momentum_confirm_score(r)
        # V3: 美股族群連動加分（只對科技族群有效）
        if ind in ALLOWED_INDUSTRIES:
            us_b, us_notes = us_bonus_for(ind, us_chg)
            score = min(score + us_b, 100)
            notes = notes + us_notes
        # V4.2: 投信買超 → +10 分
        sitc = sitc_net.get(r["ticker"], 0)
        r["sitc_net_shares"] = sitc
        if sitc > 0:
            score = min(score + 10, 100)
            notes = notes + [f"投信+{sitc/1000:,.0f}張"]
        r["momentum_score"] = score
        r["momentum_notes"] = notes
        if score >= 80:
            r["tier"] = "⭐⭐⭐"; r["next_day_prob"] = "≥85%"
        elif score >= 60:
            r["tier"] = "⭐⭐"; r["next_day_prob"] = "70-85%"
        else:
            r["tier"] = "⭐"; r["next_day_prob"] = "<70%"

    # 隔日高機率（全市場，所有 ≥80）
    high_prob = sorted([r for r in exact if r.get("momentum_score", 0) >= 80],
                       key=lambda x: -x["momentum_score"])

    # 🆕 V3 找最強族群：科技限定 + 市值 ≥ 100 億
    by_ind_for_pick = defaultdict(list)
    for r in exact:
        ind = r.get("industry") or "未分類"
        if ind in ALLOWED_INDUSTRIES and r.get("mcap_pass"):  # 🔒 科技+市值
            by_ind_for_pick[ind].append(r)
    strongest_industry = None
    for ind, lst in sorted(by_ind_for_pick.items(), key=lambda x: -len(x[1])):
        bull_ratio = sum(1 for x in lst if x.get("bullish")) / max(len(lst), 1)
        if len(lst) >= 3 and bull_ratio >= 0.5:  # V3: 市值濾後候選變少，3 檔即可
            strongest_industry = ind
            break

    # 🚨 V4 大盤體制濾網：0050 < MA200（Stage 4 熊市）禁止進場
    v44_paused = bool(in_stage2 and regime_info and regime_info.get("below_ma20"))
    if not in_stage2:
        print("  ⛔ V4: 0050 跌破 MA200 → 禁止進場（熊市段）", file=sys.stderr)
        tomorrow_top5 = []
    elif v44_paused:
        # V4.4（2026-09-04 上線）：Stage 2 但 0050 < 自身 20MA → 暫停新倉（持倉出場照舊）
        # 5y 回測：+247%/CAGR 27.7%/期望+10.5%/PF 3.51/MDD -19.8% vs V4.3 +186%/-28.3%
        print("  ⏸️ V4.4: 0050 跌破自身 20MA → 大盤短弱，今日不開新倉", file=sys.stderr)
        tomorrow_top5 = []
    elif strongest_industry:
        # V4.1 過濾條件：黑名單 + 動能門檻
        def _pass(r):
            if r["ticker"] in recent_losers: return False
            if r.get("momentum_score", 0) < score_threshold: return False
            return True

        # 從最強族群挑前 5 檔（V4 原版邏輯，不再多樣化）
        in_industry = sorted(
            [r for r in by_ind_for_pick[strongest_industry] if _pass(r)],
            key=lambda x: (-x.get("momentum_score", 0),
                           -x.get("change_pct", 0),
                           -x.get("vol_ratio", 0)))
        tomorrow_top5 = in_industry[:5]

        # 若不足 3 檔，從其他科技族群補
        if len(tomorrow_top5) < 3:
            existing = {x["ticker"] for x in tomorrow_top5}
            extra_pool = [r for r in exact
                          if r.get("industry") in ALLOWED_INDUSTRIES
                          and r.get("mcap_pass") and _pass(r)
                          and r["ticker"] not in existing]
            extra_pool.sort(key=lambda x: -x.get("momentum_score", 0))
            tomorrow_top5 += extra_pool[: 5-len(tomorrow_top5)]
    else:
        # fallback：科技族群 + 市值合格 + V5 過濾
        tech_pool = [r for r in exact
                     if r.get("industry") in ALLOWED_INDUSTRIES
                     and r.get("mcap_pass")
                     and r["ticker"] not in recent_losers
                     and r.get("momentum_score", 0) >= score_threshold]
        tomorrow_top5 = sorted(tech_pool,
                               key=lambda x: -x.get("momentum_score", 0))[:5]

    lines = []
    def p(s=""):
        print(s); lines.append(s)

    p("\n" + "=" * 60)
    p(f"🔥 ATH 真正創 2y 月線新高：{len(exact)} 檔")
    p("=" * 60)
    by_ind_exact = defaultdict(list)
    for r in exact:
        by_ind_exact[r.get("industry") or "未分類"].append(r)
    for ind, items in sorted(by_ind_exact.items(), key=lambda x: -len(x[1])):
        bn = sum(1 for x in items if x["bullish"])
        p(f"  {ind}: {len(items)} 檔（多頭排列 {bn}）")

    p("\n" + "=" * 60)
    p(f"⭐⭐⭐ 明日高機率續漲 Top 5（動能 ≥ 80）")
    p("=" * 60)
    if not tomorrow_top5:
        p("  （無高機率股）")
    else:
        for i, r in enumerate(tomorrow_top5, 1):
            ind = r.get("industry") or "未分類"
            notes = "、".join(r.get("momentum_notes", []))
            p(f"  #{i} {r['ticker']} {r['name']:<8} {ind:<8} {r['tier']} 分數 {r['momentum_score']}/100 ({r['next_day_prob']})")
            p(f"     ${r['today']:.1f} {r['change_pct']:+.1f}% 量{r['vol_ratio']:.1f}x RSI{r['rsi']:.0f} | {notes}")

    p("\n" + "=" * 60)
    p(f"🟡 接近 2y 月線新高（>=95%）：{len(near)} 檔")
    p("=" * 60)
    by_ind_near = defaultdict(list)
    for r in near:
        by_ind_near[r.get("industry") or "未分類"].append(r)
    ranked = sorted(by_ind_near.items(), key=lambda x: -len(x[1]))
    p("\n📊 族群統計（接近 2y 月線高 5% 內）：")
    for ind, items in ranked[:20]:
        bn = sum(1 for x in items if x["bullish"])
        p(f"  {ind}: {len(items)} 檔（多頭 {bn}）")
    if ranked:
        p(f"\n🏆 族群最多：{ranked[0][0]}（{len(ranked[0][1])} 檔）")

    tangle_list = sorted(
        [r for r in results if r.get("tangle")
         and r.get("industry") in ALLOWED_INDUSTRIES
         and (mcap.get(r["ticker"]) or 0) >= MIN_MCAP_BILLIONS],
        key=lambda x: -x.get("vol_ratio", 0))[:8]
    if tangle_list:
        print(f"  🌀 糾結突破(T2測試軌道): " +
              "、".join(f"{r['ticker']}{r['name']}(量{r['vol_ratio']:.1f}x)" for r in tangle_list[:5]),
              file=sys.stderr)

    out = {
        "timestamp": dt.date.today().isoformat(),
        "tangle_breakout": [{k: r.get(k) for k in
                             ("ticker", "name", "industry", "today", "change_pct",
                              "vol_ratio", "ma20", "tangle")} for r in tangle_list],
        "trade_date": str(trade_date()),              # 這份掛單要打的台股日期
        "data_date": data_date,                       # 訊號所依據的最後一根 K 棒
        "ref_trading_date": str(ref_date) if ref_date else None,
        "patched_bars": n_patched,
        "basis": "yfinance 2y monthly + 動能確認分數（最強族群挑 5）；最新K棒以證交所 MI_INDEX 校驗",
        "total_analyzed": len(results),
        "exact_ath": exact,
        "near_ath_top30": near[:30],
        "tomorrow_top5": tomorrow_top5,
        "tomorrow_top5_industry": strongest_industry,  # 🆕 最強族群名稱
        "high_prob_count": len(high_prob),
        "industry_stats": [{"industry": ind, "count": len(items),
             "bullish_count": sum(1 for x in items if x["bullish"])}
            for ind, items in ranked],
        "top_industry": ranked[0][0] if ranked else None,
        "market_regime": regime_info,  # 🆕 V4: 0050 體制資料
        "v4_blocked": (not in_stage2),  # 🆕 V4: 是否禁止進場
        "v44_paused": v44_paused,       # 🆕 V4.4: Stage2 但 0050<20MA → 暫停新倉
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(TXT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n💾 輸出 {OUTPUT_PATH} / {TXT_PATH}")


if __name__ == "__main__":
    main()
