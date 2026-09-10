# -*- coding: utf-8 -*-
"""
盤中停損 vs 收盤判定：用戶 2026-09-10 松川盤中觸 303 賣出（收盤 308 未觸發）引發的問題
═══════════════════════════════════════════════════════════════
現行規則：收盤 < max(20MA, 進場×0.93) → 隔日開盤賣（已驗證優於觸發日收盤賣/隔日收盤賣）
本測新增：盤中觸價即停損 —— 當日 Low ≤ 樓地板 → 當場以樓地板價成交（同日出場）

變體（其餘全同 V4.6：正式月線 ATH + 收盤價進場）：
  close_rule   = 現行（基準）
  intraday_7   = 盤中觸 進場×0.93 即賣
  intraday_ma  = 盤中觸 max(20MA, 進場×0.93) 即賣（20MA 亦盤中觸價）
"""
import sys, os, io, json
import datetime as dt
if not isinstance(sys.stdout, io.TextIOWrapper) or (sys.stdout.encoding or '').lower() != 'utf-8':
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from collections import defaultdict, deque
import backtest_strategy as bs
import backtest_v4_1 as v41
import backtest_v45_candidate as v45
import backtest_entry_timing as et
import backtest_ath_definition as ad
from industry_map_loader import get_industry

ALLOWED=v41.ALLOWED; MIN_MCAP=v41.MIN_MCAP; SCORE=80; LOSER_W=7; FLOOR=0.93


def run(mode, tstart, history, mcap, us_chg, regime, below20, cums, df_idx, all_dates):
    cash=bs.INITIAL; positions={}; trades=[]; losers=deque(maxlen=400)
    for di,d in enumerate(all_dates):
        if di<200: continue
        ds=d.strftime("%Y-%m-%d"); ps=all_dates[di-1].strftime("%Y-%m-%d")
        cutoff=d-pd.Timedelta(days=LOSER_W)
        rl={t for ed,t in losers if pd.Timestamp(ed)>=cutoff}
        cands=[]
        for c,df in history.items():
            i=df_idx[c].get(d)
            if i is None or i<200: continue
            f=bs.daily_features(df,i)
            if not f: continue
            f["ticker"]=c; f["industry"]=get_industry(c); f["_i"]=i; cands.append(f)
        cur={r["ticker"]:r for r in cands}
        bi=defaultdict(list)
        for r in cands: bi[r.get("industry") or "未分類"].append(r)
        iu={k:sum(1 for x in v if x["change_pct"]>0)/max(len(v),1) for k,v in bi.items()}
        for r in cands: r["industry_strong"]=iu.get(r.get("industry") or "未分類",0)>=0.6

        for c in list(positions):
            cf=cur.get(c)
            if not cf: continue
            pos=positions[c]; i=cf["_i"]; df=history[c]
            lo=float(df["Low"].iloc[i]); cl=cf["close"]
            pos["peak"]=max(pos["peak"],cl)
            floor=pos["entry_price"]*FLOOR
            trig=None; sp=None
            if mode!="close_rule":
                line = floor if mode=="intraday_7" else max(floor, cf["ma20"])
                if lo <= line:                                  # 盤中觸價 → 當場成交
                    trig="intraday"; sp=min(line, float(df["Open"].iloc[i]))
            if trig is None and (cl<cf["ma20"] or cl<pos["peak"]*0.7 or cl<floor):
                nd=all_dates[di+1] if di+1<len(all_dates) else None
                if nd is None: continue
                ni=df_idx[c].get(nd)
                if ni is None: continue
                trig="close"; sp=float(df["Open"].iloc[ni]); xd=str(nd.date())
            if trig:
                if trig=="intraday": xd=ds
                cash+=pos["shares"]*sp*(1-bs.COMMISSION-bs.TAX)
                ret=(sp/pos["entry_price"]-1)*100
                trades.append({"ticker":c,"entry_date":pos["entry_date"],"exit_date":xd,
                               "entry":pos["entry_price"],"exit":sp,"ret_pct":ret,"how":trig})
                if ret<0: losers.append((xd,c))
                del positions[c]

        if ds<tstart or not regime.get(ds,False) or below20.get(ds,False): continue
        ath=[r for r in cands if r["is_ath"] and r.get("industry") in ALLOWED
             and (mcap.get(r["ticker"]) or 0)>=MIN_MCAP and r["ticker"] not in rl]
        for r in ath:
            sc,_=bs.momentum_score(r); r["score"]=min(sc+v41.us_bonus(r["industry"],ps,us_chg),100)
        ab=defaultdict(list)
        for r in ath: ab[r["industry"]].append(r)
        strongest=None
        for ind,lst in sorted(ab.items(),key=lambda x:-len(x[1])):
            if len(lst)>=3 and sum(1 for x in lst if x["bullish"])/len(lst)>=0.5: strongest=ind; break
        pool=[r for r in (ab[strongest] if strongest else ath) if r["score"]>=SCORE]
        pool=[r for r in pool if not v45.hit_any(cums[r["ticker"]],r["_i"])]
        pool.sort(key=lambda x:(-x["score"],-x["change_pct"]))
        slots=bs.MAX_POS-len(positions)
        if slots<=0: continue
        nd=all_dates[di+1] if di+1<len(all_dates) else None
        if nd is None: continue
        for r in pool[:slots]:
            c=r["ticker"]
            if c in positions: continue
            ni=df_idx[c].get(nd)
            if ni is None: continue
            bp=float(history[c]["Close"].iloc[ni])        # V4.6 收盤價進場
            if cash<bs.PER_POS*0.5: break
            cps=bp*(1+bs.COMMISSION); sh=int(min(bs.PER_POS,cash)/cps/1000)*1000
            if sh<1000: continue
            cash-=sh*cps
            positions[c]={"entry_price":bp,"shares":sh,"peak":bp,"entry_date":str(nd.date())}
    fd=all_dates[-1]
    for c,pos in positions.items():
        i=df_idx[c].get(fd)
        if i is not None: cash+=pos["shares"]*float(history[c]["Close"].iloc[i])*(1-bs.COMMISSION-bs.TAX)
    return cash,trades


def main():
    bs.START_DATE="2020-08-01"; bs.END_DATE=dt.date.today().isoformat()
    codes=bs.load_universe(); mcap=v41.load_mcap()
    us_chg=v41.fetch_us_sectors(); regime=v41.fetch_0050(); below20=v45.fetch_0050_series()
    history=bs.fetch_history(codes)
    print(f"[bt] {len(history)} 檔")
    if len(history)<100: return
    for c,df in history.items(): ad.PMM[id(df)]=ad.build_prior_month_max(df)
    all_dates=sorted(set().union(*[set(df.index) for df in history.values()]))
    all_dates=[d for d in all_dates if d>=pd.Timestamp(bs.START_DATE)]
    df_idx={c:{d:i for i,d in enumerate(df.index)} for c,df in history.items()}
    cums=v45.precompute_cums(history)
    bs.daily_features=ad.patched_features; ad.MODE["v"]="monthly"
    today=dt.date.today()
    W=[("5y",(today-dt.timedelta(days=365*5)).isoformat(),5.0),("2y",(today-dt.timedelta(days=365*2)).isoformat(),2.0)]
    res={}
    print("\n"+"="*78); print("停損執行方式（V4.6 收盤買 + 正式月線定義）"); print("="*78)
    for mode in ["close_rule","intraday_7","intraday_ma"]:
        for wn,ws,wy in W:
            cash,tr=run(mode,ws,history,mcap,us_chg,regime,below20,cums,df_idx,all_dates)
            st=et.stats(cash,tr,wy)
            ic=sum(1 for t in tr if t["how"]=="intraday")
            st["intraday_exits"]=ic
            res[f"{mode}_{wn}"]=st
            print(f"  {mode:<12}{wn}: 報酬{st['total_pct']:>8.1f}% CAGR{st['cagr']:>6.1f}% 筆{st['trades']:>4} "
                  f"勝{st['win_rate']:>5.1f}% 期望{st['expectancy']:>6.2f}% PF{st['pf']:>5.2f} MDD{st['mdd']:>6.1f}% 盤中出場{ic}")
    json.dump(res,io.open("backtest_intraday_stop.json","w",encoding="utf-8"),ensure_ascii=False,indent=1,
              default=lambda o:o.item() if hasattr(o,"item") else str(o))
    print("\n💾 backtest_intraday_stop.json")


if __name__=="__main__": main()
