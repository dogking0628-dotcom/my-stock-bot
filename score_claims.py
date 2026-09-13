# -*- coding: utf-8 -*-
"""
論點自動對帳 + 講者命中率記分板 + 回測候選彙整。

讀 data/claims.jsonl（analyze_transcript.py 產出），對 route=due_check 且到期的論點，
用 FinMind 日K（免封鎖；token 選填）依 check 規格自動判定 hit / miss，寫回 jsonl，
再產出 analyst_scorecard.md：
  1. 講者 × 頻道 命中率（n / hit / miss / pending / 命中率 / 平均超額報酬）
  2. 依論點類型（短線預測、目標價、指數）分開統計
  3. 已對帳明細（含實際數字）
  4. 回測候選規則彙整（route=backtest，附 rule 規格，供 backtest_*.py 樣板使用）

用法：
  python score_claims.py             # 對帳到期論點 + 產記分板
  python score_claims.py --dry       # 只列出會對帳哪些，不打 API
  python score_claims.py --rescore   # 已判定的也重算
  python score_claims.py --notify    # 有新判定就推 LINE

check 規格（由分析層產生）：
  metric: close|high|low|pct_change|limit_up|new_high_250|new_low_250
  op: > >= < <=   value: 數字   window: on_due（到期日當天）| any_day（區間內任一日）| all_days（區間內每一日）
  base_date: pct_change 的基準收盤日（缺則用影片日）
評分區間 = 影片日之後第一個交易日 ～ 到期日（含）。
"""
import sys, io, os, re, json, argparse, datetime as dt, time, urllib.request, urllib.parse
from pathlib import Path
from collections import defaultdict

if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CLAIMS_JSONL = DATA / "claims.jsonl"
SCORECARD = ROOT / "analyst_scorecard.md"
PRICE_CACHE = DATA / "checkpoints" / "price_cache.json"
UA = {"User-Agent": "Mozilla/5.0"}
LIMIT_UP_PCT = 9.5
INDEX_IDS = {"TAIEX": "TAIEX", "TWII": "TAIEX", "TPEX": "TPEx", "OTC": "TPEx"}

def log(m): print(f"[score] {m}", flush=True)

def finmind_token():
    t = os.environ.get("FINMIND_TOKEN", "")
    if not t:
        try: t = (ROOT / "finmind_token.txt").read_text(encoding="utf-8").strip()
        except Exception: t = ""
    return t


# ────────────────────────────────────────────────────────────────────────────
# 價格
# ────────────────────────────────────────────────────────────────────────────
_cache = None
def load_cache():
    global _cache
    if _cache is None:
        try: _cache = json.loads(PRICE_CACHE.read_text(encoding="utf-8"))
        except Exception: _cache = {}
    return _cache

def save_cache():
    if _cache is not None:
        PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PRICE_CACHE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")

def fetch_prices(ticker, start, end):
    """回傳 [{date, open, high, low, close}]，升冪。FinMind TaiwanStockPrice（指數用 TAIEX/TPEx）。"""
    data_id = INDEX_IDS.get(ticker.upper(), ticker)
    key = f"{data_id}:{start}:{end}"
    cache = load_cache()
    if key in cache:
        return cache[key]
    p = {"dataset": "TaiwanStockPrice", "data_id": data_id, "start_date": start, "end_date": end}
    tok = finmind_token()
    if tok: p["token"] = tok
    url = "https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(p)
    j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read())
    if j.get("status") != 200:
        raise RuntimeError(f"FinMind {j.get('msg')}")
    rows = [{"date": r["date"], "open": r["open"], "high": r["max"], "low": r["min"], "close": r["close"]}
            for r in j.get("data", []) if r.get("close") not in (None, 0)]
    rows.sort(key=lambda r: r["date"])
    cache[key] = rows
    return rows


# ────────────────────────────────────────────────────────────────────────────
# 判定
# ────────────────────────────────────────────────────────────────────────────
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}

def evaluate(claim, price_fn=None):
    """回傳 (status, detail)。status ∈ hit / miss / pending / invalid"""
    price_fn = price_fn or fetch_prices        # 呼叫時才綁定，測試可換假價格
    chk = claim.get("check") or {}
    tk, metric, op, val = str(chk.get("ticker") or "").upper(), chk.get("metric"), chk.get("op"), chk.get("value")
    window = chk.get("window") or "any_day"
    due = claim.get("due_date"); vdate = claim.get("video_date")
    if not (tk and metric and due and vdate):
        return "invalid", "check 欄位不全"
    if due > dt.date.today().isoformat():
        return "pending", f"到期 {due}"
    base_date = chk.get("base_date") or vdate
    lookback_start = (dt.date.fromisoformat(min(base_date, vdate)) - dt.timedelta(days=400 if metric in ("new_high_250", "new_low_250") else 10)).isoformat()
    rows = price_fn(tk, lookback_start, due)
    if not rows:
        return "invalid", "無價格資料"
    # 基準收盤：base_date 當天或之前最近一個交易日
    base_rows = [r for r in rows if r["date"] <= base_date]
    base_close = base_rows[-1]["close"] if base_rows else None
    period = [r for r in rows if r["date"] > vdate and r["date"] <= due]
    if not period:
        return "pending", "區間內尚無交易日"
    if window == "on_due":
        period = period[-1:]

    def series():
        prev = base_close
        out = []
        hist = [r["close"] for r in rows if r["date"] <= vdate]
        for r in period:
            if metric == "close": v = r["close"]
            elif metric == "high": v = r["high"]
            elif metric == "low": v = r["low"]
            elif metric == "pct_change": v = (r["close"] / base_close - 1) * 100 if base_close else None
            elif metric == "limit_up":
                v = (r["close"] / prev - 1) * 100 if prev else None
            elif metric == "new_high_250":
                v = r["close"]; thr = max(hist[-250:]) if hist else None
                out.append((r["date"], v, thr)); hist.append(r["close"]); prev = r["close"]; continue
            elif metric == "new_low_250":
                v = r["close"]; thr = min(hist[-250:]) if hist else None
                out.append((r["date"], v, thr)); hist.append(r["close"]); prev = r["close"]; continue
            else:
                v = None
            out.append((r["date"], v, None)); prev = r["close"]
        return out

    pts = series()
    if metric == "limit_up":
        hits = [d for d, v, _ in pts if v is not None and v >= LIMIT_UP_PCT]
        best = max((v for _, v, _ in pts if v is not None), default=None)
        return ("hit" if hits else "miss"), f"區間最大單日漲幅 {best:.1f}%" + (f"，漲停日 {hits[0]}" if hits else "")
    if metric in ("new_high_250", "new_low_250"):
        cmp = (lambda v, t: v > t) if metric == "new_high_250" else (lambda v, t: v < t)
        hits = [d for d, v, t in pts if t is not None and cmp(v, t)]
        last = pts[-1]
        return ("hit" if hits else "miss"), f"門檻 {last[2]}，區間收盤 {min(v for _, v, _ in pts)}~{max(v for _, v, _ in pts)}" + (f"，達成日 {hits[0]}" if hits else "")
    if op not in OPS or val is None:
        return "invalid", "op/value 缺"
    vals = [(d, v) for d, v, _ in pts if v is not None]
    if not vals:
        return "invalid", "無法計算"
    ok = [d for d, v in vals if OPS[op](v, val)]
    if window == "all_days":
        status = "hit" if len(ok) == len(vals) else "miss"
    else:
        status = "hit" if ok else "miss"
    fmt = (lambda x: f"{x:+.1f}%") if metric == "pct_change" else (lambda x: f"{x:g}")
    extreme = max(vals, key=lambda x: x[1]) if op in (">", ">=") else min(vals, key=lambda x: x[1])
    detail = f"條件 {metric} {op} {val}；到期日值 {fmt(vals[-1][1])}，區間極值 {fmt(extreme[1])}（{extreme[0]}）" + (f"，達成日 {ok[0]}" if ok and window != "all_days" else "")
    if base_close is not None and metric != "pct_change":
        detail += f"；基準收盤 {base_close:g}"
    return status, detail


# ────────────────────────────────────────────────────────────────────────────
# 記分板
# ────────────────────────────────────────────────────────────────────────────
def read_claims():
    if not CLAIMS_JSONL.exists(): return []
    out = []
    for ln in CLAIMS_JSONL.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try: out.append(json.loads(ln))
            except json.JSONDecodeError: pass
    return out

def write_claims(rows):
    tmp = CLAIMS_JSONL.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, CLAIMS_JSONL)

def pct(h, n): return f"{h / n * 100:.0f}%" if n else "–"

def build_scorecard(rows):
    today = dt.date.today().isoformat()
    L = [f"# 分析師 / 財經網紅 論點記分板（自動對帳，{today}）", "",
         "> 資料：data/claims.jsonl（analyze_transcript.py 萃取）→ score_claims.py 用 FinMind 日K 依 check 規格判定。",
         "> 只計 route=due_check 且可機器對帳的預測；基本面/心法/無期限論點不計分。命中率 n<10 僅供參考。",
         "> 用途：半年後決定「誰的話值得進資訊層」；**任何人的命中率都不構成下單理由**。", ""]
    due = [r for r in rows if r.get("route") == "due_check"]
    graded = [r for r in due if r.get("status") in ("hit", "miss")]
    L += [f"論點總數 {len(rows)}｜掛對帳 {len(due)}｜已判定 {len(graded)}（命中 {sum(r['status']=='hit' for r in graded)}）｜待到期 {sum(r.get('status')=='pending' for r in due)}｜無法判定 {sum(r.get('status')=='invalid' for r in due)}", ""]

    def table(title, keyfn):
        agg = defaultdict(lambda: {"n": 0, "hit": 0, "miss": 0, "pending": 0, "invalid": 0, "videos": set()})
        for r in due:
            a = agg[keyfn(r)]; a["n"] += 1; a[r.get("status", "pending")] = a.get(r.get("status", "pending"), 0) + 1; a["videos"].add(r.get("video_id"))
        L.extend(["", f"## {title}", "", "| 講者/來源 | 預測數 | 命中 | 落空 | 待到期 | 命中率（已判定） | 影片數 |", "|---|---|---|---|---|---|---|"])
        for k, a in sorted(agg.items(), key=lambda kv: (-(kv[1]["hit"] + kv[1]["miss"]), kv[0])):
            g = a["hit"] + a["miss"]
            L.append(f"| {k} | {a['n']} | {a['hit']} | {a['miss']} | {a['pending']} | {pct(a['hit'], g)}{'' if g >= 10 else ' (n<10)'} | {len(a['videos'])} |")
    table("依講者", lambda r: f"{r.get('speaker') or '?'}（{r.get('channel') or r.get('program') or ''}）")
    table("依頻道", lambda r: r.get("channel") or r.get("program") or "?")

    def kind_of(r):
        m = (r.get("check") or {}).get("metric")
        if (r.get("check") or {}).get("ticker", "").upper() in INDEX_IDS: return "指數/大盤"
        return {"limit_up": "漲停預測", "pct_change": "短線漲跌幅", "close": "目標價/價位", "high": "目標價/價位", "low": "目標價/價位",
                "new_high_250": "創高/破底", "new_low_250": "創高/破底"}.get(m, "其他")
    table("依預測類型", kind_of)

    L += ["", "## 已對帳明細（最近 60 筆）", "", "| # | 影片日 | 講者 | 標的 | 主張 | 到期 | 結果 | 實際 |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(graded, key=lambda r: r.get("due_date") or "", reverse=True)[:60]:
        mark = "✅" if r["status"] == "hit" else "❌"
        L.append(f"| {r.get('no')} | {r.get('video_date')} | {r.get('speaker')} | {'/'.join(map(str, r.get('tickers') or []))} | {str(r.get('claim'))[:40].replace('|','／')} | {r.get('due_date')} | {mark} | {str(r.get('score_detail','')).replace('|','／')} |")
    pend = [r for r in due if r.get("status") == "pending"]
    if pend:
        L += ["", "## 待到期（前 30）", "", "| # | 講者 | 標的 | 主張 | 到期 |", "|---|---|---|---|---|"]
        for r in sorted(pend, key=lambda r: r.get("due_date") or "")[:30]:
            L.append(f"| {r.get('no')} | {r.get('speaker')} | {'/'.join(map(str, r.get('tickers') or []))} | {str(r.get('claim'))[:40].replace('|','／')} | {r.get('due_date')} |")

    bt = [r for r in rows if r.get("route") == "backtest"]
    L += ["", f"## 回測候選規則彙整（{len(bt)} 條；需 2y+5y 雙窗、紅線 期望≥+8%/PF≥2.5/MDD≤-30%）", ""]
    if bt:
        L += ["| # | 講者 | 規則 | 進場 | 出場 | 母體 | 類似 |", "|---|---|---|---|---|---|---|"]
        for r in bt:
            ru = r.get("rule") or {}
            L.append(f"| {r.get('no')} | {r.get('speaker')} | {str(r.get('claim'))[:40].replace('|','／')} | {str(ru.get('entry','')).replace('|','／')} | {str(ru.get('exit','')).replace('|','／')} | {str(ru.get('universe','')).replace('|','／')} | {ru.get('similar_to') or ''} |")
        # 關鍵詞聚類：同一句型出現幾次
        kw = defaultdict(list)
        for r in bt:
            for k in ("補漲", "糾結", "突破", "拉回", "季線", "月線", "創高", "漲停", "量", "外資", "投信", "底部", "W底", "缺口"):
                if k in str(r.get("claim")) + str(r.get("rule")): kw[k].append(r.get("no"))
        if kw:
            L += ["", "**規則關鍵詞出現次數**（多位講者重複提到的先排回測）", ""]
            for k, v in sorted(kw.items(), key=lambda kv: -len(kv[1])):
                L.append(f"- {k}：{len(v)} 條（#{', #'.join(map(str, v))}）")
    else:
        L.append("（尚無）")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="論點自動對帳 + 講者命中率記分板")
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--notify", action="store_true"); ap.add_argument("--sleep", type=float, default=0.3)
    a = ap.parse_args(argv)
    rows = read_claims()
    if not rows:
        log("data/claims.jsonl 為空，先跑 analyze_transcript.py"); return 0
    todo = [r for r in rows if r.get("route") == "due_check" and (a.rescore or r.get("status") in (None, "pending"))]
    log(f"論點 {len(rows)} 條，待對帳 {len(todo)} 條")
    new_graded, n_err = [], 0
    for r in todo:
        if a.dry:
            log(f"  #{r.get('no')} {r.get('speaker')} {r.get('tickers')} 到期 {r.get('due_date')} check={r.get('check')}"); continue
        try:
            st, detail = evaluate(r)
        except Exception as e:
            st, detail = "pending", f"價格抓取失敗：{str(e)[:80]}"; n_err += 1
        if st != r.get("status") or detail != r.get("score_detail"):
            r["status"], r["score_detail"], r["scored_at"] = st, detail, dt.date.today().isoformat()
            if st in ("hit", "miss"): new_graded.append(r)
        if a.sleep: time.sleep(a.sleep)
    if not a.dry:
        write_claims(rows); save_cache()
        SCORECARD.write_text(build_scorecard(rows), encoding="utf-8")
        log(f"新判定 {len(new_graded)}（命中 {sum(r['status']=='hit' for r in new_graded)}）／抓價失敗 {n_err}／記分板 → {SCORECARD.name}")
        if a.notify and new_graded:
            try:
                import notify_line
                lines = [f"📊 論點對帳 {len(new_graded)} 筆"]
                for r in new_graded[:8]:
                    lines.append(f"{'✅' if r['status']=='hit' else '❌'} {r.get('speaker')}｜{'/'.join(map(str, r.get('tickers') or []))}｜{str(r.get('claim'))[:24]}")
                notify_line.push("\n".join(lines))
            except Exception as e:
                log(f"LINE 失敗：{e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
