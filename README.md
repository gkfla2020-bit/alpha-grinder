# Alpha Grinder

크로스섹셔널 멀티팩터 롱/숏 크립토 전략 진화 엔진 + 자동매매 봇  
유전 알고리즘으로 87+ 팩터 조합 탐색 → 최적 전략 실시간 자동매매

## 파일 구조

```
alpha_grinder.py         ← 유전 알고리즘 팩터 진화 (87+ 팩터, 6가지 ML 조합)
live_bot_v5.py           ← 자동매매 봇 (전략 #1, 넷 익스포저 = 0, 풀 시드)
grinder_results-5.json   ← 진화 결과 (18개 벤치마크 돌파 전략)
deep_verify5.py          ← 전략 심층 검증 (5단계)
qs_report.py             ← QuantStats 리포트 생성 (vs BTC Buy&Hold)
```

## 전략 #1 성과 (최우수, Gen 5440)

| 항목 | 값 |
|------|-----|
| OOS Sharpe | 1.97 |
| IS Sharpe | 1.67 |
| 연수익 | 52% |
| MDD | -15% |
| 불마켓 | 1.92 |
| 베어마켓 | 1.55 |
| 횡보장 | 1.98 |
| 8bps 비용 Sharpe | 1.55 |
| 유니버스 통과 | 9/9 |
| Walk-Forward 통과 | 17/17 folds |

29팩터 선형 조합 · 5일 리밸런싱 · 달러 뉴트럴 (넷 익스포저 = 0)

## 빠른 시작 (윈도우 CMD)

### 설치
```cmd
py -m pip install -r requirements.txt
```

### 자동매매 (live_bot_v5.py)
```cmd
py live_bot_v5.py signal    # 시그널만 확인 (주문 안 나감)
py live_bot_v5.py rebal     # 리밸런싱 1회 실행
py live_bot_v5.py status    # 현재 포지션/잔고 조회
py live_bot_v5.py close     # 전체 청산
py live_bot_v5.py auto      # 자동 실행 (5일마다 리밸런싱)
```

테스트넷 키가 코드에 내장되어 있어서 `.env` 없이 바로 실행 가능.  
실전 전환 시 `BASE_URL`을 `https://fapi.binance.com`으로 변경 + `.env`에 실전 키 설정.

### 알파 그라인더 (alpha_grinder.py)
```cmd
py alpha_grinder.py
```
서버에 돌려두면 전략이 스스로 진화. 벤치마크(OOS 1.34) 돌파 시 텔레그램 알림.  
텔레그램 명령어: `/status` `/top` `/help`

### 검증
```cmd
py deep_verify5.py          # 5단계 심층 검증
py qs_report.py             # QuantStats HTML 리포트
```

## v5 봇 특징

- 넷 익스포저 = 정확히 0 (롱/숏 달러 동일 강제, 2% 임계값 자동 보정)
- 풀 시드 사용 (잔고 100% 활용)
- 마진 부족 시 자동 수량 축소 + 재시도 (최대 3회)
- UTF-8 로깅 (`bot_v5.log`)

## 테스트넷 검증 결과

```
잔고: $5,053
주문: 9건 전량 체결
롱: $2,455 (XRP, SOL, DOGE, AVAX, LINK)
숏: $2,524 (ETH, BNB, ADA, DOT)
넷: $-69 (1.4%) / 그로스: $4,979
시드 활용: 99%
```

## 알파 그라인더 스펙

- 87+ 팩터 (모멘텀/리버설/변동성/기술적/베타/오더플로우/크로스에셋 등)
- 6가지 ML 조합 (linear, ridge, xgb, pca, rank_product, conditional)
- 9개 유니버스 동시 시뮬 (전체/최근1년/최근2년 × 비용 3/5/8bps + 부트스트랩)
- Walk-Forward OOS + 국면별 생존력 + 비용 내성 검증
- 이전 학습 자동 이어받기 (`grinder_results.json`)
- 텔레그램 실시간 모니터링
