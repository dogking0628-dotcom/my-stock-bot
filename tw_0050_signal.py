"""
台股 0050 策略訊號模組
────────────────────────────────────────────────────────
策略：MA200(5日確認) + 熊市三段加碼(10/20/30%) + 牛市超漲減倉(>MA200×1.40)
每日呼叫 check() 回傳當日動作字典，並自動更新狀態檔
"""
import os, json, datetime as dt
import yfinance as yf
import numpy as np

STATE_PATH  = os.path.join(os.path.dirname(__file__), "tw_state.json")
CONFIRM     = 5       # MA200確認天數
DROP_LVL    = [-0.10, -0.20, -0.30]
DROP_AMT    = [0.10,  0.10,  None]  # None = 全倉
EXT_ENTER   = 1.40   # 超漲觸發：價格 > MA200 × 1.40
EXT_EXIT    = 1.30   # 超漲反轉：價格回到 MA200 × 1.30 以下

# ── 狀態管理 ──────────────────────────────────────────────────────────────────
def _default_state():
    return {
        "regime":       "bull",     # bull / bear / reduced
        "allocation":   1.0,        # 1.0=100%，0.7=70%，etc.
        "ref_price":    0.0,        # 熊市/減倉參考價
        "dip_flags":    [False, False, False],  # 三段加碼是否已觸發
        "consec_below": 0,          # 連續低於MA200天數
        "consec_above": 0,          # 連續高於MA200天數
        "bull_high":    0.0,        # 牛市中最高點（追蹤超漲用）
        "last_close":   0.0,
        "last_ma200":   0.0,
        "last_update":  None,
    }

def load_state():
    if not os.path.exists(STATE_PATH):
        return _default_state()
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        s = json.load(f)
    # 補齊舊版缺少的欄位
    d = _default_state()
    d.update(s)
    return d

def save_state(s):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ── 下載資料 ──────────────────────────────────────────────────────────────────
def _fetch_0050(days=260):
    """下載 0050 最近 N 個交易日（最多重試3次），回傳 (prices_series, ma200_series)"""
    import time
    for attempt in range(3):
        time.sleep(3 + attempt * 2)
        try:
            raw = yf.download("0050.TW", period=f"{days+60}d",
                              auto_adjust=True, progress=False)
            if raw is not None and not raw.empty:
                prices = raw["Close"].squeeze().dropna()
                ma200  = prices.rolling(200).mean()
                return prices, ma200
        except Exception:
            pass
        print(f"[TW] 0050 下載失敗，第{attempt+1}次重試...")
    return None, None

# ── 確認訊號計算 ───────────────────────────────────────────────────────────────
def _confirmed_signal(prices, ma200, confirm=CONFIRM):
    """
    回傳最新的確認訊號 (1=牛, 0=熊) 以及連續天數
    """
    raw = (prices > ma200).astype(int).dropna()
    if len(raw) < confirm:
        return 1, 0  # 資料不足預設牛市

    state, consec, pending = int(raw.iloc[0]), 1, int(raw.iloc[0])
    for v in raw.iloc[1:]:
        v = int(v)
        if v == pending:
            consec += 1
        else:
            pending, consec = v, 1
        if consec >= confirm:
            state = v

    return state, consec

# ── 主要檢查函式 ───────────────────────────────────────────────────────────────
def check():
    """
    執行今日訊號檢查，回傳字典：
    {
      "date":        "2026-05-03",
      "close":       90.5,
      "ma200":       72.3,
      "ext_pct":     0.253,          # 距MA200超漲%
      "ext_trigger": 101.22,         # 減倉觸發價
      "regime":      "bull",
      "allocation":  1.0,
      "action":      "HOLD" / "BEAR_IN" / "BULL_IN" / "DIP1/2/3" / "REDUCE" / "REDUCE_EXIT",
      "action_text": "不動",
      "details":     "...",
      "error":       None or str,
    }
    """
    today = dt.datetime.now().strftime("%Y-%m-%d")
    result = {
        "date": today, "close": 0, "ma200": 0,
        "ext_pct": 0, "ext_trigger": 0,
        "regime": "bull", "allocation": 1.0,
        "action": "HOLD", "action_text": "今日不動",
        "details": "", "error": None,
    }

    # ── 下載資料 ──────────────────────────────────────────────────────────────
    prices, ma200 = _fetch_0050()
    if prices is None or len(prices) < 210:
        result["error"] = "0050 資料下載失敗或不足210天"
        return result

    close  = float(prices.iloc[-1])
    ma200v = float(ma200.iloc[-1])
    if np.isnan(ma200v):
        result["error"] = "MA200 計算失敗（歷史資料不足）"
        return result

    result["close"]       = round(close, 2)
    result["ma200"]       = round(ma200v, 2)
    result["ext_pct"]     = round(close / ma200v - 1, 4)
    result["ext_trigger"] = round(ma200v * EXT_ENTER, 2)
    result["ext_exit"]    = round(ma200v * EXT_EXIT, 2)

    # ── 讀取前日狀態 ─────────────────────────────────────────────────────────
    state = load_state()
    regime     = state["regime"]
    alloc      = state["allocation"]
    ref_price  = state["ref_price"]
    dip_flags  = state["dip_flags"][:]
    bull_high  = max(state.get("bull_high", 0.0), close if regime == "bull" else 0)
    actions    = []

    # ── 取得確認訊號 ─────────────────────────────────────────────────────────
    conf_sig, consec_days = _confirmed_signal(prices, ma200)

    # ── 體制切換判斷 ─────────────────────────────────────────────────────────
    if regime in ("bull", "reduced") and conf_sig == 0:
        # 牛/減倉 → 熊（5日確認）
        if alloc > 0.70:
            actions.append(("BEAR_IN",
                f"MA200確認{consec_days}天轉熊 → 賣至70%（持倉 {alloc:.0%}→70%）"))
            alloc = 0.70
        regime    = "bear"
        ref_price = close
        dip_flags = [False, False, False]
        bull_high = 0.0

    elif regime == "bear" and conf_sig == 1:
        # 熊 → 牛（5日確認）
        old_alloc = alloc
        actions.append(("BULL_IN",
            f"MA200確認{consec_days}天轉牛 → 現金全買（持倉 {old_alloc:.0%}→100%）"))
        alloc     = 1.0
        regime    = "bull"
        ref_price = 0.0
        dip_flags = [False, False, False]
        bull_high = close

    # ── 熊市三段加碼 ─────────────────────────────────────────────────────────
    if regime == "bear" and ref_price > 0:
        drop = (close - ref_price) / ref_price
        for i, (lvl, amt) in enumerate(zip(DROP_LVL, DROP_AMT)):
            if drop <= lvl and not dip_flags[i]:
                add_pct = 1.0 - alloc if amt is None else min(amt, 1.0 - alloc)
                new_alloc = alloc + add_pct
                tag   = ["DIP1", "DIP2", "DIP3"][i]
                names = ["加碼①10%", "加碼②10%", "全倉③"]
                actions.append((tag,
                    f"熊市跌{lvl:.0%}（參考價 {ref_price:.1f}）→ {names[i]}（持倉 {alloc:.0%}→{new_alloc:.0%}）"))
                alloc = new_alloc
                dip_flags[i] = True

    # ── 牛市超漲減倉 ─────────────────────────────────────────────────────────
    if regime == "bull":
        bull_high = max(bull_high, close)
        if close > ma200v * EXT_ENTER:
            # 觸發減倉
            if alloc > 0.70:
                actions.append(("REDUCE",
                    f"超MA200漲{(close/ma200v-1):.0%}（>{EXT_ENTER-1:.0%}觸發）→ 賣30%（持倉 {alloc:.0%}→70%）"))
                alloc     = 0.70
                regime    = "reduced"
                ref_price = close
                dip_flags = [False, False, False]

        elif regime == "reduced" and close < ma200v * EXT_EXIT:
            # 減倉反轉
            actions.append(("REDUCE_EXIT",
                f"超漲回落至MA200×{EXT_EXIT}以下 → 買回30%（持倉 {alloc:.0%}→100%）"))
            alloc     = 1.0
            regime    = "bull"
            ref_price = 0.0
            dip_flags = [False, False, False]

    # 如仍是 reduced 體制，也要追蹤三段加碼
    if regime == "reduced" and ref_price > 0:
        drop = (close - ref_price) / ref_price
        for i, (lvl, amt) in enumerate(zip(DROP_LVL, DROP_AMT)):
            if drop <= lvl and not dip_flags[i]:
                add_pct   = 1.0 - alloc if amt is None else min(amt, 1.0 - alloc)
                new_alloc = alloc + add_pct
                tag    = ["DIP1", "DIP2", "DIP3"][i]
                names  = ["加碼①10%", "加碼②10%", "全倉③"]
                actions.append((tag,
                    f"減倉後跌{lvl:.0%} → {names[i]}（持倉 {alloc:.0%}→{new_alloc:.0%}）"))
                alloc = new_alloc
                dip_flags[i] = True

    # ── 更新狀態 ─────────────────────────────────────────────────────────────
    state.update({
        "regime":      regime,
        "allocation":  round(alloc, 4),
        "ref_price":   round(ref_price, 4),
        "dip_flags":   dip_flags,
        "bull_high":   round(bull_high, 4),
        "last_close":  round(close, 4),
        "last_ma200":  round(ma200v, 4),
        "last_update": today,
    })
    save_state(state)

    # ── 整理回傳 ─────────────────────────────────────────────────────────────
    result["regime"]     = regime
    result["allocation"] = alloc

    if actions:
        primary = actions[0]
        result["action"]      = primary[0]
        result["action_text"] = _action_label(primary[0])
        result["details"]     = "\n".join(f"  → {a[1]}" for a in actions)
    else:
        result["action"]      = "HOLD"
        result["action_text"] = "今日不動"
        result["details"]     = _hold_detail(regime, alloc, close, ma200v,
                                              ref_price, dip_flags, bull_high)
    return result

# ── 輔助文字 ──────────────────────────────────────────────────────────────────
def _action_label(action):
    return {
        "BEAR_IN":      "🔴 轉熊 賣至70%",
        "BULL_IN":      "🟢 轉牛 全倉買回",
        "DIP1":         "🟡 加碼①10%（跌10%）",
        "DIP2":         "🟡 加碼②10%（跌20%）",
        "DIP3":         "🔥 全倉買滿（跌30%）",
        "REDUCE":       "🟠 超漲減倉30%",
        "REDUCE_EXIT":  "🟢 減倉反轉 買回",
    }.get(action, action)

def _hold_detail(regime, alloc, close, ma200, ref_price, dip_flags, bull_high):
    lines = []
    regime_cn = {"bull":"🐂 牛市", "bear":"🐻 熊市", "reduced":"🟠 牛市(減倉中)"}
    lines.append(f"體制：{regime_cn.get(regime, regime)}  持倉：{alloc:.0%}")

    ext = close / ma200 - 1 if ma200 > 0 else 0
    lines.append(f"距MA200：{ext:+.1%}  減倉觸發：{ma200*EXT_ENTER:.1f}（需漲{(ma200*EXT_ENTER/close-1)*100:.1f}%）")

    if regime in ("bear", "reduced") and ref_price > 0:
        drop = (close - ref_price) / ref_price
        for i, lvl in enumerate(DROP_LVL):
            if not dip_flags[i]:
                need = ref_price * (1 + lvl)
                lines.append(f"  加碼{['①','②','③'][i]}觸發（跌{abs(lvl):.0%}）：{need:.1f}（需再跌{(need/close-1)*100:.1f}%）")
                break  # 只顯示下一個未觸發的

    return "\n".join(lines)

# ── LINE 訊息區塊 ──────────────────────────────────────────────────────────────
def build_line_block(result):
    """回傳用於 LINE 訊息的文字區塊"""
    if result.get("error"):
        return f"🇹🇼 台股 0050\n  ⚠️ {result['error']}"

    is_action = result["action"] != "HOLD"
    lines = ["🇹🇼 台股 0050 策略"]

    if is_action:
        lines.append(f"🚨 訊號觸發：{result['action_text']}")
        lines.append(result["details"])
        lines.append(f"📌 0050：${result['close']}  MA200：${result['ma200']}")
        lines.append("👉 今日盤後以收盤價執行")
    else:
        lines.append(result["details"])
        lines.append(f"📌 收盤：${result['close']}  MA200：${result['ma200']}")

    return "\n".join(lines)
