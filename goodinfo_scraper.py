"""
GoodInfo ATH 突破股清單爬蟲
-----------------------------
策略：股價創歷史高點（還原權值）+ 股價創多日高點
擷取後套用量能篩選 + F3 過濾 → 推播 LINE + 存 Excel
"""
import cloudscraper, re, time, json, os, sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
import notify_line

# ── 篩選參數 ───────────────────────────────────────────────────────────────────
MIN_PRICE    = 10         # 排除仙股
MIN_VOLUME   = 500_000    # 日均量 > 50萬股
TOP_N        = 20         # 最多推播幾檔
SKIP_PREFER  = True       # 跳過特別股（代號含英文字母，如 2836A）


def make_cookie():
    tz = -480
    serial = time.time() * 1000 / 86400000 - tz / 1440 + 25569
    arr = ["4.4", "45226.3562699916", "46337.4673811027",
           str(tz), str(round(serial, 10)), "0", "0", "0"]
    return "|".join(arr), str(round(serial, 10))


def fetch_ath_list() -> list[dict]:
    """
    抓取 GoodInfo ATH 突破股清單
    欄位順序：代號(0) 名稱(1) 還原成交(2) 還原最高(3) 還原最低(4)
              還原漲跌價(5) 還原漲跌幅(6) 更新日期(7) ...
    """
    from bs4 import BeautifulSoup

    client_key, reinit = make_cookie()
    s = cloudscraper.create_scraper()
    s.cookies.set("CLIENT_KEY", client_key, domain="goodinfo.tw", path="/")
    hdrs = {
        "Referer":          "https://goodinfo.tw/tw/StockList.asp",
        "Accept-Language":  "zh-TW,zh;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type":     "application/x-www-form-urlencoded",
    }
    params = {
        "MARKET_CAT":   "智慧選股",
        "INDUSTRY_CAT": "股價創歷史高點–還原權值@@股價創多日高點@@還原權值–歷史",
        "SHEET":        "漲跌及成交統計",
        "SHEET2":       "最高/最低股價統計–還原權值(十年/二十年/歷史)",
        "STEP":         "DATA",
        "IS_RELOAD_REPORT": "T",
        "REINIT":       reinit,
    }

    resp = s.post("https://goodinfo.tw/tw/StockList.asp",
                  data=params, headers=hdrs, timeout=25)
    html = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    stocks = []
    seen   = set()

    for tr in soup.find_all("tr"):
        # 只取有股票連結的 tr
        a = tr.find("a", href=re.compile(r"StockDetail\.asp\?STOCK_ID=\d+"))
        if not a:
            continue
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 7:
            continue

        raw_code = tds[0]   # 可能是 "2836A"
        name     = tds[1]

        # 過濾 ETF（00xxx / 02xxx）
        if raw_code.startswith("00") or raw_code.startswith("02"):
            continue
        # 過濾特別股（代號含非數字）
        pure_code = re.sub(r"[^0-9]", "", raw_code)
        if SKIP_PREFER and raw_code != pure_code:
            continue
        if pure_code in seen:
            continue
        seen.add(pure_code)

        try:    price   = float(tds[2])
        except: price   = 0.0
        try:    chg_pct = float(tds[6].replace("%", ""))
        except: chg_pct = 0.0

        stocks.append({
            "code":    pure_code,
            "name":    name,
            "price":   price,
            "chg_pct": chg_pct,
        })

    return stocks


def quick_score(code: str) -> dict:
    """yfinance 確認：量能 + 股價 + F3 -8%"""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{code}.TW")
        hist   = ticker.history(period="20d")
        if hist.empty:
            return {"ok": False, "reason": "無資料"}
        avg_vol = int(hist["Volume"].mean())
        price   = float(hist["Close"].iloc[-1])
        if price < MIN_PRICE:
            return {"ok": False, "reason": f"股價{price:.0f}<{MIN_PRICE}"}
        if avg_vol < MIN_VOLUME:
            return {"ok": False, "reason": f"量{avg_vol//10000:.0f}萬<{MIN_VOLUME//10000:.0f}萬"}
        # F3：近7天跌>8%排除
        ret7 = hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1
        if ret7 < -0.08:
            return {"ok": False, "reason": f"F3跌{ret7:.1%}"}
        try:    pe = round(float(ticker.info.get("trailingPE") or 0), 1)
        except: pe = 0.0
        return {"ok": True, "reason": "通過", "avg_vol": avg_vol,
                "price": price, "pe": pe}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:60]}


def build_line_message(passed: list, filtered: list, today: str) -> str:
    lines = [
        f"📈 ATH突破選股日報 {today}",
        f"GoodInfo 歷史高點（還原權值）+ 量能篩選",
        f"原始清單: {len(passed)+len(filtered)} 檔 | 通過: {len(passed)} 檔",
        "═" * 22,
    ]
    if passed:
        lines.append("✅ 通過篩選（量足 + F3）：")
        for i, s in enumerate(passed[:TOP_N], 1):
            sc     = s.get("score", {})
            pe_str = f" PE:{sc['pe']}" if sc.get("pe") else ""
            vol_k  = sc.get("avg_vol", 0) // 10000
            lines.append(
                f"  {i:2d}. {s['code']} {s['name'][:6]}"
                f"  ${sc.get('price', s['price']):.1f}"
                f"  {s['chg_pct']:+.1f}%  量:{vol_k:.0f}萬{pe_str}"
            )
    else:
        lines.append("⚠️ 今日無符合條件的 ATH 突破股")

    if filtered:
        lines.append(f"\n❌ 篩選掉 {len(filtered)} 檔：")
        for s in filtered[:8]:
            lines.append(f"  {s['code']} {s['name'][:6]} — {s.get('filter_reason','')}")

    lines += [
        "─" * 22,
        "策略：ATH還原權值 + 日均量>50萬 + F3(-8%)",
        "搭配鄭大位階確認後操作",
    ]
    return "\n".join(lines)


def save_excel(passed: list, filtered: list, today: str):
    """將每日選股結果附加寫入 Excel（ath_log.xlsx）"""
    try:
        import openpyxl
        from openpyxl.styles import PatternFill, Font
    except ImportError:
        print("[Excel] openpyxl 未安裝，跳過 Excel 存檔（pip install openpyxl）",
              file=sys.stderr)
        return

    out = Path(__file__).parent / "ath_log.xlsx"
    if out.exists():
        wb = openpyxl.load_workbook(out)
    else:
        wb = openpyxl.Workbook()
        # 移除預設空白頁
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]

    # 確保工作表存在
    if "選股紀錄" not in wb.sheetnames:
        ws = wb.create_sheet("選股紀錄")
        headers = ["日期", "代號", "名稱", "還原收盤", "漲跌幅%",
                   "均量(萬)", "PE", "結果", "篩除原因"]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9E1F2")
    else:
        ws = wb["選股紀錄"]

    green = PatternFill("solid", fgColor="E2EFDA")
    red   = PatternFill("solid", fgColor="FCE4D6")

    for s in passed[:TOP_N]:
        sc = s.get("score", {})
        row = [
            today,
            s["code"],
            s["name"],
            sc.get("price", s["price"]),
            s["chg_pct"],
            round(sc.get("avg_vol", 0) / 10000, 1),
            sc.get("pe", ""),
            "通過",
            "",
        ]
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.fill = green

    for s in filtered:
        sc = s.get("score", {})
        row = [
            today,
            s["code"],
            s["name"],
            s["price"],
            s["chg_pct"],
            "",
            "",
            "篩除",
            s.get("filter_reason", ""),
        ]
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.fill = red

    # 自動調整欄寬
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 30)

    wb.save(out)
    print(f"[Excel] 結果已附加至：{out}")


def main():
    today = date.today().strftime("%Y-%m-%d")
    print(f"[ATH掃描] {today}")

    print("抓取 GoodInfo ATH 清單...")
    time.sleep(2)
    stocks = fetch_ath_list()
    print(f"  取得 {len(stocks)} 檔候選")

    if not stocks:
        msg = f"⚠️ ATH掃描 {today}\n無法取得 GoodInfo 資料（可能被限流）"
        notify_line.push(msg)
        print(msg)
        return

    passed, filtered = [], []
    for i, st in enumerate(stocks[:50]):
        sc = quick_score(st["code"])
        if sc["ok"]:
            st["score"] = sc
            passed.append(st)
        else:
            st["filter_reason"] = sc["reason"]
            filtered.append(st)
        if (i + 1) % 5 == 0:
            print(f"  評分中... {i+1}/{min(50, len(stocks))}")
        time.sleep(0.3)

    msg = build_line_message(passed, filtered, today)
    print("\n" + msg)
    notify_line.push(msg)

    # 存 JSON
    out_j = Path(__file__).parent / "ath_results.json"
    with open(out_j, "w", encoding="utf-8") as f:
        json.dump({"date": today, "passed": passed, "filtered": filtered},
                  f, ensure_ascii=False, indent=2)
    print(f"JSON 結果：{out_j}")

    # 存 Excel
    save_excel(passed, filtered, today)


if __name__ == "__main__":
    main()
