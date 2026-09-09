#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
警示股守門 alert_guard.py（2026-09-09，大立光處置分盤 -49.6 萬事件的制度化回應）
═════════════════════════════════════════════════
① 官方名單：證交所 注意股(notice)/處置股(punish) 當日 API → cache
② 預估公式（證交所「公布注意交易資訊」漲幅條款，可自算）：
   第1款  近 6 營業日累積漲幅 > +32%（跌 -32% 同）
   第2款  近 30 營業日累積 > +100%
   第3款  近 60 營業日累積 > +130%
   第4款  近 90 營業日累積 > +160%
   （週轉率/本益比/量增條款需股本與細部資料，v1 未涵蓋——漲幅四款已覆蓋動能股 9 成觸發原因）
③ 處置預估：注意「累計次數」欄——10 營業日內 3 次注意→處置；累計 ≥2 標「危險邊緣」
用法：from alert_guard import load_alert_lists, classify
"""
import io, os, json, time, urllib.request
import datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "alert_lists_cache.json")
UA = {"User-Agent": "Mozilla/5.0"}
THRESH = [(6, 32.0), (30, 100.0), (60, 130.0), (90, 160.0)]
NEAR_PP = 6.0   # 距觸發線 6pp 內就提前警告


def load_alert_lists(max_age_hours=20):
    """注意/處置名單（含次數與處置起迄）；cache 避免重打"""
    try:
        c = json.load(io.open(CACHE, encoding="utf-8"))
        if time.time() - c.get("ts", 0) < max_age_hours * 3600:
            return c
    except Exception:
        pass
    out = {"ts": time.time(), "notice": {}, "punish": {}}
    try:
        j = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://www.twse.com.tw/rwd/zh/announcement/notice?response=json", headers=UA), timeout=20).read())
        f = j.get("fields") or []
        ic, inum = f.index("證券代號"), f.index("累計次數")
        for row in j.get("data") or []:
            code = str(row[ic]).strip()
            try: n = int(row[inum])
            except Exception: n = 1
            out["notice"][code] = max(n, out["notice"].get(code, 0))
    except Exception:
        pass
    try:
        j = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=UA), timeout=20).read())
        f = j.get("fields") or []
        ic, it = f.index("證券代號"), f.index("處置起迄時間")
        for row in j.get("data") or []:
            code = str(row[ic]).strip()
            span = str(row[it]).strip()
            # 只留仍在處置期內的（起迄格式 115/09/05～115/09/18）
            try:
                end_s = span.split("～")[-1].split("~")[-1].strip()
                y, m, d2 = end_s.split("/")
                end_d = dt.date(int(y) + 1911, int(m), int(d2))
                if end_d >= dt.date.today():
                    out["punish"][code] = span
            except Exception:
                out["punish"][code] = span
    except Exception:
        pass
    try:
        json.dump(out, io.open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return out


def cum_returns(closes):
    """closes: list/array 最新在尾。回 {6:pct,30:...,60:...,90:...}"""
    out = {}
    n = len(closes)
    for win, _ in THRESH:
        if n > win and closes[-1 - win] > 0:
            out[win] = (closes[-1] / closes[-1 - win] - 1) * 100
    return out


def classify(code, closes, lists=None):
    """回傳 alert dict 或 None。
    level: PUNISH(處置中) / NOTICE(已列注意) / NEAR(距注意線<6pp) """
    lists = lists or {}
    if code in (lists.get("punish") or {}):
        return {"level": "PUNISH", "msg": f"🚫處置中({lists['punish'][code]})分盤交易,流動性差,勿新倉"}
    cums = cum_returns(list(closes))
    hits, nears = [], []
    for win, th in THRESH:
        v = cums.get(win)
        if v is None: continue
        if v > th: hits.append(f"{win}日+{v:.0f}%>{th:.0f}%")
        elif v > th - NEAR_PP: nears.append(f"{win}日+{v:.0f}%(再{th - v:.1f}pp觸注意)")
    n_notice = (lists.get("notice") or {}).get(code, 0)
    if n_notice:
        tail = "，再1次注意→處置" if n_notice >= 2 else ""
        return {"level": "NOTICE", "msg": f"⚠️已列注意(累計{n_notice}次{tail})" + ("；" + hits[0] if hits else "")}
    if hits:
        return {"level": "NOTICE_EST", "msg": "⚠️達注意標準(" + hits[0] + ")今晚恐公告"}
    if nears:
        return {"level": "NEAR", "msg": "📈接近注意線:" + nears[0]}
    return None


if __name__ == "__main__":
    import sys
    if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
        try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        except Exception: pass
    L = load_alert_lists(max_age_hours=0)
    print(f"注意股 {len(L['notice'])} 檔: {dict(list(L['notice'].items())[:10])}")
    print(f"處置中 {len(L['punish'])} 檔: {list(L['punish'].items())[:6]}")
