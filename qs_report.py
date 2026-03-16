#!/usr/bin/env python3
"""
전략 #1 QuantStats 풀 리포트
- 벤치마크: BTC 바이앤홀드
- HTML 리포트 생성
"""
import json, requests, time, warnings
import pandas as pd, numpy as np
import quantstats as qs
warnings.filterwarnings('ignore')

SYMBOLS = {
    'BTCUSDT':'BTC','ETHUSDT':'ETH','XRPUSDT':'XRP','SOLUSDT':'SOL',
    'BNBUSDT':'BNB','DOGEUSDT':'DOGE','ADAUSDT':'ADA','AVAXUSDT':'AVAX',
    'LINKUSDT':'LINK','DOTUSDT':'DOT',
}

def fetch_klines(sym, limit=1000):
    all_data=[]; end_time=None
    for _ in range(12):
        params={'symbol':sym,'interval':'1d','limit':limit}
        if end_time: params['endTime']=end_time
        try:
            r=requests.get('https://api.binance.com/api/v3/klines',params=params,timeout=15)
            d=r.json()
            if not isinstance(d,list) or len(d)==0: break
            all_data=d+all_data; end_time=d[0][0]-1
            if len(d)<limit: break; time.sleep(0.2)
        except: time.sleep(1); continue
    if not all_data: return None
    df=pd.DataFrame(all_data,columns=['ot','open','high','low','close','vol','ct','qv','trades','tbv','tbq','ig'])
    df['date']=pd.to_datetime(df['ot'],unit='ms').dt.normalize()
    for c in ['open','high','low','close','vol','qv','tbv','trades']: df[c]=df[c].astype(float)
    return df.drop_duplicates(subset='date').set_index('date').sort_index()

def fetch_funding(sym):
    ad=[]; st=int(pd.Timestamp('2019-01-01').timestamp()*1000)
    for _ in range(25):
        p={'symbol':sym,'limit':1000,'startTime':st}
        try:
            d=requests.get('https://fapi.binance.com/fapi/v1/fundingRate',params=p,timeout=10).json()
            if not d or not isinstance(d,list): break
            ad.extend(d); st=d[-1]['fundingTime']+1
            if len(d)<1000: break; time.sleep(0.1)
        except: break
    if not ad: return None
    df=pd.DataFrame(ad)
    df['date']=pd.to_datetime(df['fundingTime'],unit='ms')
    df['rate']=df['fundingRate'].astype(float)
    return df.set_index('date')['rate'].resample('D').sum()

def zs(df):
    m=df.mean(axis=1); s=df.std(axis=1).replace(0,1)
    return df.sub(m,axis=0).div(s,axis=0)

print("데이터 수집 중...")
kl, fu = {}, {}
for sym, nm in SYMBOLS.items():
    k = fetch_klines(sym, 1000)
    if k is None: continue
    kl[sym] = k
    f = fetch_funding(sym)
    fu[sym] = f
    print(f"  {nm:>5}: {len(k)}일, 펀딩 {len(f) if f is not None else 0}일")
    time.sleep(0.1)

cl = pd.DataFrame({s: kl[s]['close'] for s in kl}).dropna()
hi = pd.DataFrame({s: kl[s]['high'] for s in kl}).reindex(cl.index).ffill()
lo = pd.DataFrame({s: kl[s]['low'] for s in kl}).reindex(cl.index).ffill()
vo = pd.DataFrame({s: kl[s]['vol'] for s in kl}).reindex(cl.index).ffill()
qv = pd.DataFrame({s: kl[s]['qv'] for s in kl}).reindex(cl.index).ffill()
tb = pd.DataFrame({s: kl[s]['tbv'] for s in kl}).reindex(cl.index).ffill()
tr = pd.DataFrame({s: kl[s]['trades'] for s in kl}).reindex(cl.index).ffill()
for sym in fu:
    if fu[sym] is None: continue
    fu[sym] = fu[sym].reindex(cl.index).fillna(0)

ret = cl.pct_change()
dates = cl.index
syms = list(cl.columns)
btc_r = ret.iloc[:, 0]
print(f"기간: {dates[0].date()} ~ {dates[-1].date()} ({len(dates)}일)")

print("팩터 생성 중...")
F = {}
# 모멘텀
for lb in [3,5,7,10,14,21,30,45,60,90,120]:
    F[f'mom{lb}'] = zs(cl.pct_change(lb).rank(axis=1,pct=True))
# 리버설
for lb in [1,2,3,5,10]:
    F[f'rev{lb}'] = zs(1-cl.pct_change(lb).rank(axis=1,pct=True))
# 볼륨 모멘텀
for lb in [7,14,21,30]:
    vw=(ret*vo).rolling(lb).sum()/vo.rolling(lb).sum().replace(0,1)
    F[f'vmom{lb}'] = zs(vw.rank(axis=1,pct=True))
# 캐리/펀딩
for lb_c in [7,21]:
    cdf=pd.DataFrame(0.0,index=dates,columns=syms)
    for s in syms:
        f=fu.get(s)
        if f is None: continue
        cdf[s]=-f.reindex(dates).fillna(0).rolling(lb_c,min_periods=1).mean()*10000
    F[f'carry{lb_c}']=zs(cdf)
for lb_f in [7,14]:
    fmr=pd.DataFrame(0.0,index=dates,columns=syms)
    for s in syms:
        f=fu.get(s)
        if f is None: continue
        fr=f.reindex(dates).fillna(0)
        mu=fr.rolling(lb_f,min_periods=1).mean()
        sig=fr.rolling(lb_f,min_periods=1).std().replace(0,0.001)
        fmr[s]=-(fr-mu)/sig
    F[f'fmr{lb_f}']=zs(fmr)
# 변동성
for lb in [10,20,40]:
    F[f'lvol{lb}']=zs(ret.rolling(lb).std()*np.sqrt(365)).rank(axis=1,pct=True,ascending=False)
F['vbrk']=zs((vo/vo.rolling(20).mean().replace(0,1)).rank(axis=1,pct=True))
rng=(hi-lo)/cl
F['rcomp']=zs((rng.rolling(20).mean()/rng.rolling(5).mean().replace(0,1)).rank(axis=1,pct=True))
F['vol_reg']=zs((ret.rolling(10).std()/ret.rolling(60).std().replace(0,1)).rank(axis=1,pct=True))
dv=ret.clip(upper=0).rolling(20).std()
uv=ret.clip(lower=0).rolling(20).std().replace(0,0.001)
F['vol_asym']=zs((dv/uv).rank(axis=1,pct=True,ascending=False))
atr_df=pd.DataFrame(index=dates,columns=syms,dtype=float)
for c in syms:
    tr_val=pd.concat([hi[c]-lo[c],(hi[c]-cl[c].shift(1)).abs(),(lo[c]-cl[c].shift(1)).abs()],axis=1).max(axis=1)
    atr_df[c]=tr_val.rolling(14).mean()/cl[c]
F['atr']=zs(atr_df.rank(axis=1,pct=True,ascending=False))

# 기술적 지표
for lb_rsi in [7,14,21]:
    d=cl.diff(); g=d.clip(lower=0).rolling(lb_rsi).mean()
    l=(-d.clip(upper=0)).rolling(lb_rsi).mean().replace(0,1)
    F[f'rsi{lb_rsi}']=zs(1-(100-100/(1+g/l)).rank(axis=1,pct=True))
ema12=cl.ewm(span=12).mean(); ema26=cl.ewm(span=26).mean()
macd=ema12-ema26; signal=macd.ewm(span=9).mean()
F['macd']=zs((macd-signal).rank(axis=1,pct=True))
F['macd_hist']=zs(((macd-signal)/cl).rank(axis=1,pct=True))
for lb_bb in [20,40]:
    ma=cl.rolling(lb_bb).mean(); sd=cl.rolling(lb_bb).std().replace(0,1)
    F[f'bb{lb_bb}']=zs(((cl-ma)/sd).rank(axis=1,pct=True,ascending=False))
tp=(hi+lo+cl)/3; ma_tp=tp.rolling(20).mean()
md=tp.rolling(20).apply(lambda x:np.mean(np.abs(x-x.mean())),raw=True).replace(0,1)
F['cci']=zs(((tp-ma_tp)/(0.015*md)).rank(axis=1,pct=True,ascending=False))
hh=hi.rolling(14).max(); ll=lo.rolling(14).min()
F['willr']=zs(((hh-cl)/(hh-ll).replace(0,1)).rank(axis=1,pct=True))
hh14=hi.rolling(14).max(); ll14=lo.rolling(14).min()
stoch_k=(cl-ll14)/(hh14-ll14).replace(0,1)
F['stoch_k']=zs(stoch_k.rank(axis=1,pct=True,ascending=False))
F['stoch_d']=zs(stoch_k.rolling(3).mean().rank(axis=1,pct=True,ascending=False))
# 고저/레인지
for lb_hl in [20,60,120]:
    hhx=cl.rolling(lb_hl).max(); llx=cl.rolling(lb_hl).min()
    F[f'hilo{lb_hl}']=zs(((cl-llx)/(hhx-llx).replace(0,1)).rank(axis=1,pct=True))
F['hl_pos']=zs(((cl-lo)/(hi-lo).replace(0,1)).rank(axis=1,pct=True))
# 베타
for lb_b in [30,60]:
    betas=pd.DataFrame(index=dates,columns=syms,dtype=float)
    for c in syms: betas[c]=ret[c].rolling(lb_b).cov(btc_r)/btc_r.rolling(lb_b).var().replace(0,1)
    F[f'beta{lb_b}']=zs(1-betas.rank(axis=1,pct=True).astype(float))
# 잔차 모멘텀
betas60=pd.DataFrame(index=dates,columns=syms,dtype=float)
for c in syms: betas60[c]=ret[c].rolling(60).cov(btc_r)/btc_r.rolling(60).var().replace(0,1)
resid=pd.DataFrame(index=dates,columns=syms,dtype=float)
for c in syms: resid[c]=ret[c]-betas60[c].fillna(1)*btc_r
for lb_r in [7,14,30]:
    F[f'resid{lb_r}']=zs(resid.rolling(lb_r).sum().rank(axis=1,pct=True))
for w1,w2 in [(14,60),(30,90)]:
    cc=pd.DataFrame(index=dates,columns=syms,dtype=float)
    for c in syms: cc[c]=ret[c].rolling(w1).corr(btc_r)-ret[c].rolling(w2).corr(btc_r)
    F[f'corr_{w1}v{w2}']=zs(cc.rank(axis=1,pct=True).astype(float))

# 거래량/오더플로우
F['qv_surge']=zs((qv/qv.rolling(20).mean().replace(0,1)).rank(axis=1,pct=True))
F['qv_mom7']=zs(qv.pct_change(7,fill_method=None).rank(axis=1,pct=True))
br=tb/vo.replace(0,1)
F['buy_pct']=zs(br.rank(axis=1,pct=True))
for lb_bc in [3,5,10]:
    F[f'buy_chg{lb_bc}']=zs(br.pct_change(lb_bc,fill_method=None).rank(axis=1,pct=True))
F['trade_mom']=zs(tr.pct_change(7,fill_method=None).rank(axis=1,pct=True))
F['trade_acc']=zs(tr.pct_change(3,fill_method=None).rank(axis=1,pct=True)-tr.pct_change(14,fill_method=None).rank(axis=1,pct=True))
obv=pd.DataFrame(0.0,index=dates,columns=syms)
for c in syms:
    sign=np.sign(ret[c].values); v=vo[c].values
    obv[c]=np.cumsum(sign*v)
F['obv_mom']=zs(obv.pct_change(14,fill_method=None).rank(axis=1,pct=True))
vwap=qv/vo.replace(0,1)
F['vwap_dev']=zs(((cl-vwap)/cl).rank(axis=1,pct=True))
for lb_pv in [3,7,14]:
    F[f'pv_div{lb_pv}']=zs(cl.pct_change(lb_pv,fill_method=None).rank(axis=1,pct=True)-vo.pct_change(lb_pv,fill_method=None).rank(axis=1,pct=True))
for lb_sk in [14,30]:
    F[f'skew{lb_sk}']=zs(ret.rolling(lb_sk).apply(lambda x:pd.Series(x).skew(),raw=False).rank(axis=1,pct=True))
F['kurt']=zs(ret.rolling(30).apply(lambda x:pd.Series(x).kurtosis(),raw=False).rank(axis=1,pct=True,ascending=False))
F['autocorr']=zs(ret.rolling(20).apply(lambda x:pd.Series(x).autocorr(),raw=False).rank(axis=1,pct=True))
streak_df=pd.DataFrame(0.0,index=dates,columns=syms)
for c in syms:
    s=0; v=ret[c].values; o=np.zeros(len(v))
    for t in range(1,len(v)):
        if v[t]>0: s=max(1,s+1)
        elif v[t]<0: s=min(-1,s-1)
        else: s=0
        o[t]=s
    streak_df[c]=o
F['streak']=zs(streak_df.rank(axis=1,pct=True))
F['max_loss']=zs(ret.rolling(30).min().rank(axis=1,pct=True,ascending=False))
if 'ETHUSDT' in cl.columns and 'BTCUSDT' in cl.columns:
    eth_btc=cl['ETHUSDT']/cl['BTCUSDT']
    eb_mom=eth_btc.pct_change(14,fill_method=None)
    eb_df=pd.DataFrame(eb_mom.values[:,None]*np.ones((1,len(syms))),index=dates,columns=syms)
    eb_df['BTCUSDT']=-eb_df['BTCUSDT']
    F['eth_btc_mom']=zs(eb_df.rank(axis=1,pct=True))
mkt_ret=ret.mean(axis=1)
excess=ret.sub(mkt_ret,axis=0)
F['excess7']=zs(excess.rolling(7).sum().rank(axis=1,pct=True))
F['excess30']=zs(excess.rolling(30).sum().rank(axis=1,pct=True))
qv_share=qv.div(qv.sum(axis=1),axis=0)
F['dom_chg']=zs(qv_share.pct_change(7,fill_method=None).rank(axis=1,pct=True))
for fast,slow in [(5,20),(10,30),(20,60),(50,200)]:
    if slow>len(dates)//2: continue
    ma_f=cl.rolling(fast).mean(); ma_s=cl.rolling(slow).mean()
    F[f'ma{fast}x{slow}']=zs(((ma_f-ma_s)/ma_s).rank(axis=1,pct=True))
for s_w,l_w in [(5,30),(10,60),(20,90)]:
    F[f'garch_{s_w}v{l_w}']=zs((ret.rolling(s_w).std()/ret.rolling(l_w).std().replace(0,1)).rank(axis=1,pct=True))
print(f"  {len(F)}개 팩터 생성 완료")

# ══════════════════════════════════════════════════════════════
# 전략 #1 백테스트 → 일별 수익률 시리즈 생성
# ══════════════════════════════════════════════════════════════
with open('grinder_results-5.json') as f:
    strat1 = json.load(f)['strategies'][0]

weights = strat1['weights']
rebal = strat1['rebal']

# 스코어 계산
scores = np.zeros((len(ret), len(syms)))
for fname, w in weights.items():
    if fname not in F or abs(w) < 1e-6: continue
    fvals = F[fname].reindex(ret.index).values
    scores += w * np.nan_to_num(fvals, 0)

# 백테스트 (일별 PnL 추출)
rets_np = ret.values
T, N = rets_np.shape
start = 60
cost = 3.0
pnl = np.zeros(T)
w_pos = np.zeros(N)

for t in range(start, T):
    pnl[t] = np.nansum(w_pos * rets_np[t])
    if t % rebal == 0:
        row = np.nan_to_num(scores[t], 0)
        dm = row - np.nanmean(row)
        ab = np.sum(np.abs(dm))
        if ab < 1e-10: continue
        nw = dm / ab
        pnl[t] -= np.sum(np.abs(nw - w_pos)) * cost / 10000
        w_pos = nw

# 일별 수익률 시리즈
strategy_returns = pd.Series(pnl[start:], index=dates[start:], name='Strategy #1')

# BTC 바이앤홀드 수익률
btc_returns = ret['BTCUSDT'].iloc[start:]
btc_returns.name = 'BTC Buy&Hold'

# 동일가중 바이앤홀드 (10코인 평균)
equal_returns = ret.iloc[start:].mean(axis=1)
equal_returns.name = 'Equal-Weight B&H'

print(f"\n전략 #1 백테스트 완료")
print(f"  기간: {strategy_returns.index[0].date()} ~ {strategy_returns.index[-1].date()}")
print(f"  일수: {len(strategy_returns)}")
cum = (1 + strategy_returns).cumprod()
print(f"  누적 수익: {cum.iloc[-1]-1:.1%}")
print(f"  연 Sharpe: {strategy_returns.mean()/strategy_returns.std()*np.sqrt(365):.3f}")

# ══════════════════════════════════════════════════════════════
# QuantStats HTML 리포트 생성
# ══════════════════════════════════════════════════════════════
print("\nQuantStats 리포트 생성 중...")

# 1) 전략 vs BTC 풀 리포트
qs.reports.html(
    strategy_returns,
    benchmark=btc_returns,
    title='Strategy #1 vs BTC Buy&Hold',
    output='strategy1_report.html'
)
print("  ✅ strategy1_report.html 생성")

# 2) 전략 vs 동일가중 풀 리포트
qs.reports.html(
    strategy_returns,
    benchmark=equal_returns,
    title='Strategy #1 vs Equal-Weight Portfolio',
    output='strategy1_vs_equal.html'
)
print("  ✅ strategy1_vs_equal.html 생성")

# 3) 콘솔 요약
print("\n" + "=" * 70)
print("전략 #1 vs BTC vs 동일가중 요약")
print("=" * 70)

for name, rets_s in [('Strategy #1', strategy_returns), ('BTC B&H', btc_returns), ('Equal-Weight', equal_returns)]:
    cum = (1 + rets_s).cumprod()
    total_ret = cum.iloc[-1] - 1
    ann_ret = (1 + total_ret) ** (365 / len(rets_s)) - 1
    vol = rets_s.std() * np.sqrt(365)
    sharpe = ann_ret / vol if vol > 0 else 0
    dd = cum / cum.cummax() - 1
    mdd = dd.min()
    calmar = ann_ret / abs(mdd) if mdd != 0 else 0
    win_rate = (rets_s > 0).mean()
    print(f"\n  {name}:")
    print(f"    누적 수익:    {total_ret:>8.1%}")
    print(f"    연 수익률:    {ann_ret:>8.1%}")
    print(f"    연 변동성:    {vol:>8.1%}")
    print(f"    Sharpe:       {sharpe:>8.3f}")
    print(f"    MDD:          {mdd:>8.1%}")
    print(f"    Calmar:       {calmar:>8.2f}")
    print(f"    승률:         {win_rate:>8.1%}")

print("\n완료! strategy1_report.html / strategy1_vs_equal.html 열어봐")
