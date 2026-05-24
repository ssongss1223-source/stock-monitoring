#!/bin/bash
# 배포 + 파이프라인 실행 통합 스크립트
# 사용: bash /opt/stock-monitor/scripts/deploy_and_run.sh [--skip-relabel] [--skip-build]
#
# 순서: git pull → notify 모니터 시작 → 파이프라인 실행 (stock 유저)
#   1. build_historical_matrix.py  (재라벨링, --skip-relabel 으로 생략 가능)
#   2. feature_engineering.py --mode build  (피처 계산 → DB 저장 + parquet, --skip-build 로 생략 가능)
#   3. feature_engineering.py --mode train  (DB → parquet 갱신, skip-build 시에도 실행)
#   4. train_models.py

set -e

APP_DIR=/opt/stock-monitor
PY=$APP_DIR/.venv/bin/python
LOG=/opt/stock-monitor/logs/pipeline.log

SKIP_RELABEL=0
SKIP_BUILD=0
for arg in "$@"; do
    case $arg in
        --skip-relabel) SKIP_RELABEL=1 ;;
        --skip-build)   SKIP_BUILD=1 ;;
    esac
done

echo "[$(date '+%H:%M')] git pull 시작"
git -C "$APP_DIR" pull
echo "[$(date '+%H:%M')] git pull 완료"

# 기존 파이프라인 프로세스 확인
if pgrep -f 'build_historical_matrix\|feature_engineering\|train_models' > /dev/null; then
    echo "오류: 파이프라인이 이미 실행 중입니다. 종료 후 다시 시도하세요."
    exit 1
fi

# 로그 초기화 (stock + KHSong 모두 쓸 수 있도록 666)
sudo bash -c "echo '' > $LOG && chown stock:stock $LOG && chmod 666 $LOG"

# Telegram 모니터 시작
pkill -f notify_pipeline.sh 2>/dev/null || true
nohup bash "$APP_DIR/scripts/notify_pipeline.sh" > /tmp/notify.log 2>&1 &
echo "[$(date '+%H:%M')] Telegram 모니터 시작 (PID $!)"

# ── 1단계: 재라벨링 ──────────────────────────────────────────────────
if [ $SKIP_RELABEL -eq 0 ]; then
    CMD="$PY $APP_DIR/scripts/build_historical_matrix.py --skip-features >> $LOG 2>&1 && echo RELABEL_DONE >> $LOG"
else
    echo RELABEL_DONE >> $LOG
    CMD="echo 'RELABEL_DONE(건너뜀)' >> $LOG"
fi

# ── 2단계: 피처 빌드 (ohlcv → DB 저장 + parquet) ────────────────────
if [ $SKIP_BUILD -eq 0 ]; then
    CMD="$CMD && $PY $APP_DIR/scripts/feature_engineering.py --mode build >> $LOG 2>&1 && echo BUILD_DONE >> $LOG"
else
    # build 건너뛸 때도 train 모드로 parquet 갱신
    CMD="$CMD && $PY $APP_DIR/scripts/feature_engineering.py --mode train >> $LOG 2>&1 && echo BUILD_DONE >> $LOG"
fi

# ── 3단계: 모델 학습 ────────────────────────────────────────────────
CMD="$CMD && $PY $APP_DIR/scripts/train_models.py >> $LOG 2>&1 && echo TRAIN_DONE >> $LOG"

# stock 유저로 파이프라인 실행
sudo -u stock bash -c "export PYTHONPATH=$APP_DIR && cd $APP_DIR && nohup bash -c \"$CMD\" > /dev/null 2>&1 &"
echo "[$(date '+%H:%M')] 파이프라인 시작됨 — 로그: $LOG"
