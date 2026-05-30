# 데이터 품질 검증

VM에서 `scripts/verify_data_quality.py`를 실행하고, 결과를 요약·진단한다.

## 목적

매일 16:00 KST `run_collect` 직후 데이터가 정상으로 적재됐는지 빠르게 확인.
- 적재 누락 / NULL 폭증 / 라벨 채움 지연 / 분포 이상 감지
- 실패해도 자동 차단은 하지 않음 (관찰·경고용)

## 할 일

1. VM SSH로 검증 스크립트 실행:
   ```bash
   cd /opt/stock-monitor && sudo -u stock ./.venv/bin/python scripts/verify_data_quality.py
   ```
2. 출력에서 `[WARN]` / `[FAIL]` 라인 모두 추출.
3. 항목별로 원인 분류:
   - **적재**: ohlcv_daily / universe_daily / universe_features_daily 행수 누락
   - **NULL**: 특정 피처 NULL 비율 임계치(5% 기본) 초과
   - **라벨**: T+15 경과 행에 label_* 미채움
   - **분포**: RSI / bb_position / ma_ratio 범위 이탈
4. 위반 항목별로 다음 액션 후보 제시 (스크립트 자체 버그 / 데이터 소스 문제 / 임계치 조정 필요 등).
5. 임계치 위반이 신규 발생인지, 지속 중인지 git/checkpoint로 교차 확인.

## 응답 형식

## 검증 결과 요약
- 실행 시각: YYYY-MM-DD HH:MM KST
- 전체 통과 / 경고 N건 / 실패 M건

## 위반 상세
- [섹션] 항목: 값 (임계치) — 추정 원인

## 권장 액션
1. ...
2. ...

## 추가 점검 필요 항목
- ...

## 규칙
- 스크립트 결과는 객관적으로 보고. 정상 항목은 짧게 ("OK"만).
- `[FAIL]`이면 진짜 데이터 문제 가능성 — 코드 수정 먼저 고려하지 말고 데이터부터 확인.
- `[WARN]`이면 임계치 튜닝 여지 — 며칠치 추세 확인 후 조정 검토.
- 검증 자체에 오류 (import 실패, DB 락 등) 나면 그 사실만 먼저 보고하고 분석 중단.
- 결과 보고 후 사용자 결정 없이 코드를 자동 수정하지 않는다.
