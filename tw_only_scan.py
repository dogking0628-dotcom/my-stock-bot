#!/usr/bin/env python3
"""
台股專用掃描器（每日 14:30 台股收盤後執行，掛在 post_close_review.yml）
推播：0050 MA200 訊號 + 大盤脈動（天險警報 + 87MA 強弱）
"""
import sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import tw_0050_signal
import tw_market_pulse
import notify_line


def main():
    today = dt.date.today().strftime("%Y-%m-%d")

    # ── 0050 MA200 訊號 ────────────────────────────────────────────────────
    result = tw_0050_signal.check()
    if result.get("error"):
        notify_line.push(f"❌ 台股掃描失敗 {today}\n{result['error']}")
        return

    tw_block  = tw_0050_signal.build_line_block(result)
    is_action = result["action"] != "HOLD"
    regime_cn = {"bull": "🐂牛市", "bear": "🐻熊市", "reduced": "🟠減倉中"}
    header    = f"🚨 台股訊號觸發 {today}" if is_action else f"📊 台股日報 {today}"

    # ── 大盤脈動（天險 + 87MA）────────────────────────────────────────────
    pulse_block = None
    try:
        pulse = tw_market_pulse.check()
        pulse_block = tw_market_pulse.build_line_block(pulse)
        if pulse.get("tian_xian_hit"):
            header = f"🌋 量能天險！{header}"
    except Exception as e:
        print(f"[Pulse] 失敗（非致命）: {type(e).__name__}: {e}", flush=True)

    parts = [
        header,
        "═" * 20,
        tw_block,
        "─" * 20,
        f"體制：{regime_cn.get(result['regime'], result['regime'])}  持倉：{result['allocation']:.0%}",
        f"收盤：${result['close']}  MA200：${result['ma200']}  距離：{result['ext_pct']:+.1%}",
        f"減倉觸發價：${result['ext_trigger']}",
    ]
    if pulse_block:
        parts += ["─" * 20, pulse_block]

    notify_line.push("\n".join(parts))   # DEBUG=true 時會自動 print，正式則推 LINE


if __name__ == "__main__":
    main()
