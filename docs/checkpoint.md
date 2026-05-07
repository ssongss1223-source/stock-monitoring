# Checkpoint

## Current Goal
- 거래량 분석 60분봉 교체 완료 → `python main.py --run-now` 파이프라인 검증

## Current Status
- Phase 1 전체 구현 완료, GCP 배포 완료 (systemd 실행 중)
- 거래량 분석을 일봉 binary flag → 60분봉 종목별 percentile 방식으로 교체 완료 (세션 12)
- 텔레그램 실제 발송 확인됨 (세션 11)
- 미검증: 60분봉 교체 후 `main.py --run-now` 회귀 테스트 아직 미실행

## Done
- Phase 1 에이전트 전체 구현 (market/universe/tech/volume/buy/sell/report/orchestrator)
- OHLCV Parquet 영속화 (일봉 pykrx, 60분봉 yfinance)
- 패턴학습 모듈 (코사인 유사도, 윈도우 최적화, HIGH/MEDIUM/LOW 등급)
- 텔레그램 3단계 메시지 포맷 (전체 분석 목록 + 상승예측 목록 + 상세)
- GCP e2-micro VM + systemd 서비스 등록
- **거래량 분석 60분봉 교체** (세션 12):
  - `VolumeProfileCache` — 종목별 시간대별 P90/P95/P99 산출 + JSON 캐시
  - 5개 신규 feature: `hourly_vol_ratio_p95`, `hourly_vol_zscore_high`, `relative_turnover_high`, `vwap_above_60m`, `obv_slope_up_60m`
  - `foreign_inst_buy` (pykrx 일봉) 유지 — 스마트머니 중기 신호
  - `volume.yaml` 교체 (최대 20점), `buy_grade.yaml` volume_min 재조정 (S:10, A:7, B:4)
  - `orchestrator.py` → `vol_agent.run(df_60m=df_60m)` 전달
  - `config.py` → `VOLUME_PROFILE_CACHE` 경로 추가

## Remaining
- `python main.py --run-now` 회귀 테스트 (60분봉 교체 후 첫 전체 실행)
- `python backtest/pattern_backtest.py --watchlist --test-start 20250401` — HIGH 정밀도 >60% 확인
- GCP VM `--run-now` 최종 검증
- Secret Manager 연동 (현재 .env 직접 기입으로 대체 중)

## Risks / Blockers
- yfinance 한국 주식 60분봉 데이터 누락 시 해당 종목 `volume_score=0` → 매수 신호 미생성 (의도된 동작이나 범위 파악 필요)
- 새 volume_score 범위(0~20)로 buy_grade 임계값 재조정했으나 실데이터 배분 확인 필요

## Next Actions
1. `python main.py --run-now` 실행 → 매수 신호 생성 수 및 등급 분포 확인
2. `volume_score` 분포 확인 — 60분봉 없는 종목 비율 파악
3. 이상 없으면 GCP VM에 `git pull` + 재시작

## References
- 설계: `설계문서.md`, `거래량분석_설계문서.md`
- 스코어링 규칙: `config/scoring/v1_baseline/` (volume.yaml, buy_grade.yaml)
- 거래량 에이전트: `agents/volume_analysis.py` (VolumeProfileCache 포함)
- 캐시: `data/volume_profile_cache.json` (런타임 생성), `data/pattern_cache.json`
- 계획서: `.claude/plans/volume_analysis_60m.md`

## Last Updated
- 2026-05-07 세션 12
