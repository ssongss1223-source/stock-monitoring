# Checkpoint

## Current Goal
- XGBoost 정상 동작 확인 + live xgb_prob 분포 검증 후 필터링 적용

## Current Status
- VM 운영 중, 내일 07:00 KST 정기 분석에서 XGBoost 처음으로 정상 실행 예정
- 텔레그램 전체 분석대상 목록 제거 완료
- XGBoost 이전 실행은 모두 실패 상태였음 (sklearn 미설치, scoring_version 컬럼 누락)

## Done
- 텔레그램 전체 분석대상 목록(350종목) 제거 (`agents/report.py`)
- VM에 scikit-learn 설치 (XGBoost 추론 필수 의존성)
- DB 마이그레이션: `signal_history`에 `scoring_version` 컬럼 추가
- VM git pull + 서비스 재시작 완료
- label_3d_5pct 정의 확인: **3일 내 최고가**가 진입가(T+1 시가) 대비 +5% 터치 여부 (`backtest/labeler.py`)

## Remaining

**[즉시]**
- 내일 07:00 KST 텔레그램 수신 후 xgb_prob 분포 확인
- xgb_prob 분포 보고 필터 threshold 결정 (현재 S등급 내 필터 없음)

**[중기] XGBoost를 실제 필터로 활용**
- 현재: S등급 기술 신호 → xgb_prob 참고 표시만
- 목표: S등급 중 xgb_prob >= threshold 만 발송 (또는 순위 상위 N개)
- 선행: live xgb_prob 분포 확인 필요

**[중기] 신호 사후 검증 자동화**
- `signal_history × ohlcv_daily` JOIN → 3거래일 후 max_high 계산
- 방향 B: `/verify N` 텔레그램 명령으로 최근 N일 성과 조회

**[중기] 종목 그룹별 모델**
- 변동성 구간(고/중/저)별 모델 + 그룹별 라벨 최적화
- 선행 조건: live 데이터 충분히 축적 + sector 데이터 적재
- 주의: 그룹 기준 피처(hist_volatility_20d)와 피처 독립성, walk-forward validation 필요

**[중기] sector 데이터 적재**
- `pykrx.stock.get_market_sector_classifications()` → ticker_master

**[중기] 장세 레이어 강화**
- 외국인/기관 5일 net을 지수 레벨에서 집계 → market_ctx 추가

**[보류] market_cap NULL**
- KRX API 차단, 대체 불가

## Risks / Blockers
- 이전까지 XGBoost 추론이 항상 실패해서 signal_history에 xgb_prob 미저장 → 내일부터 첫 정상 데이터 축적 시작
- label_3d_5pct = "3일 내 고가 터치" 기준, 실제 수익 실현과는 다름 (고가에 매도 가능 여부 별개)
- AUC 0.6168 = 참고용, 단독 필터로 쓰기엔 약함
- ticker_master 비어 있어 sector 피처 없음

## Next Actions
1. 내일 07:00 KST 텔레그램에서 `ML(3일+5%): XX%` 표시 확인
2. VM DB에서 xgb_prob 분포 쿼리로 의미있는 threshold 탐색
3. threshold 결정 후 S등급 → xgb_prob 필터 적용 코드 수정

## References
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **라벨 정의**: `backtest/labeler.py` — entry=T+1시가, max_high_3d=T+1~T+3 최고가
- **모델**: `data/models/xgb_label_3d_5pct.json` (채택) / 9개 전체 `xgb_label_*.json`
- **ML 스크립트**: `scripts/feature_engineering.py`, `scripts/train_xgboost.py`
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 22:00 UTC(07:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목
- **분석 소요시간**: 약 90분 (세마포어=2, 351종목)

## Last Updated
- 2026-05-12 23:00
