"""
tw_market_pulse.py  —  台股大盤脈動（每日 14:00 併入台股推播）
─────────────────────────────────────────────────────────────────
1. 天險警報：上市 + 上櫃 成交金額 >= 1.5兆 TWD
2. 87MA 強弱：TAIEX（上市）& 上櫃指數 vs 87日均線
              → 鄭大 jo5 強弱判斷

資料來源：
  上市 成交金額  → TWSE MI_INDEX?type=MS（元）
  上櫃 成交金額  → TPEx API（元，失敗時以 TWSE × 0.28 估算）
  TAIEX 87MA     → yfinance ^TWII
  上櫃指數 87MA  → TPEx 歷史資料（失敗時略過）
"""
import re, json, time, requests, yfinance as yf, pandas as pd
from datetime import date, datetime, timedelta

TIAN_XIAN = 1.5e12          # 1.5 兆 TWD
TWSE_HDR = {"Referer": "https://www.twse.com.tw/", "User-Agent": "Mozilla/5.0"}
TPEX_HDR = {"Referer": "https://www.tpex.org.tw/", "User-Agent": "Mozilla/5.0"}


# ─────────────────────────────────────────────────────────────────────────────
# 成交金額（TWD）
# ─────────────────────────────────────────────────────────────────────────────

def _parse_amount(s: str) -> float:
    """'907,096,283,500' → 907096283500.0"""
    return float(s.replace(",", "")) if s.replace(",", "").isdigit() else 0.0


def get_twse_amount() -> float | None:
    """上市 成交金額（元）—— TWSE MI_INDEX?type=MS，取一般股票那行"""
    try:
        r = requests.get(
            "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=MS",
            headers=TWSE_HDR, timeout=10)
        d = r.json()
        for t in d.get("tables", []):
            if not isinstance(t, dict) or "大盤統計" not in t.get("title", ""):
                continue
            fields = t.get("fields", [])
            data   = t.get("data", [])
            if "成交金額(元)" not in fields:
                continue
            amt_idx = fields.index("成交金額(元)")
            for row in data:
                if "一般股票" in row[0]:
                    return _parse_amount(row[amt_idx])
    except Exception as e:
        print(f"[Pulse] TWSE API err: {e}", flush=True)
    return None


def get_tpex_amount() -> float | None:
    """上櫃 成交金額（元）—— TPEx 每日市場成交統計 JSON"""
    try:
        today = date.today()
        roc   = f"{today.year-1911}/{today.month:02d}/{today.day:02d}"
        r = requests.get(
            f"https://www.tpex.org.tw/web/stock/aftertrading/otc_day_trading_summary/"
            f"result.php?l=zh-tw&d={roc}&response=json",
            headers=TPEX_HDR, timeout=8)
        if r.status_code != 200 or not r.text.strip():
            return None
        d = r.json()
        # 找 iTotalAmount 或 sTableArray[0] 的第一行
        for key in ("iTotalAmount", "total_amount"):
            if key in d:
                return float(str(d[key]).replace(",", ""))
        # 嘗試解析 sTableArray
        rows = d.get("sTableArray", []) or d.get("aaData", [])
        for row in rows:
            if "合計" in str(row) or len(row) > 5:
                for cell in row:
                    s = str(cell).replace(",", "")
                    if s.isdigit() and len(s) > 11:
                        return float(s)
    except Exception as e:
        print(f"[Pulse] TPEx amount err: {e}", flush=True)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 87MA 強弱
# ─────────────────────────────────────────────────────────────────────────────

def _ma87_status(close: pd.Series, label: str) -> dict:
    if len(close) < 87:
        return {"ok": False, "label": label, "reason": f"資料不足({len(close)}筆)"}
    ma87  = close.rolling(87).mean()
    last  = float(close.iloc[-1])
    m87   = float(ma87.iloc[-1])
    above = last > m87
    dist  = (last / m87 - 1) * 100
    return {"ok": True, "label": label,
            "close": round(last, 2), "ma87": round(m87, 2),
            "above": above, "dist": round(dist, 2)}


def _finmind_closes(data_id: str):
    """FinMind 官方指數收盤序列（TAIEX/TPEx，含當日）；失敗回 None"""
    try:
        start = (date.today() - timedelta(days=200)).isoformat()
        r = requests.get(
            f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice"
            f"&data_id={data_id}&start_date={start}", timeout=15)
        rows = [x for x in r.json().get("data", []) if x.get("close")]
        if len(rows) >= 87:
            return pd.Series([float(x["close"]) for x in rows])
    except Exception:
        pass
    return None


def get_taiex_87ma() -> dict:
    """上市 TAIEX 87MA —— yfinance ^TWII"""
    try:
        h = yf.download("^TWII", period="1y", progress=False)
        close = None if h.empty else h["Close"].squeeze()
        if close is None or len(close) < 87:
            close = _finmind_closes("TAIEX")   # Actions 上 yfinance 常 null → FinMind 後備
        if close is None:
            return {"ok": False, "label": "上市TAIEX", "reason": "下載失敗"}
        return _ma87_status(close, "上市TAIEX")
    except Exception as e:
        return {"ok": False, "label": "上市TAIEX", "reason": str(e)[:60]}


def get_otc_87ma() -> dict:
    """
    上櫃指數 87MA —— TPEx 歷史日資料
    格式：[{"date":"2026/08/17","OTCindex":"231.50",...}, ...]
    """
    try:
        today   = date.today()
        past90  = today - timedelta(days=130)   # 多取幾天以確保 87 筆
        roc_e   = f"{today.year-1911}/{today.month:02d}/{today.day:02d}"
        roc_s   = f"{past90.year-1911}/{past90.month:02d}/{past90.day:02d}"
        r = requests.get(
            f"https://www.tpex.org.tw/web/stock/aftertrading/otc_index/"
            f"otcHistChart_query.php?l=zh-tw&sd={roc_s}&ed={roc_e}&response=json",
            headers=TPEX_HDR, timeout=10)
        if r.status_code != 200 or not r.text.strip():
            return {"ok": False, "label": "上櫃指數", "reason": "TPEx 無回應"}
        d = r.json()
        # 可能的 key 名稱
        rows = d.get("aaData") or d.get("data") or d.get("Data") or []
        if not rows:
            return {"ok": False, "label": "上櫃指數", "reason": "TPEx 無資料"}
        vals = []
        for row in rows:
            for cell in row:
                s = str(cell).replace(",", "")
                try:
                    v = float(s)
                    if 100 < v < 5000:   # 上櫃指數合理範圍
                        vals.append(v)
                        break
                except:
                    pass
        if len(vals) < 10:
            raise ValueError(f"解析後僅{len(vals)}筆")
        close = pd.Series(vals)
        return _ma87_status(close, "上櫃指數")
    except Exception:
        close = _finmind_closes("TPEx")        # TPEx 擋外部連線 → FinMind 後備
        if close is not None:
            return _ma87_status(close, "上櫃指數")
        return {"ok": False, "label": "上櫃指數", "reason": "TPEx/FinMind 皆失敗"}


# ─────────────────────────────────────────────────────────────────────────────
# 彙整
# ─────────────────────────────────────────────────────────────────────────────

def check() -> dict:
    today = date.today().strftime("%Y-%m-%d")

    twse_amt = get_twse_amount()
    tpex_amt = get_tpex_amount()

    # 上櫃失敗時以上市 × 0.28 估算（歷史比例）
    if twse_amt and tpex_amt is None:
        tpex_est = twse_amt * 0.28
        tpex_amt_used = tpex_est
        tpex_estimated = True
    else:
        tpex_amt_used = tpex_amt or 0
        tpex_estimated = (tpex_amt is None)

    total_amt = (twse_amt or 0) + tpex_amt_used

    taiex = get_taiex_87ma()
    otc   = get_otc_87ma()

    return {
        "date":           today,
        "twse_amt":       twse_amt,
        "tpex_amt":       tpex_amt,
        "tpex_estimated": tpex_estimated,
        "total_amt":      total_amt,
        "tian_xian_hit":  total_amt >= TIAN_XIAN if total_amt else False,
        "taiex_87ma":     taiex,
        "otc_87ma":       otc,
    }


def build_line_block(r: dict) -> str:
    lines = []

    # ── 成交量天險 ───────────────────────────────────────────────────────────
    total = r.get("total_amt", 0)
    twse  = r.get("twse_amt")
    tpex  = r.get("tpex_amt")
    est   = r.get("tpex_estimated", False)

    if total:
        t_T    = total / 1e12
        flag   = " 🚨天險觸發！" if r["tian_xian_hit"] else ""
        twse_s = f"{twse/1e12:.2f}兆" if twse else "N/A"
        tpex_s = (f"{(tpex or r['twse_amt']*0.28)/1e12:.2f}兆{'(估)' if est else ''}"
                  if twse else "N/A")
        lines += [
            f"💰 今日成交金額{flag}",
            f"  上市 {twse_s}  上櫃 {tpex_s}",
            f"  合計 {t_T:.2f}兆 ／ 天險 1.5兆",
        ]
        if r["tian_xian_hit"]:
            lines.append("  ⚠️  量能過熱，警惕短線過度追高")
    else:
        lines.append("💰 成交金額：資料取得失敗（非交易日或伺服器問題）")

    lines.append("")

    # ── 87MA 強弱 ────────────────────────────────────────────────────────────
    for mx in [r.get("taiex_87ma"), r.get("otc_87ma")]:
        if not mx:
            continue
        if not mx.get("ok"):
            lines.append(f"📊 {mx['label']}：{mx.get('reason','無法取得')}")
            continue
        icon  = "🟢" if mx["above"] else "🔴"
        state = "強（站上87MA）" if mx["above"] else "弱（跌破87MA）"
        lines.append(
            f"{icon} {mx['label']}：{mx['close']:,.0f}"
            f"  87MA {mx['ma87']:,.0f}  {state}  ({mx['dist']:+.1f}%)"
        )

    # ── jo5 綜合判斷 ─────────────────────────────────────────────────────────
    t_ok = r.get("taiex_87ma", {}).get("above")
    o_ok = r.get("otc_87ma",   {}).get("above")

    if t_ok is True and o_ok is True:
        judge = "✅ 上市+上櫃雙強 → 全面多頭環境"
    elif t_ok is True and o_ok is False:
        judge = "⚡ 上市強 / 上櫃弱 → 大型藍籌主導"
    elif t_ok is False and o_ok is True:
        judge = "⚡ 上市弱 / 上櫃強 → 中小型股活躍"
    elif t_ok is False and o_ok is False:
        judge = "❌ 上市+上櫃雙弱 → 偏空保守觀望"
    elif t_ok is True:
        judge = "🟢 上市站上87MA（上櫃資料暫缺）"
    elif t_ok is False:
        judge = "🔴 上市跌破87MA（上櫃資料暫缺）"
    else:
        judge = "❓ 87MA 資料不足，無法判斷"

    lines.append(f"\n📌 {judge}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 獨立執行測試
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    r = check()
    print(json.dumps(r, ensure_ascii=False, indent=2,
                     default=lambda x: str(x)))
    print("\n─── LINE 推播內容 ───")
    print(build_line_block(r))
