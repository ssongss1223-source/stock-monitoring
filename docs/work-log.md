# Work Log

## 목적
이 파일은 세션별 작업 이력과 상세 기록을 누적해서 저장한다.

`docs/checkpoint.md`에 남기에는 길거나 상세한 정보,
예를 들어 디버깅 메모, 시도한 방법, 실패 원인, 세션별 진행 내용 등을 기록하는 용도로 사용한다.

## 작성 원칙
- 상세 기록은 이 파일에 누적한다.
- 너무 장황한 일지보다는, 실제로 다음 작업에 도움이 되는 정보를 남긴다.
- 긴 설명이 필요하면 항목별 bullet로 정리한다.
- `checkpoint.md`에는 요약만 남기고, 상세 내용은 이 파일로 분리한다.

## 기록 템플릿

## YYYY-MM-DD HH:mm
- 작업:
- 변경 사항:
- 관련 파일:
- 메모:
- 다음 아이디어:

## 예시

## 2026-05-06 14:10
- 작업: 로그인 리다이렉트 문제 원인 분석
- 변경 사항: 문제를 재현했고, 인증 초기화 흐름을 추적함
- 관련 파일: `src/auth/useAuth.ts`, `src/app/router.tsx`
- 메모: 사용자 상태가 안정되기 전에 redirect가 먼저 발생함
- 다음 아이디어: 초기화 순서를 조정한 뒤 로그인 흐름 재검증

---

## 2026-05-07 세션 12 — 거래량 분석 60분봉 기반 교체

- 작업: `VolumeAnalysisAgent` 일봉 binary flag 9개 → 60분봉 종목별 percentile 방식으로 완전 교체
- 변경 사항:
  - `agents/volume_analysis.py` 전체 재작성
    - `VolumeProfileCache` 클래스 추가: 종목별 시간대별 P90/P95/P99 산출, `data/volume_profile_cache.json`에 당일 1회 캐시
    - 신규 5개 feature 함수: `_same_time_vol_ratio`, `_volume_zscore_60m`, `_relative_turnover_60m`, `_vwap_above_60m`, `_obv_slope_60m`
    - `_foreign_inst_flow` (pykrx 일봉 기반) 유지 — 스마트머니 중기 신호 보존
    - `run()` 시그니처 변경: `df_60m` 파라미터 추가. `df_60m=None`이면 `volume_score=0` 반환
  - `config/scoring/v1_baseline/volume.yaml`: 9개 rule → 6개 rule, 최대 점수 24 → 20점
  - `config/scoring/v1_baseline/buy_grade.yaml`: volume_min 재조정 (S: 12→10, A: 8→7, B: 5→4)
  - `agents/orchestrator.py`: `vol_agent.run(df_60m=df_60m)` 전달 추가 (1줄 변경)
  - `config.py`: `VOLUME_PROFILE_CACHE = "data/volume_profile_cache.json"` 추가
- 핵심 설계:
  - 종목마다 자신의 60분봉 히스토리(~60일)로 고유 임계값 산출 → 대형주/소형주 동일 기준 적용 방지
  - `hourly_vol_ratio_p95`: 마지막 거래일 내 어느 봉이든 동일 시간대 P95 초과 시 True (5pt, 핵심 신호)
  - `volume_zscore_high`: 과거 분포 대비 최대 거래량 z-score ≥ 2.0 (4pt)
  - `relative_turnover_high`: 당일 거래대금 합산 과거 P80 이상 (3pt)
  - `vwap_above_60m`: 장 마감 종가 > 당일 VWAP (2pt)
  - `obv_slope_up_60m`: 최근 20개 60분봉 OBV 기울기 양수 (3pt)
  - `foreign_inst_buy`: pykrx 5거래일 순매수 (3pt)
- 메모:
  - yfinance 데이터 없는 종목은 volume_score=0 → 매수 신호 없음 (의도된 동작)
  - OBV slope는 MVP 단순화(slope > 0). 추후 과거 slope 분포 P70 기반으로 고도화 가능
- 다음 아이디어: `python main.py --run-now`으로 60분봉 교체 후 첫 전체 파이프라인 검증