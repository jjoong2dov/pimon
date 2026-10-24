#!/bin/sh
# 바로 전 판으로 되돌리기.  사용: sh deploy/rollback.sh pi@파이주소
#   --remove 를 붙이면 pimon 을 완전히 지운다(설정 /etc/pimon 은 남김).
set -eu
HOST=${1:?사용: sh deploy/rollback.sh pi@파이주소 [--remove]}
if [ "${2:-}" = "--remove" ]; then
  ssh "$HOST" sh -s <<'REMOTE'
sudo systemctl disable --now pimon 2>/dev/null || true
sudo rm -f /etc/systemd/system/pimon.service
sudo systemctl daemon-reload
sudo rm -rf /opt/pimon /run/pimon
echo "pimon 지움 (/etc/pimon 은 남김)"
REMOTE
  exit 0
fi
ssh "$HOST" sh -s <<'REMOTE'
set -eu
PREV=$(readlink /opt/pimon/previous) || { echo "되돌릴 전 판이 없음"; exit 1; }
CUR=$(readlink /opt/pimon/current)
# 먼저 멈춰야 지금 판의 ExecStopPost(LED 되돌리기)가 지금 판의 unit 과 코드로 돈다 (deploy.sh 의 switch 와 같은 순서)
sudo systemctl stop pimon
sudo ln -sfn "$PREV" /opt/pimon/current.new && sudo mv -T /opt/pimon/current.new /opt/pimon/current
sudo ln -sfn "$CUR" /opt/pimon/previous
sudo cp "$PREV/deploy/pimon.service" /etc/systemd/system/pimon.service
sudo systemctl daemon-reload
sudo systemctl start pimon
sleep 3
if (cd /opt/pimon/current && python3 -m pimon health --config /etc/pimon/pimon.toml); then
    echo "되돌림: $CUR -> $PREV"
else
    echo "되돌린 판이 응답하지 않음. 손으로 확인 필요: sudo journalctl -u pimon"
    exit 1
fi
REMOTE
