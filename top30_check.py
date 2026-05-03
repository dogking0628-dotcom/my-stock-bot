#!/usr/bin/env python3
"""
雲端版 Top 30 大市值對照 — 用 yfinance
─────────────────────────────────────────
比對你目前持有清單 vs 美股實際 Top 30 大市值
推送 LINE 提示應「賣出已掉出/買入新進」哪些股票
"""
import os, sys, json, datetime as dt
import yfinance as yf
import notify_line

# 候選池：S&P 500 排名靠前的常見大型股（廣泛覆蓋）
CANDIDATE_POOL = [
    "AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","TSLA","AVGO","BRK-B",
    "TSM","WMT","JPM","LLY","V","MA","ORCL","NFLX","XOM","JNJ",
    "COST","PG","HD","ABBV","BAC","CVX","KO","PEP","ASML","MRK",
    "UNH","CRM","TMO","AMD","ADBE","ACN","LIN","CSCO","BABA","WFC",
    "MCD","DIS","ABT","PM","TM","QCOM","DHR","INTU","TXN","IBM",
    "VZ","CMCSA","NOW","UBER","NEE","CAT","RTX","HON","SPGI","COP",
    "GS","AXP","NKE","MS","PFE","T","GE","BLK","ELV","BKNG",
    "PLTR","APP","COIN","MSTR","SHOP",
]

HOLDINGS_PATH = os.path.join(os.path.dirname(__file__), "holdings.json")

def load_holdings():
    if not os.path.exists(HOLDINGS_PATH):
        # 建立預設
        default = ["AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA",
                   "AMD","AVGO","QCOM","MU","INTC","TXN","AMAT",
                   "JPM","GS","MS","V","MA","UNH","LLY","ABBV",
                   "HD","MCD","COST","XOM","CVX","ORCL","CRM","CAT"]
        with open(HOLDINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"holdings": default}, f, ensure_ascii=False, indent=2)
        return default
    with open(HOLDINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("holdings", [])

def fetch_market_caps(tickers):
    """用 yfinance 抓市值（單位：十億美元）— 雙重來源：fast_info + info fallback"""
    print(f"Fetching market caps for {len(tickers)} candidates...")
    results = []
    for t in tickers:
        mcap, name = 0, t
        try:
            tk = yf.Ticker(t)
            try:
                fi = tk.fast_info
                mcap = (fi.get("market_cap") or 0)
                if mcap == 0 and fi.get("last_price") and fi.get("shares"):
                    mcap = fi.get("last_price") * fi.get("shares")
            except: pass
            if mcap == 0:
                info = tk.info
                mcap = info.get("marketCap") or 0
                name = info.get("longName") or info.get("shortName") or t
        except Exception as e:
            print(f"  {t}: {e}")
            continue
        if mcap > 0:
            results.append({"ticker": t, "name": name, "mcap": mcap / 1e9})
        else:
            print(f"  {t}: NO MARKET CAP DATA")
    return results

def main():
    holdings = load_holdings()
    all_caps = fetch_market_caps(CANDIDATE_POOL)
    if not all_caps:
        notify_line.push("❌ Top30 檢查：yfinance 無法取得市值資料")
        return

    all_caps.sort(key=lambda x: -x["mcap"])
    top30 = all_caps[:30]
    top_set = {t["ticker"] for t in top30}
    held_set = set(holdings)

    keep = held_set & top_set
    remove = held_set - top_set
    add = top_set - held_set

    today = dt.datetime.now().strftime("%m/%d")
    lines = [f"📊 Top30 大市值對照 {today}", "═" * 18]

    if not remove and not add:
        lines.append(f"✅ 持股完全符合前 30 大（{len(keep)} 檔）")
    else:
        if remove:
            lines.append(f"🔴 已掉出前30 — 賣出 ({len(remove)} 檔)")
            for t in sorted(remove):
                lines.append(f"  • SELL {t}")
            lines.append("")
        if add:
            lines.append(f"🟢 新進前30 — 買入 ({len(add)} 檔)")
            mcap_lookup = {t["ticker"]: t for t in top30}
            for t in sorted(add):
                info = mcap_lookup.get(t, {})
                lines.append(f"  • BUY  {t} ${info.get('mcap',0):,.0f}B  {info.get('name','')[:18]}")
            lines.append("")

    lines.append("─" * 18)
    lines.append(f"持有 {len(holdings)} 檔，符合 {len(keep)} 檔")
    lines.append("")
    lines.append("📋 美股 Top 30 大市值：")
    for i, t in enumerate(top30, 1):
        marker = "✓" if t["ticker"] in keep else ("🆕" if t["ticker"] in add else " ")
        lines.append(f"{i:>2}. {marker} {t['ticker']:<6} ${t['mcap']:>5,.0f}B  {t['name'][:14]}")

    msg = "\n".join(lines)
    print(msg)
    notify_line.push(msg)

if __name__ == "__main__":
    main()
