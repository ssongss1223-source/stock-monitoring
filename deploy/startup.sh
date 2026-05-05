#!/bin/bash
# VM 최초 1회 실행 — 의존성 설치 + 코드 배포 + systemd 등록
set -e

REPO_URL="https://github.com/ssongss1223/stock-monitoring.git"  # 본인 repo URL로 변경
APP_DIR="/opt/stock-monitor"
SERVICE_USER="stock"

# 1. 시스템 패키지
apt-get update -q
apt-get install -y python3.11 python3.11-venv python3-pip git

# 2. 앱 유저 생성
id -u $SERVICE_USER &>/dev/null || useradd -m -s /bin/bash $SERVICE_USER

# 3. 코드 클론
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi
chown -R $SERVICE_USER:$SERVICE_USER "$APP_DIR"

# 4. Python 가상환경 + 의존성
sudo -u $SERVICE_USER python3.11 -m venv "$APP_DIR/.venv"
sudo -u $SERVICE_USER "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u $SERVICE_USER "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# 5. Secret Manager에서 .env 생성
PROJECT_ID=$(gcloud config get-value project)
ENV_FILE="$APP_DIR/.env"

write_secret() {
    local key=$1
    local secret_name=$2
    local value
    value=$(gcloud secrets versions access latest --secret="$secret_name" --project="$PROJECT_ID" 2>/dev/null || echo "")
    if [ -n "$value" ]; then
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

rm -f "$ENV_FILE"
write_secret "TELEGRAM_BOT_TOKEN" "TELEGRAM_BOT_TOKEN"
write_secret "TELEGRAM_CHAT_ID"   "TELEGRAM_CHAT_ID"
write_secret "KRX_ID"             "KRX_ID"
write_secret "KRX_PW"             "KRX_PW"
chown $SERVICE_USER:$SERVICE_USER "$ENV_FILE"
chmod 600 "$ENV_FILE"

# 6. 데이터 디렉토리 초기화
sudo -u $SERVICE_USER mkdir -p "$APP_DIR/data"
for f in portfolio.json watchlist.json pattern_cache.json; do
    [ -f "$APP_DIR/data/$f" ] || echo '{}' > "$APP_DIR/data/$f"
done

# 7. systemd 서비스 등록
cat > /etc/systemd/system/stock-monitor.service << EOF
[Unit]
Description=Stock Monitoring Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python main.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stock-monitor
systemctl start stock-monitor

echo "=== 배포 완료 ==="
echo "상태 확인: systemctl status stock-monitor"
echo "로그 확인: journalctl -u stock-monitor -f"
