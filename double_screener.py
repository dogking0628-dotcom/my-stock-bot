# -*- coding: utf-8 -*-
"""
3年翻倍股初篩器 v1 — Quality × Growth × Revision代理 × 估值（2026-09-04 用戶框架量化子集）
═════════════════════════════════════════════════
漏斗（FinMind 免費額度 → 可續跑，progress 檔自動存）：
  S0 市值 ≥100億（~507 檔，零 API）
  S1 月營收動能：近3月YoY均 ≥15%，或 ≥5% 且加速 ≥10pp（1 call/檔）
  S2 Quality：近4季ROE ≥12%、EPS 近4季>前4季、毛利率趨勢、股本膨脹（2 call/檔）
  S3 估值：PE 5年百分位、PEG proxy = PE / EPS成長（1 call/檔）
  S4 技術位：距 200MA / 距 2y 高（yfinance 批次，零額度）
輸出：double_candidates.md（評分卡）+ double_screener_result.json + csv
評分（量化子集 60 分）：營收動能20 + Quality 25 + 估值 15；質化 40 分（治理/護城河/Re-rating）留給用戶。
用法：python double_screener.py [--resume]（額度卡住會自動存檔退出，再跑即續）
"""
import sys, io, os, json, time, urllib.request, urllib.error
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT); sys.path.insert(0, ROOT)
from industry_map_loader import get_industry

PROG = "double_screener_progress.json"
MIN_MCAP = 100
API = "https://api.finmindtrade.com/api/v4/data"
SLEEP = 0.35
quota_dead = False


def fm(dataset, sid, start):
    """FinMind 抓取；402/429 連續失敗視為額度用盡"""
    global quota_dead
    u = f"{API}?dataset={dataset}&data_id={sid}&start_date={start}"
    for attempt in range(2):
        try:
            j = json.loads(urllib.request.urlopen(u, timeout=25).read())
            time.sleep(SLEEP)
            return j.get("data") or []
        except urllib.error.HTTPError as e:
            if e.code in (402, 429):
                if attempt == 0:
                    print("  ⏳ 額度受限，休息 65s 再試...", file=sys.stderr)
                    time.sleep(65); continue
                quota_dead = True
                return None
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return []


def load_prog():
    if os.path.exists(PROG):
        return json.load(io.open(PROG, encoding="utf-8"))
    return {"s1": {}, "s2": {}, "s3": {}}


def save_prog(p):
    json.dump(p, io.open(PROG, "w", encoding="utf-8"), ensure_ascii=False)


def stage1(sid, prog):
    """月營收動能：近3月 YoY 均值 / 前3月 YoY 均值 / 12m 營收成長"""
    if sid in prog["s1"]:
        return prog["s1"][sid]
    rows = fm("TaiwanStockMonthRevenue", sid, (dt.date.today() - dt.timedelta(days=430 + 370)).isoformat())
    if rows is None: return None
    rev = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in rows}
    keys = sorted(rev)
    out = {"ok": False}
    if len(keys) >= 16:
        def yoy(k):
            prev = (k[0] - 1, k[1])
            return (rev[k] / rev[prev] - 1) * 100 if rev.get(prev) else None
        yoys = [(k, yoy(k)) for k in keys[-6:]]
        yoys = [(k, v) for k, v in yoys if v is not None]
        if len(yoys) >= 6:
            y3 = sum(v for _, v in yoys[-3:]) / 3
            yp3 = sum(v for _, v in yoys[:3]) / 3
            r12 = sum(rev[k] for k in keys[-12:]); r12p = sum(rev[k] for k in keys[-24:-12]) if len(keys) >= 24 else None
            out = {"ok": True, "yoy3": round(y3, 1), "yoy_prev3": round(yp3, 1),
                   "accel": round(y3 - yp3, 1),
                   "rev12m_g": round((r12 / r12p - 1) * 100, 1) if r12p else None}
    prog["s1"][sid] = out; save_prog(prog)
    return out


def _pick(rows, names):
    """long-format 財報取值：{date: value}（type/origin_name 模糊匹配）"""
    out = {}
    for r in rows:
        ty = r.get("type") or ""
        if ty.endswith("_per"):        # FinMind 每欄都有 _per 佔比版，會蓋掉金額
            continue
        t = ty + "|" + (r.get("origin_name") or "")
        if any(n in t for n in names):
            out[r["date"]] = r["value"]
    return out


def stage2(sid, prog):
    """Quality：ROE(近4季)、EPS 4q 動能、毛利率趨勢、股本膨脹"""
    if sid in prog["s2"]:
        return prog["s2"][sid]
    start = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    inc = fm("TaiwanStockFinancialStatements", sid, start)
    if inc is None: return None
    bal = fm("TaiwanStockBalanceSheet", sid, start)
    if bal is None: return None
    eps = _pick(inc, ["EPS", "基本每股盈餘"])
    ni = _pick(inc, ["IncomeAfterTaxes", "本期淨利"])
    revq = _pick(inc, ["Revenue|", "營業收入"])
    gp = _pick(inc, ["GrossProfit", "營業毛利"])
    eq = _pick(bal, ["權益總額", "權益總計", "TotalEquity|", "Total equity"])
    cap = _pick(bal, ["普通股股本", "OrdinaryShare|"])
    ds = sorted(eps)
    out = {"ok": False}
    if len(ds) >= 8:
        e4 = sum(eps[d] for d in ds[-4:]); e4p = sum(eps[d] for d in ds[-8:-4])
        ni4 = sum(ni.get(d, 0) for d in ds[-4:])
        eqs = [eq[d] for d in sorted(eq)[-4:] if d in eq]
        roe = ni4 / (sum(eqs) / len(eqs)) * 100 if eqs and sum(eqs) > 0 else None
        def gm(dd):
            r, g = revq.get(dd), gp.get(dd)
            return g / r * 100 if r and g is not None and r > 0 else None
        gms = [gm(d) for d in ds[-8:]]
        gms = [x for x in gms if x is not None]
        gm_now = sum(gms[-4:]) / len(gms[-4:]) if len(gms) >= 4 else None
        gm_prev = sum(gms[:-4]) / len(gms[:-4]) if len(gms) >= 8 else None
        caps = sorted(cap)
        cap_chg = (cap[caps[-1]] / cap[caps[0]] - 1) * 100 if len(caps) >= 2 and cap[caps[0]] else 0
        out = {"ok": True, "eps4": round(e4, 2), "eps4_prev": round(e4p, 2),
               "eps_g": round((e4 / e4p - 1) * 100, 1) if e4p > 0 else (999 if e4 > 0 else None),
               "roe": round(roe, 1) if roe is not None else None,
               "gm_now": round(gm_now, 1) if gm_now is not None else None,
               "gm_chg": round(gm_now - gm_prev, 1) if gm_now is not None and gm_prev is not None else None,
               "cap_chg": round(cap_chg, 1)}
    prog["s2"][sid] = out; save_prog(prog)
    return out


def stage3(sid, prog):
    """估值：現在 PE、PE 5y 百分位"""
    if sid in prog["s3"]:
        return prog["s3"][sid]
    rows = fm("TaiwanStockPER", sid, (dt.date.today() - dt.timedelta(days=365 * 5)).isoformat())
    if rows is None: return None
    pes = [r["PER"] for r in rows if r.get("PER") and 0 < r["PER"] < 300]
    out = {"ok": False}
    if len(pes) >= 200:
        cur = pes[-1]
        pct = sum(1 for x in pes if x <= cur) / len(pes) * 100
        out = {"ok": True, "pe": round(cur, 1), "pe_pct5y": round(pct, 0)}
    prog["s3"][sid] = out; save_prog(prog)
    return out


def score(m):
    """量化 60 分：營收動能 20 + Quality 25 + 估值 15"""
    s = 0; why = []
    y3, ac = m.get("yoy3") or 0, m.get("accel") or 0
    if y3 >= 30: s += 12
    elif y3 >= 15: s += 8
    elif y3 >= 5: s += 4
    if ac >= 10: s += 8; why.append(f"營收加速+{ac:.0f}pp")
    elif ac >= 0: s += 4
    roe = m.get("roe")
    if roe is not None:
        if roe >= 20: s += 10
        elif roe >= 15: s += 8
        elif roe >= 12: s += 5
    eg = m.get("eps_g")
    if eg is not None:
        if eg >= 40: s += 10; why.append(f"EPS+{eg:.0f}%")
        elif eg >= 20: s += 7
        elif eg > 0: s += 3
    gc = m.get("gm_chg")
    if gc is not None and gc > 0.5: s += 3; why.append("毛利率↑")
    if abs(m.get("cap_chg") or 0) < 10: s += 2
    pe, pp = m.get("pe"), m.get("pe_pct5y")
    peg = pe / eg if pe and eg and eg > 0 else None
    m["peg"] = round(peg, 2) if peg is not None and peg < 99 else None
    if peg is not None:
        if peg < 0.8: s += 8; why.append(f"PEG{peg:.1f}")
        elif peg < 1.2: s += 6
        elif peg < 1.5: s += 3
    if pp is not None:
        if pp <= 40: s += 7
        elif pp <= 70: s += 4
        else: why.append(f"PE位階{pp:.0f}%高")
    return s, why


def main():
    mc = json.load(io.open("marketcap_cache.json", encoding="utf-8"))
    names = {s["code"]: s["name"] for s in json.load(io.open("tw_universe.json", encoding="utf-8"))["stocks"]}
    uni = sorted([c for c, v in mc.items() if isinstance(v, (int, float)) and v >= MIN_MCAP],
                 key=lambda c: -mc[c])
    print(f"S0 市值≥{MIN_MCAP}億: {len(uni)} 檔")
    prog = load_prog()

    # S1 月營收
    s1_pass = []
    for i, sid in enumerate(uni):
        r = stage1(sid, prog)
        if quota_dead:
            print(f"⛔ 額度用盡於 S1 第 {i}/{len(uni)} 檔，已存進度，之後重跑續抓"); break
        if r and r.get("ok") and (r["yoy3"] >= 15 or (r["yoy3"] >= 5 and r["accel"] >= 10)):
            s1_pass.append(sid)
        if i % 50 == 0:
            print(f"  S1 {i}/{len(uni)}... 通過 {len(s1_pass)}")
    done_s1 = sum(1 for c in uni if c in prog["s1"])
    s1_pass = [c for c in uni if c in prog["s1"] and prog["s1"][c].get("ok")
               and (prog["s1"][c]["yoy3"] >= 15 or (prog["s1"][c]["yoy3"] >= 5 and prog["s1"][c]["accel"] >= 10))]
    print(f"S1 月營收動能: 已掃 {done_s1}/{len(uni)} → 通過 {len(s1_pass)} 檔")

    # S2 Quality
    for i, sid in enumerate(s1_pass):
        if quota_dead: break
        stage2(sid, prog)
        if i % 20 == 0: print(f"  S2 {i}/{len(s1_pass)}...")
    s2_pass = [c for c in s1_pass if c in prog["s2"] and prog["s2"][c].get("ok")
               and (prog["s2"][c].get("roe") or 0) >= 12
               and (prog["s2"][c].get("eps_g") or -1) > 0]
    print(f"S2 Quality: 通過 {len(s2_pass)} 檔")

    # S3 估值
    for i, sid in enumerate(s2_pass):
        if quota_dead: break
        stage3(sid, prog)
    print(f"S3 估值: 完成 {sum(1 for c in s2_pass if c in prog['s3'])}/{len(s2_pass)}")

    # S4 技術位（yfinance 零額度）
    tech = {}
    if s2_pass:
        try:
            import yfinance as yf
            df = yf.download(" ".join(f"{c}.TW" for c in s2_pass), period="2y",
                             auto_adjust=True, progress=False, threads=True, group_by="ticker")
            for c in s2_pass:
                try:
                    cl = df[f"{c}.TW"]["Close"].dropna()
                    if len(cl) < 200: continue
                    px = float(cl.iloc[-1])
                    tech[c] = {"px": px,
                               "vs200": round((px / float(cl.rolling(200).mean().iloc[-1]) - 1) * 100, 1),
                               "vs_hi2y": round((px / float(cl.max()) - 1) * 100, 1)}
                except Exception: pass
        except Exception as e:
            print("S4 tech fail", e)

    # 匯總評分
    cands = []
    for c in s2_pass:
        m = {}
        m.update(prog["s1"].get(c) or {}); m.update(prog["s2"].get(c) or {}); m.update(prog["s3"].get(c) or {})
        m.update(tech.get(c) or {})
        sc, why = score(m)
        cands.append({"ticker": c, "name": names.get(c, "?"), "industry": get_industry(c),
                      "mcap": mc[c], "score60": sc, "why": why, **{k: m.get(k) for k in
                      ("yoy3", "accel", "rev12m_g", "roe", "eps_g", "gm_now", "gm_chg", "cap_chg",
                       "pe", "pe_pct5y", "peg", "px", "vs200", "vs_hi2y")}})
    cands.sort(key=lambda x: -x["score60"])
    json.dump({"date": dt.date.today().isoformat(), "universe": len(uni), "s1_pass": len(s1_pass),
               "s2_pass": len(s2_pass), "candidates": cands},
              io.open("double_screener_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    L = [f"# 3年翻倍股初篩（量化 60 分）｜{dt.date.today()}",
         f"漏斗：市值≥{MIN_MCAP}億 {len(uni)} → 月營收動能 {len(s1_pass)} → Quality {len(s2_pass)}",
         "質化 40 分（治理/護城河/Re-rating/三情境）請帶到 ChatGPT 深挖。", "",
         "| # | 代號 | 名稱 | 產業 | 分 | 市值億 | 營收YoY3m | 加速 | ROE | EPS成長 | 毛利Δ | PE | PE位階 | PEG | 距200MA | 距2y高 | 亮點 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(cands[:40], 1):
        L.append(f"| {i} | {x['ticker']} | {x['name']} | {(x['industry'] or '?')[:4]} | **{x['score60']}** | "
                 f"{x['mcap']:.0f} | {x['yoy3']}% | {x['accel']:+}pp | {x['roe']}% | {x['eps_g']}% | "
                 f"{x['gm_chg']} | {x['pe']} | {x['pe_pct5y']}% | {x['peg']} | {x['vs200']}% | {x['vs_hi2y']}% | "
                 f"{'、'.join(x['why'][:3])} |")
    io.open("double_candidates.md", "w", encoding="utf-8", newline="\n").write("\n".join(L))
    # CSV
    import csv as _csv
    with io.open("double_candidates.csv", "w", encoding="utf-8-sig", newline="") as f:
        wcsv = _csv.DictWriter(f, fieldnames=list(cands[0].keys()) if cands else ["ticker"])
        wcsv.writeheader()
        for x in cands: wcsv.writerow({k: ("、".join(v) if isinstance(v, list) else v) for k, v in x.items()})
    print(f"\n💾 double_candidates.md / .csv / double_screener_result.json（前40檔）")
    print(f"Top10: " + "、".join(f"{x['ticker']}{x['name']}({x['score60']})" for x in cands[:10]))
    if quota_dead:
        print("⚠️ 本輪額度用盡，結果為部分資料——再跑一次 python double_screener.py 會自動續抓")


if __name__ == "__main__":
    main()
