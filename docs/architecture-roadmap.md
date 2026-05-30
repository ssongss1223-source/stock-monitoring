# Architecture Roadmap

> **목적**: 이 프로젝트의 장기 진화 방향과 주요 아키텍처 결정을 한 곳에 기록.
> 단순 체크리스트(checkpoint.md)나 세션별 일지(work-log)와 달리, **왜 이런 구조로 가는가**를 설명한다.

---

## 큰 그림

**비전**: feature 후보군을 끊임없이 생성·테스트·검증·승격할 수 있는 **자동화된 ML 실험 플랫폼**.

**현재 위치**: Phase 0 (단일 모델 + wide 테이블 구조)
**다음 1년 목표**: Phase 5-7 (모델 다양화 + Promotion Gate + Spot 학습 분리)

---

## 핵심 결정 사항 (2026-05-30)

### D1. 데이터 마트는 4계층 + 4메타 객체로 분리한다

**배경**:
- 현재 `universe_daily`가 만능 테이블 (원천 복제 + 1차 피처 + 스코어 + ML 예측 + 라벨 모두)
- 컬럼 100+ 이고 라벨/모델 추가마다 `ALTER TABLE` 필요 → 2회 BinderException 발생 (b228779에서 try/except 격리)
- feature 후보군이 늘어날수록 테이블 변경 위험·복잡도 비선형 증가

**결정**:
```
[L0 원천]    ohlcv_daily · hourly_60m · market_index · macro_daily  (그대로)
[L1 피처]    universe_features_daily  (wide 유지, sklearn 친화)
[L2 분석]    universe_predictions  (long, model_ver 포함)
            universe_labels       (long)
            universe_scores       (옵션)
[L3 라이브]  signal_history · signal_xgb_probs · backtest_labels  (그대로)

⚙ 메타:
  feature_catalog       — 어떤 feature 있고 어디 들어가는지
  model_registry        — 모델별 version·feature·status (shadow/production/retired)
  evaluation_history    — 모델·라벨·날짜별 OOS 평가 결과
  experiment_runs       — 자동화된 실험 기록 (P6+)
```

**근거**:
- wide → long 전환의 핵심 이득: **모델/라벨 추가 = INSERT만, ALTER 0**
- 학습 시 pivot 비용은 pandas 한 줄로 해결
- universe_daily의 wide 컬럼은 점진 폐기 (기존 코드 호환 유지하면서 6개월 후 DROP)

**적용 시점**: Phase 3 (3주차 예정)

---

### D2. 검증 지표는 AUC 외에 TopK·Brier·Lift 추가

**배경**:
- AUC = 전체 종목 순위 분리력
- 실전 텔레그램은 매일 상위 ~12종목만 발송 → AUC 0.65 모델도 top 12에선 별로일 수 있음
- 현재 평가 함수 자체가 없음 (학습 시 한 번 측정한 AUC만 하드코딩 — `_LABEL_AUC`)

**결정 — 추가할 지표**:
| 지표 | 정의 | 목적 |
|------|------|------|
| TopK precision | 상위 K종목 중 라벨=1 비율 | 실전 신호 정확도 |
| TopK return | 상위 K종목의 평균 실현 수익률 | 수익화 가능성 |
| Hit rate by decile | 상위 10/20/.../100%의 정답률 | 모델 작동 구간 |
| Brier score | (prob - actual)² 평균 | calibration |
| Lift @ K | TopK precision ÷ 전체 평균 | 랜덤 대비 |
| Coverage | 신호 발생일 ÷ 전체일 | 과도한 보수성 방지 |

**적용 시점**: Phase 1-2 (1-2주차)

---

### D3. 모델은 XGB + LGBM 외에 CatBoost·Stacking·(나중) TabPFN 추가

**배경**:
- 현재 XGB + LGBM soft voting (ET는 RAM 부족으로 제외 — dfce4c6)
- e2-medium 증설 (4GB)로 모델 확장 여력 확보

**결정 — 추가 후보 (난이도 순)**:
1. **CatBoost** ★ — XGB/LGBM과 인터페이스 동일, 범주형·결측 강함
2. **Logistic Regression + L1/L2** ★ — 베이스라인 + 해석 가능
3. **Stacking (XGB+LGBM+LR → LR meta)** ★★ — 앙상블 효과 ↑
4. **TabPFN** ★★ — 사전학습 transformer, 작은 데이터에 강함 (8GB+ 필요할 수도)
5. **TabNet** ★★★ — 딥러닝 tabular, GPU 권장 (P7+)

**적용 시점**: Phase 4 (CatBoost 시작), P5-7 (점진)

---

### D4. 운영-학습 VM 분리는 Phase 5부터

**배경**:
- Phase 4까지는 e2-medium 단일로 가능 (학습 시 잠시 느려지는 정도)
- Phase 5(모델 4개+ 다양화) 시점에 운영·학습 동시성 충돌 시작
- Spot VM 비용이 매우 저렴 (~$1/월)

**결정 — Phase 5 분리 아키텍처**:
```
운영 VM:    e2-small (2GB) 또는 e2-medium (4GB) — 24/7 active
   ├─ 16:00 KST run_collect
   ├─ 05:00 KST run_daily (production 모델만 추론)
   └─ 텔레그램 발송

학습 VM:    e2-standard-4 Spot (16GB) — 주말에만 띄움
   ├─ Walk-forward CV
   ├─ 새 모델 학습 (shadow 등록)
   └─ 끝나면 자동 stop
```

**적용 시점**: Phase 5 (1-2개월 후)

---

### D5. Promotion Gate로 모델 자동 승격·롤백

**배경**:
- 새 모델 추가 시 수동 비교·승격은 실수 위험
- "shadow" → "production" 전환 기준이 명확해야 안전

**결정 — Promotion 기준 초안**:
- baseline 대비 `TopK_precision` **+2% 이상** AND
- `Brier score` 악화 **5% 이내** AND
- 7일 연속 OOS 평가 통과

자동 승격되면:
- `model_registry.status`: shadow → production
- 기존 production → retired (롤백 가능)

**적용 시점**: Phase 7 (3-4개월 후)

---

### D6. 텔레그램 정렬: 거래량점수 → ML확률 → AUC → 추세점수

**배경**:
- 기존: AUC 가중 ML score 단일 정렬
- 사용자 요구: 거래량 강한 종목을 최상단에 두고, 그다음 모델 신뢰, 그다음 추세

**결정 — 정렬 키 (e6bfb72)**:
```python
sort_key = (-volume_score, -prob, -auc, -trend_score)
```

**그룹 표시 순서**: 단기/대형 → 단기/중소형 → 스윙/대형 → 스윙/중소형
**중복 제거**: 위 순서로 선점, 후순위 그룹에서 제외

---

### D7. VM 증설: e2-micro → e2-medium + 50GB (2026-05-30)

**배경**:
- e2-micro (1GB) 에서 swap 1.2GB 사용 중 → RAM 압박
- ET 모델 못 올림, 학습 어려움
- 디스크 73% 사용 (7.6GB 남음) → 6개월 내 한계
- GCP 무료 크레딧 ₩419,105 / 66일 남음 → 활용

**결정 — 옵션 C 채택**:
```
운영 VM    e2-medium (2 vCPU, 4GB RAM) + 디스크 50GB
   ├─ 월 비용: ~₩20,000 (~$15)
   └─ 크레딧으로 차감 → 8/4까지 청구 ₩0

(향후 P5+)
학습 VM    e2-standard-4 Spot (16GB) — 필요 시 추가
   └─ ~₩1,300/월
```

**Before / After**:
| 항목 | Before (e2-micro) | After (e2-medium) |
|------|-------------------|-------------------|
| RAM | 969Mi (avail 515Mi) | 3.8Gi (avail 3.2Gi) |
| Swap 사용 | 1.2Gi 🔴 | 0 🟢 |
| Disk 여유 | 7.6G (73% used) | 27G (44% used) |
| CPU baseline | 0.25 vCPU | 1.0 vCPU |

---

### D8. 8/4 무료 체험 만료 → 7월 말 유료 Cloud Billing 업그레이드

**배경**:
- 무료 체험 만료 시 VM 자동 stop (데이터는 30일 유예 후 삭제 가능)
- 카드 청구 시작이 아니라 "리소스 중지"
- Budget Alert ₩30,000 이미 설정

**결정**:
- **7월 말까지** Console에서 유료 Cloud Billing 업그레이드 1회 클릭
- 무료 크레딧은 그대로 사용 (소진 후 카드 청구)
- 캘린더 알림 등록 권장

---

## Phase별 로드맵 요약

| Phase | 기간 | 목표 | 의존성 | VM 요구 |
|-------|------|------|--------|---------|
| **P1** | 1-2일 | `verify_data_quality.py` + TopK 평가 함수 | 없음 | e2-medium |
| **P2** | 2-3일 | `feature_catalog` + `model_registry` + `evaluation_history` | P1 | e2-medium |
| **P3** | 3-5일 | `universe_predictions` + `universe_labels` (long) | P2 | e2-medium |
| **P4** | 2-3일 | CatBoost 추가, long 마트에서 첫 학습·평가 | P3 | e2-medium |
| **P5** | 2일 | TopK·Brier·Lift 등 evaluation_history 확장 | P3 | e2-medium |
| **P6** | 1-2주 | Feature DSL (yaml → 자동 백필·shadow 등록) | P2-P5 | + Spot 학습 VM |
| **P7** | 1주 | Promotion Gate (shadow→production 자동) | P6 | + Spot 학습 VM |

---

## 보존 원칙

이 문서는 **결정의 근거(Why)**를 기록한다.
- **What/How**는 코드와 commit message에 남음
- **When**은 checkpoint.md와 work-log에 남음
- **Why**는 여기에만 남음

미래의 자신/협업자가 "왜 이 구조로 갔지?"를 물을 때 답을 찾는 곳.

---

## 변경 이력

| 날짜 | 변경 | 결정 ID |
|------|------|---------|
| 2026-05-30 | 초안 작성 — Phase 0→1 진입 전 큰 그림 정립 | D1-D8 |
