# Checkpoint

## Current Goal
- `signal_xgb_probs` 데이터 누적 확인 + 신호 사후 검증 자동화 구현

## Current Status
- soft voting 운영 중 (XGB+LGBM+ET). 5/16 수동 실행 후 `signal_xgb_probs` 여전히 비어있음 (MCP로 확인)
- DuckDB MCP 서버 구축 완료 — 대화 중 직접 DB 쿼리 가능 (tmp_*.py 불필요)
- 서비스 문서 정비 완료 (`docs/system.md`, `docs/mlops.md`)

## Done
- DuckDB MCP 서버 구축 (`mcp/mcp_duckdb.py`, `.mcp.json`) — 세션 37
- 서비스 문서 작성 (`docs/system.md`, `docs/mlops.md`) — 세션 37
- Soft voting 전환 + VM 배포 (XGB+LGBM+ET 항상 앙상블) — 세션 35
- S등급 RR≥2.0 필터 + ML확률 정렬 — 세션 34
- CatBoost → ExtraTrees 교체 + 재학습 — 세션 34

## Remaining

**[확인 필요] signal_xgb_probs 비어있음**
- 5/19(월) 정규 실행 후 데이터 들어오는지 확인
- MCP로 바로 쿼리: `SELECT label, COUNT(*), AVG(xgb_prob) FROM signal_xgb_probs GROUP BY label`

**[중기] 신호 사후 검증 자동화**
- `signal_history × ohlcv_daily` JOIN → n일 후 max_high 자동 계산
- `/verify N` 텔레그램 명령 구현

**[보류]**
- sector 데이터 / market_cap NULL (KRX API 차단)

## Risks / Blockers
- `signal_xgb_probs`가 비어있는 원인 불명 — 파이프라인이 정상 완료됐는지 VM 로그 확인 필요
- 학습 데이터 누적 속도에 따라 모델 재학습 시점 판단 필요

## Next Actions
1. 5/19(월) 정규 파이프라인 실행 후 `signal_xgb_probs` MCP로 확인
2. VM 로그 확인 — 5/16 수동 실행이 정상 완료됐는지
3. 사후 검증 자동화 구현 (`backtest_labels` 자동 갱신 + 텔레그램 `/verify`)

## References
- **시스템 개요**: `docs/system.md`
- **MLOps/DB 스키마**: `docs/mlops.md`
- **DuckDB MCP**: `mcp/mcp_duckdb.py` (Claude Code 대화 중 직접 쿼리)
- **라이브 추론**: `agents/ml_scorer.py` (`score_all_labels()` — soft voting 고정)
- **필터링**: `agents/orchestrator.py` line ~168 (S등급 + RR≥2.0, top 10 cap)
- **재학습**: `run_ml_pipeline.ps1`
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC / 분석 21:00 UTC

## Last Updated
- 2026-05-16 KST (세션 37)
