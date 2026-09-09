# -*- coding: utf-8 -*-
"""
逐字稿 → 論點萃取 → 對照系統狀態 → 操作檢討與建議（自動落檔）。

接在 fetch_transcript.py 之後：
  python analyze_transcript.py --new            # 分析 index.json 裡「抓到但還沒分析」的逐字稿
  python analyze_transcript.py <影片ID> [...]    # 指定影片（可配 --force 重分析）
  python analyze_transcript.py --new --notify   # 排程用：分析完把摘要推 LINE

每支影片產出：
  data/transcripts/<影片ID>.analysis.md   完整報告（論點表、持倉影響、操作檢討、建議修正）
  analyst_claims.md                        尾端追加一節：論點驗證庫列（編號接續）＋ 到期對帳列
  data/evidence_candidates.csv             證據帳本「候選」（不直接寫 evidence_ledger.csv，入帳前不算）
  data/transcripts/index.json              標 analyzed_at / analysis 檔名

鐵律（寫進 system prompt，模型只能分流、不能下單）：
  論點永不直接變成買賣建議；技術規則→回測候選；籌碼→資訊層；基本面→證據候選；預測→掛到期日；心法→playbook。
  任何「改策略」的建議都標 requires_backtest=true，要走 2y+5y 雙窗＋紅線（期望≥+8%/PF≥2.5/MDD≤-30%）才可能上線。

需要環境變數 ANTHROPIC_API_KEY（GitHub Secret 同名）。沒有就印說明後 exit 0，不拖垮排程。
模型 claude-opus-5、串流、effort high，並開啟 server-side fallbacks（遇安全拒答自動換模型續跑）。
"""
import sys, io, os, re, json, csv, argparse, datetime as dt
from pathlib import Path

if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TDIR = DATA / "transcripts"
INDEX = TDIR / "index.json"
CLAIMS = ROOT / "analyst_claims.md"
EVIDENCE_CAND = DATA / "evidence_candidates.csv"
MODEL = os.environ.get("ANALYZE_MODEL", "claude-opus-5")

def log(m): print(f"[analyze] {m}", flush=True)

def load_json(p, default):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return default

def save_json(p, obj):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)

def read_text(p, limit=None):
    try:
        s = Path(p).read_text(encoding="utf-8")
        return s if limit is None else s[:limit]
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────────────────────
# 系統狀態快照（餵給模型對照用；全部來自 repo 現成 JSON，唯讀）
# ────────────────────────────────────────────────────────────────────────────
def system_snapshot():
    snap = {}
    th = load_json(ROOT / "market_thermometer.json", {})
    snap["market_thermometer"] = {k: th.get(k) for k in ("date", "turnover", "ma87", "top50_breadth")}
    ath = load_json(ROOT / "ath_industry_report.json", {})
    snap["ath_report"] = {
        "data_date": ath.get("data_date"), "market_regime": ath.get("market_regime"),
        "v44_paused": ath.get("v44_paused"), "v4_blocked": ath.get("v4_blocked"),
        "tomorrow_top5_industry": ath.get("tomorrow_top5_industry"),
        "tomorrow_top5": [{k: x.get(k) for k in ("code", "name", "industry", "momentum_score", "close")}
                          for x in (ath.get("tomorrow_top5") or [])[:5]],
        "exact_ath_codes": [x.get("code") for x in (ath.get("exact_ath") or [])][:80],
        "industry_stats_top8": (ath.get("industry_stats") or [])[:8],
    }
    v41 = load_json(ROOT / "daily_v41_signal.json", {})
    snap["v44_signal"] = {k: v41.get(k) for k in ("timestamp", "strategy", "strongest_industry", "v4_blocked", "picks", "top5")}
    hold = load_json(ROOT / "double_holdings.json", {})
    snap["holdings"] = {"rule": (hold.get("_meta") or {}).get("rule"), "tw": hold.get("tw")}
    smr = load_json(ROOT / "smart_money_radar.json", {})
    snap["smart_money_radar"] = {"date": smr.get("date"), "sectors_top10": (smr.get("sectors") or [])[:10],
                                 "top_buy": (smr.get("top_buy") or [])[:10], "top_sell": (smr.get("top_sell") or [])[:10]}
    snap["double_pool_final_md"] = read_text(ROOT / "double_pool_final.md", 3000)
    aq = read_text(ROOT / "double_action_queue.csv").splitlines()
    snap["double_action_queue_head"] = aq[:25]
    snap["playbook_sections"] = re.findall(r"^## \[[^\]]+\][^\n]*", read_text(ROOT / "playbook.md"), re.M)
    claims = read_text(CLAIMS)
    snap["existing_claims_tail"] = claims[-6000:]
    snap["rules"] = {
        "V4.4 進場": "創2y月線ATH + 多頭排列(>20MA>60MA>200MA) + 動能≥80 + 科技7族群 + 市值≥100億 + 0050>MA200 + 0050>自身20MA；出場=收盤破20MA或進場-7%先到",
        "翻倍池": "thesis-based 管理；賣出只有三理由：thesis 壞/EPS 連兩季下修/治理紅旗；災難線=成本-15%；ADD 必須有新鮮證據帳本",
        "憲法 v1": "只買訊號股、單筆≤50萬、停損不凹、不融資；翻倍池計畫性買進條款待用戶確認",
        "改策略紅線": "先回測 2y+5y 雙窗 → 期望≥+8% / PF≥2.5 / MDD≤-30% → 用戶拍板 → 才上線",
    }
    return snap


SYSTEM_PROMPT = """你是一個台股量化投資系統的「論點驗證庫」管理員。系統有兩個引擎：短線動能 V4.4（創高追價、20MA 出場）與長線 3 年翻倍池（thesis-based）。
用戶會餵你財經節目/影片的逐字稿，你的工作是把內容拆成可驗證的論點，對照「系統目前狀態」，然後分流。你不是投顧，永遠不能直接給買賣建議。

分流規則（每條論點只能選一種 route）：
- backtest：可寫成明確技術/籌碼規則的主張 → 回測候選（需 2y+5y 雙窗、紅線 期望≥+8%/PF≥2.5/MDD≤-30%）
- due_check：有明確標的＋明確時間/價位的預測 → 掛到期日，寫可客觀對帳的標準
- evidence：基本面/產能/訂單/EPS 這類可查證的事實 → 證據帳本候選（需標來源；入帳前不算）
- info_layer：籌碼、資金流向、無法回測但有參考價值 → 資訊層備忘
- playbook：心法、紀律、情緒管理 → 併入操作心法庫的某個情境段
- reject：與系統已否決結論衝突、或明顯節目效果/置入 → 否決並說明
- unverifiable：無期限、無標的、無法證偽 → 記錄但不裁決

對照系統狀態時要具體：標的在不在 ATH 名單、在不在持股、與大盤體制（0050 vs MA200/20MA、量能天險）是否一致、與既有 analyst_claims 是否重複或衝突（重複就標「同 #N」）。
「操作修正與建議」只能是：補證據帳本、掛對帳、排回測、thesis 覆核、併 playbook、參數檢討（一律 requires_backtest=true）。不得出現「買進/賣出/加碼/減碼」字眼作為建議。
自動字幕/轉錄的數字可能有誤，涉及具體數字的主張要在 note 標「數字待核」。

時間基準：預測一律以「影片上傳日」為起點（不是今天）。「明天」= 上傳日後第一個交易日，「本週」= 上傳日所在週的週五，「短線」預設 10 個交易日，「中期」預設 60 個交易日。影片若是過去的，到期日可能已經過了，照樣掛，評分程式會用歷史股價自動對帳。

due_check 的論點必須附機器可讀的 check（評分程式直接用）：
 {"ticker": "4 碼代號；加權指數用 TAIEX、櫃買用 TPEX", "metric": "close|high|low|pct_change|limit_up|new_high_250|new_low_250",
  "op": ">|>=|<|<=", "value": number 或 null（limit_up/new_high 不需要）, "window": "on_due|any_day|all_days",
  "base_date": "YYYY-MM-DD（pct_change 的基準收盤日，通常=影片上傳日）"}
 例：「國巨本週一根漲停」→ metric limit_up, window any_day, due_date 該週五。
 例：「台積電 9/30 前過 2510」→ metric close, op >, value 2510, window any_day。
 例：「明天噴」→ metric pct_change, op >=, value 5, window on_due, due_date 下一交易日。
 無法寫成 check 的預測改 route=unverifiable。
backtest 的論點必須附 rule：{"entry": str, "exit": str, "universe": str, "params": {..}, "similar_to": "已有回測/論點編號或 null"}。

只輸出一個 JSON 物件（不要 markdown code fence、不要前後說明），schema：
{
 "video": {"program": str, "speakers": [str], "style": str, "one_line": str},
 "claims": [
   {"speaker": str, "camp": str, "claim": str, "tickers": [str], "kind": "prediction|rule|fundamental|macro|mindset|placement|other",
    "route": "backtest|due_check|evidence|info_layer|playbook|reject|unverifiable",
    "verification": str, "due_date": "YYYY-MM-DD 或 null", "criteria": str,
    "check": {…} 或 null, "rule": {…} 或 null,
    "system_check": str, "verdict": str, "note": str, "timestamp": "mm:ss 或 null"}
 ],
 "holdings_impact": [{"ticker": str, "name": str, "said": str, "thesis_effect": "支持|無關|挑戰", "action": str}],
 "operation_review": {
   "consistent": [str], "conflicts": [str],
   "adjustments": [{"item": str, "type": "backtest_candidate|evidence|playbook|param_review|due_check", "rationale": str, "requires_backtest": bool}]
 },
 "evidence_candidates": [{"ticker": str, "claim_type": "next_platform|new_capacity|new_product_mix|order_visibility|eps_revision|governance|other", "claim": str, "confidence": 0.0, "direction": "positive|negative|neutral"}],
 "line_summary": str
}
verdict 用系統慣用標記：⏳ 待對帳 / 🧪 排入回測 / 📥 證據候選 / 📝 備忘 / ➡️ 併入 playbook / ❌ 否決 / ✅ 已驗證。
line_summary ≤ 300 字，繁體中文，先講對持倉/系統最重要的一件事。"""


def call_model(transcript_md, snap, meta=None):
    import anthropic
    client = anthropic.Anthropic()
    up = (meta or {}).get("upload_date") or ""
    up_iso = f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else "未知"
    user = (
        "【系統目前狀態（唯讀快照）】\n" + json.dumps(snap, ensure_ascii=False, indent=1)
        + "\n\n【今天】" + dt.date.today().isoformat()
        + f"\n【影片上傳日（預測的時間基準）】{up_iso}　頻道：{(meta or {}).get('channel', '')}"
        + "\n\n【逐字稿】\n" + transcript_md
    )
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"模型拒答：{getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        log("  ⚠ 輸出被 max_tokens 截斷，JSON 可能不完整")
    text = "".join(b.text for b in msg.content if b.type == "text")
    u = msg.usage
    log(f"  tokens in={u.input_tokens} cached={getattr(u, 'cache_read_input_tokens', 0)} out={u.output_tokens} model={msg.model}")
    return parse_json(text)


def parse_json(text):
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j + 1])
        raise


# ────────────────────────────────────────────────────────────────────────────
# 落檔
# ────────────────────────────────────────────────────────────────────────────
def next_claim_no():
    s = read_text(CLAIMS)
    nums = [int(x) for x in re.findall(r"^\| (\d+) \|", s, re.M)]
    return (max(nums) + 1) if nums else 1

def cell(s):
    return str(s if s is not None else "").replace("|", "／").replace("\n", " ").strip()

def append_claims(res, meta, vid):
    """在 analyst_claims.md 尾端加一節（表格列編號接續）。回傳 (first_no, last_no)。"""
    today = dt.date.today().strftime("%m-%d")
    no = next_claim_no(); first = no
    v = res.get("video") or {}
    src_tag = f"[影片 {vid}]"
    rows = ["", f"## 📺 {dt.date.today().isoformat()} {cell(meta.get('channel'))}《{cell(meta.get('title'))[:40]}》 {src_tag}",
            f"> {cell(v.get('one_line'))}　來賓：{'、'.join(v.get('speakers') or [])}　風格：{cell(v.get('style'))}",
            "", "| # | 日期 | 來源/派別 | 主張 | 驗證方式 | 結果 | 裁決 |", "|---|---|---|---|---|---|---|"]
    due_rows = []
    for c in res.get("claims") or []:
        src = f"{cell(c.get('speaker'))}/{cell(c.get('camp'))} {src_tag}" if no == first else f"{cell(c.get('speaker'))}/{cell(c.get('camp'))}"
        ts = f"[{c['timestamp']}] " if c.get("timestamp") else ""
        claim = ts + cell(c.get("claim"))
        if c.get("tickers"): claim += f"（{'/'.join(map(str, c['tickers']))}）"
        note = f"；{cell(c.get('note'))}" if c.get("note") else ""
        verify = cell(c.get("verification"))
        if c.get("due_date"): verify += f"；到期 {c['due_date']}"
        rows.append(f"| {no} | {today} | {src} | {claim} | {verify} | {cell(c.get('system_check'))}{note} | {cell(c.get('verdict'))} |")
        if c.get("route") == "due_check" and c.get("due_date"):
            due_rows.append(f"| {c['due_date'][5:]} | {no} | {'/'.join(map(str, c.get('tickers') or [])) or '–'} | {cell(c.get('claim'))[:40]} | {cell(c.get('criteria'))} |")
        no += 1
    if due_rows:
        rows += ["", "| 到期 | # | 標的 | 主張 | 對帳標準 |", "|---|---|---|---|---|"] + due_rows
    orv = res.get("operation_review") or {}
    if orv.get("conflicts") or orv.get("adjustments"):
        rows.append("")
        for x in orv.get("conflicts") or []: rows.append(f"> ⚠ 與系統衝突：{cell(x)}")
        for a in orv.get("adjustments") or []:
            rb = "（需回測）" if a.get("requires_backtest") else ""
            rows.append(f"> 🔧 {cell(a.get('type'))}{rb}：{cell(a.get('item'))} — {cell(a.get('rationale'))}")
    with CLAIMS.open("a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    return first, no - 1

def write_report(res, meta, vid, first, last):
    v = res.get("video") or {}
    L = [f"# 影片分析：{cell(meta.get('title'))}", "",
         f"- 影片：{meta.get('url')}　頻道：{cell(meta.get('channel'))}　上傳：{meta.get('upload_date')}",
         f"- 節目/來賓：{cell(v.get('program'))}／{'、'.join(v.get('speakers') or [])}　風格：{cell(v.get('style'))}",
         f"- 一句話：{cell(v.get('one_line'))}",
         f"- 論點已入庫 analyst_claims.md #{first}~#{last}（{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}，模型 {MODEL}）",
         "- ⚠ 自動萃取；論點永不直接變成買賣建議，一切走流水線裁決", "",
         "## 持倉影響", "", "| 標的 | 節目說了什麼 | 對 thesis | 系統動作 |", "|---|---|---|---|"]
    for h in res.get("holdings_impact") or []:
        L.append(f"| {cell(h.get('ticker'))} {cell(h.get('name'))} | {cell(h.get('said'))} | {cell(h.get('thesis_effect'))} | {cell(h.get('action'))} |")
    if not res.get("holdings_impact"): L.append("| – | 未提及持股 | – | – |")
    orv = res.get("operation_review") or {}
    L += ["", "## 操作檢討", "", "**與系統一致**"]
    L += [f"- {cell(x)}" for x in orv.get("consistent") or []] or ["- （無）"]
    L += ["", "**與系統衝突**"]
    L += [f"- {cell(x)}" for x in orv.get("conflicts") or []] or ["- （無）"]
    L += ["", "**建議修正（全部需經流水線；標「需回測」者未過紅線不得上線）**"]
    for a in orv.get("adjustments") or []:
        L.append(f"- [{cell(a.get('type'))}]{'（需回測）' if a.get('requires_backtest') else ''} {cell(a.get('item'))}：{cell(a.get('rationale'))}")
    if not orv.get("adjustments"): L.append("- （無）")
    L += ["", "## 論點明細", "", "| # | 講者 | 論點 | 分流 | 驗證/到期 | 對照系統 | 裁決 |", "|---|---|---|---|---|---|---|"]
    for i, c in enumerate(res.get("claims") or [], start=first):
        ver = cell(c.get("verification")) + (f"；{c['due_date']}" if c.get("due_date") else "")
        L.append(f"| {i} | {cell(c.get('speaker'))} | {cell(c.get('claim'))} | {cell(c.get('route'))} | {ver} | {cell(c.get('system_check'))} | {cell(c.get('verdict'))} |")
    L += ["", "## LINE 摘要", "", cell(res.get("line_summary")), ""]
    out = TDIR / f"{vid}.analysis.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out.name

CLAIMS_JSONL = DATA / "claims.jsonl"

def append_claims_jsonl(res, meta, vid, first):
    """機器可讀的論點庫：score_claims.py 用它算命中率、彙整回測候選。一行一條。"""
    up = meta.get("upload_date") or ""
    up_iso = f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else None
    v = res.get("video") or {}
    with CLAIMS_JSONL.open("a", encoding="utf-8") as f:
        for i, c in enumerate(res.get("claims") or [], start=first):
            row = {"no": i, "video_id": vid, "video_date": up_iso, "channel": meta.get("channel"),
                   "program": v.get("program"), "title": meta.get("title"),
                   "speaker": c.get("speaker"), "camp": c.get("camp"), "kind": c.get("kind"), "route": c.get("route"),
                   "claim": c.get("claim"), "tickers": c.get("tickers") or [], "due_date": c.get("due_date"),
                   "criteria": c.get("criteria"), "check": c.get("check"), "rule": c.get("rule"),
                   "verdict": c.get("verdict"), "note": c.get("note"), "timestamp": c.get("timestamp"),
                   "status": "pending" if c.get("route") == "due_check" else "n/a",
                   "created_at": dt.datetime.now().isoformat(timespec="minutes")}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_evidence_candidates(res, meta, vid):
    cands = res.get("evidence_candidates") or []
    if not cands: return 0
    new = not EVIDENCE_CAND.exists()
    with EVIDENCE_CAND.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ticker", "claim_type", "claim", "source_url", "source_name", "published_at", "research_date", "confidence", "direction", "notes"])
        up = meta.get("upload_date") or ""
        pub = f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else ""
        for c in cands:
            w.writerow([c.get("ticker"), c.get("claim_type"), c.get("claim"), meta.get("url"), cell(meta.get("channel")),
                        pub, dt.date.today().isoformat(), c.get("confidence"), c.get("direction"),
                        f"影片 {vid} 自動萃取，未核實；入 evidence_ledger 前需人工確認"])
    return len(cands)


# ────────────────────────────────────────────────────────────────────────────
def analyze_one(vid, index, force=False, dry=False):
    meta = index.get(vid)
    if not meta or meta.get("status") != "ok":
        log(f"{vid}：index 無成功逐字稿，略過"); return None
    if meta.get("analyzed_at") and not force:
        log(f"{vid}：已分析（{meta['analyzed_at']}），略過"); return None
    path = TDIR / meta["file"]
    if not path.exists():
        log(f"{vid}：找不到 {path.name}"); return None
    text = path.read_text(encoding="utf-8")
    log(f"▶ {vid} {cell(meta.get('title'))[:50]}（{len(text)} 字）")
    snap = system_snapshot()
    if dry:
        log("  --dry：不呼叫模型"); return None
    res = call_model(text, snap, meta)
    first, last = append_claims(res, meta, vid)
    rpt = write_report(res, meta, vid, first, last)
    n_ev = append_evidence_candidates(res, meta, vid)
    append_claims_jsonl(res, meta, vid, first)
    meta.update({"analyzed_at": dt.datetime.now().isoformat(timespec="minutes"), "analysis": rpt,
                 "claims_range": [first, last], "evidence_candidates": n_ev})
    log(f"  ✓ 論點 #{first}~#{last}、證據候選 {n_ev}、報告 {rpt}")
    return res

def main(argv=None):
    ap = argparse.ArgumentParser(description="逐字稿 → 論點入庫 + 操作檢討")
    ap.add_argument("videos", nargs="*", help="影片 ID（省略則配 --new）")
    ap.add_argument("--new", action="store_true", help="分析所有抓到但未分析的逐字稿")
    ap.add_argument("--force", action="store_true", help="已分析的也重分析（會再追加一節，請自行清理）")
    ap.add_argument("--max", type=int, default=5, help="單次最多分析幾支")
    ap.add_argument("--notify", action="store_true", help="推 LINE 摘要")
    ap.add_argument("--dry", action="store_true", help="只列出會分析哪些，不呼叫模型")
    a = ap.parse_args(argv)

    index = load_json(INDEX, {})
    targets = list(a.videos)
    if a.new:
        targets += [k for k, v in index.items() if v.get("status") == "ok" and (a.force or not v.get("analyzed_at"))]
    targets = list(dict.fromkeys(targets))[:a.max]
    if not targets:
        log("沒有待分析的逐字稿"); return 0
    if not os.environ.get("ANTHROPIC_API_KEY") and not a.dry:
        log("未設定 ANTHROPIC_API_KEY：本機 set ANTHROPIC_API_KEY=...；GitHub 在 Secrets 加 ANTHROPIC_API_KEY。這次略過分析。")
        return 0

    summaries, n_ok = [], 0
    for vid in targets:
        try:
            res = analyze_one(vid, index, a.force, a.dry)
        except Exception as e:
            log(f"  ✗ {vid} 失敗：{str(e)[:300]}")
            index.setdefault(vid, {})["analyze_error"] = str(e)[:300]
            res = None
        save_json(INDEX, index)
        if res:
            n_ok += 1
            summaries.append((index[vid], res))
    if a.notify and summaries:
        try:
            import notify_line
            parts = []
            for meta, res in summaries:
                parts.append(f"📺 {cell(meta.get('channel'))[:10]}｜{cell(meta.get('title'))[:30]}\n{cell(res.get('line_summary'))}\n（#{meta['claims_range'][0]}~#{meta['claims_range'][1]} 已入 analyst_claims）")
            notify_line.push("\n\n".join(parts))
        except Exception as e:
            log(f"LINE 失敗：{e}")
    log(f"完成：分析 {n_ok}/{len(targets)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
