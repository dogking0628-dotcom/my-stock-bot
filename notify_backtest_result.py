#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讀 backtest_hot_money_radar.json，推結果到 LINE"""
import os, json, sys, urllib.request

token = os.environ.get("LINE_TOKEN", "")
user = os.environ.get("LINE_USER_ID", "")
if not token or not user:
    print("LINE token/user missing")
    sys.exit(0)

try:
    with open("backtest_hot_money_radar.json", encoding="utf-8") as f:
        d = json.load(f)
except FileNotFoundError:
    msg = "❌ 回測失敗（找不到結果檔）\n查 GitHub Actions logs"
else:
    final = d["final_cash"]
    n = d["n_trades"]
    trades = d["trades"]
    total_ret = (final / 1_000_000 - 1) * 100
    wins = sum(1 for t in trades if t["ret_pct"] > 0)
    wr = wins / n * 100 if n else 0
    total_gains = sum(t["ret_pct"] for t in trades if t["ret_pct"] > 0)
    total_loss = abs(sum(t["ret_pct"] for t in trades if t["ret_pct"] < 0))
    pf = total_gains / total_loss if total_loss > 0 else 999
    best = max(trades, key=lambda x: x["ret_pct"]) if trades else {}
    worst = min(trades, key=lambda x: x["ret_pct"]) if trades else {}

    # 計算 CAGR
    from datetime import datetime
    try:
        start = datetime.strptime(d["params"]["start"], "%Y-%m-%d")
        end = datetime.strptime(d["params"]["end"], "%Y-%m-%d")
        years = (end - start).days / 365.25
        cagr = ((final / 1_000_000) ** (1 / years) - 1) * 100 if years > 0 else 0
    except Exception:
        cagr = 0
        years = 0

    msg = (
        f"📊 Hot Money Radar 回測完成\n\n"
        f"期間：{d['params']['start']} ~ {d['params']['end']} ({years:.1f}年)\n"
        f"策略：升溫族群 +{d['params']['rising_threshold']}%\n"
        f"韌性過濾：{d['params']['use_resilience']}\n"
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 績效\n"
        f"  總報酬：{total_ret:+.1f}%\n"
        f"  CAGR：{cagr:+.1f}%\n"
        f"  最終資金：${final:,.0f}\n"
        f"  交易筆數：{n}\n"
        f"  勝率：{wr:.0f}%\n"
        f"  獲利因子：{pf:.2f}\n\n"
        f"🎯 最佳：{best.get('ticker','-')} {best.get('industry','')} {best.get('ret_pct',0):+.1f}%\n"
        f"💀 最差：{worst.get('ticker','-')} {worst.get('industry','')} {worst.get('ret_pct',0):+.1f}%\n\n"
        f"vs V4 五年績效對照：\n"
        f"  V4: +226.8% / +24.8% CAGR / PF 3.40 / 勝率 46%\n\n"
        f"完整 trades 在 backtest_hot_money_radar.json"
    )

body = json.dumps({"to": user, "messages": [{"type": "text", "text": msg}]},
                  ensure_ascii=False).encode("utf-8")
req = urllib.request.Request("https://api.line.me/v2/bot/message/push",
                             data=body, method="POST",
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"LINE notify: {r.status}")
except Exception as e:
    print(f"notify failed: {e}")
