# -*- coding: utf-8 -*-
"""
V2 × V4.6 整合回測（2026-09-15，用戶要求）
═══════════════════════════════════════════════════════════════
問題：V2 的線型品質濾網（量增≥1.5x、長紅或跳空、RSI 55~75、收盤靠高）加進 V4.6 有沒有用？
     以及「V2 有、V4.6 沒有」的差集股（例：9/15 順德）期望值是多少？
所有變體：進場=隔日收盤（V4.6）、出場=收盤破20MA/峰值-30%/-7%、V4.4 減速器、V4.5 警示濾除、每檔 20 萬(回測)。
  A_v46     ：V4.6 原版（月線 ATH、動能≥80）— 對照
  B_hard    ：V4.6 + V2 四條件全部硬過
  C_bonus   ：V4.6 + V2 每符合一條動能 +5（門檻仍 80）
  D_v2only  ：月線 ATH + V2 四條件 + 動能 <80（＝V4.6 濾掉、V2 會推的差集）
  E_v2exec  ：V2 選股（日線 ATH + V2 四條件、不看動能）+ V4.6 執行
紅線：2y+5y 皆 期望≥+8% / PF≥2.5 / MDD≤-30%。過了也要用戶拍板才上線。
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import backtest_strategy as bs
import backtest_v4_1 as v41
import backtest_v45_candidate as v45
import backtest_entry_timing as et
import backtest_ath_definition as ad

RED = {"exp": 8.0, "pf": 2.5, "mdd": -30.0}


def v2_conds(r):
    return [r.get("vol_ratio", 0) >= 1.5,
            bool(r.get("long_red")) or bool(r.get("gap_up")),
            55 <= r.get("rsi", 0) <= 75,
            bool(r.get("close_near_high"))]


def v2_count(r):
    return sum(1 for x in v2_conds(r) if x)


def make_filter(kind):
    def f(mode, r, df, i):
        if kind == "none":
            return True
        if kind == "hard":
            return all(v2_conds(r))
        if kind == "v2only":
            return all(v2_conds(r)) and r.get("score", 0) < 80
        return True
    return f


_ORIG_SCORE = bs.momentum_score


def bonus_score(r):
    sc, notes = _ORIG_SCORE(r)
    b = 5 * v2_count(r)
    if b:
        notes = list(notes) + [f"V2+{b}"]
    return sc + b, notes


VARIANTS = [
    # name, ath_mode, threshold, filter, bonus
    ("A_v46",    "monthly", 80, "none",   False),
    ("B_hard",   "monthly", 80, "hard",   False),
    ("C_bonus",  "monthly", 80, "none",   True),
    ("D_v2only", "monthly", 0,  "v2only", False),
    ("E_v2exec", "daily",   0,  "hard",   False),
]


def redline(st):
    return st["expectancy"] >= RED["exp"] and st["pf"] >= RED["pf"] and st["mdd"] >= RED["mdd"]


def main():
    bs.START_DATE = "2020-08-01"
    bs.END_DATE = dt.date.today().isoformat()
    print(f"[bt] END_DATE = {bs.END_DATE}")
    codes = bs.load_universe()
    mcap = v41.load_mcap()
    us_chg = v41.fetch_us_sectors()
    regime = v41.fetch_0050()
    below20 = v45.fetch_0050_series()
    history = bs.fetch_history(codes)
    print(f"[bt] 取得 {len(history)} 檔歷史")
    if len(history) < 100:
        print("資料不足"); return
    for c, df in history.items():
        ad.PMM[id(df)] = ad.build_prior_month_max(df)
    all_dates = sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(bs.START_DATE)]
    df_idx = {c: {d: i for i, d in enumerate(df.index)} for c, df in history.items()}
    cums = v45.precompute_cums(history)
    bs.daily_features = ad.patched_features

    today = dt.date.today()
    WINDOWS = [("5y", (today - dt.timedelta(days=365 * 5)).isoformat(), 5.0),
               ("2y", (today - dt.timedelta(days=365 * 2)).isoformat(), 2.0)]
    results, rows = {}, []
    print("\n" + "=" * 90)
    print("V2 × V4.6 整合（進場=隔日收盤、出場同 V4.6）")
    print("=" * 90)
    for name, ath_mode, thr, filt, bonus in VARIANTS:
        ad.MODE["v"] = ath_mode
        et.SCORE_THRESHOLD = thr
        et.sig_filter_ok = make_filter(filt)
        bs.momentum_score = bonus_score if bonus else _ORIG_SCORE
        for wname, wstart, wyears in WINDOWS:
            cash, tr, nsig, nfil = et.run_variant(f"{name}_{wname}", "CLOSE", filt, wstart, history,
                                                  mcap, us_chg, regime, below20, cums, df_idx, all_dates)
            st = et.stats(cash, tr, wyears)
            st["signals"] = nsig; st["filled"] = nfil; st["redline"] = redline(st)
            results[f"{name}_{wname}"] = st
            results[f"{name}_{wname}_trades"] = tr
            rows.append((name, wname, st))
            print(f"  {name:<9} {wname}: 報酬{st['total_pct']:>8.1f}% CAGR{st['cagr']:>6.1f}% 筆{st['trades']:>4} "
                  f"勝{st['win_rate']:>5.1f}% 期望{st['expectancy']:>6.2f}% PF{st['pf']:>5.2f} MDD{st['mdd']:>6.1f}% "
                  f"訊號{nsig:>4} {'✅' if st['redline'] else '❌'}")
    bs.momentum_score = _ORIG_SCORE
    et.SCORE_THRESHOLD = 80

    json.dump(results, io.open("backtest_v2_fusion.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    # markdown
    desc = {"A_v46": "V4.6 原版（對照）", "B_hard": "V4.6 + V2 四條件硬過", "C_bonus": "V4.6 + V2 每條 +5 分",
            "D_v2only": "V2 有、V4.6 沒有（動能<80 的差集）", "E_v2exec": "V2 選股 + V4.6 執行"}
    L = [f"# V2 × V4.6 整合回測（{today}）", "",
         "> 進場一律隔日收盤買（V4.6）、出場同 V4.6、V4.4 減速器與 V4.5 警示濾除皆開。紅線：2y+5y 皆 期望≥+8% / PF≥2.5 / MDD≤-30%。",
         "> V2 四條件：量增≥1.5x、長紅或跳空、RSI 55~75、收盤靠當日高。", "",
         "| 變體 | 說明 | 窗 | 報酬 | CAGR | 筆數 | 勝率 | 每筆期望 | PF | MDD | 訊號數 | 紅線 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, wname, st in rows:
        L.append(f"| {name} | {desc[name]} | {wname} | {st['total_pct']:+.1f}% | {st['cagr']:.1f}% | {st['trades']} | {st['win_rate']:.1f}% | {st['expectancy']:+.2f}% | {st['pf']:.2f} | {st['mdd']:.1f}% | {st['signals']} | {'✅' if st['redline'] else '❌'} |")
    both = {n: all(results[f"{n}_{w}"]["redline"] for w in ("5y", "2y")) for n, *_ in VARIANTS}
    L += ["", "雙窗皆過紅線：" + "、".join(n for n, ok in both.items() if ok) if any(both.values()) else "雙窗皆過紅線：無（A 對照除外時見表）", ""]
    io.open("backtest_v2_fusion.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n💾 backtest_v2_fusion.json / .md")


if __name__ == "__main__":
    main()
