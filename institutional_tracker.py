#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Institutional Tracker — 法人籌碼追蹤（方案 C）
═════════════════════════════════════════════════
① T86 投信買賣超（證交所官方，前一交易日）
② 3 檔主動式 ETF 每日持股（00981A 統一/00980A 野村/00982A 群益）
   來源 MoneyDJ，快照存檔 → 與前一日比對 → 新增/加碼/減碼/剔除
③ 輸出 institutional_signal.json 給 industry_ath_yf(+10分) 與 pickers(標籤) 用

每日 cron 第一步跑（在 industry_ath_yf.py 之前）
"""
import sys, io, os, json, re, time, urllib.request
import datetime as dt
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNAL_PATH = os.path.join(ROOT, "institutional_signal.json")
SNAPSHOT_PATH = os.path.join(ROOT, "etf_holdings_snapshot.json")

ACTIVE_ETFS = ["00981A", "00980A", "00982A"]
ADD_THRESHOLD = 0.10     # 張數 +10% 視為加碼
REDUCE_THRESHOLD = -0.10 # 張數 -10% 視為減碼

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


# ── ① T86 投信買賣超 ──────────────────────────────
def fetch_t86(max_back=6):
    """走回最多 6 天找最近交易日的投信買賣超。回傳 ({code: 股數}, yyyymmdd)"""
    d = dt.date.today()
    for _ in range(max_back):
        ds = d.strftime("%Y%m%d")
        url = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
               f"?date={ds}&selectType=ALLBUT0999&response=json")
        try:
            j = json.loads(_get(url))
            if j.get("stat") == "OK" and j.get("data"):
                fields = j.get("fields", [])
                idx = next(i for i, f in enumerate(fields) if "投信買賣超" in f)
                out = {}
                for row in j["data"]:
                    code = row[0].strip()
                    try:
                        out[code] = int(row[idx].replace(",", ""))
                    except Exception:
                        pass
                print(f"  [T86] {ds}: {len(out)} 檔（投信買超 "
                      f"{sum(1 for v in out.values() if v > 0)} 檔）")
                return out, ds
        except Exception as e:
            print(f"  [T86] {ds}: {type(e).__name__}")
        d -= dt.timedelta(days=1)
        time.sleep(1.2)
    return {}, None


# ── ② 主動 ETF 持股 ──────────────────────────────
def load_name_code_map():
    """tw_universe.json name→code（正規化：去 * 與空白）"""
    m = {}
    try:
        with io.open(os.path.join(ROOT, "tw_universe.json"), encoding="utf-8") as f:
            u = json.load(f)
        for s in u.get("stocks", []):
            key = s["name"].replace("*", "").replace(" ", "").strip()
            m[key] = s["code"]
    except Exception as e:
        print(f"  [map] tw_universe 載入失敗: {e}")
    return m


def fetch_etf_holdings(etf_id, name_map):
    """MoneyDJ 持股表 → {code: {name, shares, weight}}（code 對不到者以 name 為 key）"""
    url = f"https://www.moneydj.com/etf/x/basic/basic0007a.xdjhtm?etfid={etf_id}.TW"
    try:
        body = _get(url).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [{etf_id}] 抓取失敗: {type(e).__name__}")
        return None
    holdings = {}
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    for r in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if len(cells) < 3 or not cells[0]:
            continue
        name, shares_s, weight_s = cells[0], cells[1], cells[2]
        try:
            shares = float(shares_s.replace(",", ""))
            weight = float(weight_s.replace(",", ""))
        except Exception:
            continue
        if weight <= 0 or weight > 100:
            continue
        key = name.replace("*", "").replace(" ", "").strip()
        code = name_map.get(key)
        holdings[code or name] = {"name": name.strip(), "shares": shares,
                                  "weight": weight}
    if holdings:
        print(f"  [{etf_id}] {len(holdings)} 檔持股")
        return holdings
    print(f"  [{etf_id}] 解析 0 檔（版型可能變更）")
    return None


def diff_holdings(old, new):
    """比對兩日持股 → {new, added, reduced, removed}，元素含 name/weight"""
    changes = {"new": [], "added": [], "reduced": [], "removed": []}
    if not old or not new:
        return changes
    for code, cur in new.items():
        prev = old.get(code)
        if prev is None:
            changes["new"].append({"code": code, **cur})
        else:
            ps = prev.get("shares") or 0
            if ps > 0:
                chg = (cur["shares"] - ps) / ps
                if chg >= ADD_THRESHOLD:
                    changes["added"].append({"code": code, **cur,
                                             "chg_pct": round(chg * 100, 1)})
                elif chg <= REDUCE_THRESHOLD:
                    changes["reduced"].append({"code": code, **cur,
                                               "chg_pct": round(chg * 100, 1)})
    for code, prev in old.items():
        if code not in new:
            changes["removed"].append({"code": code, **prev})
    return changes


def main():
    today = dt.date.today().isoformat()
    print(f"[1/3] T86 投信買賣超...")
    sitc, t86_date = fetch_t86()

    print(f"[2/3] 主動 ETF 持股（{'/'.join(ACTIVE_ETFS)}）...")
    name_map = load_name_code_map()
    old_snap = {}
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with io.open(SNAPSHOT_PATH, encoding="utf-8") as f:
                old_snap = json.load(f)
        except Exception:
            old_snap = {}

    etfs = {}
    all_changes = {}
    for etf in ACTIVE_ETFS:
        h = fetch_etf_holdings(etf, name_map)
        time.sleep(1.5)
        if h is None:
            # 抓失敗 → 沿用昨日快照（避免誤判整批剔除）
            h = (old_snap.get("etfs") or {}).get(etf, {})
            print(f"  [{etf}] 沿用前次快照 {len(h)} 檔")
            etfs[etf] = h
            all_changes[etf] = {"new": [], "added": [], "reduced": [],
                                "removed": [], "stale": True}
            continue
        etfs[etf] = h
        old_h = (old_snap.get("etfs") or {}).get(etf, {})
        # 同日重跑不做 diff（避免自比）
        if old_snap.get("date") == today:
            all_changes[etf] = (old_snap.get("changes") or {}).get(
                etf, {"new": [], "added": [], "reduced": [], "removed": []})
        else:
            all_changes[etf] = diff_holdings(old_h, h)

    # 個股 → 哪些 ETF 持有（consensus）
    stock_etf = {}
    for etf, h in etfs.items():
        for code, v in h.items():
            e = stock_etf.setdefault(code, {"name": v["name"], "etfs": {}})
            e["etfs"][etf] = v["weight"]
    for code, e in stock_etf.items():
        e["consensus"] = len(e["etfs"])

    print(f"[3/3] 輸出 signal...")
    n_consensus2 = sum(1 for e in stock_etf.values() if e["consensus"] >= 2)
    print(f"  ETF 持股聯集 {len(stock_etf)} 檔（≥2 檔共識 {n_consensus2} 檔）")
    for etf, c in all_changes.items():
        if c.get("new") or c.get("removed"):
            print(f"  [{etf}] 🆕新增 {[x['name'] for x in c['new']]}"
                  f" ❌剔除 {[x['name'] for x in c['removed']]}")

    snapshot = {"date": today, "etfs": etfs, "changes": all_changes}
    with io.open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    signal = {
        "date": today,
        "t86_date": t86_date,
        "sitc_net": sitc,          # {code: 股數}（正=買超）
        "etf_stock": stock_etf,    # {code: {name, etfs:{id:weight}, consensus}}
        "etf_changes": all_changes,
    }
    with io.open(SIGNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(signal, f, ensure_ascii=False, indent=1)
    print(f"💾 已輸出 {SIGNAL_PATH}")


if __name__ == "__main__":
    main()
