# -*- coding: utf-8 -*-
"""
研究結果合併器：data/research_json/<ticker>.json → data/*.csv（upsert，同 ticker 覆蓋；其他列保留）
JSON 格式見 double_screener_v2.py 研究員提示（ticker/name/research_date/各分數/eps_2026~2029/eps_rev/thesis/risk/evidence[]）
寫入：
  qualitative_research.csv（分數 + source_1~3 取 evidence 前三個來源名 + thesis/risk）
  evidence_ledger.csv（每條 evidence 一列；同 ticker 先清掉舊列再寫）
  thesis_updates.csv / revision_updates.csv / consensus_pool.csv（eps_2026~2029）
用法：python research_merge.py [data/research_json]
"""
import sys, io, os, json, csv, glob

ROOT = os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT)
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "research_json")
D = "data"
QUAL_COLS = ["ticker", "research_date", "next_platform_score", "new_capacity_score", "new_product_mix_score",
             "order_visibility_score", "tam_score", "governance_score", "rerating_score", "customer_concentration_pct",
             "source_1", "source_2", "source_3", "thesis", "risk", "notes"]
EV_COLS = ["ticker", "claim_type", "claim", "source_url", "source_name", "published_at", "research_date", "confidence", "direction", "notes"]
TH_COLS = ["ticker", "thesis_status", "governance_red_flag", "thesis_note"]
RV_COLS = ["ticker", "eps_rev_1m", "eps_rev_3m", "eps_rev_6m"]
CP_COLS = ["ticker", "name", "eps_2026", "eps_2027", "eps_2028", "eps_2029", "eps_source", "thesis", "risk"]


def read_rows(path):
    if not os.path.exists(path): return [], []
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f); rows = list(r); return rows, (r.fieldnames or [])


def write_rows(path, rows, base_cols):
    cols = list(dict.fromkeys(base_cols + [k for r in rows for k in r.keys() if k]))
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def upsert(path, base_cols, ticker, newrow):
    """只補空格：既有列（例如 ChatGPT 填的）非空欄位保留，新值只填進空欄；ref_* 參考欄一律保留"""
    rows, _ = read_rows(path)
    old = next((r for r in rows if (r.get("ticker") or "").strip() == ticker), None)
    rows = [r for r in rows if (r.get("ticker") or "").strip() != ticker]
    merged = dict(old or {})
    for k, v in newrow.items():
        if v is None or v == "": continue
        if (merged.get(k) or "") == "": merged[k] = v
        elif k in ("thesis", "risk", "notes", "thesis_note") and str(v) not in str(merged[k]): merged[k] = f"{merged[k]}｜{v}"
    rows.append(merged); rows.sort(key=lambda r: r.get("ticker") or "")
    try:
        write_rows(path, rows, base_cols)
    except PermissionError:          # 檔案被 Excel 等程式鎖住 → 寫到 .pending.csv（screener 會一併讀取），下次再合併
        pend = path.replace(".csv", ".pending.csv")
        prow, _ = read_rows(pend); prow = [r for r in prow if (r.get("ticker") or "").strip() != ticker]; prow.append(merged)
        write_rows(pend, prow, base_cols)
        print(f"  ⚠️ {path} 被鎖住（Excel 開著？），改寫到 {pend}")


def main():
    files = sorted(glob.glob(os.path.join(SRC, "*.json")))
    if not files: print(f"沒有 JSON：{SRC}"); return
    for fp in files:
        j = json.load(io.open(fp, encoding="utf-8"))
        t = str(j.get("ticker") or os.path.basename(fp).split(".")[0]).strip()
        ev = [e for e in (j.get("evidence") or []) if e.get("claim") and e.get("source_url")]
        srcs = list(dict.fromkeys(e.get("source_url") or e.get("source_name") for e in ev))[:3]
        q = {"ticker": t, "research_date": j.get("research_date", ""),
             **{k: j.get(k) for k in ("next_platform_score", "new_capacity_score", "new_product_mix_score",
                                       "order_visibility_score", "tam_score", "governance_score", "rerating_score",
                                       "customer_concentration_pct")},
             "source_1": srcs[0] if len(srcs) > 0 else "", "source_2": srcs[1] if len(srcs) > 1 else "", "source_3": srcs[2] if len(srcs) > 2 else "",
             "thesis": j.get("thesis", ""), "risk": j.get("risk", ""), "notes": j.get("notes", "")}
        if not ev:   # 無來源 → 分數全部作廢（v3 Hard Rule 1）
            for k in ("next_platform_score", "new_capacity_score", "new_product_mix_score", "order_visibility_score", "tam_score", "governance_score", "rerating_score"):
                q[k] = None
        upsert(os.path.join(D, "qualitative_research.csv"), QUAL_COLS, t, q)
        # evidence ledger：聯集（同 ticker + 同 URL + 同 claim_type 視為重複）
        rows, _ = read_rows(os.path.join(D, "evidence_ledger.csv"))
        seen = {((r.get("ticker") or "").strip(), (r.get("source_url") or "").strip(), (r.get("claim_type") or "").strip()) for r in rows}
        ev = [e for e in ev if (t, (e.get("source_url") or "").strip(), (e.get("claim_type") or "").strip()) not in seen]
        for e in ev:
            rows.append({"ticker": t, "claim_type": e.get("claim_type", ""), "claim": e.get("claim", ""), "source_url": e.get("source_url", ""),
                         "source_name": e.get("source_name", ""), "published_at": (e.get("published_at") or "")[:10],
                         "research_date": j.get("research_date", ""), "confidence": e.get("confidence", ""),
                         "direction": e.get("direction", ""), "notes": "stale" if e.get("stale") else ""})
        write_rows(os.path.join(D, "evidence_ledger.csv"), rows, EV_COLS)
        upsert(os.path.join(D, "thesis_updates.csv"), TH_COLS, t,
               {"ticker": t, "thesis_status": j.get("thesis_status") or "OK", "governance_red_flag": bool(j.get("governance_red_flag")),
                "thesis_note": j.get("notes", "")})
        upsert(os.path.join(D, "revision_updates.csv"), RV_COLS, t,
               {"ticker": t, "eps_rev_1m": j.get("eps_rev_1m"), "eps_rev_3m": j.get("eps_rev_3m"), "eps_rev_6m": j.get("eps_rev_6m")})
        if any(j.get(k) for k in ("eps_2026", "eps_2027", "eps_2028", "eps_2029")):
            upsert(os.path.join(D, "consensus_pool.csv"), CP_COLS, t,
                   {"ticker": t, "name": j.get("name", ""), **{k: j.get(k) for k in ("eps_2026", "eps_2027", "eps_2028", "eps_2029")},
                    "eps_source": j.get("eps_source", ""), "thesis": j.get("thesis", ""), "risk": j.get("risk", "")})
        print(f"✅ {t} {j.get('name','')}: evidence {len(ev)} 條、EPS {[j.get(k) for k in ('eps_2026','eps_2027','eps_2028','eps_2029')]}、not_found {j.get('not_found')}")


if __name__ == "__main__":
    main()
