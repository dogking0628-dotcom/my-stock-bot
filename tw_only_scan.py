#!/usr/bin/env python3
"""
台股專用掃描器（每日 14:00 台股收盤後執行）
只推台股 0050 訊號，不跑美股掃描
"""
import sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import tw_0050_signal
import notify_line

def main():
    today  = dt.date.today().strftime("%Y-%m-%d")
    result = tw_0050_signal.check()

    if result.get("error"):
        notify_line.push(f"❌ 台股掃描失敗 {today}\n{result['error']}")
        return

    tw_block = tw_0050_signal.build_line_block(result)
    is_action = result["action"] != "HOLD"

    header = f"🚨 台股訊號觸發 {today}" if is_action else f"📊 台股日報 {today}"
    regime_cn = {"bull":"🐂牛市", "bear":"🐻熊市", "reduced":"🟠減倉中"}

    msg = "\n".join([
        header,
        "═" * 20,
        tw_block,
        "─" * 20,
        f"體制：{regime_cn.get(result['regime'], result['regime'])}  持倉：{result['allocation']:.0%}",
        f"收盤：${result['close']}  MA200：${result['ma200']}  距離：{result['ext_pct']:+.1%}",
        f"減倉觸發價：${result['ext_trigger']}",
    ])

    notify_line.push(msg)   # DEBUG=true 時會自動 print，正式則推 LINE

if __name__ == "__main__":
    main()
