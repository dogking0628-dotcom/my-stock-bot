# -*- coding: utf-8 -*-
"""
從證交所 / 櫃買 OpenAPI 抓全市場「股票 → 產業別」對應表
covered: TSE 1084 + OTC 884 ≈ 1968 檔（與 Shioaji 全市場吻合）
"""
import os, sys, json, urllib.request

CACHE_PATH = os.path.join(os.path.dirname(__file__), "tw_industry_map.json")
TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

# 證交所/櫃買產業代碼 → 中文名（共用，TPEX 用同一套）
IND_CODE_TO_NAME = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "07": "化學", "08": "玻璃陶瓷",
    "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車",
    "13": "電子業", "14": "建材營造", "15": "航運", "16": "觀光",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
    "21": "化學工業", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體", "25": "電腦及週邊", "26": "光電",
    "27": "通信網路", "28": "電子零組件", "29": "電子通路",
    "30": "資訊服務", "31": "其他電子", "32": "文化創意",
    "33": "觀光餐旅", "34": "數位雲端", "35": "運動休閒",
    "36": "居家生活", "80": "管理股票", "91": "存託憑證",
}

def _fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _norm(d):
    """嘗試多種欄位名取代碼+產業"""
    code = (d.get("公司代號") or d.get("SecuritiesCompanyCode")
            or d.get("Code") or d.get("證券代號"))
    ind_code = (d.get("產業別") or d.get("SecuritiesIndustryCode")
                or d.get("IndustryCode"))
    return code, ind_code

def fetch_industry_map():
    out = {}
    for url in [TWSE_URL, TPEX_URL]:
        try:
            data = _fetch_json(url)
            for item in data:
                code, ind_code = _norm(item)
                if not code or not ind_code: continue
                code = str(code).strip().zfill(4)
                ind_code = str(ind_code).strip().zfill(2)
                ind_name = IND_CODE_TO_NAME.get(ind_code, f"代碼{ind_code}")
                out[code] = ind_name
            print(f"[ind_map] {url}: 累計 {len(out)} 檔", file=sys.stderr)
        except Exception as e:
            print(f"[ind_map] FAIL {url}: {e}", file=sys.stderr)
    return out

_cached_map = None
def load_map(refresh=False):
    global _cached_map
    if _cached_map is not None and not refresh:
        return _cached_map
    if not refresh and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cached_map = json.load(f)
                return _cached_map
        except Exception: pass
    m = fetch_industry_map()
    if m:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        _cached_map = m
    return _cached_map or {}

def get_industry(code):
    """證交所官方產業別；未上市/未上櫃回 None"""
    return load_map().get(str(code).strip().zfill(4))


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    m = load_map(refresh=True)
    print(f"\n總計：{len(m)} 檔")
    # 統計族群分布
    from collections import Counter
    cnt = Counter(m.values())
    print("\n族群分布（前 15）：")
    for ind, n in cnt.most_common(15):
        print(f"  {ind}: {n} 檔")
    # 抽樣驗證
    print("\n抽樣驗證：")
    for code in ["2330", "2454", "2317", "8299", "3711", "2882", "2412", "3008"]:
        print(f"  {code} = {m.get(code)}")
