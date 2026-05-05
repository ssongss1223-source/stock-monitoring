#!/bin/bash
# 코드 업데이트 + 서비스 재시작
set -e

APP_DIR="/opt/stock-monitor"

git -C "$APP_DIR" pull
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
systemctl restart stock-monitor

echo "업데이트 완료"
systemctl status stock-monitor --no-pager
