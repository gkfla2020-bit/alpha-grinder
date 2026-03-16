"""
라이브 봇 v5 — 전략 #1 (Gen 5440 최우수)
==========================================
v4 대비 변경:
  - 넷 익스포저 = 정확히 0 (롱/숏 달러 동일 강제)
  - 풀 시드 사용 (잔고 100% 활용)
  - 리밸런싱 시 넷 보정 로직 강화

vmom7+vmom14+fmr7+atr+cci+hilo120+beta30+resid30+buy_pct+excess7+ma10x30+ma20x60
Linear multi-factor · 5-day rebalance · Dollar-neutral L/S

OOS Sharpe 1.87 / 전 국면 양수 / 8bps에서도 Sharpe 1.42

사용법:
  python live_bot_v5.py signal    # 시그널만 확인
  python live_bot_v5.py rebal     # 리밸런싱 실행
  python live_bot_v5.py status    # 상태 조회
  python live_bot_v5.py close     # 전체 청산
  python live_bot_v5.py auto      # 자동 실행 (5일마다)
"""

import os, sys, time, hmac, hashlib, json, logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

# ── 설정 ──
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

API_KEY = os.getenv('BINANCE_TESTNET_KEY',
    '78Psqjd3YRp7NBMp5FW0HK0y54f8EQah01vX0s2YhTTA7maRNsvyuBF1rRfF7RM4')
API_SECRET = os.getenv('BINANCE_TESTNET_SECRET',
    'R9qSUsHNS6fUqeCzOabl5uOuIRadrkqMIHkT3yyLf8TCcMK8O8uWS86cKsSbGbnB')
BASE_URL = 'https://testnet.binancefuture.com'

SYMBOLS = ['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','BNBUSDT',
           'DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT']
NAMES = {'BTCUSDT':'BTC','ETHUSDT':'ETH','XRPUSDT':'XRP','SOLUSDT':'SOL',
         'BNBUSDT':'BNB','DOGEUSDT':'DOGE','ADAUSDT':'ADA','AVAXUSDT':'AVAX',
         'LINKUSDT':'LINK','DOTUSDT':'DOT'}

REBAL_DAYS = 5
LEVERAGE = 1
DATA_DAYS = 200

# ── 전략 #1 웨이트 (Gen 5440, OOS 1.87) ──
STRATEGY_WEIGHTS = {
    "mom10": 0.0266, "rev3": 0.0156, "rev10": 0.0214,
    "vmom7": 0.0762, "vmom14": 0.0505, "vmom21": 0.0129,
    "vmom30": 0.0202, "fmr7": 0.0484, "vbrk": -0.0231,
    "vol_asym": -0.0135, "atr": 0.0339, "cci": -0.0441,
    "willr": -0.0102, "hilo20": -0.0163, "hilo120": 0.0582,
    "beta30": -0.0317, "resid7": -0.0167, "resid14": 0.0166,
    "resid30": -0.0412, "corr_30v90": -0.0158, "buy_pct": -0.0446,
    "obv_mom": -0.0118, "pv_div3": -0.023, "pv_div14": -0.0135,
    "eth_btc_mom": -0.0163, "excess7": -0.0696,
    "ma10x30": 0.0371, "ma20x60": -0.0382, "garch_20v90": -0.0133
}

STATE_FILE = os.path.join(os.path.dirname(__file__), 'bot_v5_state.json')

# ── 텔레그램 ──
TG_TOKEN = os.getenv('TG_BOT_TOKEN', '8793543603:AAFgjJ5GfO93as3ssmcFEL9tiZhmSIYXBgE')
TG_CHAT = os.getenv('TG_CHAT_ID', '8451071451')

def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    try:
        for i in range(0, len(msg), 4000):
            requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                json={'chat_id': TG_CHAT, 'text': msg[i:i+4000], 'parse_mode': 'HTML'},
                timeout=10)
    except: pass

log = logging.getLogger('bot_v5')
log.setLevel(logging.INFO)
_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
_sh = logging.StreamHandler(
    open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False))
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(
    os.path.join(os.path.dirname(__file__), 'bot_v5.log'), encoding='utf-8')
_fh.setFormatter(_fmt)
log.addHandler(_sh)
log.addHandler(_fh)

# ═══════════════════════════════════════
#  API 헬퍼
# ═══════════════════════════════════════
def sign(params):
    query = urlencode(params)
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params['signature'] = sig
    return params

def api_get(path, params=None):
    p = params or {}; p['timestamp'] = int(time.time()*1000); p['recvWindow'] = 10000
    r = requests.get(BASE_URL+path, params=sign(p), headers={'X-MBX-APIKEY':API_KEY}, timeout=15)
    return r.json()

def api_post(path, params=None):
    p = params or {}; p['timestamp'] = int(time.time()*1000); p['recvWindow'] = 10000
    r = requests.post(BASE_URL+path, params=sign(p), headers={'X-MBX-APIKEY':API_KEY}, timeout=15)
    return r.json()

def api_delete(path, params=None):
    p = params or {}; p['timestamp'] = int(time.time()*1000); p['recvWindow'] = 10000
    r = requests.delete(BASE_URL+path, params=sign(p), headers={'X-MBX-APIKEY':API_KEY}, timeout=15)
    return r.json()

def get_account():
    return api_get('/fapi/v2/account')

def get_positions():
    acc = get_account()
    pos = {}
    for p in acc.get('positions', []):
        amt = float(p['positionAmt'])
        if amt != 0 and p['symbol'] in SYMBOLS:
            pos[p['symbol']] = amt
    return pos

def get_balance():
    acc = get_account()
    for a in acc.get('assets', []):
        if a['asset'] == 'USDT':
            return float(a['walletBalance'])
    return 0.0

def get_prices():
    r = requests.get(BASE_URL+'/fapi/v1/ticker/price', timeout=10).json()
    return {t['symbol']: float(t['price']) for t in r if t['symbol'] in SYMBOLS}

def get_exchange_info():
    r = requests.get(BASE_URL+'/fapi/v1/exchangeInfo', timeout=10).json()
    info = {}
    for s in r.get('symbols', []):
        if s['symbol'] not in SYMBOLS: continue
        filters = {f['filterType']: f for f in s['filters']}
        lot = filters.get('LOT_SIZE', {})
        info[s['symbol']] = {
            'minQty': float(lot.get('minQty', 0.001)),
            'stepSize': float(lot.get('stepSize', 0.001)),
            'pricePrecision': s.get('pricePrecision', 2),
            'quantityPrecision': s.get('quantityPrecision', 3),
        }
    return info

def round_step(value, step):
    if step <= 0: return value
    precision = max(0, -int(np.floor(np.log10(step))))
    return round(round(value / step) * step, precision)

def set_leverage(symbol, lev=1):
    try: api_post('/fapi/v1/leverage', {'symbol': symbol, 'leverage': lev})
    except: pass

def place_order(symbol, side, qty, info, retry=3):
    si = info.get(symbol)
    if not si: return False
    qty = round_step(qty, si['stepSize'])
    if qty < si['minQty']: return False
    for attempt in range(retry):
        p = {'symbol': symbol, 'side': side, 'type': 'MARKET',
             'quantity': f"{qty:.{si['quantityPrecision']}f}"}
        log.info(f"  📤 {side} {NAMES.get(symbol,symbol)} {qty}" +
                 (f" (재시도 {attempt+1})" if attempt > 0 else ""))
        r = api_post('/fapi/v1/order', p)
        if 'orderId' in r:
            log.info(f"     ✅ 체결 (ID: {r['orderId']})")
            return True
        msg = r.get('msg', str(r))
        log.warning(f"     ❌ 실패: {msg}")
        if 'insufficient' in msg.lower() and attempt < retry - 1:
            qty = round_step(qty * 0.7, si['stepSize'])
            if qty < si['minQty']:
                log.warning(f"     ⚠️ 수량 축소 후 최소 미달, 포기")
                return False
            log.info(f"     🔄 수량 70%로 축소: {qty}")
            time.sleep(1)
        elif attempt < retry - 1:
            time.sleep(2)
        else:
            return False
    return False

# ═══════════════════════════════════════
#  데이터 수집
# ═══════════════════════════════════════
def fetch_klines(symbol, limit=200):
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol, 'interval': '1d', 'limit': limit}, timeout=15)
        data = r.json()
        if not isinstance(data, list): return None
        df = pd.DataFrame(data, columns=[
            'ot','open','high','low','close','vol','ct','qv',
            'trades','tbv','tbq','ig'])
        df['date'] = pd.to_datetime(df['ot'], unit='ms').dt.normalize()
        for c in ['open','high','low','close','vol','qv','tbv']:
            df[c] = df[c].astype(float)
        df['trades'] = df['trades'].astype(float)
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        log.error(f"klines 실패 {symbol}: {e}")
        return None

def fetch_funding(symbol):
    all_data, et = [], None
    for _ in range(4):
        p = {'symbol': symbol, 'limit': 1000}
        if et: p['endTime'] = et
        try:
            d = requests.get('https://fapi.binance.com/fapi/v1/fundingRate',
                params=p, timeout=10).json()
            if not d or not isinstance(d, list): break
            all_data.extend(d); et = d[0]['fundingTime'] - 1
        except: break
    if not all_data: return None
    df = pd.DataFrame(all_data)
    df['date'] = pd.to_datetime(df['fundingTime'], unit='ms')
    df['rate'] = df['fundingRate'].astype(float)
    return df.set_index('date')['rate'].resample('D').sum()

# ═══════════════════════════════════════
#  팩터 엔진 (전략 #1 전용 29개 팩터)
# ═══════════════════════════════════════
def zs(df):
    mu = df.mean(axis=1); sig = df.std(axis=1).replace(0, 1)
    return df.sub(mu, axis=0).div(sig, axis=0)

def build_factors(cl, hi, lo, vo, qv, tb, tr, fu):
    syms = list(cl.columns); dates = cl.index
    ret = cl.pct_change(); btc_r = ret.iloc[:, 0]
    F = {}

    F['mom10'] = zs(cl.pct_change(10).rank(axis=1, pct=True))
    F['rev3'] = zs(1 - cl.pct_change(3).rank(axis=1, pct=True))
    F['rev10'] = zs(1 - cl.pct_change(10).rank(axis=1, pct=True))

    for lb in [7, 14, 21, 30]:
        vw = (ret * vo).rolling(lb).sum() / vo.rolling(lb).sum().replace(0, 1)
        F[f'vmom{lb}'] = zs(vw.rank(axis=1, pct=True))

    fmr = pd.DataFrame(0.0, index=dates, columns=syms)
    for s in syms:
        f = fu.get(s)
        if f is None: continue
        fr = f.reindex(dates).fillna(0)
        mu = fr.rolling(7, min_periods=1).mean()
        sig = fr.rolling(7, min_periods=1).std().replace(0, 0.001)
        fmr[s] = -(fr - mu) / sig
    F['fmr7'] = zs(fmr)

    rng = (hi - lo).replace(0, 1)
    F['vbrk'] = zs(((cl - lo) / rng).rolling(20).mean().rank(axis=1, pct=True))

    dv = ret.clip(upper=0).rolling(20).std()
    uv = ret.clip(lower=0).rolling(20).std().replace(0, 0.001)
    F['vol_asym'] = zs((dv / uv).rank(axis=1, pct=True, ascending=False))

    atr = pd.DataFrame(index=dates, columns=syms, dtype=float)
    for c in syms:
        tr_val = pd.concat([
            hi[c] - lo[c],
            (hi[c] - cl[c].shift(1)).abs(),
            (lo[c] - cl[c].shift(1)).abs()
        ], axis=1).max(axis=1)
        atr[c] = tr_val.rolling(14).mean() / cl[c]
    F['atr'] = zs(atr.rank(axis=1, pct=True, ascending=False))

    tp = (hi + lo + cl) / 3
    ma_tp = tp.rolling(20).mean()
    md = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True).replace(0, 1)
    F['cci'] = zs(((tp - ma_tp) / (0.015 * md)).rank(axis=1, pct=True, ascending=False))

    hh14 = hi.rolling(14).max(); ll14 = lo.rolling(14).min()
    F['willr'] = zs(((hh14 - cl) / (hh14 - ll14).replace(0, 1)).rank(axis=1, pct=True))

    for lb_hl in [20, 120]:
        hh = cl.rolling(lb_hl).max(); ll = cl.rolling(lb_hl).min()
        F[f'hilo{lb_hl}'] = zs(((cl - ll) / (hh - ll).replace(0, 1)).rank(axis=1, pct=True))

    betas30 = pd.DataFrame(index=dates, columns=syms, dtype=float)
    for c in syms:
        betas30[c] = ret[c].rolling(30).cov(btc_r) / btc_r.rolling(30).var().replace(0, 1)
    F['beta30'] = zs(1 - betas30.rank(axis=1, pct=True).astype(float))

    betas60 = pd.DataFrame(index=dates, columns=syms, dtype=float)
    for c in syms:
        betas60[c] = ret[c].rolling(60).cov(btc_r) / btc_r.rolling(60).var().replace(0, 1)
    resid = pd.DataFrame(index=dates, columns=syms, dtype=float)
    for c in syms: resid[c] = ret[c] - betas60[c].fillna(1) * btc_r
    for lb_res in [7, 14, 30]:
        F[f'resid{lb_res}'] = zs(resid.rolling(lb_res).sum().rank(axis=1, pct=True))

    corr30 = pd.DataFrame(index=dates, columns=syms, dtype=float)
    corr90 = pd.DataFrame(index=dates, columns=syms, dtype=float)
    for c in syms:
        corr30[c] = ret[c].rolling(30).corr(btc_r)
        corr90[c] = ret[c].rolling(90).corr(btc_r)
    F['corr_30v90'] = zs((corr30 - corr90).rank(axis=1, pct=True))

    F['buy_pct'] = zs((tb / vo.replace(0, 1)).rolling(5).mean().rank(axis=1, pct=True))

    obv = pd.DataFrame(0.0, index=dates, columns=syms)
    for c in syms:
        s = np.sign(ret[c].to_numpy(dtype=float, copy=True))
        v = vo[c].to_numpy(dtype=float, copy=True)
        obv[c] = np.cumsum(s * v)
    F['obv_mom'] = zs(obv.pct_change(14, fill_method=None).rank(axis=1, pct=True))

    for lb_pv in [3, 14]:
        F[f'pv_div{lb_pv}'] = zs(
            cl.pct_change(lb_pv).rank(axis=1, pct=True) -
            vo.pct_change(lb_pv).rank(axis=1, pct=True))

    if 'ETHUSDT' in cl.columns and 'BTCUSDT' in cl.columns:
        eth_btc = cl['ETHUSDT'] / cl['BTCUSDT']
        eb_mom = eth_btc.pct_change(14)
        eb_df = pd.DataFrame(
            eb_mom.to_numpy(dtype=float, copy=True)[:, None] * np.ones((1, len(syms))),
            index=dates, columns=syms)
        eb_df['BTCUSDT'] = -eb_df['BTCUSDT']
        F['eth_btc_mom'] = zs(eb_df.rank(axis=1, pct=True))

    eq_ret7 = cl.pct_change(7)
    univ_mean7 = eq_ret7.mean(axis=1)
    excess = eq_ret7.sub(univ_mean7, axis=0)
    F['excess7'] = zs(excess.rank(axis=1, pct=True))

    for fast, slow in [(10, 30), (20, 60)]:
        ma_f = cl.rolling(fast).mean(); ma_s = cl.rolling(slow).mean()
        F[f'ma{fast}x{slow}'] = zs(((ma_f - ma_s) / ma_s).rank(axis=1, pct=True))

    F['garch_20v90'] = zs((
        ret.rolling(20).std() / ret.rolling(90).std().replace(0, 1)
    ).rank(axis=1, pct=True))

    log.info(f"  팩터 {len(F)}개 계산 완료")
    return F

# ═══════════════════════════════════════
#  시그널 계산 (v5: 넷 익스포저 = 정확히 0)
# ═══════════════════════════════════════
def calc_signal():
    """전략 #1 시그널 → dict {symbol: weight}, 롱합 = 0.5, 숏합 = -0.5 (넷 = 0)"""
    log.info("=" * 60)
    log.info("📊 전략 #1 시그널 계산 v5 (넷 익스포저 = 0)")

    log.info("[1/4] 데이터 수집...")
    all_kl, all_fu = {}, {}
    for sym in SYMBOLS:
        kl = fetch_klines(sym, DATA_DAYS)
        if kl is None:
            log.warning(f"  {NAMES[sym]}: 데이터 실패"); continue
        all_kl[sym] = kl
        all_fu[sym] = fetch_funding(sym)
        time.sleep(0.08)

    cl = pd.DataFrame({s: all_kl[s]['close'] for s in all_kl}).dropna()
    hi = pd.DataFrame({s: all_kl[s]['high'] for s in all_kl}).reindex(cl.index).ffill()
    lo = pd.DataFrame({s: all_kl[s]['low'] for s in all_kl}).reindex(cl.index).ffill()
    vo = pd.DataFrame({s: all_kl[s]['vol'] for s in all_kl}).reindex(cl.index).ffill()
    qv = pd.DataFrame({s: all_kl[s]['qv'] for s in all_kl}).reindex(cl.index).ffill()
    tb = pd.DataFrame({s: all_kl[s]['tbv'] for s in all_kl}).reindex(cl.index).ffill()
    tr = pd.DataFrame({s: all_kl[s]['trades'] for s in all_kl}).reindex(cl.index).ffill()

    for sym in all_fu:
        if all_fu[sym] is None: continue
        avg_rate = all_fu[sym].mean()
        all_fu[sym] = all_fu[sym].reindex(cl.index).fillna(avg_rate)

    syms = list(cl.columns)
    log.info(f"  {len(syms)}개 코인, {len(cl)}일 데이터")
    if len(cl) < 130:
        log.error("데이터 부족 (최소 130일 필요)"); return None

    log.info("[2/4] 팩터 계산...")
    F = build_factors(cl, hi, lo, vo, qv, tb, tr, all_fu)
    fn = list(F.keys()); nf = len(fn)

    log.info("[3/4] 스코어 계산...")
    w = np.zeros(nf)
    for k, v in STRATEGY_WEIGHTS.items():
        if k in fn: w[fn.index(k)] = v
    sm = np.sum(np.abs(w))
    if sm > 0: w /= sm

    missing = [k for k in STRATEGY_WEIGHTS if k not in fn]
    if missing: log.warning(f"  ⚠️ 누락 팩터: {missing}")

    latest_scores = np.zeros(len(syms))
    for i in range(nf):
        if abs(w[i]) < 1e-6: continue
        latest_scores += w[i] * np.nan_to_num(F[fn[i]].iloc[-1].to_numpy(dtype=float, copy=True), 0)

    # ── v5 핵심: 넷 익스포저 = 정확히 0 ──
    # 스코어를 디미닝 후, 롱/숏 각각 정규화하여 합이 +0.5 / -0.5 되게
    dm = latest_scores - np.nanmean(latest_scores)

    long_mask = dm > 0
    short_mask = dm < 0

    if not np.any(long_mask) or not np.any(short_mask):
        log.error("롱/숏 분리 불가 (한쪽만 존재)"); return None

    weights = np.zeros(len(syms))
    # 롱 쪽: 합 = +0.5
    long_sum = np.sum(dm[long_mask])
    weights[long_mask] = dm[long_mask] / long_sum * 0.5
    # 숏 쪽: 합 = -0.5
    short_sum = np.sum(np.abs(dm[short_mask]))
    weights[short_mask] = dm[short_mask] / short_sum * 0.5  # dm[short_mask]는 음수

    result = {}
    log.info("\n[4/4] 최종 시그널 (넷 = 0 강제):")
    for i, sym in enumerate(syms):
        result[sym] = float(weights[i])
        d = "🟢 롱" if weights[i] > 0.01 else "🔴 숏" if weights[i] < -0.01 else "⚪ —"
        log.info(f"  {NAMES[sym]:>5}: {d} {abs(weights[i]):.1%}")

    long_sum = sum(v for v in result.values() if v > 0)
    short_sum = sum(v for v in result.values() if v < 0)
    log.info(f"\n  롱 합: {long_sum:+.4f} / 숏 합: {short_sum:+.4f} / 넷: {long_sum+short_sum:+.6f}")
    return result

# ═══════════════════════════════════════
#  리밸런싱 (v5: 풀 시드 + 넷 = 0)
# ═══════════════════════════════════════
def rebalance():
    tg("🔄 <b>[봇 v5] 리밸런싱 시작</b>\n"
       f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    signal = calc_signal()
    if signal is None:
        log.error("시그널 계산 실패, 리밸런싱 중단")
        tg("❌ <b>시그널 계산 실패</b> — 리밸런싱 중단")
        return

    balance = get_balance()
    log.info(f"\n💰 잔고: ${balance:,.2f} (전액 사용)")
    if balance < 10:
        log.error("잔고 부족")
        tg(f"❌ <b>잔고 부족</b>: ${balance:,.2f}")
        return

    prices = get_prices()
    info = get_exchange_info()
    cur_pos = get_positions()

    for sym in SYMBOLS:
        set_leverage(sym, LEVERAGE)

    # v5: 전액 사용
    alloc = balance * LEVERAGE
    target_qty = {}
    for sym in SYMBOLS:
        if sym not in prices or sym not in info:
            target_qty[sym] = 0.0; continue
        target_qty[sym] = alloc * signal.get(sym, 0) / prices[sym]

    log.info("\n📋 주문 실행:")
    orders = 0

    # 1단계: 포지션 축소/청산
    for sym in SYMBOLS:
        if sym not in info: continue
        cur = cur_pos.get(sym, 0.0)
        tgt = target_qty.get(sym, 0.0)
        diff = tgt - cur
        if cur > 0 and diff < 0:
            side, qty = 'SELL', min(abs(diff), abs(cur))
        elif cur < 0 and diff > 0:
            side, qty = 'BUY', min(abs(diff), abs(cur))
        else: continue
        if sym in prices and abs(qty * prices[sym]) < 5: continue
        if qty < info[sym]['minQty']: continue
        place_order(sym, side, qty, info); orders += 1; time.sleep(0.15)

    # 2단계: 포지션 증가/신규
    time.sleep(0.5)
    cur_pos = get_positions()
    for sym in SYMBOLS:
        if sym not in info: continue
        cur = cur_pos.get(sym, 0.0)
        diff = target_qty.get(sym, 0.0) - cur
        if sym in prices and abs(diff * prices[sym]) < 5: continue
        if abs(diff) < info[sym]['minQty']: continue
        side = 'BUY' if diff > 0 else 'SELL'
        place_order(sym, side, abs(diff), info); orders += 1; time.sleep(0.15)

    log.info(f"\n✅ 주문 완료: {orders}건")

    # ── v5: 넷 익스포저 강제 보정 ──
    time.sleep(1)
    prices = get_prices()
    final_pos = get_positions()

    def calc_net_gross(pos):
        tl, ts = 0, 0
        for s, a in pos.items():
            v = a * prices.get(s, 0)
            if v > 0: tl += v
            else: ts += abs(v)
        return tl, ts

    total_long, total_short = calc_net_gross(final_pos)
    net = total_long - total_short
    gross = total_long + total_short

    # 넷이 그로스의 2% 초과하면 보정 (v4보다 엄격: 3% → 2%)
    max_attempts = 5
    for attempt in range(max_attempts):
        if gross == 0 or abs(net) <= gross * 0.02:
            break
        log.warning(f"\n⚠️ 넷 불균형 [{attempt+1}/{max_attempts}]: "
                    f"${net:,.1f} (그로스의 {abs(net)/gross:.1%})")

        if net > 0:
            # 롱 과다 → 가장 큰 롱 축소
            candidates = [(s, a * prices.get(s, 0)) for s, a in final_pos.items() if a > 0]
        else:
            # 숏 과다 → 가장 큰 숏 축소
            candidates = [(s, abs(a) * prices.get(s, 0)) for s, a in final_pos.items() if a < 0]
        candidates.sort(key=lambda x: x[1], reverse=True)

        for sym, val in candidates[:3]:
            reduce_val = abs(net) / 2  # 넷의 절반만 보정 (과보정 방지)
            reduce_amt = reduce_val / prices.get(sym, 1)
            max_reduce = abs(final_pos.get(sym, 0)) * 0.5
            reduce_amt = min(reduce_amt, max_reduce)
            if sym in info and reduce_amt >= info[sym]['minQty']:
                side = 'SELL' if net > 0 else 'BUY'
                ok = place_order(sym, side, reduce_amt, info)
                if ok:
                    time.sleep(0.5)
                    final_pos = get_positions()
                    total_long, total_short = calc_net_gross(final_pos)
                    net = total_long - total_short
                    gross = total_long + total_short
                    if gross > 0 and abs(net) <= gross * 0.02:
                        log.info(f"  ✅ 보정 완료: 넷 ${net:,.1f} ({abs(net)/gross:.1%})")
                        break

    log.info("\n📊 최종 포지션:")
    total_long, total_short = 0, 0
    for sym in SYMBOLS:
        amt = final_pos.get(sym, 0.0)
        if amt == 0: continue
        val = amt * prices.get(sym, 0)
        if amt > 0: total_long += val
        else: total_short += abs(val)
        d = "🟢" if amt > 0 else "🔴"
        log.info(f"  {d} {NAMES[sym]:>5}: {abs(amt):.4f} (${abs(val):,.1f})")
    net_final = total_long - total_short
    gross_final = total_long + total_short
    net_pct = abs(net_final) / gross_final * 100 if gross_final > 0 else 0
    log.info(f"\n  롱: ${total_long:,.1f} / 숏: ${total_short:,.1f}")
    log.info(f"  넷: ${net_final:,.1f} ({net_pct:.1f}%) / 그로스: ${gross_final:,.1f}")
    log.info(f"  시드 활용: {gross_final/balance*100:.0f}%" if balance > 0 else "")

    save_state({
        'last_rebal': datetime.now(timezone.utc).isoformat(),
        'balance': balance, 'positions': {s: final_pos.get(s, 0) for s in SYMBOLS},
        'signal': signal, 'orders': orders,
        'net_exposure': net_final, 'gross_exposure': gross_final,
    })
    log.info("=" * 60)

    # ── 텔레그램 리밸런싱 리포트 ──
    lines = [
        f"✅ <b>[봇 v5] 리밸런싱 완료</b>",
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
        f"💰 잔고: ${balance:,.0f} | 주문: {orders}건",
        f"",
        f"📊 <b>포지션</b>",
    ]
    for sym in SYMBOLS:
        amt = final_pos.get(sym, 0.0)
        if amt == 0: continue
        val = amt * prices.get(sym, 0)
        d = "🟢" if amt > 0 else "🔴"
        lines.append(f"  {d} {NAMES[sym]:>5}: ${abs(val):,.0f} ({signal.get(sym,0):+.1%})")
    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"  롱: ${total_long:,.0f} / 숏: ${total_short:,.0f}",
        f"  넷: ${net_final:,.0f} ({net_pct:.1f}%)",
        f"  그로스: ${gross_final:,.0f} (시드 {gross_final/balance*100:.0f}%)" if balance > 0 else "",
        f"",
        f"⏭ 다음 리밸런싱: {REBAL_DAYS}일 후",
    ]
    tg('\n'.join(lines))

# ═══════════════════════════════════════
#  유틸리티
# ═══════════════════════════════════════
def save_state(data):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except: pass

def load_state():
    try:
        with open(STATE_FILE, 'r') as f: return json.load(f)
    except: return {}

def status():
    print("=" * 60)
    print("  🏆 전략 #1 봇 v5 — 넷 익스포저 = 0 / 풀 시드")
    print("  vmom7+vmom14+fmr7+atr+cci+hilo120+beta30+resid30+...")
    print("  OOS Sharpe 1.87 / 등급 A")
    print("=" * 60)
    balance = get_balance()
    print(f"\n  💰 잔고: ${balance:,.2f}")
    prices = get_prices(); pos = get_positions()
    if not pos:
        print("  📭 포지션 없음")
    else:
        print(f"\n  📊 포지션 ({len(pos)}개):")
        total_long, total_short = 0, 0
        for sym, amt in sorted(pos.items(), key=lambda x: abs(x[1]*prices.get(x[0],0)), reverse=True):
            price = prices.get(sym, 0); val = amt * price
            d = "🟢 롱" if amt > 0 else "🔴 숏"
            if amt > 0: total_long += val
            else: total_short += abs(val)
            print(f"    {NAMES.get(sym,sym):>5}: {d} {abs(amt):.4f} @ ${price:,.2f} = ${abs(val):,.1f}")
        net = total_long - total_short
        gross = total_long + total_short
        net_pct = abs(net) / gross * 100 if gross > 0 else 0
        print(f"\n    롱: ${total_long:,.1f} / 숏: ${total_short:,.1f}")
        print(f"    넷: ${net:,.1f} ({net_pct:.1f}%) / 그로스: ${gross:,.1f}")
        print(f"    시드 활용: {gross/balance*100:.0f}%" if balance > 0 else "")
    state = load_state()
    if state.get('last_rebal'):
        print(f"\n  ⏰ 마지막 리밸런싱: {state['last_rebal']}")
    print("=" * 60)

def signal_only():
    signal = calc_signal()
    if signal:
        print("\n  💡 페이퍼 트레이딩 모드 — 주문 미실행")

def close_all():
    log.info("🛑 전체 청산 시작")
    tg("🛑 <b>[봇 v5] 전체 청산 시작</b>")
    info = get_exchange_info(); pos = get_positions()
    if not pos: log.info("포지션 없음"); tg("📭 포지션 없음"); return
    for sym, amt in pos.items():
        if sym not in info: continue
        place_order(sym, 'SELL' if amt > 0 else 'BUY', abs(amt), info)
        time.sleep(0.2)
    save_state({'last_rebal': datetime.now(timezone.utc).isoformat(), 'action': 'close_all'})
    log.info("✅ 청산 완료")
    tg("✅ <b>[봇 v5] 전체 청산 완료</b>")

def auto_run():
    INTERVAL = REBAL_DAYS * 24 * 3600
    log.info("=" * 60)
    log.info("🤖 자동 실행 모드 v5 — 넷 = 0 / 풀 시드")
    log.info(f"   리밸런싱: {REBAL_DAYS}일마다 / 레버리지: {LEVERAGE}x")
    log.info("   종료: Ctrl+C")
    log.info("=" * 60)
    tg(f"🤖 <b>[봇 v5] 자동매매 시작</b>\n\n"
       f"📐 전략 #1 (Gen 5440, OOS 1.87)\n"
       f"📅 리밸런싱: {REBAL_DAYS}일마다\n"
       f"⚖️ 넷 익스포저 = 0 / 풀 시드\n"
       f"🏦 레버리지: {LEVERAGE}x\n\n"
       f"Ctrl+C로 종료")
    try: rebalance()
    except Exception as e:
        log.error(f"첫 리밸런싱 실패: {e}")
        tg(f"❌ <b>첫 리밸런싱 실패</b>\n{e}")
    while True:
        try:
            next_time = datetime.now(timezone.utc) + timedelta(days=REBAL_DAYS)
            log.info(f"\n⏰ 다음 리밸런싱: {next_time.strftime('%Y-%m-%d %H:%M UTC')}")
            time.sleep(INTERVAL)
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] 리밸런싱 실행")
            rebalance()
        except KeyboardInterrupt:
            log.info("\n🛑 자동 실행 종료")
            tg("🛑 <b>[봇 v5] 자동매매 종료</b>")
            break
        except Exception as e:
            log.error(f"에러: {e}"); log.info("120초 후 재시도...")
            tg(f"⚠️ <b>[봇 v5] 에러 발생</b>\n{e}\n120초 후 재시도")
            time.sleep(120)

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'auto'
    cmds = {
        'signal': signal_only, 'rebal': rebalance, 'status': status,
        'close': close_all, 'auto': auto_run
    }
    if cmd in cmds:
        cmds[cmd]()
    else:
        print(f"사용법: python {sys.argv[0]} [signal|rebal|status|close|auto]")
