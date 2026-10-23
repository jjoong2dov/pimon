#!/bin/sh
# 직전 배포로 되돌리기.  사용: sh deploy/rollback.sh pi@파이주소
#   --remove 를 붙이면 pimon 을 완전히 지운다(설정 /etc/pimon 은 남김).
set -eu
HOST=${1:?사용: sh deploy/rollback.sh pi@파이주소 [--remove]}
MODE=${2:-}
if [ "$MODE" = "--remove" ]; then
  ssh "$HOST" 'sudo systemctl disable --now pimon 2>/dev/null || true
sudo rm -f /etc/systemd/system/pimon.service && sudo systemctl daemon-reload
sudo rm -rf /opt/pimon /opt/pimon.prev
echo "pimon 지움 (/etc/pimon 은 남김)"'
  exit 0
fi
ssh "$HOST" 'set -eu
[ -d /opt/pimon.prev ] || { echo "되돌릴 이전 판이 없음 (/opt/pimon.prev)"; exit 1; }
sudo rm -rf /opt/pimon && sudo mv /opt/pimon.prev /opt/pimon
sudo systemctl restart pimon
sleep 3
systemctl is-active pimon'
