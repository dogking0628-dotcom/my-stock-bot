#!/usr/bin/env python3
"""
動態股票池載入器
─────────────────────────────────────────
- 美股：從 Wikipedia 抓 S&P 500 約 503 檔
- 台股：從 TWSE/TPEX 官網抓所有上市+上櫃約 2000+ 檔
- 結果快取到 JSON，每週更新一次
"""
import os, json, datetime as dt, ssl
import urllib.request
import re

# 忽略 SSL 驗證（TWSE 證書問題）
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

CACHE_DIR = os.path.dirname(__file__)
SP500_CACHE = os.path.join(CACHE_DIR, "sp500_universe.json")
TW_CACHE    = os.path.join(CACHE_DIR, "tw_universe.json")
CACHE_DAYS  = 7  # 7 天更新一次

def _is_cache_fresh(path):
    if not os.path.exists(path): return False
    age_days = (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(path))).days
    return age_days < CACHE_DAYS

# 已知會跑出但不存在/已下市的 ticker 黑名單
SP500_BLACKLIST = {"TSYS","CSRA","GGP","MBIA","IQVIA","LVMH","SCANA","TMK","XLNX","MXIM","ATVI"}

def fetch_sp500():
    """從 Wikipedia 抓 S&P 500 列表（用 pandas 正確解析第一個表）"""
    if _is_cache_fresh(SP500_CACHE):
        with open(SP500_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)["tickers"]
    try:
        # 優先用 pandas read_html（精準抓第一個 current constituents 表）
        try:
            import pandas as pd
            tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                                  storage_options={"User-Agent":"Mozilla/5.0"})
            sp500 = tables[0]  # 第一個表 = 當前 S&P 500 成分股
            tickers = sp500["Symbol"].dropna().astype(str).tolist()
        except Exception:
            # fallback: regex 解析（只取第一個 <table> 的內容）
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
            # 只解析第一個 wikitable
            first_table = html.split("</table>")[0]
            m = re.findall(r'<td[^>]*><a[^>]*>([A-Z][A-Z0-9.]{0,5}(?:-[A-Z])?)</a>', first_table)
            tickers = m

        # 清理：去黑名單 + 字數合理 + BRK.B → BRK-B
        cleaned = []
        for t in tickers:
            t = t.replace(".", "-").strip()
            if not t or len(t) > 5: continue
            if t in SP500_BLACKLIST: continue
            if not t.replace("-","").isalnum(): continue
            cleaned.append(t)
        # 去重保序
        tickers = list(dict.fromkeys(cleaned))[:520]
        with open(SP500_CACHE, "w", encoding="utf-8") as f:
            json.dump({"updated": dt.datetime.now().isoformat(), "tickers": tickers}, f)
        print(f"[universe] S&P 500: {len(tickers)} 檔（已過濾黑名單 {len(SP500_BLACKLIST)} 檔）")
        return tickers
    except Exception as e:
        print(f"[universe] S&P500 fetch failed: {e}")
        from config import UNIVERSE
        return UNIVERSE

def fetch_tw_universe():
    """從 TWSE/TPEX 官網抓所有上市+上櫃股票"""
    if _is_cache_fresh(TW_CACHE):
        with open(TW_CACHE, "r", encoding="utf-8") as f:
            return [(t["code"], t["name"]) for t in json.load(f)["stocks"]]
    out = []
    for mode, label in [("2", "上市"), ("4", "上櫃")]:
        try:
            url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30, context=_ssl_ctx).read().decode("big5", errors="ignore")
            # 抓 <td>1101 台泥</td> 或多種變化
            matches = re.findall(r'(\d{4,6})[\s　]+([^\s<&]{1,15})', html)
            for code, name in matches:
                # 只取 4 位數股票代號（排除權證 6-7 位、ETF 5 位 00 開頭等）
                if len(code) != 4: continue
                if code.startswith("00"): continue  # 排除 ETF
                # 篩股票區間：1xxx-9xxx，但跳過特定範圍（特別股、TDR、KY 等已在4位數內）
                if not code[0] in "123456789": continue
                # 名稱長度合理
                if len(name) < 1 or len(name) > 12: continue
                out.append((code, name))
        except Exception as e:
            print(f"[universe] TW {label} fetch failed: {e}")
    # 去重
    seen = set(); deduped = []
    for c, n in out:
        if c not in seen:
            seen.add(c); deduped.append((c, n))
    if deduped:
        with open(TW_CACHE, "w", encoding="utf-8") as f:
            json.dump({"updated": dt.datetime.now().isoformat(),
                       "stocks": [{"code":c,"name":n} for c, n in deduped]}, f, ensure_ascii=False)
    return deduped if deduped else _fallback_tw()

def _fallback_tw():
    from tw_breakout_filter import TW_UNIVERSE
    return TW_UNIVERSE

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sp500 = fetch_sp500()
    tw = fetch_tw_universe()
    print(f"S&P 500: {len(sp500)} 檔")
    print(f"  前 10: {', '.join(sp500[:10])}")
    print(f"台股全市場: {len(tw)} 檔")
    print(f"  前 10: {', '.join(f'{c}({n})' for c, n in tw[:10])}")
