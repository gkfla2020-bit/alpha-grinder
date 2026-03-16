"""
알파 그라인더 v5 — 현물 풀히스토리 + 멀티유니버스 진화
=====================================================
v4 대비 변경:
  - 현물(spot) API로 2020년부터 ~5.5년 데이터 수집
  - 펀딩비: 실데이터 있는 구간 그대로 + 없는 구간 평균값 백필
  - 불장/베어/횡보 전 구간 커버 → 실전 강건성 극대화

팩터 87+개 (펀딩비 팩터 포함):
  모멘텀(11) 리버설(5) 볼륨모멘텀(4) 펀딩/캐리(4)
  변동성(8) 기술적지표(12) 고저/레인지(4) BTC베타/상관(8)
  코사인유사도(2) 거래량/오더플로우(10) 가격-거래량괴리(3)
  시계열통계(6) 크로스에셋(5) 이동평균크로스(4) 조건부변동성(3)

실행: py alpha_grinder.py
"""

import numpy as np
import pandas as pd
import requests
import json
import time
import os
import threading
import warnings
from datetime import datetime, timezone
warnings.filterwarnings('ignore')
from dotenv import load_dotenv

DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(DIR, '.env'))
TG_TOKEN = os.getenv('TG_BOT_TOKEN', '8793543603:AAFgjJ5GfO93as3ssmcFEL9tiZhmSIYXBgE')
TG_CHAT = os.getenv('TG_CHAT_ID', '8451071451')
SAVE_PATH = os.path.join(DIR, 'grinder_results.json')

SYMBOLS = {
    'BTCUSDT':'BTC','ETHUSDT':'ETH','XRPUSDT':'XRP','SOLUSDT':'SOL',
    'BNBUSDT':'BNB','DOGEUSDT':'DOGE','ADAUSDT':'ADA','AVAXUSDT':'AVAX',
    'LINKUSDT':'LINK','DOTUSDT':'DOT',
}
SYM_LIST = list(SYMBOLS.keys())

# 벤치마크: 기존 5팩터 전략
BENCH_OOS = 1.34
BENCH_WEIGHTS = {'mom7':0.30,'mom60':0.15,'rev3':0.20,'vmom14':0.20,'carry21':0.15}

POP_SIZE = 60
ELITE = 6
MAX_GEN = 99999
REBAL_OPTIONS = [3,5,7,10,14,21]
COMBINE_MODES = ['linear','rank_product','conditional','ridge','xgb','pca']

PHASE = {
    'explore': {'mutate':0.50,'std':0.25,'fresh':0.20,'until':20},
    'learn':   {'mutate':0.35,'std':0.15,'fresh':0.10,'until':50},
    'refine':  {'mutate':0.20,'std':0.08,'fresh':0.05,'until':99999},
}
def get_phase(g):
    if g<20: return 'explore'
    if g<50: return 'learn'
    return 'refine'

# ═══════════════════════════════════════
#  텔레그램
# ═══════════════════════════════════════
def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    try:
        # 4096자 제한
        for i in range(0, len(msg), 4000):
            requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                json={'chat_id':TG_CHAT,'text':msg[i:i+4000],'parse_mode':'HTML'},
                timeout=10)
    except: pass

def tg_poll(state):
    if not TG_TOKEN or not TG_CHAT: return
    off = 0
    while state.get('run',True):
        try:
            r = requests.get(f'https://api.telegram.org/bot{TG_TOKEN}/getUpdates',
                params={'offset':off,'timeout':30}, timeout=35).json()
            for u in r.get('result',[]):
                off = u['update_id']+1
                txt = u.get('message',{}).get('text','').strip()
                cid = str(u.get('message',{}).get('chat',{}).get('id',''))
                if cid != TG_CHAT: continue
                if txt=='/status':
                    g=state.get('gen',0); ph=get_phase(g)
                    hrs=(time.time()-state.get('t0',time.time()))/3600
                    tested=state.get('tested',0)
                    speed=tested/max(hrs,0.01)
                    # 진화 단계 이모지
                    if ph=='explore': phase_icon="🥚 탐험"
                    elif ph=='learn': phase_icon="🐣 학습"
                    else: phase_icon="🐉 정교화"
                    # 진행 바
                    bar_len=20
                    if g<20: pct=g/20; stage="Phase 1/3"
                    elif g<50: pct=(g-20)/30; stage="Phase 2/3"
                    else: pct=min(1,(g-50)/200); stage="Phase 3/3"
                    filled=int(pct*bar_len)
                    bar="█"*filled+"░"*(bar_len-filled)
                    beat=state.get('beat',0)
                    best=state.get('best_oos',0)
                    tg(f"🧬 <b>알파 그라인더 상태</b>\n\n"
                       f"{phase_icon} 세대 {g}\n"
                       f"[{bar}] {stage}\n\n"
                       f"🔬 테스트: {tested:,}개 ({speed:.0f}/h)\n"
                       f"🏆 벤치 돌파: {beat}개\n"
                       f"📈 역대 최고 OOS: {best:+.3f}\n"
                       f"📊 벤치마크: {BENCH_OOS:+.2f}\n"
                       f"⏱ 가동: {hrs:.1f}시간\n\n"
                       f"{'🔥 벤치마크 돌파 전략 발견!' if beat>0 else '⏳ 아직 벤치마크 미돌파...'}")
                elif txt=='/top':
                    hof=state.get('hof',[])
                    if not hof:
                        g=state.get('gen',0)
                        tg(f"😤 아직 벤치 돌파 전략 없음\n\n"
                           f"세대 {g}까지 진화 중...\n"
                           f"벤치마크 OOS {BENCH_OOS}를 이겨야 함\n\n"
                           f"💪 포기하지 마세요, 진화는 계속됩니다")
                        continue
                    lines=["🏆 <b>명예의 전당 — 벤치마크 돌파</b>",
                           f"벤치: OOS {BENCH_OOS}\n"]
                    medals=["🥇","🥈","🥉","4️⃣","5️⃣"]
                    for i,h in enumerate(hof[:5]):
                        m=medals[i] if i<5 else f"{i+1}."
                        diff=h['oos']-BENCH_OOS
                        lines.append(f"{m} <b>OOS {h['oos']:+.3f}</b> (벤치+{diff:+.3f})")
                        lines.append(f"   IS {h['is']:+.3f} | gap {h['gap']:.2f}")
                        lines.append(f"   📐 {h['name']}")
                        lines.append(f"   🔧 [{h['mode']}] rb={h['rebal']}d")
                        lines.append(f"   🌍 불{h['bull']:+.2f} 베어{h['bear']:+.2f} 횡보{h['side']:+.2f}")
                        lines.append(f"   ✅ 유니버스 {h['uni_pass']}/9")
                        lines.append(f"   🧬 세대 {h.get('found_gen',0)}에서 발견\n")
                    tg('\n'.join(lines))
                elif txt=='/help':
                    tg("📋 <b>알파 그라인더 명령어</b>\n\n"
                       "/status — 현재 진화 상태\n"
                       "/top — 명예의 전당 (벤치 돌파 Top5)\n"
                       "/help — 이 메시지\n\n"
                       "🧬 전략이 스스로 진화합니다\n"
                       "🏆 벤치마크 돌파 시 자동 알림")
        except: pass
        time.sleep(1)

def send_full_report(h):
    """벤치마크 돌파 시 상세 검정 리포트"""
    diff=h['oos']-BENCH_OOS
    # 돌파 정도에 따른 이모지
    if diff>0.5: hype="🚀🚀🚀🔥🔥🔥"
    elif diff>0.3: hype="🚀🚀🔥🔥"
    elif diff>0.1: hype="🚀🔥"
    else: hype="🚀"

    lines = [
        f"{hype}",
        f"<b>벤치마크 돌파 전략 발견!</b>",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>전략 DNA</b>",
        f"  🧬 조합: {h['name']}",
        f"  🔧 모드: {h['mode']}",
        f"  📅 리밸: {h['rebal']}일",
        f"  🔄 뒤집기: {'예' if h.get('flip') else '아니오'}",
        f"  🧫 세대 {h.get('found_gen',0)}에서 탄생",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>성과 비교 (vs 벤치마크)</b>",
        f"",
        f"  {'지표':>14} {'신규':>8} {'벤치':>8} {'차이':>8}",
        f"  {'─'*42}",
        f"  {'OOS Sharpe':>14} {h['oos']:>+8.3f} {BENCH_OOS:>+8.2f} {diff:>+8.3f}",
        f"  {'IS Sharpe':>14} {h['is']:>+8.3f}",
        f"  {'IS 연수익':>14} {h['ret']:>+7.1%}",
        f"  {'IS MDD':>14} {h['mdd']:>7.1%}",
        f"  {'IS/OOS 괴리':>14} {h['gap']:>8.3f}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🌍 <b>멀티유니버스 서바이벌 ({h['uni_pass']}/9)</b>",
    ]
    for u in h.get('uni_detail',[]):
        icon = "✅" if u['sh']>0 else "❌"
        bar_w=max(0,min(10,int(u['sh']*5)))
        bar="▓"*bar_w+"░"*(10-bar_w)
        lines.append(f"  {icon} {u['name']:>16} [{bar}] {u['sh']:+.3f}")
    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📈 <b>Walk-Forward OOS ({h['n_folds']} folds)</b>",
        f"  통과율: {h['pass_rate']:.0%}",
        f"  평균: {h['oos']:+.3f}  최소: {h['oos_min']:+.3f}",
        f"",
    ]
    for i,f in enumerate(h.get('fold_detail',[])):
        icon = "✅" if f>0 else "❌"
        bar_w=max(0,min(10,int(f*5)))
        bar="▓"*bar_w
        lines.append(f"  {icon} fold{i+1}: {f:+.3f} {bar}")
    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🔥 <b>국면별 생존력</b>",
        f"  {'🐂 불마켓':>10}: {h['bull']:+.3f} {'✅' if h['bull']>0 else '❌'}",
        f"  {'🐻 베어':>10}: {h['bear']:+.3f} {'✅' if h['bear']>0 else '❌'}",
        f"  {'😐 횡보':>10}: {h['side']:+.3f} {'✅' if h['side']>0 else '❌'}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>비용 내성</b>",
        f"  3bps: {h.get('sh_3',0):+.3f} {'✅' if h.get('sh_3',0)>0 else '❌'}",
        f"  5bps: {h.get('sh_5',0):+.3f} {'✅' if h.get('sh_5',0)>0 else '❌'}",
        f"  8bps: {h.get('sh_8',0):+.3f} {'✅' if h.get('sh_8',0)>0 else '❌'}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🧬 <b>팩터 가중치</b>",
    ]
    # 가중치 큰 순서로 정렬
    wd=h.get('weights_detail',{})
    sorted_w=sorted(wd.items(),key=lambda x:abs(x[1]),reverse=True)
    for fn,w in sorted_w:
        bar_w=int(abs(w)*30)
        bar="█"*bar_w
        direction="📈" if w>0 else "📉"
        lines.append(f"  {direction} {fn:>12}: {w:>+5.0%} {bar}")
    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"💾 grinder_results.json에 저장됨",
        f"",
        f"이 전략을 실전에 적용하시겠습니까? 🤔",
    ]
    tg('\n'.join(lines))

# ═══════════════════════════════════════
#  데이터
# ═══════════════════════════════════════
def fetch_klines(sym, limit=1000):
    """현물 API + 페이지네이션으로 2020년부터 전체 수집"""
    all_data=[]; end_time=None
    for _ in range(12):
        params={'symbol':sym,'interval':'1d','limit':limit}
        if end_time: params['endTime']=end_time
        try:
            r=requests.get('https://api.binance.com/api/v3/klines',
                params=params,timeout=15)
            d=r.json()
            if not isinstance(d,list) or len(d)==0: break
            all_data=d+all_data; end_time=d[0][0]-1
            if len(d)<limit: break
            time.sleep(0.2)
        except: time.sleep(1); continue
    if not all_data: return None
    df=pd.DataFrame(all_data,columns=['ot','open','high','low','close','vol','ct','qv','trades','tbv','tbq','ig'])
    df['date']=pd.to_datetime(df['ot'],unit='ms').dt.normalize()
    for c in ['open','high','low','close','vol','qv','tbv','trades']:
        df[c]=df[c].astype(float)
    df=df.drop_duplicates(subset='date').set_index('date').sort_index()
    return df

def fetch_funding(sym):
    """정방향 페이지네이션으로 전체 펀딩비 수집 (2019~현재)"""
    ad=[]
    st=int(pd.Timestamp('2019-01-01').timestamp()*1000)
    for _ in range(25):
        p={'symbol':sym,'limit':1000,'startTime':st}
        try:
            d=requests.get('https://fapi.binance.com/fapi/v1/fundingRate',params=p,timeout=10).json()
            if not d or not isinstance(d,list): break
            ad.extend(d); st=d[-1]['fundingTime']+1
            if len(d)<1000: break
            time.sleep(0.1)
        except: break
    if not ad: return None
    df=pd.DataFrame(ad)
    df['date']=pd.to_datetime(df['fundingTime'],unit='ms')
    df['rate']=df['fundingRate'].astype(float)
    return df.set_index('date')['rate'].resample('D').sum()

def load_data():
    print("[데이터 수집 — 현물 + 펀딩비]")
    kl,fu={},{}
    for sym,nm in SYMBOLS.items():
        k=fetch_klines(sym,1000)
        if k is None: print(f"  {nm}: 실패"); continue
        kl[sym]=k
        f=fetch_funding(sym)
        fu[sym]=f
        f_days=len(f) if f is not None else 0
        print(f"  {nm:>5}: {len(k)}일 (펀딩 {f_days}일)"); time.sleep(0.1)
    cl=pd.DataFrame({s:kl[s]['close'] for s in kl}).dropna()
    hi=pd.DataFrame({s:kl[s]['high'] for s in kl}).reindex(cl.index).ffill()
    lo=pd.DataFrame({s:kl[s]['low'] for s in kl}).reindex(cl.index).ffill()
    vo=pd.DataFrame({s:kl[s]['vol'] for s in kl}).reindex(cl.index).ffill()
    qv=pd.DataFrame({s:kl[s]['qv'] for s in kl}).reindex(cl.index).ffill()
    tb=pd.DataFrame({s:kl[s]['tbv'] for s in kl}).reindex(cl.index).ffill()
    tr=pd.DataFrame({s:kl[s]['trades'] for s in kl}).reindex(cl.index).ffill()
    # 펀딩비: 없는 날짜는 0으로 채움 (look-ahead bias 방지)
    for sym in fu:
        if fu[sym] is None: continue
        aligned=fu[sym].reindex(cl.index)
        real_count=aligned.notna().sum()
        aligned=aligned.fillna(0)  # 데이터 없는 날은 0 (중립)
        fu[sym]=aligned
        print(f"    {SYMBOLS.get(sym,sym):>5} 펀딩비: 실데이터 {real_count}/{len(cl)}일 ({real_count/len(cl)*100:.0f}%)")
    print(f"  {cl.index[0].date()} ~ {cl.index[-1].date()} ({len(cl)}일)")
    print(f"  펀딩비: 실데이터 + 0 백필 (look-ahead bias 제거)")
    return cl,hi,lo,vo,qv,tb,tr,fu

# ═══════════════════════════════════════
#  40+ 팩터
# ═══════════════════════════════════════
def zs(df):
    mu=df.mean(axis=1); sig=df.std(axis=1).replace(0,1)
    return df.sub(mu,axis=0).div(sig,axis=0)

def build_factors(cl,hi,lo,vo,qv,tb,tr,fu):
    syms=list(cl.columns); dates=cl.index; ret=cl.pct_change()
    F={}; btc_r=ret.iloc[:,0]

    # ══ 1. 모멘텀 (11개) ══
    for lb in [3,5,7,10,14,21,30,45,60,90,120]:
        F[f'mom{lb}']=zs(cl.pct_change(lb).rank(axis=1,pct=True))

    # ══ 2. 리버설 (5개) ══
    for lb in [1,2,3,5,10]:
        F[f'rev{lb}']=zs(1-cl.pct_change(lb).rank(axis=1,pct=True))

    # ══ 3. 볼륨 모멘텀 (4개) ══
    for lb in [7,14,21,30]:
        vw=(ret*vo).rolling(lb).sum()/vo.rolling(lb).sum().replace(0,1)
        F[f'vmom{lb}']=zs(vw.rank(axis=1,pct=True))

    # ══ 4. 펀딩/캐리 (4개) ══
    for lb_carry in [7,21]:
        cdf=pd.DataFrame(0.0,index=dates,columns=syms)
        for s in syms:
            f=fu.get(s)
            if f is None: continue
            cdf[s]=-f.reindex(dates).fillna(0).rolling(lb_carry,min_periods=1).mean()*10000
        F[f'carry{lb_carry}']=zs(cdf)
    for lb_fmr in [7,14]:
        fmr=pd.DataFrame(0.0,index=dates,columns=syms)
        for s in syms:
            f=fu.get(s)
            if f is None: continue
            fr=f.reindex(dates).fillna(0)
            mu=fr.rolling(lb_fmr,min_periods=1).mean()
            sig=fr.rolling(lb_fmr,min_periods=1).std().replace(0,0.001)
            fmr[s]=-(fr-mu)/sig
        F[f'fmr{lb_fmr}']=zs(fmr)

    # ══ 5. 변동성 (8개) ══
    for lb in [10,20,40]:
        F[f'lvol{lb}']=zs((ret.rolling(lb).std()*np.sqrt(365)).rank(axis=1,pct=True,ascending=False))
    F['vbrk']=zs((vo/vo.rolling(20).mean().replace(0,1)).rank(axis=1,pct=True))
    rng=(hi-lo)/cl
    F['rcomp']=zs((rng.rolling(20).mean()/rng.rolling(5).mean().replace(0,1)).rank(axis=1,pct=True))
    F['vol_reg']=zs((ret.rolling(10).std()/ret.rolling(60).std().replace(0,1)).rank(axis=1,pct=True))
    dv=ret.clip(upper=0).rolling(20).std()
    uv=ret.clip(lower=0).rolling(20).std().replace(0,0.001)
    F['vol_asym']=zs((dv/uv).rank(axis=1,pct=True,ascending=False))
    # ATR 정규화
    atr=pd.DataFrame(index=dates,columns=syms,dtype=float)
    for c in syms:
        tr_val=pd.concat([hi[c]-lo[c],(hi[c]-cl[c].shift(1)).abs(),(lo[c]-cl[c].shift(1)).abs()],axis=1).max(axis=1)
        atr[c]=tr_val.rolling(14).mean()/cl[c]
    F['atr']=zs(atr.rank(axis=1,pct=True,ascending=False))

    # ══ 6. 기술적 지표 (12개) ══
    # RSI 여러 룩백
    for lb_rsi in [7,14,21]:
        d=cl.diff(); g=d.clip(lower=0).rolling(lb_rsi).mean()
        l=(-d.clip(upper=0)).rolling(lb_rsi).mean().replace(0,1)
        F[f'rsi{lb_rsi}']=zs(1-(100-100/(1+g/l)).rank(axis=1,pct=True))
    # MACD
    ema12=cl.ewm(span=12).mean(); ema26=cl.ewm(span=26).mean()
    macd=ema12-ema26; signal=macd.ewm(span=9).mean()
    F['macd']=zs((macd-signal).rank(axis=1,pct=True))
    F['macd_hist']=zs(((macd-signal)/cl).rank(axis=1,pct=True))
    # 볼린저 밴드 위치
    for lb_bb in [20,40]:
        ma=cl.rolling(lb_bb).mean(); sd=cl.rolling(lb_bb).std().replace(0,1)
        F[f'bb{lb_bb}']=zs(((cl-ma)/sd).rank(axis=1,pct=True,ascending=False))
    # CCI
    tp=(hi+lo+cl)/3; ma_tp=tp.rolling(20).mean()
    md=tp.rolling(20).apply(lambda x:np.mean(np.abs(x-x.mean())),raw=True).replace(0,1)
    F['cci']=zs(((tp-ma_tp)/(0.015*md)).rank(axis=1,pct=True,ascending=False))
    # Williams %R
    hh60=hi.rolling(14).max(); ll60=lo.rolling(14).min()
    F['willr']=zs(((hh60-cl)/(hh60-ll60).replace(0,1)).rank(axis=1,pct=True))
    # Stochastic K
    hh14=hi.rolling(14).max(); ll14=lo.rolling(14).min()
    stoch_k=(cl-ll14)/(hh14-ll14).replace(0,1)
    F['stoch_k']=zs(stoch_k.rank(axis=1,pct=True,ascending=False))
    # Stochastic D (slow)
    F['stoch_d']=zs(stoch_k.rolling(3).mean().rank(axis=1,pct=True,ascending=False))

    # ══ 7. 고저/레인지 (4개) ══
    for lb_hl in [20,60,120]:
        hh=cl.rolling(lb_hl).max(); ll=cl.rolling(lb_hl).min()
        F[f'hilo{lb_hl}']=zs(((cl-ll)/(hh-ll).replace(0,1)).rank(axis=1,pct=True))
    F['hl_pos']=zs(((cl-lo)/(hi-lo).replace(0,1)).rank(axis=1,pct=True))

    # ══ 8. BTC 베타/상관 (8개) ══
    for lb_beta in [30,60]:
        betas=pd.DataFrame(index=dates,columns=syms,dtype=float)
        for c in syms:
            betas[c]=ret[c].rolling(lb_beta).cov(btc_r)/btc_r.rolling(lb_beta).var().replace(0,1)
        F[f'beta{lb_beta}']=zs(1-betas.rank(axis=1,pct=True).astype(float))
    # 잔차 모멘텀
    betas60=pd.DataFrame(index=dates,columns=syms,dtype=float)
    for c in syms:
        betas60[c]=ret[c].rolling(60).cov(btc_r)/btc_r.rolling(60).var().replace(0,1)
    resid=pd.DataFrame(index=dates,columns=syms,dtype=float)
    for c in syms: resid[c]=ret[c]-betas60[c].fillna(1)*btc_r
    for lb_res in [7,14,30]:
        F[f'resid{lb_res}']=zs(resid.rolling(lb_res).sum().rank(axis=1,pct=True))
    # 상관 변화
    for w1,w2 in [(14,60),(30,90)]:
        cc=pd.DataFrame(index=dates,columns=syms,dtype=float)
        for c in syms: cc[c]=ret[c].rolling(w1).corr(btc_r)-ret[c].rolling(w2).corr(btc_r)
        F[f'corr_{w1}v{w2}']=zs(cc.rank(axis=1,pct=True).astype(float))

    # ══ 9. 코사인유사도 (2개) ══
    from scipy.spatial.distance import cosine as cdist
    ba=btc_r.values.copy()
    for win in [20,40]:
        cos_arr=np.zeros((len(dates),len(syms)))
        for ci,c in enumerate(syms):
            ca=ret[c].values.copy()
            for t in range(win,len(dates)):
                a,b=ba[t-win:t],ca[t-win:t]
                if np.std(a)<1e-10 or np.std(b)<1e-10: continue
                cos_arr[t,ci]=1-cdist(a,b)
        cos=pd.DataFrame(cos_arr,index=dates,columns=syms)
        F[f'cos{win}']=zs(1-cos.astype(float).rank(axis=1,pct=True))

    # ══ 10. 거래량/오더플로우 (10개) ══
    F['qv_surge']=zs((qv/qv.rolling(20).mean().replace(0,1)).rank(axis=1,pct=True))
    F['qv_mom7']=zs(qv.pct_change(7).rank(axis=1,pct=True))
    br=tb/vo.replace(0,1)
    F['buy_pct']=zs(br.rank(axis=1,pct=True))
    for lb_bc in [3,5,10]:
        F[f'buy_chg{lb_bc}']=zs(br.pct_change(lb_bc).rank(axis=1,pct=True))
    F['trade_mom']=zs(tr.pct_change(7).rank(axis=1,pct=True))
    F['trade_acc']=zs(tr.pct_change(3).rank(axis=1,pct=True)-tr.pct_change(14).rank(axis=1,pct=True))
    # OBV (On Balance Volume)
    obv=pd.DataFrame(0.0,index=dates,columns=syms)
    for c in syms:
        sign=np.sign(ret[c].values); v=vo[c].values
        obv[c]=np.cumsum(sign*v)
    obv_mom=obv.pct_change(14)
    F['obv_mom']=zs(obv_mom.rank(axis=1,pct=True))
    # VWAP 괴리 (일봉 근사: 거래대금/거래량 vs 종가)
    vwap=qv/vo.replace(0,1)
    F['vwap_dev']=zs(((cl-vwap)/cl).rank(axis=1,pct=True))

    # ══ 11. 가격-거래량 괴리 (3개) ══
    for lb_pv in [3,7,14]:
        F[f'pv_div{lb_pv}']=zs(cl.pct_change(lb_pv).rank(axis=1,pct=True)-vo.pct_change(lb_pv).rank(axis=1,pct=True))

    # ══ 12. 시계열 통계 (6개) ══
    # 스큐
    for lb_sk in [14,30]:
        F[f'skew{lb_sk}']=zs(ret.rolling(lb_sk).apply(lambda x:pd.Series(x).skew(),raw=False).rank(axis=1,pct=True))
    # 첨도 (꼬리 리스크)
    F['kurt']=zs(ret.rolling(30).apply(lambda x:pd.Series(x).kurtosis(),raw=False).rank(axis=1,pct=True,ascending=False))
    # 자기상관 (mean reversion vs trend)
    F['autocorr']=zs(ret.rolling(20).apply(lambda x:pd.Series(x).autocorr(),raw=False).rank(axis=1,pct=True))
    # 연속 상승/하락
    streak=pd.DataFrame(0.0,index=dates,columns=syms)
    for c in syms:
        s=0; v=ret[c].values; o=np.zeros(len(v))
        for t in range(1,len(v)):
            if v[t]>0: s=max(1,s+1)
            elif v[t]<0: s=min(-1,s-1)
            else: s=0
            o[t]=s
        streak[c]=o
    F['streak']=zs(streak.rank(axis=1,pct=True))
    # 최대 일간 손실 (꼬리 리스크)
    F['max_loss']=zs(ret.rolling(30).min().rank(axis=1,pct=True,ascending=False))

    # ══ 13. 크로스에셋 (5개) ══
    # ETH/BTC 비율 모멘텀
    if 'ETHUSDT' in cl.columns and 'BTCUSDT' in cl.columns:
        eth_btc=cl['ETHUSDT']/cl['BTCUSDT']
        eb_mom=eth_btc.pct_change(14)
        # 알트 선호도: ETH/BTC 오르면 알트 강세
        for c in syms:
            if c=='BTCUSDT': continue
        eb_df=pd.DataFrame(eb_mom.values[:,None]*np.ones((1,len(syms))),index=dates,columns=syms)
        eb_df['BTCUSDT']=-eb_df['BTCUSDT']  # BTC는 반대
        F['eth_btc_mom']=zs(eb_df.rank(axis=1,pct=True))
    # 평균 상관 (시장 전체 상관 높으면 분산 안 됨)
    avg_corr=pd.DataFrame(index=dates,columns=syms,dtype=float)
    for c in syms:
        others=[s for s in syms if s!=c]
        corrs=pd.DataFrame({s:ret[c].rolling(30).corr(ret[s]) for s in others})
        avg_corr[c]=corrs.mean(axis=1)
    F['avg_corr']=zs(1-avg_corr.rank(axis=1,pct=True).astype(float))  # 낮은 상관 = 좋음
    # 시장 대비 초과수익
    mkt_ret=ret.mean(axis=1)
    excess=ret.sub(mkt_ret,axis=0)
    F['excess7']=zs(excess.rolling(7).sum().rank(axis=1,pct=True))
    F['excess30']=zs(excess.rolling(30).sum().rank(axis=1,pct=True))
    # 도미넌스 변화 (시총 프록시: 거래대금 비중)
    qv_share=qv.div(qv.sum(axis=1),axis=0)
    F['dom_chg']=zs(qv_share.pct_change(7).rank(axis=1,pct=True))

    # ══ 14. 이동평균 크로스 (4개) ══
    for fast,slow in [(5,20),(10,30),(20,60),(50,200)]:
        if slow>len(dates)//2: continue
        ma_f=cl.rolling(fast).mean(); ma_s=cl.rolling(slow).mean()
        F[f'ma{fast}x{slow}']=zs(((ma_f-ma_s)/ma_s).rank(axis=1,pct=True))

    # ══ 15. 조건부 변동성 (3개) ══
    # GARCH 프록시: 최근 변동성 대비 과거
    for s_w,l_w in [(5,30),(10,60),(20,90)]:
        F[f'garch_{s_w}v{l_w}']=zs((ret.rolling(s_w).std()/ret.rolling(l_w).std().replace(0,1)).rank(axis=1,pct=True))

    print(f"  팩터 {len(F)}개 생성")
    return F

# ═══════════════════════════════════════
#  멀티유니버스 생성
# ═══════════════════════════════════════
def make_universes(rets_np, factor_np):
    """9개 유니버스 생성"""
    T = rets_np.shape[0]
    universes = []

    # U1~U3: 전체기간, 비용 다르게
    universes.append({'name':'전체 3bps','rets':rets_np,'fac':factor_np,'cost':3.0})
    universes.append({'name':'전체 5bps','rets':rets_np,'fac':factor_np,'cost':5.0})
    universes.append({'name':'전체 8bps','rets':rets_np,'fac':factor_np,'cost':8.0})

    # U4~U5: 최근 365일
    if T > 365:
        r1 = rets_np[-365:]
        f1 = [f[-365:] for f in factor_np]
        universes.append({'name':'최근1년 3bps','rets':r1,'fac':f1,'cost':3.0})
        universes.append({'name':'최근1년 8bps','rets':r1,'fac':f1,'cost':8.0})
    else:
        universes.append({'name':'최근1년 3bps','rets':rets_np,'fac':factor_np,'cost':3.0})
        universes.append({'name':'최근1년 8bps','rets':rets_np,'fac':factor_np,'cost':8.0})

    # U6: 최근 730일
    if T > 730:
        r2 = rets_np[-730:]
        f2 = [f[-730:] for f in factor_np]
        universes.append({'name':'최근2년 5bps','rets':r2,'fac':f2,'cost':5.0})
    else:
        universes.append({'name':'최근2년 5bps','rets':rets_np,'fac':factor_np,'cost':5.0})

    # U7~U9: 부트스트랩 (블록 셔플, 블록=5일)
    rng = np.random.RandomState(42)
    for seed in [42, 123, 777]:
        rs = np.random.RandomState(seed)
        block = 5
        n_blocks = T // block
        idx = np.arange(n_blocks)
        rs.shuffle(idx)
        new_idx = []
        for b in idx:
            new_idx.extend(range(b*block, min((b+1)*block, T)))
        new_idx = np.array(new_idx[:T])
        br = rets_np[new_idx]
        bf = [f[new_idx] for f in factor_np]
        universes.append({'name':f'부트스트랩#{seed}','rets':br,'fac':bf,'cost':3.0})

    return universes

# ═══════════════════════════════════════
#  백테스트 + 검증
# ═══════════════════════════════════════
def fast_bt(rets, scores, rebal=7, start=60, cost=3.0):
    T,N = rets.shape
    pnl = np.zeros(T); w = np.zeros(N)
    for t in range(start, T):
        pnl[t] = np.nansum(w * rets[t])
        if t % rebal == 0:
            row = np.nan_to_num(scores[t], 0)
            dm = row - np.nanmean(row)
            ab = np.sum(np.abs(dm))
            if ab < 1e-10: continue
            nw = dm / ab
            pnl[t] -= np.sum(np.abs(nw-w)) * cost / 10000
            w = nw
    c = np.cumsum(pnl[start:])
    if len(c)<30: return None
    n=len(c); ar=c[-1]*365/n
    av=np.std(pnl[start:])*np.sqrt(365)
    sh=ar/av if av>0.001 else 0
    eq=1.0+c; pk=np.maximum.accumulate(eq)
    mdd=np.min((eq-pk)/np.maximum(pk,0.001))
    return {'sharpe':sh,'ret':ar,'mdd':mdd}

def walk_forward(rets, scores, rebal=7, cost=3.0):
    T=rets.shape[0]; folds=[]; s=0
    while s+365+180<=T:
        ts=s+365; te=min(ts+180,T)
        if te-ts<30: s+=90; continue
        r=fast_bt(rets[ts:te],scores[ts:te],rebal=rebal,start=0,cost=cost)
        if r: folds.append(r['sharpe'])
        s+=90
    return folds

def regime_test(rets, scores, btc_close, rebal=7, cost=3.0):
    T=rets.shape[0]
    btc=btc_close[:T] if len(btc_close)>=T else btc_close
    mom=np.zeros(T)
    for t in range(60,T): mom[t]=(btc[t]/btc[t-60])-1
    results={}
    for nm,cond in [('bull',mom>0.1),('bear',mom<-0.1),('side',(mom>=-0.1)&(mom<=0.1))]:
        idx=np.where(cond)[0]
        if len(idx)<30: results[nm]=0.0; continue
        w=np.zeros(rets.shape[1]); pnls=[]
        for t in idx:
            if t>=T: continue
            pnls.append(np.nansum(w*rets[t]))
            if t%rebal==0:
                row=np.nan_to_num(scores[t],0)
                dm=row-np.nanmean(row); ab=np.sum(np.abs(dm))
                if ab>1e-10: w=dm/ab
        if len(pnls)<10: results[nm]=0.0; continue
        ar=np.sum(pnls)*365/len(pnls)
        av=np.std(pnls)*np.sqrt(365)
        results[nm]=ar/av if av>0.001 else 0
    return results.get('bull',0),results.get('bear',0),results.get('side',0)

# ═══════════════════════════════════════
#  유전자
# ═══════════════════════════════════════
def make_gene(nf):
    w=np.random.random(nf)
    w*=(np.random.random(nf)>0.4)
    if np.sum(w>0)<2:
        ix=np.random.choice(nf,3,replace=False); w[ix]=np.random.random(3)
    s=np.sum(np.abs(w))
    if s>0: w/=s
    return {'w':w,'rb':int(np.random.choice(REBAL_OPTIONS)),
            'mode':np.random.choice(COMBINE_MODES),
            'flip':bool(np.random.random()<0.12)}

def cross(p1,p2,nf):
    cw=np.where(np.random.random(nf)<0.5,p1['w'],p2['w'])
    s=np.sum(np.abs(cw))
    if s>0: cw/=s
    return {'w':cw,
            'rb':p1['rb'] if np.random.random()<0.5 else p2['rb'],
            'mode':p1['mode'] if np.random.random()<0.5 else p2['mode'],
            'flip':p1['flip'] if np.random.random()<0.5 else p2['flip']}

def mut(g,nf,ph):
    cfg=PHASE[ph]; w=g['w'].copy()
    for i in range(nf):
        if np.random.random()<cfg['mutate']:
            w[i]+=np.random.normal(0,cfg['std'])
            if np.random.random()<0.08:
                w[i]=0 if w[i]!=0 else np.random.random()*0.3
    w=np.clip(w,-0.5,1.0)
    if np.sum(np.abs(w)>0.01)<2:
        ix=np.random.choice(nf,2,replace=False); w[ix]=np.abs(np.random.normal(0.2,0.1,2))
    s=np.sum(np.abs(w))
    if s>0: w/=s
    rb=g['rb']
    if np.random.random()<0.12: rb=int(np.random.choice(REBAL_OPTIONS))
    mode=g['mode']
    if np.random.random()<0.08: mode=np.random.choice(COMBINE_MODES)
    flip=g['flip']
    if np.random.random()<0.05: flip=not flip
    return {'w':w,'rb':rb,'mode':mode,'flip':flip}

def gene_scores(g, fac, rets):
    w=g['w']; mode=g['mode']; nf=len(w)
    # 활성 팩터 인덱스
    active=[i for i in range(nf) if abs(w[i])>=0.01]
    if len(active)<2: active=list(range(min(3,nf)))

    if mode=='linear':
        c=np.zeros_like(rets)
        for i in range(nf):
            if abs(w[i])<1e-6: continue
            c+=w[i]*np.nan_to_num(fac[i],0)

    elif mode=='rank_product':
        c=np.ones_like(rets); act=0
        for i in range(nf):
            if abs(w[i])<0.01: continue
            f=np.nan_to_num(fac[i],0)
            mn=np.nanmin(f,axis=1,keepdims=True)
            mx=np.nanmax(f,axis=1,keepdims=True)
            rng=np.maximum(mx-mn,1e-10)
            nr=(f-mn)/rng
            if w[i]<0: nr=1-nr
            c*=(nr*abs(w[i])+(1-abs(w[i]))); act+=1
        if act==0: c=np.zeros_like(rets)

    elif mode=='conditional':
        c=np.zeros_like(rets); T=rets.shape[0]
        v20=np.zeros(T)
        for t in range(20,T): v20[t]=np.std(np.nanmean(rets[t-20:t],axis=1))
        med=np.median(v20[60:]) if T>60 else 0.01
        half=nf//2
        for t in range(60,T):
            if v20[t]>med:
                for i in range(half):
                    if abs(w[i])<1e-6: continue
                    c[t]+=w[i]*np.nan_to_num(fac[i][t],0)
            else:
                for i in range(half,nf):
                    if abs(w[i])<1e-6: continue
                    c[t]+=w[i]*np.nan_to_num(fac[i][t],0)

    elif mode=='ridge':
        # Ridge 회귀: 과거 데이터로 팩터→수익률 학습, 롤링
        from sklearn.linear_model import Ridge
        T,N=rets.shape; c=np.zeros_like(rets)
        train_win=252  # 1년 학습
        # 활성 팩터만 사용
        for t in range(train_win+60, T):
            # 학습 데이터: t-train_win ~ t
            X_train=np.zeros((train_win,len(active)))
            y_train=np.zeros(train_win)
            for j,ai in enumerate(active):
                X_train[:,j]=np.nanmean(fac[ai][t-train_win:t],axis=1)
            y_train=np.nanmean(rets[t-train_win:t],axis=1)
            # NaN 처리
            mask=~(np.isnan(X_train).any(axis=1)|np.isnan(y_train))
            if mask.sum()<30: continue
            try:
                mdl=Ridge(alpha=1.0)
                mdl.fit(X_train[mask],y_train[mask])
                # 예측: 현재 팩터값으로 종목별 스코어
                X_now=np.zeros((N,len(active)))
                for j,ai in enumerate(active):
                    X_now[:,j]=np.nan_to_num(fac[ai][t],0)
                c[t]=mdl.predict(X_now)
            except: pass

    elif mode=='xgb':
        # XGBoost: 비선형 팩터 조합 학습
        try:
            from xgboost import XGBRegressor
        except ImportError:
            # xgboost 없으면 linear로 폴백
            c=np.zeros_like(rets)
            for i in range(nf):
                if abs(w[i])<1e-6: continue
                c+=w[i]*np.nan_to_num(fac[i],0)
            if g.get('flip'): c=-c
            return c
        T,N=rets.shape; c=np.zeros_like(rets)
        train_win=252
        # 50일마다 재학습 (속도)
        retrain_freq=50; last_mdl=None
        for t in range(train_win+60, T):
            if last_mdl is None or t%retrain_freq==0:
                X_train=np.zeros((train_win,len(active)))
                y_train=np.zeros(train_win)
                for j,ai in enumerate(active):
                    X_train[:,j]=np.nanmean(fac[ai][t-train_win:t],axis=1)
                y_train=np.nanmean(rets[t-train_win:t],axis=1)
                mask=~(np.isnan(X_train).any(axis=1)|np.isnan(y_train))
                if mask.sum()<30: continue
                try:
                    mdl=XGBRegressor(n_estimators=50,max_depth=3,learning_rate=0.1,
                                     verbosity=0,n_jobs=1)
                    mdl.fit(X_train[mask],y_train[mask])
                    last_mdl=mdl
                except: continue
            if last_mdl is not None:
                X_now=np.zeros((N,len(active)))
                for j,ai in enumerate(active):
                    X_now[:,j]=np.nan_to_num(fac[ai][t],0)
                try: c[t]=last_mdl.predict(X_now)
                except: pass

    elif mode=='pca':
        # PCA: 팩터 차원 축소 → 상위 3개 주성분으로 스코어
        from sklearn.decomposition import PCA as PCA_
        T,N=rets.shape; c=np.zeros_like(rets)
        train_win=252
        n_comp=min(3,len(active))
        for t in range(train_win+60, T):
            if t%30!=0 and t!=train_win+60: # 30일마다 재계산
                c[t]=c[t-1] if t>0 else 0; continue
            # 팩터 행렬 (train_win x active x N)
            X=np.zeros((train_win,len(active)))
            for j,ai in enumerate(active):
                X[:,j]=np.nanmean(fac[ai][t-train_win:t],axis=1)
            X=np.nan_to_num(X,0)
            if np.std(X)<1e-10: continue
            try:
                pca=PCA_(n_components=n_comp)
                pca.fit(X)
                # 현재 팩터값을 PCA 변환
                X_now=np.zeros((N,len(active)))
                for j,ai in enumerate(active):
                    X_now[:,j]=np.nan_to_num(fac[ai][t],0)
                # 각 종목의 PC1 스코어를 시그널로
                transformed=pca.transform(X_now.T).T  # (n_comp, N) 아님
                # X_now: (N, n_active) → transform → (N, n_comp)
                pc_scores=pca.transform(X_now)  # (N, n_comp)
                # 가중합: PC1 * explained_var[0] + PC2 * ...
                ev=pca.explained_variance_ratio_
                c[t]=np.sum(pc_scores*ev[None,:],axis=1)
            except: pass

    else:
        # 알 수 없는 모드 → linear 폴백
        c=np.zeros_like(rets)
        for i in range(nf):
            if abs(w[i])<1e-6: continue
            c+=w[i]*np.nan_to_num(fac[i],0)

    if g.get('flip'): c=-c
    return c

def gname(g,fn):
    parts=[f"{fn[i]}:{g['w'][i]:+.0%}" for i in range(len(g['w'])) if abs(g['w'][i])>=0.03]
    return ' '.join(parts)

def gshort(g,fn):
    return '+'.join([fn[i] for i in range(len(g['w'])) if abs(g['w'][i])>=0.03])

# ═══════════════════════════════════════
#  평가 (멀티유니버스 + 벤치마크)
# ═══════════════════════════════════════
def evaluate(g, universes, btc_close, fnames):
    """
    9개 유니버스 시뮬 + WF OOS + 국면 + 벤치마크 비교
    """
    rb = g['rb']

    # 1) 메인 유니버스 IS
    u0 = universes[0]
    sc0 = gene_scores(g, u0['fac'], u0['rets'])
    is_r = fast_bt(u0['rets'], sc0, rebal=rb, cost=u0['cost'])
    if is_r is None: return None
    is_sh = is_r['sharpe']

    # 빠른 컷: IS 0.3 미만이면 스킵
    if is_sh < 0.3:
        return {'fit':is_sh*0.05,'stage':0,'is':is_sh,'oos':0,
                'uni_pass':0,'beat':False}

    # 2) 9개 유니버스 전부 시뮬
    uni_detail = []
    uni_pass = 0
    for u in universes:
        sc = gene_scores(g, u['fac'], u['rets'])
        r = fast_bt(u['rets'], sc, rebal=rb, start=min(60,u['rets'].shape[0]-31), cost=u['cost'])
        sh = r['sharpe'] if r else -1
        uni_detail.append({'name':u['name'],'sh':sh})
        if sh > 0: uni_pass += 1

    # 3) WF OOS (메인)
    folds = walk_forward(u0['rets'], sc0, rebal=rb, cost=u0['cost'])
    oos_avg = np.mean(folds) if folds else 0
    oos_min = min(folds) if folds else -1
    n_pos = sum(1 for f in folds if f>0)
    pass_rate = n_pos/len(folds) if folds else 0

    # 4) 국면
    bull,bear,side = regime_test(u0['rets'],sc0,btc_close,rebal=rb,cost=u0['cost'])

    # 5) 비용별 Sharpe
    sh_3 = is_sh
    r5 = fast_bt(u0['rets'],sc0,rebal=rb,cost=5.0)
    sh_5 = r5['sharpe'] if r5 else 0
    r8 = fast_bt(u0['rets'],sc0,rebal=rb,cost=8.0)
    sh_8 = r8['sharpe'] if r8 else 0

    # 괴리
    gap = abs(is_sh - oos_avg)

    # 적합도
    fit = oos_avg*0.35 + is_sh*0.10 + sh_8*0.10
    fit += (uni_pass/9)*0.15
    if bull>0 and bear>0 and side>0: fit+=0.10
    fit += min(bull,bear,side)*0.05
    fit += pass_rate*0.10
    if gap>0.3: fit-=(gap-0.3)*0.5
    if is_r['mdd']<-0.25: fit-=0.10

    # 벤치마크 돌파?
    beat = (oos_avg > BENCH_OOS and uni_pass==9 and pass_rate>=1.0
            and bull>0 and bear>0 and side>0 and gap<0.3
            and oos_min>0 and sh_8>0.5 and is_r['mdd']>-0.20)

    # 스테이지
    stage=0
    if is_sh>0.5: stage=1
    if stage==1 and uni_pass==9: stage=2
    if stage==2 and pass_rate>=1.0: stage=3
    if stage==3 and bull>0 and bear>0 and side>0: stage=4
    if stage==4 and oos_avg>BENCH_OOS and gap<0.5: stage=5

    return {
        'fit':fit,'stage':stage,'is':is_sh,'oos':oos_avg,'oos_min':oos_min,
        'pass_rate':pass_rate,'n_folds':len(folds),'fold_detail':folds,
        'bull':bull,'bear':bear,'side':side,
        'sh_3':sh_3,'sh_5':sh_5,'sh_8':sh_8,
        'gap':gap,'ret':is_r['ret'],'mdd':is_r['mdd'],
        'uni_pass':uni_pass,'uni_detail':uni_detail,
        'beat':beat,
    }

# ═══════════════════════════════════════
#  저장
# ═══════════════════════════════════════
def save(hof, gen, fn):
    data={'updated':datetime.now(timezone.utc).isoformat(),'gen':gen,
          'benchmark_oos':BENCH_OOS,'strategies':[]}
    for h in hof:
        data['strategies'].append({
            'name':h['name'],'mode':h['mode'],'rebal':h['rebal'],'flip':h.get('flip',False),
            'weights':{fn[i]:round(float(h['gene']['w'][i]),4)
                       for i in range(len(h['gene']['w'])) if abs(h['gene']['w'][i])>=0.01},
            'is':round(h['is'],4),'oos':round(h['oos'],4),'oos_min':round(h.get('oos_min',0),4),
            'bull':round(h['bull'],3),'bear':round(h['bear'],3),'side':round(h['side'],3),
            'sh_3':round(h.get('sh_3',0),3),'sh_5':round(h.get('sh_5',0),3),
            'sh_8':round(h.get('sh_8',0),3),
            'gap':round(h['gap'],3),'ret':round(h['ret'],4),'mdd':round(h['mdd'],4),
            'stage':h['stage'],'uni_pass':h['uni_pass'],
            'pass_rate':round(h.get('pass_rate',0),3),
            'found_gen':h.get('found_gen',0),'beat_bench':h.get('beat',False),
        })
    with open(SAVE_PATH,'w',encoding='utf-8') as f:
        json.dump(data,f,indent=2,ensure_ascii=False)

def load_prev(fn, nf):
    """이전 학습 결과에서 전략 복원 → 초기 population에 주입"""
    if not os.path.exists(SAVE_PATH):
        return [], 0
    try:
        with open(SAVE_PATH,'r',encoding='utf-8') as f:
            data=json.load(f)
        prev_gen=data.get('gen',0)
        genes=[]
        for s in data.get('strategies',[]):
            w=np.zeros(nf)
            for k,v in s.get('weights',{}).items():
                if k in fn: w[fn.index(k)]=v
            sm=np.sum(np.abs(w))
            if sm>0: w/=sm
            genes.append({'w':w,'rb':s.get('rebal',7),
                          'mode':s.get('mode','linear'),
                          'flip':s.get('flip',False)})
        return genes, prev_gen
    except:
        return [], 0

# ═══════════════════════════════════════
#  메인
# ═══════════════════════════════════════
def main():
    print("="*65)
    print("  🧬 알파 그라인더 v5 — 현물 풀히스토리 진화")
    print(f"  벤치마크: 기존 5팩터 OOS Sharpe {BENCH_OOS}")
    print("  Ctrl+C로 종료")
    print("="*65)

    cl,hi,lo,vo,qv,tb,tr,fu = load_data()
    tg("📡 <b>데이터 수집 완료</b>\n"
       f"  {len(SYMBOLS)}개 코인 로드됨")
    print("\n[팩터 생성]")
    tg("⚙️ <b>팩터 생성 중...</b>\n  100+개 팩터 계산 시작")
    F = build_factors(cl,hi,lo,vo,qv,tb,tr,fu)
    fn = list(F.keys())
    nf = len(fn)
    tg(f"✅ <b>팩터 생성 완료</b>\n  총 {nf}개 팩터")

    rets_np = cl.pct_change().fillna(0).values.copy()
    fac_np = [F[f].values.copy() for f in fn]
    btc_col = SYM_LIST[0] if SYM_LIST[0] in cl.columns else cl.columns[0]
    btc_close = cl[btc_col].values.copy()

    print("\n[유니버스 생성]")
    tg("🌍 <b>유니버스 생성 중...</b>\n  9개 시뮬레이션 환경 구축")
    universes = make_universes(rets_np, fac_np)
    print(f"  {len(universes)}개 유니버스 준비")
    tg(f"🌍 <b>유니버스 준비 완료</b>\n  {len(universes)}개 환경")
    for u in universes:
        print(f"    {u['name']:>20}: {u['rets'].shape[0]}일, {u['cost']}bps")

    # 벤치마크 성과 계산
    print("\n[벤치마크 계산]")
    tg("📏 <b>벤치마크 계산 중...</b>\n  기존 5팩터 전략 성과 측정")
    bench_idx = [fn.index(k) for k in BENCH_WEIGHTS if k in fn]
    bench_w = np.zeros(nf)
    for k,v in BENCH_WEIGHTS.items():
        if k in fn: bench_w[fn.index(k)] = v
    bench_gene = {'w':bench_w,'rb':7,'mode':'linear','flip':False}
    bench_sc = gene_scores(bench_gene, fac_np, rets_np)
    bench_r = fast_bt(rets_np, bench_sc, rebal=7, cost=3.0)
    bench_folds = walk_forward(rets_np, bench_sc, rebal=7, cost=3.0)
    print(f"  벤치 IS Sharpe: {bench_r['sharpe']:+.3f}" if bench_r else "  벤치 계산 실패")
    print(f"  벤치 OOS folds: {[f'{f:+.2f}' for f in bench_folds]}")
    actual_bench = np.mean(bench_folds) if bench_folds else BENCH_OOS
    print(f"  벤치 OOS 평균: {actual_bench:+.3f} (목표: {BENCH_OOS})")
    tg(f"📏 <b>벤치마크 측정 완료</b>\n"
       f"  IS Sharpe: {bench_r['sharpe']:+.3f}\n"
       f"  OOS 평균: {actual_bench:+.3f}\n"
       f"  목표: {BENCH_OOS}" if bench_r else
       f"📏 벤치마크 계산 실패, 목표값 {BENCH_OOS} 사용")

    # 텔레그램
    state={'run':True,'gen':0,'tested':0,'beat':0,'best_oos':-999,
           'best_combo':'-','hof':[],'t0':time.time()}
    if TG_TOKEN and TG_CHAT:
        threading.Thread(target=tg_poll,args=(state,),daemon=True).start()
        tg(f"🧬 <b>알파 그라인더 v5 가동!</b>\n\n"
           f"📡 현물 풀히스토리 (2020~현재)\n"
           f"💰 펀딩비: 실데이터+평균백필\n\n"
           f"📊 팩터: {nf}개\n"
           f"🌍 유니버스: {len(universes)}개\n"
           f"🔧 ML: linear/ridge/xgb/pca/rank/conditional\n"
           f"👑 벤치마크 OOS: {BENCH_OOS}\n"
           f"실측 벤치 OOS: {actual_bench:+.3f}\n\n"
           f"이 벤치마크를 이기는 전략이 나오면 알려드립니다 🏆\n\n"
           f"/status — 현재 상태\n"
           f"/top — 명예의 전당\n"
           f"/help — 도움말")
        print("  텔레그램 연결됨")
    else:
        print("  텔레그램 미설정")

    pop = [make_gene(nf) for _ in range(POP_SIZE)]
    hof = []
    best_ever = -999
    DATA_REFRESH = 50

    # 이전 학습 이어받기
    prev_genes, prev_gen = load_prev(fn, nf)
    if prev_genes:
        n_inject = min(len(prev_genes), POP_SIZE // 3)
        for i in range(n_inject):
            pop[i] = prev_genes[i]
        # 이전 전략 변이체도 추가
        for i in range(min(n_inject, POP_SIZE // 3)):
            idx = n_inject + i
            if idx < POP_SIZE:
                pop[idx] = mut(prev_genes[i % len(prev_genes)], nf, 'explore')
        print(f"  📂 이전 학습 복원: {n_inject}개 전략 주입 (이전 세대 {prev_gen})")
        tg(f"📂 <b>이전 학습 복원!</b>\n"
           f"  {n_inject}개 전략 주입\n"
           f"  이전 세대: {prev_gen}")
    else:
        print("  🆕 새로운 학습 시작")

    try:
        for gen in range(MAX_GEN):
            t0=time.time(); ph=get_phase(gen); state['gen']=gen

            # 데이터 갱신 (50세대마다)
            if gen>0 and gen%DATA_REFRESH==0:
                print(f"\n  📡 데이터 갱신...")
                try:
                    cl2,hi2,lo2,vo2,qv2,tb2,tr2,fu2=load_data()
                    F2=build_factors(cl2,hi2,lo2,vo2,qv2,tb2,tr2,fu2)
                    if len(F2)==nf:
                        rets_np=cl2.pct_change().fillna(0).values.copy()
                        fac_np=[F2[f].values.copy() for f in fn]
                        btc_close=cl2[btc_col].values.copy() if btc_col in cl2.columns else cl2.iloc[:,0].values.copy()
                        universes=make_universes(rets_np,fac_np)
                        print(f"  ✅ 갱신 완료")
                except Exception as e:
                    print(f"  ⚠️ 갱신 실패: {e}")

            # 평가
            scored=[]
            for g in pop:
                r=evaluate(g,universes,btc_close,fn)
                state['tested']=state.get('tested',0)+1
                if r is None: continue
                scored.append({'gene':g,**r})

            if not scored:
                pop=[make_gene(nf) for _ in range(POP_SIZE)]; continue

            scored.sort(key=lambda x:x['fit'],reverse=True)
            best=scored[0]
            avg_fit=np.mean([s['fit'] for s in scored])

            # 벤치 돌파 체크
            for s in scored:
                if s.get('beat'):
                    state['beat']=state.get('beat',0)+1
                    entry={
                        'gene':{'w':s['gene']['w'].copy(),'rb':s['gene']['rb'],
                                'mode':s['gene']['mode'],'flip':s['gene'].get('flip',False)},
                        'name':gshort(s['gene'],fn),'mode':s['gene']['mode'],
                        'rebal':s['gene']['rb'],'flip':s['gene'].get('flip',False),
                        'is':s['is'],'oos':s['oos'],'oos_min':s.get('oos_min',0),
                        'pass_rate':s.get('pass_rate',0),'n_folds':s.get('n_folds',0),
                        'fold_detail':s.get('fold_detail',[]),
                        'bull':s['bull'],'bear':s['bear'],'side':s['side'],
                        'sh_3':s.get('sh_3',0),'sh_5':s.get('sh_5',0),'sh_8':s.get('sh_8',0),
                        'gap':s['gap'],'ret':s['ret'],'mdd':s['mdd'],
                        'stage':s['stage'],'uni_pass':s['uni_pass'],
                        'uni_detail':s.get('uni_detail',[]),
                        'beat':True,'found_gen':gen,
                        'weights_detail':{fn[i]:float(s['gene']['w'][i])
                                          for i in range(nf) if abs(s['gene']['w'][i])>=0.01},
                    }
                    dup=False
                    for h in hof:
                        if np.sum(np.abs(h['gene']['w']-s['gene']['w']))<0.1:
                            if s['oos']>h['oos']: hof.remove(h)
                            else: dup=True
                            break
                    if not dup:
                        hof.append(entry)
                        # 텔레그램 상세 리포트!
                        if TG_TOKEN and TG_CHAT:
                            send_full_report(entry)

            hof.sort(key=lambda x:x['oos'],reverse=True)
            hof=hof[:30]
            state['hof']=hof

            new_rec=False
            if best.get('oos',0)>best_ever and best.get('stage',0)>=1:
                best_ever=best['oos']
                state['best_oos']=best_ever
                state['best_combo']=gshort(best['gene'],fn)
                new_rec=True

            # 콘솔
            el=time.time()-t0
            sb="⬛"*best.get('stage',0)+"⬜"*(5-best.get('stage',0))
            beat_str="🏆 BENCH BEAT!" if best.get('beat') else ""
            print(f"\n  세대 {gen:>4} [{ph:>7}] {el:.0f}s")
            print(f"    IS={best['is']:+.3f} OOS={best.get('oos',0):+.3f} "
                  f"uni={best.get('uni_pass',0)}/9 {sb} {beat_str}")
            print(f"    불={best.get('bull',0):+.2f} 베어={best.get('bear',0):+.2f} "
                  f"횡보={best.get('side',0):+.2f} 8bps={best.get('sh_8',0):+.2f}")
            print(f"    {gshort(best['gene'],fn)} [{best['gene']['mode']}] "
                  f"rb={best['gene']['rb']}d")
            print(f"    벤치돌파: {len([h for h in hof if h.get('beat')])}개 | "
                  f"역대OOS: {best_ever:+.3f} vs 벤치{BENCH_OOS}" if best_ever>-900 else
                  f"    벤치돌파: {len([h for h in hof if h.get('beat')])}개 | "
                  f"역대OOS: 아직없음 vs 벤치{BENCH_OOS}")

            # 텔레그램 (10세대마다)
            if TG_TOKEN and TG_CHAT and gen%10==0:
                if ph=='explore': icon="🥚"
                elif ph=='learn': icon="🐣"
                else: icon="🐉"
                hrs=(time.time()-state.get('t0',time.time()))/3600
                beat_cnt=len([h for h in hof if h.get('beat')])
                mode_str=best['gene']['mode']
                # 재미있는 진화 메시지
                if gen<5: flavor="첫 걸음마를 떼는 중..."
                elif gen<20: flavor="아직 아기... 세상을 탐험 중 🍼"
                elif gen<30: flavor="뭔가 배우기 시작했다 📚"
                elif gen<50: flavor="점점 똑똑해지는 중 🧠"
                elif gen<100: flavor="이제 좀 무서워지기 시작 😈"
                elif gen<200: flavor="괴물로 성장 중 💀"
                else: flavor="전설의 영역 🏰"
                best_oos_str=f"{best_ever:+.3f}" if best_ever>-900 else "아직없음"
                tg(f"{icon} <b>세대 {gen}</b> — {flavor}\n\n"
                   f"🏅 이번 세대 최고:\n"
                   f"  IS {best['is']:+.3f} → OOS {best.get('oos',0):+.3f}\n"
                   f"  🌍 유니버스 {best.get('uni_pass',0)}/9\n"
                   f"  🔧 {mode_str} | {gshort(best['gene'],fn)}\n\n"
                   f"📈 역대 OOS: {best_oos_str} (벤치 {BENCH_OOS})\n"
                   f"🏆 벤치 돌파: {beat_cnt}개\n"
                   f"⏱ {hrs:.1f}h 가동")

            if gen%20==0: save(hof,gen,fn)

            # 다음 세대
            cfg=PHASE[ph]; new_pop=[]
            for s in scored[:ELITE]: new_pop.append(s['gene'])
            # 망한 놈 뒤집기
            if ph=='explore':
                for s in scored[-3:]:
                    new_pop.append({'w':s['gene']['w'].copy(),'rb':s['gene']['rb'],
                                    'mode':s['gene']['mode'],'flip':not s['gene'].get('flip',False)})
            while len(new_pop)<POP_SIZE:
                ts=min(3,len(scored))
                p1=max(np.random.choice(scored,ts,replace=False),key=lambda x:x['fit'])['gene']
                p2=max(np.random.choice(scored,ts,replace=False),key=lambda x:x['fit'])['gene']
                child=cross(p1,p2,nf) if np.random.random()<0.6 else \
                    {'w':p1['w'].copy(),'rb':p1['rb'],'mode':p1['mode'],'flip':p1.get('flip',False)}
                new_pop.append(mut(child,nf,ph))
            n_fresh=max(2,int(POP_SIZE*cfg['fresh']))
            for i in range(n_fresh): new_pop[-(i+1)]=make_gene(nf)
            pop=new_pop

    except KeyboardInterrupt:
        print("\n\n  중단됨")

    save(hof,state.get('gen',0),fn)
    state['run']=False

    print(f"\n{'='*65}")
    beat_list=[h for h in hof if h.get('beat')]
    print(f"  🏁 결과: {len(beat_list)}개 벤치마크 돌파 전략")
    print(f"{'='*65}")
    for i,h in enumerate(beat_list[:10]):
        print(f"\n  [{i+1}] {h['name']}")
        print(f"      IS={h['is']:+.3f} OOS={h['oos']:+.3f} (벤치+{h['oos']-BENCH_OOS:+.3f})")
        print(f"      불={h['bull']:+.2f} 베어={h['bear']:+.2f} 횡보={h['side']:+.2f}")
        print(f"      uni={h['uni_pass']}/9 gap={h['gap']:.2f} MDD={h['mdd']:.1%}")
    if not beat_list:
        print("  벤치마크 돌파 전략 없음. 상위 5개:")
        for i,h in enumerate(hof[:5]):
            print(f"  [{i+1}] OOS={h['oos']:+.3f} {h['name']}")

    if TG_TOKEN and TG_CHAT:
        hrs=(time.time()-state.get('t0',time.time()))/3600
        tested=state.get('tested',0)
        lines=[f"🏁 <b>알파 그라인더 종료</b>\n",
               f"⏱ 총 {hrs:.1f}시간 가동",
               f"🧬 {state.get('gen',0)} 세대 진화",
               f"🔬 {tested:,}개 전략 테스트",
               f"🏆 벤치 돌파: {len(beat_list)}개",
               f"📈 역대 OOS: {best_ever:+.3f} (벤치 {BENCH_OOS})\n"]
        if beat_list:
            h=beat_list[0]
            lines+=[f"🥇 <b>최고 전략</b>",
                    f"  OOS {h['oos']:+.3f} (벤치+{h['oos']-BENCH_OOS:+.3f})",
                    f"  {h['name']}",
                    f"  [{h['mode']}] rb={h['rebal']}d"]
        else:
            lines.append("😢 벤치마크 돌파 실패... 다음에 다시!")
        tg('\n'.join(lines))
    print(f"{'='*65}")

if __name__=='__main__':
    main()
