#!/bin/bash
# 파이프라인 진행상황 Telegram 알림 모니터
# 사용: nohup bash /opt/stock-monitor/scripts/notify_pipeline.sh > /tmp/notify.log 2>&1 &
#
# 마커 순서: RELABEL_DONE → BUILD_DONE → TRAIN_DONE
# train phase: 라벨 완료(체크포인트:)마다 알림 + 1시간마다 진행 알림

BOT_TOKEN="$(grep TELEGRAM_BOT_TOKEN /opt/stock-monitor/.env | cut -d= -f2)"
CHAT_ID="$(grep TELEGRAM_CHAT_ID /opt/stock-monitor/.env | cut -d= -f2)"
LOG="/opt/stock-monitor/logs/pipeline.log"

send() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        --data-urlencode "text=$1" > /dev/null 2>&1
}

send "[파이프라인 시작됨] $(date '+%H:%M KST')
1단계: 재라벨링 (건너뜀 or 진행 중)
2단계: 피처 빌드 (universe_features_daily 백필, 3~4시간 예상)
3단계: 모델 학습 (18 라벨, ~21시간 예상)"

RELABEL_DONE=0
BUILD_DONE=0
TRAIN_DONE=0

LAST_HOURLY=$(date +%s)
CURRENT_PHASE_START=$(date +%s)
CURRENT_PHASE="relabel"

# train phase: 완료된 라벨 수 추적
LABEL_DONE_COUNT=0

while true; do
    sleep 30
    NOW=$(date +%s)

    # ── 1단계: 재라벨링 완료 감지 ──────────────────────────────────────
    if grep -q "^RELABEL_DONE" "$LOG" 2>/dev/null && [ $RELABEL_DONE -eq 0 ]; then
        RELABEL_DONE=1
        CURRENT_PHASE="build"
        CURRENT_PHASE_START=$NOW
        LAST_HOURLY=$NOW
        send "[1/3 완료] 재라벨링 완료 $(date '+%H:%M')
→ 피처 빌드 시작 (universe_features_daily 백필 중)"
    fi

    # ── 2단계: 피처 빌드 완료 감지 ─────────────────────────────────────
    if grep -q "^BUILD_DONE" "$LOG" 2>/dev/null && [ $BUILD_DONE -eq 0 ]; then
        BUILD_DONE=1
        CURRENT_PHASE="train"
        CURRENT_PHASE_START=$NOW
        LAST_HOURLY=$NOW
        ELAPSED_BUILD=$(( (NOW - CURRENT_PHASE_START) / 60 ))
        send "[2/3 완료] 피처 빌드 완료 $(date '+%H:%M')
소요: ${ELAPSED_BUILD}분
→ 모델 학습 시작 (18 라벨, 예상 ~21시간)"
    fi

    # ── 3단계: 학습 완료 감지 ───────────────────────────────────────────
    if grep -q "^TRAIN_DONE" "$LOG" 2>/dev/null && [ $TRAIN_DONE -eq 0 ]; then
        TRAIN_DONE=1
        ELAPSED_TRAIN_H=$(( (NOW - CURRENT_PHASE_START) / 3600 ))
        ELAPSED_TRAIN_M=$(( ((NOW - CURRENT_PHASE_START) % 3600) / 60 ))
        send "[3/3 완료] 모델 학습 완료! $(date '+%H:%M')
학습 소요: ${ELAPSED_TRAIN_H}시간 ${ELAPSED_TRAIN_M}분
전체 파이프라인 종료"
        exit 0
    fi

    # ── train phase: 라벨 완료(체크포인트:) 감지 → 즉시 알림 ──────────
    if [ "$CURRENT_PHASE" = "train" ]; then
        NEW_COUNT=$(grep -c "^체크포인트:" "$LOG" 2>/dev/null || echo 0)
        if [ "$NEW_COUNT" -gt "$LABEL_DONE_COUNT" ]; then
            LABEL_DONE_COUNT=$NEW_COUNT
            ELAPSED_H=$(( (NOW - CURRENT_PHASE_START) / 3600 ))
            ELAPSED_M=$(( ((NOW - CURRENT_PHASE_START) % 3600) / 60 ))
            LAST_CKPT=$(grep "^체크포인트:" "$LOG" 2>/dev/null | tail -3 | tr '\n' '\n')
            send "[학습 진행] ${LABEL_DONE_COUNT}/18 라벨 완료 $(date '+%H:%M')
경과: ${ELAPSED_H}시간 ${ELAPSED_M}분
${LAST_CKPT}"
            LAST_HOURLY=$NOW
        fi
    fi

    # ── 시간당 진행 알림 (build 또는 train phase 진행 중) ──────────────
    if [ $(( NOW - LAST_HOURLY )) -ge 3600 ]; then
        LAST_HOURLY=$NOW
        ELAPSED_H=$(( (NOW - CURRENT_PHASE_START) / 3600 ))
        ELAPSED_M=$(( ((NOW - CURRENT_PHASE_START) % 3600) / 60 ))

        if [ "$CURRENT_PHASE" = "build" ]; then
            LAST_LOG=$(tail -3 "$LOG" 2>/dev/null | tr '\n' ' | ')
            send "[빌드 진행 중] $(date '+%H:%M')
경과: ${ELAPSED_H}시간 ${ELAPSED_M}분
최근 로그: $LAST_LOG"

        elif [ "$CURRENT_PHASE" = "train" ]; then
            LAST_LOG=$(grep "^→ 최고 모델:" "$LOG" 2>/dev/null | tail -3 | tr '\n' ' | ')
            send "[학습 진행 중] $(date '+%H:%M')
${LABEL_DONE_COUNT}/18 라벨 완료, 경과: ${ELAPSED_H}시간 ${ELAPSED_M}분
최근: $LAST_LOG"
        fi
    fi
done
