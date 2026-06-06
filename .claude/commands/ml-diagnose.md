# ML 파이프라인 진단

ML 평가 작업 시작 전 또는 "0 rows / 라벨 없음" 오류 발생 시 실행.

## 언제 사용하나
- `evaluate_predictions.py` 실행 전
- "0 labeled rows" / "라벨 없음" 오류 발생 시
- 트랙 B ML 관련 작업 세션 시작 시
- 파이프라인 실행 후 상태 확인 시

## 진단 실행 명령

```
cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 scripts/diagnose_pipeline.py
```

## 결과 해석

| 항목 | 정상 | 비정상 |
|------|------|--------|
| ohlcv_daily 최신 날짜 | 평일 당일/전일 | 2일+ 이상 공백 |
| universe_daily lbl_3d | 종목수와 동일 | 0 또는 크게 적음 |
| signal_xgb_probs | `_clean` label 존재 | 데이터 없음 |
| Prec@K 평가 가능 | 교집합 날짜 >= 1 | 겹치는 날짜 없음 |

## 평가 가능 시 실행 명령

```
cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save
```

## 라벨 없음 진단 플로우

1. `signal_xgb_probs` 의 `_clean`/`first_*` label 시작 날짜 확인
2. `universe_daily` 라벨(`label_3d_3pct_clean`) 최신 날짜 확인
3. signal 날짜 >= label 날짜+1 이면 → 파이프라인 실행 대기
4. 파이프라인 실행: 평일 17:30 KST (08:30 UTC)
5. 파이프라인 후 재진단 → 교집합 날짜 확인

## 원인별 조치

- **UPDATE 버그**: `orchestrator.py` label backfill이 실패하면 라벨이 채워지지 않음. `git log` 로 최근 수정 확인.
- **파이프라인 미실행**: 주말 또는 오류로 실행되지 않았을 때. `journalctl -u stock-monitor --since "24h ago"` 로 확인.
- **label 유형 불일치**: `signal_xgb_probs.label` 컬럼 값이 `evaluate_predictions.py` 의 `_ALL_LABELS` 와 다를 때.
