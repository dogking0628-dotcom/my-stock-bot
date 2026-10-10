# -*- coding: utf-8 -*-
"""
media_radar.py — 財經影片聲量 × 技術選股 交叉雷達
═══════════════════════════════════════════════════════════════
流程：
  1. 讀 media_channels.json 訂閱頻道 → yt-dlp 列最新影片 → 只處理 lookback_days 內的新影片
  2. 抓字幕（zh-TW/zh-Hant/zh，含自動字幕）；沒有字幕且允許時用本機 faster-whisper 轉錄
  3. 逐字稿快取到 data/media/<id>.json、純文字到 data/media/transcripts/（供後續萃取論點）
  4. 從逐字稿抓「個股名稱/代號」與「主題詞」→ 對映 product_taxonomy 產品鏈
  5. 跟系統對照：ath_industry_report（ATH 名單/糾結突破/明日 Top5）、sitc_product_flow（投信產品鏈金額）、
     smart_money_radar（外資/投信族群共識）、daily_v41/v2 訊號
  6. 產出 media_radar.md / media_radar.json：
       🔥 主流（媒體熱＋系統強）、🏃 前跑（系統強但媒體還沒講）、📣 喊單/落後（媒體熱但系統弱）
用法：
  python media_radar.py                 # 完整跑
  python media_radar.py --no-fetch      # 只用快取重算報告
  python media_radar.py --no-whisper    # 無字幕就跳過
  python media_radar.py --days 3        # 覆寫 lookback_days
規則：唯讀消費系統 JSON，不改任何策略檔；whisper 逐字稿有同音字錯誤，數字類主張必須人工核。
"""
import os, sys, io, re, json, glob, time, argparse, subprocess, datetime as dt
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or "").lower() != "utf-8":
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception: pass

MEDIA_DIR = os.path.join("data", "media")
RAW_DIR = os.path.join(MEDIA_DIR, "raw")
TR_DIR = os.path.join(MEDIA_DIR, "transcripts")
for d in (MEDIA_DIR, RAW_DIR, TR_DIR): os.makedirs(d, exist_ok=True)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
YTDLP = ["yt-dlp", "--js-runtimes", "node", "--no-warnings"]

from product_taxonomy import PRODUCT_TAXONOMY, products_of
from industry_map_loader import get_industry

# ── 主題詞 → 產品鏈 ─────────────────────────────────────────────
THEME_KEYWORDS = {
    "矽光子/CPO":        ["CPO", "矽光子", "共同封裝", "光通訊", "光模塊", "光收發", "光纖對位"],
    "封測":              ["先進封裝", "CoWoS", "封測", "封裝", "VIPack"],
    "記憶體-DRAM":       ["記憶體", "DRAM", "HBM"],
    "記憶體-NAND/模組":   ["NAND", "記憶體模組", "SSD"],
    "被動元件":          ["被動元件", "MLCC", "固態電容", "電容"],
    "伺服器板卡/PCB":     ["PCB", "高多層", "HDI"],
    "ABF載板":           ["載板", "ABF"],
    "銅箔/玻纖布":        ["銅箔", "玻纖", "CCL"],
    "散熱":              ["散熱", "液冷", "水冷"],
    "半導體材料/耗材":     ["矽晶圓", "半導體材料"],
    "半導體設備":         ["半導體設備", "設備股", "濕製程"],
    "探針卡/測試介面":     ["探針卡", "測試介面", "SLT", "測試設備", "測試環節"],
    "AI伺服器-組裝ODM":   ["AI伺服器", "AI 伺服器", "機櫃", "ODM"],
    "AI伺服器-電源":      ["電源供應", "電源方案", "BBU"],
    "自動化/機器人":       ["機器人", "人形機器人", "自動化"],
    "IC設計-手機SoC/AI":  ["IC設計", "IC 設計", "ASIC", "晶片設計"],
    "晶圓代工":          ["晶圓代工", "先進製程", "2奈米", "N2", "3奈米"],
    "光學鏡頭/模組":       ["鏡頭", "光學股"],
    "金融-金控":          ["金融股", "金控"],
    "航運-貨櫃":          ["航運", "貨櫃"],
    "連接器/線材":        ["連接器", "線材", "高速傳輸線"],
    "面板/顯示":          ["面板"],
    "重電/電線電纜":       ["重電", "變壓器"],
}
# 逐字稿常見別名/同音錯字 → 代號
ALIASES = {
    "台積": "2330", "發哥": "2454", "連發科": "2454", "國劇": "2327", "台一電": "2308", "海公公": "2317",
    "日月光": "3711", "京元電": "2449", "世界先進": "5347", "臻鼎": "4958", "世芯": "3661", "華新科技": "2492",
    "禾伸堂": "3026", "和盛堂": "3026", "新商店": "6173", "玉晶光": "3406", "志愉光": "3406", "紅塑": "3131",
    "申達科": "3583", "曾達科": "3583", "力積電": "6770", "南亞科技": "2408", "旺宏電子": "2337", "群聯電子": "8299",
}
# 完全是普通詞的股名：永不比對（世界先進/三星電子等靠 ALIASES 或全名）
STOPLIST = {"世界", "統一", "中華", "大同", "聯合", "第一", "國際", "大成", "東森", "中央", "亞洲", "台灣", "大量", "幸福", "三星", "利機",
            "美食", "全家", "健康", "光明", "大眾", "永信", "中鼎", "上市", "上櫃", "大盤", "同欣", "力麗", "精確", "全球", "順利", "高峰"}
# 太像普通詞的股名：要 ≥3 次才算被點名
AMBIGUOUS = {"創意", "新光", "長榮", "遠東", "東元", "友達", "精華", "台達"}
YEAR_LIKE = set(str(y) for y in range(1990, 2041))


def log(msg): print(msg, flush=True)


def run(cmd, timeout=300):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=ENV)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


# ── 1. 影片清單與逐字稿 ────────────────────────────────────────
def list_channel(url, n):
    rc, out, err = run(YTDLP + ["--flat-playlist", "--playlist-end", str(n), "--print", "%(id)s\t%(title)s", url], 120)
    vids = []
    for line in out.splitlines():
        if "\t" in line:
            vid, title = line.split("\t", 1)
            if re.fullmatch(r"[\w-]{11}", vid.strip()): vids.append((vid.strip(), title.strip()))
    if not vids: log(f"  [list] {url} 無結果 rc={rc} {err.strip()[-160:]}")
    return vids


def parse_vtt(path):
    out, prev = [], ""
    for l in io.open(path, encoding="utf-8", errors="ignore").read().splitlines():
        if "-->" in l or l.startswith(("WEBVTT", "Kind:", "Language:")) or not l.strip(): continue
        l = re.sub(r"<[^>]+>", "", l).strip()
        if l and l != prev: out.append(l); prev = l
    return "\n".join(out)


def fetch_subs(vid):
    """回 (meta dict, text or '')"""
    for f in glob.glob(os.path.join(RAW_DIR, f"{vid}*.vtt")): os.remove(f)
    rc, out, err = run(YTDLP + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "zh-TW,zh-Hant,zh",
                                "--sub-format", "vtt", "-o", os.path.join(RAW_DIR, "%(id)s.%(ext)s"),
                                "--print", "%(title)s\t%(channel)s\t%(upload_date)s\t%(duration)s\t%(webpage_url)s", vid], 240)
    meta = {}
    for line in out.splitlines():
        if line.count("\t") == 4:
            t, ch, ud, dur, url = line.split("\t")
            meta = dict(title=t, channel=ch, upload_date=ud, duration=int(float(dur)) if dur not in ("NA", "") else 0, url=url)
    vtts = sorted(glob.glob(os.path.join(RAW_DIR, f"{vid}*.vtt")))
    text = parse_vtt(vtts[0]) if vtts else ""
    return meta, text


def whisper_transcribe(vid, cfg):
    rc, out, err = run(YTDLP + ["-f", "bestaudio[ext=m4a]/bestaudio", "-o", os.path.join(RAW_DIR, f"{vid}.%(ext)s"), vid], 600)
    auds = glob.glob(os.path.join(RAW_DIR, f"{vid}.*"))
    auds = [a for a in auds if not a.endswith((".vtt", ".json"))]
    if not auds: log(f"  [whisper] {vid} 音訊下載失敗"); return ""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("  [whisper] faster_whisper 未安裝"); return ""
    t0 = time.time()
    m = WhisperModel(cfg.get("model", "small"), device="cpu", compute_type="int8", cpu_threads=int(cfg.get("cpu_threads", 8)))
    segs, _ = m.transcribe(auds[0], language="zh", beam_size=1, vad_filter=True,
                           initial_prompt="以下是台灣財經節目逐字稿，繁體中文。主題：台股、台積電、聯發科、AI 伺服器、記憶體、被動元件、營收、法人買賣超。")
    lines = [f"[{int(s.start)//60:02d}:{int(s.start)%60:02d}] {s.text.strip()}" for s in segs]
    log(f"  [whisper] {vid} {len(lines)} 段 {time.time()-t0:.0f}s")
    try: os.remove(auds[0])
    except Exception: pass
    return "\n".join(lines)


def load_cache(vid):
    p = os.path.join(MEDIA_DIR, f"{vid}.json")
    return json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else None


def save_cache(rec):
    with io.open(os.path.join(MEDIA_DIR, f"{rec['id']}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if rec.get("text"):
        safe = re.sub(r"[^\w一-鿿-]+", "_", rec.get("channel", ""))[:20]
        with io.open(os.path.join(TR_DIR, f"{rec.get('upload_date','00000000')}_{safe}_{rec['id']}.txt"), "w", encoding="utf-8") as f:
            f.write(f"# {rec.get('title')}\n# {rec.get('channel')} {rec.get('upload_date')} {rec.get('url')} method={rec.get('method')}\n\n{rec['text']}")


def collect_videos(cfg, fetch=True, allow_whisper=True, days=None):
    days = days or cfg.get("lookback_days", 2)
    cutoff = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    whisper_cfg = cfg.get("whisper", {})
    whisper_budget = int(whisper_cfg.get("max_per_run", 1)) if (allow_whisper and whisper_cfg.get("enabled")) else 0
    seen = {}
    if fetch:
        for ch in cfg.get("channels", []):
            log(f"[頻道] {ch['name']}")
            for vid, title in list_channel(ch["url"], cfg.get("max_videos_per_channel", 3)):
                rec = load_cache(vid)
                if rec and (rec.get("text") or not rec.get("pending_whisper")):
                    seen[vid] = rec; continue
                if not rec:
                    meta, text = fetch_subs(vid)
                    if not meta: log(f"  [skip] {vid} 抓不到 metadata"); continue
                    if meta["upload_date"] < cutoff:
                        log(f"  [old] {meta['upload_date']} {meta['title'][:40]}"); continue
                    rec = dict(id=vid, channel_cfg=ch["name"], **meta, method="subs" if text else "none", text=text,
                               fetched_at=dt.datetime.now().isoformat(timespec="seconds"), pending_whisper=not text)
                    log(f"  [{'字幕' if text else '無字幕'}] {meta['upload_date']} {meta['title'][:50]} ({meta['duration']//60}分)")
                if not rec.get("text") and rec.get("pending_whisper"):
                    if whisper_budget > 0 and rec.get("duration", 0) <= whisper_cfg.get("max_minutes", 60) * 60:
                        whisper_budget -= 1
                        txt = whisper_transcribe(vid, whisper_cfg)
                        if txt: rec.update(text=txt, method="whisper-" + whisper_cfg.get("model", "small"), pending_whisper=False)
                    else:
                        log(f"  [whisper 額度/長度不符，留待下次] {rec.get('title','')[:40]}")
                save_cache(rec); seen[vid] = rec
    # 報告視窗：快取內所有 upload_date ≥ cutoff 的影片
    for p in glob.glob(os.path.join(MEDIA_DIR, "*.json")):
        rec = json.load(io.open(p, encoding="utf-8"))
        if rec.get("upload_date", "0") >= cutoff: seen.setdefault(rec["id"], rec)
    return sorted(seen.values(), key=lambda r: (r.get("upload_date", ""), r.get("id")), reverse=True)


# ── 2. 實體抽取 ───────────────────────────────────────────────
def build_name_index():
    u = json.load(io.open("tw_universe.json", encoding="utf-8"))
    code2name, patterns = {}, {}
    for s in u.get("stocks", []):
        code, name = s["code"], s["name"].replace("*", "").strip()
        code2name[code] = name
        for nm in {name, re.sub(r"[-\s]*KY$", "", name)}:
            if len(nm) >= 2 and nm not in STOPLIST: patterns[nm] = code
    patterns.update(ALIASES)
    ordered = sorted(patterns.items(), key=lambda kv: -len(kv[0]))
    return code2name, ordered


def extract(text, ordered, code2name):
    counts = Counter()
    t = text
    for nm, code in ordered:
        t, n = re.subn(re.escape(nm), "§", t)
        if n:
            if nm in AMBIGUOUS and n < 3: continue
            counts[code] += n
    for m in re.finditer(r"(?<![\d.])(\d{4})(?![\d.%])", text):
        c = m.group(1)
        if c in code2name and c not in YEAR_LIKE: counts[c] += 1
    themes = Counter()
    for prod, kws in THEME_KEYWORDS.items():
        n = sum(len(re.findall(re.escape(k), text, re.I)) for k in kws)
        if n: themes[prod] = n
    return counts, themes


# ── 3. 系統資料 ───────────────────────────────────────────────
def J(path, default=None):
    try: return json.load(io.open(path, encoding="utf-8"))
    except Exception: return default if default is not None else {}


def load_system():
    ath = J("ath_industry_report.json")
    sitc = J("sitc_product_flow.json")
    smr = J("smart_money_radar.json")
    v41 = J("daily_v41_signal.json"); v2 = J("daily_v2_signal.json")
    sysd = dict(
        ath_date=ath.get("data_date"), regime=ath.get("market_regime", {}),
        ath={e["ticker"]: e for e in ath.get("exact_ath", []) if isinstance(e, dict)},
        near={e["ticker"]: e for e in ath.get("near_ath_top30", []) if isinstance(e, dict)},
        top5={e["ticker"]: e for e in ath.get("tomorrow_top5", []) if isinstance(e, dict)},
        tangle={e["ticker"]: e for e in ath.get("tangle_breakout", []) if isinstance(e, dict)},
        industry_stats={d["industry"]: d for d in ath.get("industry_stats", [])},
        sitc={p["product"]: p for p in sitc.get("products", [])},
        smr_sectors={s["industry"]: s for s in smr.get("sectors", [])},
        v41_picks=[p.get("ticker") or p.get("code") for p in v41.get("picks", [])],
        v2_picks=[p.get("ticker") or p.get("code") for p in v2.get("picks", [])],
        v41_label=v41.get("strategy", "V4.4"), v2_label=v2.get("strategy", "V2"),
    )
    return sysd


def sys_status(code, S):
    tags = []
    if code in S["top5"]: tags.append("明日Top5")
    if code in S["ath"]: tags.append("ATH名單")
    elif code in S["near"]: tags.append("近ATH30")
    if code in S["tangle"]: tags.append("糾結突破")
    if code in S["v41_picks"]: tags.append("V4.4訊號")
    if code in S["v2_picks"]: tags.append("V2訊號")
    return "、".join(tags) if tags else "不在名單"


# ── 4. 價格位階（FinMind，限量）──────────────────────────────
def finmind_token():
    t = os.environ.get("FINMIND_TOKEN", "").strip()
    if not t and os.path.exists("finmind_token.txt"): t = io.open("finmind_token.txt", encoding="utf-8").read().strip()
    return t


def price_tags(codes, cap):
    out = {}
    if not codes: return out
    try: import requests
    except ImportError: return out
    tok = finmind_token(); H = {"Authorization": "Bearer " + tok} if tok else {}
    start = (dt.date.today() - dt.timedelta(days=100)).isoformat()
    for c in codes[:cap]:
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data", params=dict(dataset="TaiwanStockPrice", data_id=c, start_date=start),
                             headers=H, timeout=20).json()
            d = r.get("data", [])
            if len(d) < 25: continue
            cl = [x["close"] for x in d]; last = cl[-1]
            ma20 = sum(cl[-20:]) / 20; ma60 = sum(cl[-60:]) / min(60, len(cl)); hi = max(cl)
            pos = "創高" if last >= hi * 0.995 else ("季線上" if last > ma60 else ("月線上" if last > ma20 else "均線下"))
            out[c] = dict(date=d[-1]["date"], close=last, chg=round(100 * (last / cl[-2] - 1), 2), vs_ma60=round(100 * (last / ma60 - 1), 1),
                          from_hi=round(100 * (last / hi - 1), 1), pos=pos)
        except Exception: continue
    return out


# ── 5. 交叉分析與報告 ─────────────────────────────────────────
def analyze(videos, cfg):
    code2name, ordered = build_name_index()
    S = load_system()
    per_video, ticker_videos, ticker_mentions, theme_videos = [], defaultdict(set), Counter(), defaultdict(set)
    for v in videos:
        if not v.get("text"): per_video.append(dict(v, tickers=[], themes=[])); continue
        counts, themes = extract(v["text"], ordered, code2name)
        feat = [(c, n) for c, n in counts.most_common() if n >= 2]
        for c, n in feat: ticker_videos[c].add(v["id"]); ticker_mentions[c] += n
        for p in themes: theme_videos[p].add(v["id"])
        per_video.append(dict(v, tickers=[(c, code2name.get(c, c), n) for c, n in feat[:12]], themes=themes.most_common(6)))
    n_vid = len([v for v in videos if v.get("text")])
    hot_th = cfg.get("media_hot_videos", 2)

    # 產品鏈矩陣
    rows = []
    for prod, codes in PRODUCT_TAXONOMY.items():
        codes = list(dict.fromkeys(codes))
        mv = set()
        for c in codes: mv |= ticker_videos.get(c, set())
        mv |= theme_videos.get(prod, set())
        media_names = [code2name.get(c, c) for c in codes if c in ticker_videos]
        ath_codes = [c for c in codes if c in S["ath"]]
        near_codes = [c for c in codes if c in S["near"] and c not in S["ath"]]
        sp = S["sitc"].get(prod, {})
        strong = len(ath_codes) >= 2 or (sp.get("mom") == "⏫加速買" and sp.get("amt5", 0) > 0) or any(c in S["top5"] for c in codes)
        hot = len(mv) >= hot_th
        quad = "🔥主流" if (hot and strong) else ("🏃前跑" if strong else ("📣喊單/落後" if hot else ""))
        rows.append(dict(product=prod, media_videos=len(mv), media_names=media_names, ath=[code2name.get(c, c) for c in ath_codes],
                         near=[code2name.get(c, c) for c in near_codes], sitc_amt5=round(sp.get("amt5", 0), 1), sitc_mom=sp.get("mom", ""),
                         strong=strong, hot=hot, quad=quad))
    rows.sort(key=lambda r: (-(r["hot"] and r["strong"]), -r["strong"], -r["media_videos"], -len(r["ath"])))

    # 個股榜 + 位階
    top_codes = [c for c, _ in sorted(ticker_videos.items(), key=lambda kv: (-len(kv[1]), -ticker_mentions[kv[0]]))]
    prices = price_tags(top_codes, cfg.get("price_lookup_max", 25))
    stock_rows = []
    for c in top_codes[:40]:
        pr = prices.get(c, {})
        stock_rows.append(dict(code=c, name=code2name.get(c, c), videos=len(ticker_videos[c]), mentions=ticker_mentions[c],
                               industry=get_industry(c) or "", products=products_of(c)[:2], status=sys_status(c, S), **pr))
    # 系統名單裡沒被媒體提到的（前跑個股）
    quiet_leaders = []
    for c, e in S["ath"].items():
        if c in ticker_videos: continue
        quiet_leaders.append(dict(code=c, name=e.get("name"), industry=e.get("industry"), score=e.get("momentum_score", 0), tier=e.get("tier", ""),
                                  chg=round(e.get("change_pct", 0), 1), products=products_of(c)[:2]))
    quiet_leaders.sort(key=lambda r: (-r["score"], -r["chg"]))
    # 證交所大類：媒體提及 vs ATH 家數
    ind_media = Counter()
    for c in ticker_videos: ind_media[get_industry(c) or "未分類"] += len(ticker_videos[c])
    ind_rows = []
    for ind, st in S["industry_stats"].items():
        sm = S["smr_sectors"].get(ind, {})
        ind_rows.append(dict(industry=ind, ath=st.get("count", 0), bullish=st.get("bullish_count", 0), media=ind_media.get(ind, 0),
                             consensus=sm.get("consensus", ""), mom=sm.get("mom", "")))
    ind_rows.sort(key=lambda r: -r["ath"])
    return dict(generated=dt.datetime.now().isoformat(timespec="minutes"), ath_date=S["ath_date"], regime=S["regime"], n_videos=len(videos), n_text=n_vid,
                videos=[{k: v for k, v in pv.items() if k != "text"} for pv in per_video], products=rows, stocks=stock_rows,
                quiet_leaders=quiet_leaders[:15], industries=ind_rows[:10], labels=dict(v41=S["v41_label"], v2=S["v2_label"]))


def render(R):
    L = []
    rg = R["regime"] or {}
    L.append(f"# 📺 媒體聲量 × 技術選股 交叉雷達（{R['generated'][:10]}｜系統資料 {R['ath_date']}）")
    L.append(f"影片 {R['n_videos']} 支（有逐字稿 {R['n_text']}）。0050 距 MA200 {rg.get('ext_pct', 0):+.1f}%｜"
             f"{'⏸️ 0050<20MA' if rg.get('below_ma20') else '✅ 0050>20MA'}｜策略：{R['labels']['v41'].split(' (')[0]}、{R['labels']['v2'].split(' (')[0]}")
    L.append("")
    for quad, title in [("🏃前跑", "🏃 前跑產業：系統強、媒體還沒講（領先訊號，優先看）"), ("🔥主流", "🔥 主流產業：媒體熱＋系統強"), ("📣喊單/落後", "📣 喊單/落後：媒體熱但系統名單裡沒有（別追）")]:
        rs = [r for r in R["products"] if r["quad"] == quad]
        L.append(f"## {title}")
        if not rs: L.append("（無）")
        for r in rs[:10]:
            L.append(f"- **{r['product']}**｜媒體 {r['media_videos']} 支{('：' + '、'.join(r['media_names'][:5])) if r['media_names'] else ''}｜"
                     f"ATH {len(r['ath'])} 檔{('：' + '、'.join(r['ath'][:6])) if r['ath'] else ''}"
                     f"{('｜近ATH：' + '、'.join(r['near'][:4])) if r['near'] else ''}｜投信5日 {r['sitc_amt5']:+.1f} 億 {r['sitc_mom']}")
        L.append("")
    L.append("## 🔇 系統名單中媒體沒點名的領先股（前 15，依動能分）")
    L.append("| 代號 | 名稱 | 產業 | 產品鏈 | 動能 | 今日% |")
    L.append("|---|---|---|---|---|---|")
    for q in R["quiet_leaders"]:
        L.append(f"| {q['code']} | {q['name']} | {q['industry']} | {'/'.join(q['products']) or '-'} | {q['score']} {q['tier']} | {q['chg']:+.1f} |")
    L.append("")
    L.append("## 📣 名嘴點名榜 vs 系統名單")
    L.append("| 代號 | 名稱 | 影片數 | 提及 | 產品鏈 | 系統狀態 | 位階 | 收盤 | 距高 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for s in R["stocks"][:25]:
        L.append(f"| {s['code']} | {s['name']} | {s['videos']} | {s['mentions']} | {'/'.join(s['products']) or s['industry']} | {s['status']} | "
                 f"{s.get('pos', '')} | {s.get('close', '')} | {('%+.1f%%' % s['from_hi']) if 'from_hi' in s else ''} |")
    L.append("")
    L.append("## 🏭 證交所大類：ATH 家數 vs 媒體提及")
    L.append("| 產業 | ATH 檔 | 多頭 | 媒體提及 | 外資/投信 | 動能 |")
    L.append("|---|---|---|---|---|---|")
    for i in R["industries"]:
        L.append(f"| {i['industry']} | {i['ath']} | {i['bullish']} | {i['media']} | {i['consensus']} | {i['mom']} |")
    L.append("")
    L.append("## 🎬 影片清單")
    for v in R["videos"]:
        names = "、".join(f"{t[1]}({t[2]})" for t in v.get("tickers", [])[:8])
        th = "、".join(f"{p}" for p, _ in v.get("themes", [])[:4])
        L.append(f"- {v.get('upload_date','')} [{v.get('channel','')}] {v.get('title','')[:60]}（{v.get('duration',0)//60} 分，{v.get('method','')}）")
        if names or th: L.append(f"  - 點名：{names or '-'}｜主題：{th or '-'}")
        L.append(f"  - {v.get('url','')}")
    L.append("")
    L.append("> 判讀規則：🏃 前跑＝ATH ≥2 檔或投信加速買或有明日 Top5，且媒體 <2 支提到；📣 喊單＝媒體 ≥2 支但系統無。"
             "媒體提及只是聲量，**不是買賣訊號**；whisper 逐字稿的數字要人工核。逐字稿在 data/media/transcripts/，說「萃取論點」可入 analyst_claims.md。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--no-whisper", action="store_true")
    ap.add_argument("--days", type=int, default=None); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    cfg = json.load(io.open("media_channels.json", encoding="utf-8"))
    videos = collect_videos(cfg, fetch=not a.no_fetch, allow_whisper=not a.no_whisper, days=a.days)
    R = analyze(videos, cfg)
    md = render(R)
    io.open("media_radar.md", "w", encoding="utf-8").write(md)
    with io.open("media_radar.json", "w", encoding="utf-8") as f: json.dump(R, f, ensure_ascii=False, indent=1)
    if not a.quiet: print(md)
    log(f"\n[done] media_radar.md / media_radar.json（{R['n_videos']} 支）")


if __name__ == "__main__":
    main()
