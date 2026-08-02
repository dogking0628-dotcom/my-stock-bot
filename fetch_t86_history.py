# -*- coding: utf-8 -*-
"""抓 2 年 T86 投信買賣超歷史 → t86_history.json（{yyyymmdd: {code: 股數}}，可續跑）"""
import sys, io, os, json, time, urllib.request
import datetime as dt
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "t86_history.json")
UA = {"User-Agent": "Mozilla/5.0"}
YEARS = 2

hist = {}
if os.path.exists(OUT):
    with io.open(OUT, encoding="utf-8") as f:
        hist = json.load(f)
    print(f"續跑：已有 {len(hist)} 日")

end = dt.date.today()
start = end - dt.timedelta(days=int(365 * YEARS) + 10)
d = start
fetched = 0
fails = 0
while d <= end:
    ds = d.strftime("%Y%m%d")
    if d.weekday() >= 5 or ds in hist:   # 跳過週末與已抓
        d += dt.timedelta(days=1)
        continue
    url = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
           f"?date={ds}&selectType=ALLBUT0999&response=json")
    try:
        req = urllib.request.Request(url, headers=UA)
        j = json.loads(urllib.request.urlopen(req, timeout=20).read())
        if j.get("stat") == "OK" and j.get("data"):
            fields = j.get("fields", [])
            idx = next(i for i, f in enumerate(fields) if "投信買賣超" in f)
            day = {}
            for row in j["data"]:
                try:
                    v = int(row[idx].replace(",", ""))
                    if v != 0:                     # 只存非零，省空間
                        day[row[0].strip()] = v
                except Exception:
                    pass
            hist[ds] = day
            fetched += 1
        else:
            hist[ds] = {}                          # 非交易日標記空
        fails = 0
    except Exception as e:
        fails += 1
        print(f"  {ds} fail({fails}): {type(e).__name__}")
        if fails >= 5:
            print("連錯 5 次，暫存退出（可續跑）")
            break
        time.sleep(10)
        continue
    if fetched and fetched % 50 == 0:
        print(f"  已抓 {fetched} 交易日（至 {ds}），暫存...")
        with io.open(OUT, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
    d += dt.timedelta(days=1)
    time.sleep(1.6)   # 禮貌限速

with io.open(OUT, "w", encoding="utf-8") as f:
    json.dump(hist, f, ensure_ascii=False)
trading_days = sum(1 for v in hist.values() if v)
print(f"完成：共 {len(hist)} 日（交易日 {trading_days}）→ {OUT}")
