# Alpha Grinder

크로스섹셔널 멀티팩터 롱/숏 크립토 전략 진화 + 자동매매  
유전 알고리즘으로 87+ 팩터 조합 탐색 → 최적 전략 자동매매

## 구성

| 파일 | 설명 |
|------|------|
| `alpha_grinder.py` | 유전 알고리즘 팩터 진화 (87+ 팩터, 6가지 ML 조합) |
| `live_bot_v5.py` | 자동매매 봇 (전략 #1, 넷 익스포저 = 0, 풀 시드) |
| `grinder_results-5.json` | 진화 결과 (18개 벤치마크 돌파 전략) |
| `deep_verify5.py` | 전략 심층 검증 (5단계) |
| `qs_report.py` | QuantStats 리포트 생성 |

## 전략 #1 (최우수)

- OOS Sharpe 1.97 / IS Sharpe 1.67
- 연수익 52% / MDD -15%
- 전 국면 양수 (불 1.9 / 베어 1.5 / 횡보 2.0)
- 8bps 비용에서도 Sharpe 1.55
- 29팩터 선형 조합 · 5일 리밸런싱 · 달러 뉴트럴

## 빠른 시작 (윈도우 CMD)

```cmd
py -m pip install -r requirements.txt
```

### 자동매매
```cmd
py live_bot_v5.py signal         # 시그널만 확인
py live_bot_v5.py rebal          # 리밸런싱 1회
py live_bot_v5.py status         # 포지션/잔고 조회
py live_bot_v5.py close          # 전체 청산
py live_bot_v5.py auto           # 자동 (5일마다)
```

### 알파 그라인더
```cmd
py alpha_grinder.py
```
텔레그램: /status /top /help

## v5 변경사항 (vs v4)

- 넷 익스포저 = 정확히 0 (롱/숏 달러 동일 강제)
- 풀 시드 사용 (잔고 100% 활용)
- 넷 보정 임계값 3% → 2%, 최대 5회 반복
