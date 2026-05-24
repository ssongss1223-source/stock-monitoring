#!/bin/bash
# 배포 + 파이프라인 실행 통합 스크립트
# 사용: bash /opt/stock-monitor/scripts/deploy_and_run.sh [--skip-relabel] [--skip-features]
#
# 순서: git pull → notify 모니터 시작 → 파이프라인 실행 (stock 유저)
# git pull 실패 시 파이프라인을 실행하지 않음.

set -e

APP_DIR=/opt/stock-monitor
PY=$APP_DIR/.venv/bin/python
LOG=/tmp/pipeline.log

SKIP_RELABEL=0
SKIP_FEATURES=0
for arg in "$@"; do
    case $arg in
        --skip-relabel)  SKIP_RELABEL=1 ;;
        --skip-features) SKIP_FEATURES=1 ;;
    esac
done

echo "[$(date '+%H:%M')] git pull 시작"
sudo chmod o+w "$APP_DIR/backtest" "$APP_DIR/scripts"
git -C "$APP_DIR" pull
sudo chmod o-w "$APP_DIR/backtest" "$APP_DIR/scripts"
echo "[$(date '+%H:%M')] git pull 완료"

# 기존 파이프라인 프로세스 확인
if pgrep -f 'build_historical_matrix\|feature_engineering\|train_models' > /dev/null; then
    echo "오류: 파이프라인이 이미 실행 중입니다. 종료 후 다시 시도하세요."
    exit 1
fi

# 로그 초기화
sudo bash -c "echo '' > $LOG && chown stock:stock $LOG"

# Telegram 모니터 시작
pkill -f notify_pipeline.sh 2>/dev/null || true
nohup bash "$APP_DIR/scripts/notify_pipeline.sh" > /tmp/notify.log 2>&1 &
echo "[$(date '+%H:%M')] Telegram 모니터 시작 (PID $!)"

# 파이프라인 커맨드 구성
CMD=""
if [ $SKIP_RELABEL -eq 0 ]; then
    CMD="$PY $APP_DIR/scripts/build_historical_matrix.py --skip-features >> $LOG 2>&1 && echo RELABEL_DONE >> $LOG"
else
    CMD="echo 'RELABEL_DONE(건너뜀)' >> $LOG"
    echo RELABEL_DONE >> $LOG
fi

if [ $SKIP_FEATURES -eq 0 ]; then
    CMD="$CMD && $PY $APP_DIR/scripts/feature_engineering.py >> $LOG 2>&1 && echo FEAT_DONE >> $LOG"
else
    CMD="$CMD && echo 'FEAT_DONE(건너뜀)' >> $LOG"
    echo FEAT_DONE >> $LOG
fi

CMD="$CMD && $PY $APP_DIR/scripts/train_models.py >> $LOG 2>&1 && echo TRAIN_DONE >> $LOG"

# stock 유저로 파이프라인 실행
sudo -u stock bash -c "export PYTHONPATH=$APP_DIR && cd $APP_DIR && nohup bash -c \"$CMD\" > /dev/null 2>&1 &"
echo "[$(date '+%H:%M')] 파이프라인 시작됨 — 로그: $LOG"
