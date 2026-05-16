# run_ml_pipeline.ps1
# 원클릭 ML 재학습 파이프라인
#
# 사용법:
#   .\run_ml_pipeline.ps1                  # 전체 파이프라인 (VM DB 복사 포함)
#   .\run_ml_pipeline.ps1 -SkipDbCopy      # VM DB 복사 생략 (로컬 DB 사용)
#   .\run_ml_pipeline.ps1 -SkipTraining    # 학습 생략, 배포만
#   .\run_ml_pipeline.ps1 -SkipDeploy      # 배포 생략, 로컬 학습만
#
# 의존성: gcloud CLI, python (xgboost, lightgbm, catboost, duckdb, pandas)

param(
    [switch]$SkipDbCopy,
    [switch]$SkipTraining,
    [switch]$SkipDeploy
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$VM_INSTANCE = "instance-20260505-092414"
$VM_ZONE     = "us-central1-a"
$VM_APP_DIR  = "/opt/stock-monitor"
$LOCAL_ROOT  = $PSScriptRoot

Set-Location $LOCAL_ROOT

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  OK: $msg" -ForegroundColor Green }
function Err($msg)  { Write-Host "  ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── Step 1: VM DB 로컬 복사 ──────────────────────────────────────────────────
if (-not $SkipDbCopy) {
    Step "1/5  VM DB 로컬 복사"
    gcloud compute scp "${VM_INSTANCE}:${VM_APP_DIR}/data/stock.duckdb" `
        "$LOCAL_ROOT\data\stock.duckdb" --zone=$VM_ZONE
    if ($LASTEXITCODE -ne 0) { Err "VM DB 복사 실패" }
    Ok "data\stock.duckdb 복사 완료"
} else {
    Write-Host "  [건너뜀] VM DB 복사 (-SkipDbCopy)" -ForegroundColor Yellow
}

if (-not $SkipTraining) {
    # ── Step 2: DB 마이그레이션 ──────────────────────────────────────────────
    Step "2/5  로컬 DB 마이그레이션"
    python -m data.db
    if ($LASTEXITCODE -ne 0) { Err "data.db 마이그레이션 실패" }
    Ok "스키마 마이그레이션 완료"

    # ── Step 3: Labeler ──────────────────────────────────────────────────────
    Step "3/5  백테스트 라벨 생성"
    python -m backtest.labeler --all --save
    if ($LASTEXITCODE -ne 0) { Err "labeler 실패" }
    Ok "backtest_labels 저장 완료"

    # ── Step 4: Feature engineering ─────────────────────────────────────────
    Step "4/5  피처 엔지니어링"
    python -m scripts.feature_engineering
    if ($LASTEXITCODE -ne 0) { Err "feature_engineering 실패" }
    Ok "data\feature_matrix.parquet 생성 완료"

    # ── Step 5: Train ────────────────────────────────────────────────────────
    Step "5/5  멀티모델 학습 (XGB + LGBM + ExtraTrees + LR Stacking)"
    python -m scripts.train_models
    if ($LASTEXITCODE -ne 0) { Err "train_models 실패" }
    Ok "data\models\ + data\model_meta.json 생성 완료"
} else {
    Write-Host "  [건너뜀] 학습 단계 (-SkipTraining)" -ForegroundColor Yellow
}

# ── Deploy ───────────────────────────────────────────────────────────────────
if (-not $SkipDeploy) {
    Step "배포  모델 파일 VM 전송"

    # models 디렉토리 권한 확보
    gcloud compute ssh $VM_INSTANCE --zone=$VM_ZONE `
        --command="sudo chmod -R 777 ${VM_APP_DIR}/data/models ${VM_APP_DIR}/data/model_meta.json 2>/dev/null; sudo mkdir -p ${VM_APP_DIR}/data/models; echo ok"

    # 모델 파일 전송
    gcloud compute scp --recurse "$LOCAL_ROOT\data\models" `
        "${VM_INSTANCE}:${VM_APP_DIR}/data/" --zone=$VM_ZONE
    if ($LASTEXITCODE -ne 0) { Err "모델 파일 scp 실패" }

    # model_meta.json 전송
    gcloud compute scp "$LOCAL_ROOT\data\model_meta.json" `
        "${VM_INSTANCE}:${VM_APP_DIR}/data/model_meta.json" --zone=$VM_ZONE
    if ($LASTEXITCODE -ne 0) { Err "model_meta.json scp 실패" }

    Ok "모델 파일 전송 완료"

    Step "배포  VM git pull + 패키지 설치 + 서비스 재시작"
    gcloud compute ssh $VM_INSTANCE --zone=$VM_ZONE `
        --command="cd ${VM_APP_DIR} && sudo git pull && sudo .venv/bin/pip install -q lightgbm scikit-learn && sudo systemctl stop stock-monitor && sudo .venv/bin/python -m data.db && sudo systemctl start stock-monitor && systemctl is-active stock-monitor"
    if ($LASTEXITCODE -ne 0) { Err "VM 배포 실패" }
    Ok "VM 배포 완료"
} else {
    Write-Host "  [건너뜀] VM 배포 (-SkipDeploy)" -ForegroundColor Yellow
}

Write-Host "`n=== 파이프라인 완료 ===" -ForegroundColor Green
