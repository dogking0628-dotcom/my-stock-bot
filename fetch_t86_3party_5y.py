# -*- coding: utf-8 -*-
"""抓 5 年 T86 外資+投信買賣超（張）→ t86_3party_5y.json，可續跑"""
import sys, io, os, json, time, urllib.request
import datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "t86_3party_5y.json")
UA = {"User-Agent": "Mozilla/5.0"}
COLS = {"f": "外陸資買賣超股數(不含外資自營商)", "i": "投信買賣超股數"}
YEARS = 5

hist = {}
if os.path.exists(OUT):
    hist = json.load(io.open(OUT, encoding="utf-8"))
    print(f"續跑：已有 {len(hist)} 日")
# 也把既有 2y 投信歷史/6 天三方 cache 併入不重抓（只有投信的那份先略過，需外資）
end = dt.date.today()
d = end - dt.timedelta(days=int(365 * YEARS) + 10)
fetched = fails = 0
while d <= end:
    ds = d.strftime("%Y%m%d")
    if d.weekday() >= 5 or ds in hist:
        d += dt.timedelta(days=1); continue
    u = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ds}&selectType=ALLBUT0999&response=json"
    try:
        j = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read())
        if j.get("stat") == "OK" and j.get("data"):
            fl = j["fields"]; idx = {k: fl.index(v) for k, v in COLS.items()}
            day = {}
            for row in j["data"]:
                try:
                    f = int(row[idx["f"]].replace(",", "")); i = int(row[idx["i"]].replace(",", ""))
                    if f or i: day[row[0].strip()] = [f, i]
                except Exception: pass
            hist[ds] = day; fetched += 1
        else:
            hist[ds] = {}
        fails = 0
    except Exception as e:
        fails += 1; print(f"  {ds} fail({fails}) {type(e).__name__}")
        if fails >= 4: print("連錯退出（可續跑，IP 可能被暫封）"); break
        time.sleep(60); continue
    if fetched and fetched % 60 == 0:
        print(f"  已抓 {fetched}（至 {ds}）暫存")
        json.dump(hist, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    d += dt.timedelta(days=1); time.sleep(3.0)
json.dump(hist, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print(f"完成 {len(hist)} 日（交易日 {sum(1 for v in hist.values() if v)}）→ {OUT}")
