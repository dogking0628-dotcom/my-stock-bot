# -*- coding: utf-8 -*-
"""
自動下載 YouTube 影片逐字稿（字幕 → 純文字），供 analyst_claims.md 論點入庫用。

用法：
  python fetch_transcript.py <YouTube 網址或影片 ID> [...]   # 指定影片
  python fetch_transcript.py --queue                          # 吃 data/video_queue.txt 佇列
  python fetch_transcript.py --watch                          # 掃 data/video_sources.json 追蹤頻道的最新影片
  python fetch_transcript.py --watch --queue --notify         # 排程用：頻道 + 佇列 + LINE 通知

輸出：
  data/transcripts/<上傳日期>_<影片ID>.md    每支影片一檔（中繼資料 + 分段逐字稿，段首帶 [mm:ss]）
  data/transcripts/index.json                 已處理清單（去重、狀態、來源語言），排程重跑不會重抓

字幕語言優先序：zh-TW → zh-Hant → zh → zh-Hans → en；人工字幕優先，沒有才用自動字幕。
沒有任何字幕時：預設只記錄 no_subs；加 --whisper 且本機裝了 faster-whisper 才會下載音訊轉錄。

環境變數（選填）：
  YT_COOKIES_FILE   cookies.txt 路徑（GitHub Actions IP 常被 YouTube 要求登入驗證時用）
  YT_JS_RUNTIME     JS runtime 名稱，預設 node（SOP 驗證過），可填 deno/bun/quickjs
"""
import sys, io, os, re, json, time, argparse, datetime as dt
from pathlib import Path

if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = DATA_DIR / "transcripts"
INDEX = OUT_DIR / "index.json"
QUEUE = DATA_DIR / "video_queue.txt"
SOURCES = DATA_DIR / "video_sources.json"
AUDIO_DIR = OUT_DIR / "_audio"          # whisper 用暫存，已 .gitignore

LANG_PRIORITY = ["zh-TW", "zh-Hant", "zh", "zh-Hans", "en"]
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


# ────────────────────────────────────────────────────────────────────────────
# 小工具
# ────────────────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[transcript] {msg}", flush=True)


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def extract_video_id(s):
    """網址或裸 ID → 11 碼影片 ID；認不出來回 None。"""
    s = s.strip()
    if VIDEO_ID_RE.match(s):
        return s
    m = URL_ID_RE.search(s)
    return m.group(1) if m else None


def fmt_ts(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_vtt_time(t):
    t = t.strip().replace(",", ".")
    parts = t.split(":")
    try:
        parts = [float(x) for x in parts]
    except ValueError:
        return 0.0
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


# ────────────────────────────────────────────────────────────────────────────
# VTT → 純文字（處理 YouTube 自動字幕的滾動重複）
# ────────────────────────────────────────────────────────────────────────────
TAG_RE = re.compile(r"<[^>]+>")
CUE_TIME_RE = re.compile(r"(\d[\d:.,]*)\s*-->\s*(\d[\d:.,]*)")


def vtt_to_cues(text):
    """回傳 [(start_sec, line_text)]，去掉樣式標籤、位置參數、空白與滾動重複。"""
    cues, cur_start = [], None
    last_line = None
    lines = [ln.strip("﻿").rstrip() for ln in text.splitlines()]
    is_time = [bool(CUE_TIME_RE.search(ln)) for ln in lines]

    def next_is_time(i):
        """下一個非空行是不是時間碼（用來認出 srt 的 cue 序號）"""
        for j in range(i + 1, len(lines)):
            if lines[j].strip():
                return is_time[j]
        return False

    # 只以「時間碼行」當區塊邊界：YouTube 自動字幕的 cue 內會夾一行空白，不能用空行切
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE", "REGION")):
            continue
        if is_time[i]:
            cur_start = parse_vtt_time(CUE_TIME_RE.search(line).group(1))
            continue
        if cur_start is None or (line.strip().isdigit() and next_is_time(i)):
            continue  # 檔頭雜訊 / srt cue 序號
        ln = TAG_RE.sub("", line).replace("&nbsp;", " ").replace("&amp;", "&")
        ln = ln.replace("&lt;", "<").replace("&gt;", ">").strip()
        if ln:
            cues.append((cur_start, ln))

    # 自動字幕會把同一句連續出現 2-3 次（滾動效果）→ 只留第一次
    dedup = []
    for st, ln in cues:
        if ln == last_line:
            continue
        # 前一句是本句的字首（滾動累積）→ 用本句取代前一句
        if dedup and ln.startswith(dedup[-1][1]) and len(ln) > len(dedup[-1][1]):
            dedup[-1] = (dedup[-1][0], ln)
        else:
            dedup.append((st, ln))
        last_line = ln
    return dedup


def cues_to_paragraphs(cues, gap_sec=8.0, max_chars=400):
    """依時間間隔與長度切段；每段開頭帶時間戳，方便 analyst_claims 引用。"""
    paras, cur, cur_start, last_t = [], [], None, None
    zh = lambda s: bool(re.search(r"[一-鿿]", s))
    for st, ln in cues:
        if cur and (st - last_t > gap_sec or sum(len(x) for x in cur) > max_chars):
            paras.append((cur_start, cur))
            cur, cur_start = [], None
        if cur_start is None:
            cur_start = st
        cur.append(ln)
        last_t = st
    if cur:
        paras.append((cur_start, cur))
    out = []
    for st, lines in paras:
        joiner = "" if all(zh(x) for x in lines) else " "
        out.append(f"[{fmt_ts(st)}] " + joiner.join(lines))
    return out


# ────────────────────────────────────────────────────────────────────────────
# yt-dlp
# ────────────────────────────────────────────────────────────────────────────
def ydl_base_opts(cookies=None, quiet=True):
    rt = os.environ.get("YT_JS_RUNTIME", "node").strip().lower() or "node"
    opts = {
        "quiet": quiet, "no_warnings": quiet, "noprogress": True,
        "js_runtimes": {rt: {}},                 # SOP: --js-runtimes node
        "remote_components": ["ejs:github"],     # 需要時自動抓挑戰解算腳本
        "retries": 3, "socket_timeout": 30,
        "ignoreerrors": False,
    }
    cookies = cookies or os.environ.get("YT_COOKIES_FILE", "")
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = str(cookies)
    return opts


def pick_lang(available, priority):
    """available = {lang: [...]}；回傳第一個命中的語言鍵（含 zh-TW-xxx 之類的變體）。"""
    keys = list(available or {})
    for want in priority:                       # 逐一優先語言：先完全相符，再接受變體（zh-Hant-TW）
        w = want.lower()
        for k in keys:
            if k.lower() == w:
                return k
        for k in keys:
            if k.lower().startswith(w + "-"):
                return k
    return None


# 雲端 IP（GitHub Actions）常被要求登入驗證；換 player client 常可繞過，沒 cookies 也能抓字幕。
# 第一組 None = yt-dlp 預設；之後依序輪流。可用 YT_PLAYER_CLIENTS="tv,mweb" 指定單一組。
CLIENT_FALLBACKS = [None, "tv,web_embedded", "mweb", "android", "ios"]
BOT_CHECK_RE = re.compile(r"Sign in to confirm|not a bot|HTTP Error 429|Precondition check failed", re.I)


def fetch_info(video_id, cookies=None):
    import yt_dlp
    url = f"https://www.youtube.com/watch?v={video_id}"
    forced = os.environ.get("YT_PLAYER_CLIENTS", "").strip()
    plans = [forced] if forced else CLIENT_FALLBACKS
    last = None
    for clients in plans:
        opts = {**ydl_base_opts(cookies), "skip_download": True}
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients.split(",")}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info and clients:
                log(f"  player_client={clients} 成功")
            return info
        except Exception as e:
            last = e
            msg = str(e).splitlines()[0]
            if not BOT_CHECK_RE.search(msg):
                raise                       # 不是驗證/限流問題，換客戶端也沒用
            log(f"  {'預設客戶端' if not clients else 'player_client=' + clients} 被擋：{msg[:90]}…換下一組")
    raise last


def download_subtitle(info, lang, automatic, cookies=None):
    """用 yt-dlp 抓一種語言的 vtt，回傳字幕文字（不落地檔案）。"""
    import yt_dlp
    tracks = (info.get("automatic_captions") if automatic else info.get("subtitles")) or {}
    cands = tracks.get(lang) or []
    # 優先 vtt，其次 srv3/json3/ttml（有 url 就能抓）
    cands = sorted(cands, key=lambda t: {"vtt": 0, "srt": 1}.get(t.get("ext"), 5))
    if not cands:
        return None
    with yt_dlp.YoutubeDL(ydl_base_opts(cookies)) as ydl:
        for t in cands:
            url = t.get("url")
            if not url:
                continue
            try:
                data = ydl.urlopen(url).read().decode("utf-8", errors="replace")
                if t.get("ext") in ("vtt", "srt") or "-->" in data[:2000]:
                    return data
            except Exception as e:
                log(f"  字幕下載失敗 {lang}/{t.get('ext')}: {e}")
    return None


def whisper_transcribe(video_id, cookies=None, model_size=None):
    """無字幕的備援：下載音訊 + faster-whisper。沒裝套件就回 None（不裝也能跑主流程）。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("  未安裝 faster-whisper，略過轉錄（pip install faster-whisper）")
        return None
    import yt_dlp
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(AUDIO_DIR / f"{video_id}.%(ext)s")
    with yt_dlp.YoutubeDL({**ydl_base_opts(cookies), "format": "bestaudio/best",
                           "outtmpl": out_tmpl}) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    files = sorted(AUDIO_DIR.glob(f"{video_id}.*"))
    if not files:
        return None
    size = model_size or os.environ.get("WHISPER_MODEL", "small")
    log(f"  whisper({size}) 轉錄中…（無字幕影片，數字可能有誤）")
    model = WhisperModel(size, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(files[0]), language="zh", vad_filter=True)
    cues = [(s.start, s.text.strip()) for s in segments if s.text.strip()]
    for f in files:
        try: f.unlink()
        except Exception: pass
    return cues


# ────────────────────────────────────────────────────────────────────────────
# 主流程：單支影片
# ────────────────────────────────────────────────────────────────────────────
def write_transcript(info, paragraphs, lang, source):
    vid = info["id"]
    upload = info.get("upload_date") or dt.date.today().strftime("%Y%m%d")
    upload_iso = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
    fname = f"{upload}_{vid}.md"
    title = (info.get("title") or "").strip()
    channel = info.get("channel") or info.get("uploader") or ""
    dur = fmt_ts(info.get("duration") or 0)
    head = [
        f"# {title}",
        "",
        f"- 影片：https://www.youtube.com/watch?v={vid}",
        f"- 頻道：{channel}",
        f"- 上傳日：{upload_iso}　長度：{dur}",
        f"- 逐字稿來源：{source}（{lang}）" + ("；**自動字幕/轉錄，數字與專有名詞可能有誤，引用前核對**" if source != "人工字幕" else ""),
        f"- 抓取時間：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "- 用途：萃取可驗證主張 → analyst_claims.md（論點永不直接變成買賣建議）",
        "",
        "---",
        "",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / fname).write_text("\n".join(head) + "\n\n".join(paragraphs) + "\n", encoding="utf-8")
    return fname, upload_iso, title, channel


def process_video(video_id, index, langs, use_whisper=False, cookies=None, force=False):
    """回傳 (status, entry)。status ∈ ok / skip / no_subs / error"""
    if not force and index.get(video_id, {}).get("status") == "ok":
        return "skip", index[video_id]
    log(f"▶ {video_id}")
    try:
        info = fetch_info(video_id, cookies)
    except Exception as e:
        msg = str(e).splitlines()[0][:200]
        log(f"  ✗ 讀取資訊失敗：{msg}")
        entry = {"status": "error", "error": msg, "fetched_at": dt.datetime.now().isoformat(timespec="minutes")}
        index[video_id] = {**index.get(video_id, {}), **entry}
        return "error", entry
    if info.get("_type") == "playlist":
        log("  這是播放清單/頻道，不是單支影片，略過（請放進 video_sources.json）")
        return "error", {}

    title = (info.get("title") or "")[:60]
    log(f"  {title}")
    cues, lang, source = None, None, None

    k = pick_lang(info.get("subtitles"), langs)
    if k:
        raw = download_subtitle(info, k, automatic=False, cookies=cookies)
        if raw:
            cues, lang, source = vtt_to_cues(raw), k, "人工字幕"
    if not cues:
        k = pick_lang(info.get("automatic_captions"), langs)
        if k:
            raw = download_subtitle(info, k, automatic=True, cookies=cookies)
            if raw:
                cues, lang, source = vtt_to_cues(raw), k, "自動字幕"
    if not cues and use_whisper:
        try:
            cues = whisper_transcribe(video_id, cookies)
            if cues:
                lang, source = "zh", "whisper 轉錄"
        except Exception as e:
            log(f"  whisper 失敗：{str(e)[:150]}")

    base = {
        "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
        "upload_date": info.get("upload_date"), "duration": info.get("duration"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "fetched_at": dt.datetime.now().isoformat(timespec="minutes"),
    }
    if not cues:
        avail = sorted(set(list(info.get("subtitles") or []) + list(info.get("automatic_captions") or [])))
        log(f"  ✗ 無可用字幕（可用語言：{', '.join(avail) or '無'}）" + ("" if use_whisper else "；加 --whisper 可轉錄"))
        entry = {**base, "status": "no_subs", "available_langs": avail}
        index[video_id] = entry
        return "no_subs", entry

    paragraphs = cues_to_paragraphs(cues)
    fname, upload_iso, _, _ = write_transcript(info, paragraphs, lang, source)
    n_chars = sum(len(p) for p in paragraphs)
    log(f"  ✓ {source} {lang} → {fname}（{len(paragraphs)} 段 / {n_chars} 字）")
    entry = {**base, "status": "ok", "lang": lang, "source": source, "file": fname,
             "paragraphs": len(paragraphs), "chars": n_chars}
    index[video_id] = entry
    return "ok", entry


# ────────────────────────────────────────────────────────────────────────────
# 佇列與頻道追蹤
# ────────────────────────────────────────────────────────────────────────────
def read_queue():
    if not QUEUE.exists():
        return []
    ids = []
    for ln in QUEUE.read_text(encoding="utf-8").splitlines():
        s = ln.split("#", 1)[0].strip()
        if not s:
            continue
        vid = extract_video_id(s)
        if vid:
            ids.append(vid)
        else:
            log(f"佇列內認不出影片 ID，略過：{s}")
    return ids


def rewrite_queue(done_ids):
    """把已處理（ok / no_subs）的行拿掉；失敗的留著下次再試。"""
    if not QUEUE.exists() or not done_ids:
        return
    keep = []
    for ln in QUEUE.read_text(encoding="utf-8").splitlines():
        s = ln.split("#", 1)[0].strip()
        vid = extract_video_id(s) if s else None
        if vid and vid in done_ids:
            continue
        keep.append(ln)
    QUEUE.write_text("\n".join(keep).rstrip("\n") + "\n", encoding="utf-8")


def resolve_channel_by_search(query, cookies=None):
    """用 ytsearch 找頻道：取前 10 筆影片，挑上傳者名稱含關鍵字者的頻道網址；找不到回 None。"""
    import yt_dlp
    opts = {**ydl_base_opts(cookies), "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch10:{query}", download=False)
    tokens = [t.lower() for t in query.split() if len(t) >= 2]   # 任一關鍵詞命中上傳者名稱即算
    best = None
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        who = re.sub(r"\s+", "", (e.get("channel") or e.get("uploader") or "")).lower()
        url = e.get("channel_url") or e.get("uploader_url") or (f"https://www.youtube.com/channel/{e['channel_id']}" if e.get("channel_id") else None)
        if not url:
            continue
        if any(t in who for t in tokens):
            return url.rstrip("/") + "/videos", e.get("channel") or e.get("uploader")
        best = best or (url.rstrip("/") + "/videos", e.get("channel") or e.get("uploader"))
    return best


def list_source_videos(src, cookies=None):
    """頻道/播放清單 → 最新 N 支影片 ID（flat 模式，不逐支解析）。"""
    import yt_dlp
    n = int(src.get("max_latest", 3))
    opts = {**ydl_base_opts(cookies), "extract_flat": "in_playlist", "playlistend": n,
            "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(src["url"], download=False)
    ids = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        vid = e.get("id") or extract_video_id(e.get("url") or "")
        if vid and VIDEO_ID_RE.match(vid):
            ids.append(vid)
    return ids[:n]


def watch_sources(cookies=None):
    cfg = load_json(SOURCES, {})
    out, changed = [], False
    for src in cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        label = src.get("name") or src.get("url") or src.get("search") or "?"
        # 沒 url 只有 search（或 url 還是佔位符）→ 用搜尋解析頻道並回寫，之後就不用再搜
        if (not src.get("url") or "請填入" in src.get("url", "")) and src.get("search"):
            try:
                hit = resolve_channel_by_search(src["search"], cookies)
                if hit:
                    src["url"], src["resolved_channel"] = hit[0], hit[1]
                    changed = True
                    log(f"頻道「{label}」由搜尋解析為 {hit[1]} → {hit[0]}")
                else:
                    log(f"頻道「{label}」搜尋不到，略過"); continue
            except Exception as e:
                log(f"頻道「{label}」搜尋失敗：{str(e).splitlines()[0][:150]}"); continue
        if not src.get("url"):
            continue
        try:
            ids = list_source_videos(src, cookies)
            log(f"頻道「{label}」最新 {len(ids)} 支：{' '.join(ids)}")
            out.extend(ids)
        except Exception as e:
            msg = str(e).splitlines()[0][:150]
            log(f"頻道「{label}」讀取失敗：{msg}")
            # 頻道網址壞掉（404/不存在）且有 search → 清掉 url，下次改用搜尋解析
            if src.get("search") and re.search(r"404|not exist|does not exist|Unable to recognize|not found", msg, re.I):
                src["url"] = ""; changed = True
                log(f"  已清除失效網址，下次用 search「{src['search']}」重新解析")
    if changed:
        save_json(SOURCES, cfg)
    return out


def notify(new_entries):
    if not new_entries:
        return
    try:
        import notify_line
    except ImportError:
        return
    lines = [f"📝 新逐字稿 {len(new_entries)} 支"]
    for e in new_entries:
        lines.append(f"・{(e.get('channel') or '')[:12]}｜{(e.get('title') or '')[:40]}（{e.get('source')}）")
    lines.append("→ 記得萃取論點入 analyst_claims.md")
    notify_line.push("\n".join(lines))


# ────────────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="自動下載 YouTube 逐字稿到 data/transcripts/")
    ap.add_argument("videos", nargs="*", help="YouTube 網址或影片 ID")
    ap.add_argument("--queue", action="store_true", help="處理 data/video_queue.txt 佇列")
    ap.add_argument("--watch", action="store_true", help="掃 data/video_sources.json 追蹤頻道的最新影片")
    ap.add_argument("--whisper", action="store_true", help="無字幕時用 faster-whisper 轉錄（需自行安裝，慢）")
    ap.add_argument("--force", action="store_true", help="已抓過的也重抓")
    ap.add_argument("--langs", default=",".join(LANG_PRIORITY), help="字幕語言優先序，逗號分隔")
    ap.add_argument("--cookies", default=None, help="cookies.txt 路徑（或設 YT_COOKIES_FILE）")
    ap.add_argument("--max-new", type=int, default=20, help="單次最多處理幾支新影片")
    ap.add_argument("--sleep", type=float, default=2.0, help="每支影片間隔秒數（避免被限流）")
    ap.add_argument("--notify", action="store_true", help="有新逐字稿時推 LINE（沿用 notify_line）")
    args = ap.parse_args(argv)

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    targets = []
    for v in args.videos:
        vid = extract_video_id(v)
        if vid:
            targets.append(vid)
        else:
            log(f"認不出影片 ID，略過：{v}")
    queue_ids = read_queue() if args.queue else []
    targets += queue_ids
    if args.watch:
        targets += watch_sources(args.cookies)
    if not targets:
        ap.print_help()
        log("沒有任何目標。給網址、或加 --queue / --watch。")
        return 0

    targets = list(dict.fromkeys(targets))  # 去重保序
    index = load_json(INDEX, {})
    todo = [t for t in targets if args.force or index.get(t, {}).get("status") != "ok"]
    log(f"目標 {len(targets)} 支 / 已有 {len(targets) - len(todo)} / 待抓 {len(todo)}（上限 {args.max_new}）")
    todo = todo[:args.max_new]

    new_entries, done_ids, n_err = [], set(), 0
    for i, vid in enumerate(todo):
        status, entry = process_video(vid, index, langs, args.whisper, args.cookies, args.force)
        save_json(INDEX, index)  # 逐支落地，中途失敗不丟進度
        if status == "ok":
            new_entries.append(entry)
        if status in ("ok", "no_subs"):
            done_ids.add(vid)
        elif status == "error":
            n_err += 1
        if i < len(todo) - 1 and args.sleep > 0:
            time.sleep(args.sleep)

    if args.queue:
        rewrite_queue(done_ids & set(queue_ids))
    if args.notify:
        notify(new_entries)
    log(f"完成：新增 {len(new_entries)} / 無字幕 {len(done_ids) - len(new_entries)} / 失敗 {n_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
