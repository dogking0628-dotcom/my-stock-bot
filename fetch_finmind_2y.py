# -*- coding: utf-8 -*-
"""
FinMind 抓 2 年 外資+投信 買賣超（不用證交所，免封鎖）
→ t86_3party_2y.json  格式 {yyyymmdd: {code: [foreign_net_shares, sitc_net_shares]}}
範圍：product_taxonomy 內所有股 + tw_universe 科技 7 族群（V4.3 選股池），約 300-600 檔
免費額度 600 req/hr → 每檔 1 次請求（2y 一次拉完），約 30-60 分鐘
"""
import sys, io, os, json, time, urllib.request, urllib.parse
import datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from product_taxonomy import PRODUCT_TAXONOMY
from industry_map_loader import get_industry

OUT = os.path.join(ROOT, "t86_3party_2y.json")
PROG = os.path.join(ROOT, "finmind_progress.json")
ALLOWED = {"半導體","電子零組件","光電","電腦及週邊","電子通路","通信網路","其他電子"}
END = dt.date.today(); START = END - dt.timedelta(days=365*2+40)
UA = {"User-Agent": "Mozilla/5.0"}
TOKEN = os.environ.get("FINMIND_TOKEN", "")   # 有 token 額度更高（選填）


def universe():
    codes = {c for cs in PRODUCT_TAXONOMY.values() for c in cs}
    try:
        u = json.load(io.open(os.path.join(ROOT, "tw_universe.json"), encoding="utf-8"))
        for s in u["stocks"]:
            if get_industry(s["code"]) in ALLOWED: codes.add(s["code"])
    except Exception as e: print("universe warn:", e)
    return sorted(codes)


def fetch_one(code):
    p = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": code,
         "start_date": START.isoformat(), "end_date": END.isoformat()}
    if TOKEN: p["token"] = TOKEN
    u = "https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(p)
    j = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40).read())
    if j.get("status") != 200: raise RuntimeError(j.get("msg"))
    return j.get("data", [])


def main():
    hist = json.load(io.open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    done = set(json.load(io.open(PROG, encoding="utf-8"))) if os.path.exists(PROG) else set()
    codes = universe()
    todo = [c for c in codes if c not in done]
    print(f"universe {len(codes)} 檔 / 已完成 {len(done)} / 待抓 {len(todo)}")
    n_ok = n_fail = 0; consecutive_fail = 0
    for i, c in enumerate(todo, 1):
        try:
            rows = fetch_one(c)
            for r in rows:
                d = r["date"].replace("-", "")
                net = r["buy"] - r["sell"]
                nm = r["name"]
                slot = hist.setdefault(d, {}).setdefault(c, [0, 0])
                if nm == "Foreign_Investor": slot[0] += net
                elif nm == "Investment_Trust": slot[1] += net
            done.add(c); n_ok += 1; consecutive_fail = 0
        except Exception as e:
            n_fail += 1; consecutive_fail += 1
            msg = str(e)
            print(f"  {c} fail: {msg[:80]}")
            if "402" in msg or "limit" in msg.lower() or consecutive_fail >= 5:
                print("  額度/連錯 → 暫存後退出（可續跑）"); break
            time.sleep(5); continue
        if i % 25 == 0:
            print(f"  [{i}/{len(todo)}] ok {n_ok} fail {n_fail}");
            json.dump(hist, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
            json.dump(sorted(done), io.open(PROG, "w", encoding="utf-8"))
        time.sleep(0.4)   # 600/hr ≈ 6s 才安全；但 FinMind 實測寬鬆，0.4s 先試
    json.dump(hist, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(sorted(done), io.open(PROG, "w", encoding="utf-8"))
    days = [k for k, v in hist.items() if v]
    print(f"完成：{len(done)}/{len(codes)} 檔，{len(days)} 交易日 ({min(days) if days else '-'}~{max(days) if days else '-'}) → {OUT}")


if __name__ == "__main__":
    main()
