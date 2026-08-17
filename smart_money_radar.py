#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Money Radar — 雙主力資金流向雷達（外資／投信；自營因權證避險+隔日沖雜訊已剔除）
═════════════════════════════════════════════════
資料：證交所 T86 三大法人買賣超（官方，T+0 收盤後 15:00）
維度：
  ① 大類族群（證交所 30 類）× 三方 5 日 vs 20 日 → 加速/減速
  ② 子族群（記憶體/光通訊/ABF-CCL/矽光子/散熱/IC設計/晶圓）
  ③ 雙方共識：外資投信同買 🔥 / 外資獨買 🟢 / 投信獨買 🟡 / 雙賣 🔴
  ④ 個股：外資+投信合計買超/賣超 Top 10
輸出：smart_money_radar.json + LINE 區塊
每日 cron（接在 institutional_tracker 之後）；週一另做完整週報
"""
import sys, io, os, json, urllib.request, time
import datetime as dt
from collections import defaultdict
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception: pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from industry_map_loader import get_industry

CACHE_PATH = os.path.join(ROOT, "t86_3party_cache.json")
OUT_PATH = os.path.join(ROOT, "smart_money_radar.json")
UA = {"User-Agent": "Mozilla/5.0"}
COLS = {"外資": "外陸資買賣超股數(不含外資自營商)",
        "投信": "投信買賣超股數"}
# 自營商已剔除：混入權證發行商避險買盤 + 隔日沖自營，屬雜訊非方向
WINDOW_LONG = 20

# 子族群 → 改用 product_taxonomy 產品鏈細分類（~45 類，可持續擴充）
from product_taxonomy import PRODUCT_TAXONOMY as SUB_GROUPS


def fetch_day(d):
    ds = d.strftime("%Y%m%d")
    u = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
         f"?date={ds}&selectType=ALLBUT0999&response=json")
    try:
        j = json.loads(urllib.request.urlopen(
            urllib.request.Request(u, headers=UA), timeout=20).read())
        if j.get("stat") != "OK" or not j.get("data"):
            return None
        f = j["fields"]
        idx = {k: f.index(v) for k, v in COLS.items()}
        out = {}
        for row in j["data"]:
            c = row[0].strip()
            try:
                out[c] = {k: int(row[i].replace(",", "")) for k, i in idx.items()}
            except Exception:
                pass
        return out
    except Exception:
        return None


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with io.open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def ensure_days(cache, need=WINDOW_LONG + 5):
    """補抓缺的交易日（走回 45 天），寫回 cache"""
    d = dt.date.today()
    fetched = 0
    while (dt.date.today() - d).days < 45 and \
            sum(1 for k in cache if k >= (dt.date.today()-dt.timedelta(days=45)).isoformat()) < need:
        key = d.isoformat()
        if d.weekday() < 5 and key not in cache:
            r = fetch_day(d)
            cache[key] = r if r else {}   # 空 dict = 非交易日/抓不到，避免重抓
            if r: fetched += 1
            time.sleep(1.3)
        d -= dt.timedelta(days=1)
    if fetched:
        with io.open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    days = sorted([(k, v) for k, v in cache.items() if v], key=lambda x: x[0])
    return days


def agg(days, window):
    """回傳 {code: {外資,投信,自營}} 在 window 日的累計"""
    tot = defaultdict(lambda: {k: 0 for k in COLS})
    for _, r in days[-window:]:
        for c, v in r.items():
            for k in COLS:
                tot[c][k] += v[k]
    return tot


def consensus(f, s):
    if f > 0 and s > 0: return "🔥雙買"
    if f > 0 and s <= 0: return "🟢外資獨買"
    if f <= 0 and s > 0: return "🟡投信獨買"
    return "🔴雙賣"


def main():
    cache = load_cache()
    days = ensure_days(cache)
    if len(days) < 5:
        print("❌ 資料不足"); return
    d5, d20 = days[-5:], days[-WINDOW_LONG:]
    print(f"資料 {days[0][0]} ~ {days[-1][0]}（{len(days)} 交易日）")
    t5, t20 = agg(days, 5), agg(days, WINDOW_LONG)

    names = {}
    try:
        u = json.loads(io.open(os.path.join(ROOT, "tw_universe.json"), encoding="utf-8").read())
        names = {s["code"]: s["name"] for s in u["stocks"]}
    except Exception:
        pass

    # ① 大類 × 三方
    def sector_table(tot):
        by = defaultdict(lambda: {k: 0 for k in COLS})
        for c, v in tot.items():
            ind = get_industry(c)
            if not ind: continue
            for k in COLS: by[ind][k] += v[k]
        return by
    s5, s20 = sector_table(t5), sector_table(t20)
    sectors = []
    for ind in set(s5) | set(s20):
        f5, i5 = (s5[ind][k]/1000 for k in COLS)
        f20, i20 = (s20[ind][k]/1000 for k in COLS)
        tot5, tot20 = f5+i5, f20+i20
        # 加速判定：5日已達20日的 50%+ 且同向 → 加速
        if tot5 > 0 and tot5 >= abs(tot20)*0.5: mom = "⏫加速買"
        elif tot5 < 0 and -tot5 >= abs(tot20)*0.5: mom = "⏬加速賣"
        elif (tot5 > 0) != (tot20 > 0) and abs(tot5) > 5000: mom = "🔄反轉"
        else: mom = "➡️持平"
        sectors.append({"industry": ind, "f5": f5, "i5": i5, "tot5": tot5,
                        "tot20": tot20, "consensus": consensus(f5, i5), "mom": mom})
    sectors.sort(key=lambda x: -x["tot5"])

    # ② 子族群
    subs = []
    for g, codes in SUB_GROUPS.items():
        f5 = sum(t5[c]["外資"] for c in codes if c in t5)/1000
        i5 = sum(t5[c]["投信"] for c in codes if c in t5)/1000
        tot20 = sum(sum(t20[c].values()) for c in codes if c in t20)/1000
        tot5 = f5+i5
        if tot5 > 0 and tot5 >= abs(tot20)*0.5: mom = "⏫加速買"
        elif tot5 < 0 and -tot5 >= abs(tot20)*0.5: mom = "⏬加速賣"
        elif (tot5 > 0) != (tot20 > 0) and abs(tot5) > 2000: mom = "🔄反轉"
        else: mom = "➡️持平"
        subs.append({"group": g, "f5": f5, "i5": i5, "tot5": tot5,
                     "tot20": tot20, "consensus": consensus(f5, i5), "mom": mom})
    subs.sort(key=lambda x: -x["tot5"])

    # ④ 個股 Top（排除 ETF：00 開頭；只留 4 碼上市個股）
    def is_stock(c):
        return len(c) == 4 and c.isdigit() and not c.startswith("00")
    stock_tot = {c: sum(v.values()) for c, v in t5.items() if is_stock(c)}
    top_buy = sorted(stock_tot.items(), key=lambda x: -x[1])[:10]
    top_sell = sorted(stock_tot.items(), key=lambda x: x[1])[:10]
    def sk(c, v):
        return {"code": c, "name": names.get(c, ""), "industry": get_industry(c),
                "tot": v/1000, "f": t5[c]["外資"]/1000, "i": t5[c]["投信"]/1000}

    # 列印
    print(f"\n=== 近5日 大類 × 外資/投信（張） [{d5[0][0]}~{d5[-1][0]}] ===")
    print(f"{'族群':<9}{'外資':>10}{'投信':>10}{'5日合計':>10}{'20日合計':>10}  共識/動能")
    for s in sectors[:12]:
        print(f"{s['industry']:<9}{s['f5']:>+10,.0f}{s['i5']:>+10,.0f}"
              f"{s['tot5']:>+10,.0f}{s['tot20']:>+10,.0f}  {s['consensus']} {s['mom']}")
    print("  ...")
    for s in sectors[-4:]:
        print(f"{s['industry']:<9}{s['f5']:>+10,.0f}{s['i5']:>+10,.0f}"
              f"{s['tot5']:>+10,.0f}{s['tot20']:>+10,.0f}  {s['consensus']} {s['mom']}")

    print(f"\n=== 近5日 產品細類 × 外資/投信（張）— 買超前15 ===")
    print(f"{'產品細類':<14}{'外資':>10}{'投信':>10}{'5日合計':>10}{'20日合計':>10}  共識/動能")
    for s in subs[:15]:
        print(f"{s['group']:<14}{s['f5']:>+10,.0f}{s['i5']:>+10,.0f}"
              f"{s['tot5']:>+10,.0f}{s['tot20']:>+10,.0f}  {s['consensus']} {s['mom']}")
    print(f"--- 賣超後6 ---")
    for s in subs[-6:]:
        print(f"{s['group']:<14}{s['f5']:>+10,.0f}{s['i5']:>+10,.0f}"
              f"{s['tot5']:>+10,.0f}{s['tot20']:>+10,.0f}  {s['consensus']} {s['mom']}")

    print(f"\n=== 近5日 外資+投信 買超 Top10 ===")
    for c, v in top_buy:
        k = sk(c, v)
        print(f"  {c} {k['name']:<7}({(k['industry'] or '?')[:5]:<5}) 合計{k['tot']:>+8,.0f}"
              f"  外{k['f']:>+8,.0f} 投{k['i']:>+8,.0f}")
    print(f"=== 近5日 外資+投信 賣超 Top10 ===")
    for c, v in top_sell:
        k = sk(c, v)
        print(f"  {c} {k['name']:<7}({(k['industry'] or '?')[:5]:<5}) 合計{k['tot']:>+8,.0f}"
              f"  外{k['f']:>+8,.0f} 投{k['i']:>+8,.0f}")

    out = {"date": dt.date.today().isoformat(),
           "window5": [d5[0][0], d5[-1][0]], "window20": [d20[0][0], d20[-1][0]],
           "sectors": sectors, "subgroups": subs,
           "top_buy": [sk(c, v) for c, v in top_buy],
           "top_sell": [sk(c, v) for c, v in top_sell]}
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n💾 已輸出 {OUT_PATH}")


def build_block(data=None, max_rows=4):
    """LINE 精簡區塊：三方共識最強/最弱 + 子族群動能"""
    if data is None:
        try:
            with io.open(OUT_PATH, encoding="utf-8") as f: data = json.load(f)
        except Exception: return None
    L = ["💸 外資+投信資金流向(5日)"]
    fire = [s for s in data["sectors"] if s["consensus"] == "🔥雙買"][:3]
    dump = [s for s in data["sectors"] if s["consensus"] == "🔴雙賣"][:2]
    if fire:
        L.append("  🔥雙買: " + "、".join(f"{s['industry']}{s['tot5']:+,.0f}" for s in fire))
    if dump:
        L.append("  🔴雙賣: " + "、".join(f"{s['industry']}{s['tot5']:+,.0f}" for s in dump))
    subs = data.get("subgroups", [])
    hot = [s for s in subs if s["tot5"] > 0][:3]
    cold = [s for s in sorted(subs, key=lambda x: x["tot5"]) if s["tot5"] < 0][:3]
    if hot:  L.append("  📈產品買超: " + "、".join(f"{s['group']}{s['tot5']:+,.0f}" for s in hot))
    if cold: L.append("  📉產品賣超: " + "、".join(f"{s['group']}{s['tot5']:+,.0f}" for s in cold))
    return "\n".join(L) if len(L) > 1 else None


if __name__ == "__main__":
    main()
