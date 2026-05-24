#!/bin/bash
# 파이프라인 진행상황 Telegram 알림 모니터
# 사용: nohup bash /opt/stock-monitor/scripts/notify_pipeline.sh > /tmp/notify.log 2>&1 &

BOT_TOKEN="$(grep TELEGRAM_BOT_TOKEN /opt/stock-monitor/.env | cut -d= -f2)"
CHAT_ID="$(grep TELEGRAM_CHAT_ID /opt/stock-monitor/.env | cut -d= -f2)"
LOG="/tmp/pipeline.log"

send() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        --data-urlencode "text=$1" > /dev/null 2>&1
}

send "[파이프라인 시작됨] $(date '+%H:%M KST')
1단계: 재라벨링 진행 중 (216,938행, 청크 체크포인트 20,000행)
2단계: feature_engineering 대기
3단계: 학습 (18 라벨, 라벨별 체크포인트) 대기"

RELABEL_DONE=0
FEAT_DONE=0
TRAIN_DONE=0
LAST_HOUR=$(date +%s)
TRAIN_START=0

while true; do
    sleep 30

    if grep -q "^RELABEL_DONE" "$LOG" && [ $RELABEL_DONE -eq 0 ]; then
        RELABEL_DONE=1
        ELAPSED=$(grep "라벨 UPDATE 완료" "$LOG" | tail -1)
        send "[1/3 완료] 재라벨링 완료 $(date '+%H:%M')
→ feature_engineering 시작 중
$ELAPSED"
    fi

    if grep -q "^FEAT_DONE" "$LOG" && [ $FEAT_DONE -eq 0 ]; then
        FEAT_DONE=1
        TRAIN_START=$(date +%s)
        send "[2/3 완료] Feature engineering 완료 $(date '+%H:%M')
→ 모델 학습 시작 (18 라벨, 예상 ~21시간)"
    fi

    if grep -q "^TRAIN_DONE" "$LOG" && [ $TRAIN_DONE -eq 0 ]; then
        TRAIN_DONE=1
        ELAPSED_H=$(( ($(date +%s) - TRAIN_START) / 3600 ))
        ELAPSED_M=$(( (($(date +%s) - TRAIN_START) % 3600) / 60 ))
        send "[3/3 완료] 모델 학습 완료! $(date '+%H:%M')
전체 파이프라인 종료 (학습 소요: ${ELAPSED_H}시간 ${ELAPSED_M}분)"
        exit 0
    fi

    NOW=$(date +%s)
    if [ $FEAT_DONE -eq 1 ] && [ $TRAIN_DONE -eq 0 ] && [ $(( NOW - LAST_HOUR )) -ge 3600 ]; then
        LAST_HOUR=$NOW
        ELAPSED_H=$(( (NOW - TRAIN_START) / 3600 ))
        ELAPSED_M=$(( ((NOW - TRAIN_START) % 3600) / 60 ))
        LAST_LOG=$(grep -E "체크포인트|label_" "$LOG" | tail -3 | tr '\n' ' | ')
        send "[학습 진행 중] $(date '+%H:%M')
경과: ${ELAPSED_H}시간 ${ELAPSED_M}분
최근: $LAST_LOG"
    fi
done
